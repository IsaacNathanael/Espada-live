from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.nn import functional as functional
from torchvision.models import ResNet34_Weights, resnet34


@dataclass(frozen=True)
class ModelConfig:
    architecture: str = "ResNet34 U-Net"
    input_channels: int = 1
    output_classes: int = 1
    input_min_db: float = -35.0
    input_max_db: float = 5.0
    pretrained_encoder: bool = True
    attention_decoder: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class AttentionGate(nn.Module):
    def __init__(self, skip_channels: int, gate_channels: int) -> None:
        super().__init__()
        intermediate = max(min(skip_channels, gate_channels) // 2, 16)
        self.skip_projection = nn.Conv2d(skip_channels, intermediate, kernel_size=1, bias=False)
        self.gate_projection = nn.Conv2d(gate_channels, intermediate, kernel_size=1, bias=False)
        self.mask = nn.Sequential(
            nn.ReLU(inplace=True),
            nn.Conv2d(intermediate, 1, kernel_size=1),
            nn.Sigmoid(),
        )

    def forward(self, skip: torch.Tensor, gate: torch.Tensor) -> torch.Tensor:
        attention = self.mask(self.skip_projection(skip) + self.gate_projection(gate))
        return skip * attention


class DecoderBlock(nn.Module):
    def __init__(
        self,
        input_channels: int,
        skip_channels: int,
        output_channels: int,
        *,
        attention: bool = False,
    ) -> None:
        super().__init__()
        self.up = nn.ConvTranspose2d(input_channels, output_channels, kernel_size=2, stride=2)
        self.attention = AttentionGate(skip_channels, output_channels) if attention else None
        self.refine = nn.Sequential(
            nn.Conv2d(output_channels + skip_channels, output_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(output_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(output_channels, output_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(output_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, features: torch.Tensor, skip: torch.Tensor) -> torch.Tensor:
        features = self.up(features)
        if features.shape[-2:] != skip.shape[-2:]:
            features = functional.interpolate(
                features, size=skip.shape[-2:], mode="bilinear", align_corners=False
            )
        if self.attention is not None:
            skip = self.attention(skip, features)
        return self.refine(torch.cat((features, skip), dim=1))


class ResNet34UNet(nn.Module):
    """Binary U-Net with an ImageNet-pretrained ResNet34 encoder and one SAR channel."""

    def __init__(
        self, *, pretrained_encoder: bool = True, attention_decoder: bool = False
    ) -> None:
        super().__init__()
        weights = ResNet34_Weights.DEFAULT if pretrained_encoder else None
        encoder = resnet34(weights=weights)
        original_weight = encoder.conv1.weight.detach().clone()
        encoder.conv1 = nn.Conv2d(
            1, 64, kernel_size=7, stride=2, padding=3, bias=False
        )
        if pretrained_encoder:
            with torch.no_grad():
                encoder.conv1.weight.copy_(original_weight.mean(dim=1, keepdim=True))

        self.stem = nn.Sequential(encoder.conv1, encoder.bn1, encoder.relu)
        self.pool = encoder.maxpool
        self.encoder1 = encoder.layer1
        self.encoder2 = encoder.layer2
        self.encoder3 = encoder.layer3
        self.encoder4 = encoder.layer4

        self.decoder4 = DecoderBlock(512, 256, 256, attention=attention_decoder)
        self.decoder3 = DecoderBlock(256, 128, 128, attention=attention_decoder)
        self.decoder2 = DecoderBlock(128, 64, 64, attention=attention_decoder)
        self.decoder1 = DecoderBlock(64, 64, 64, attention=attention_decoder)
        self.final = nn.Sequential(
            nn.ConvTranspose2d(64, 32, kernel_size=2, stride=2),
            nn.Conv2d(32, 32, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 1, kernel_size=1),
        )

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        stem = self.stem(image)
        encoder1 = self.encoder1(self.pool(stem))
        encoder2 = self.encoder2(encoder1)
        encoder3 = self.encoder3(encoder2)
        encoder4 = self.encoder4(encoder3)
        decoded = self.decoder4(encoder4, encoder3)
        decoded = self.decoder3(decoded, encoder2)
        decoded = self.decoder2(decoded, encoder1)
        decoded = self.decoder1(decoded, stem)
        logits = self.final(decoded)
        if logits.shape[-2:] != image.shape[-2:]:
            logits = functional.interpolate(
                logits, size=image.shape[-2:], mode="bilinear", align_corners=False
            )
        return logits


class BCEDiceLoss(nn.Module):
    def __init__(self, *, positive_weight: float = 4.0, dice_weight: float = 0.5) -> None:
        super().__init__()
        self.register_buffer("positive_weight", torch.tensor([positive_weight]))
        self.dice_weight = dice_weight

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        binary_cross_entropy = functional.binary_cross_entropy_with_logits(
            logits, target, pos_weight=self.positive_weight
        )
        probability = torch.sigmoid(logits)
        dimensions = tuple(range(1, probability.ndim))
        intersection = (probability * target).sum(dim=dimensions)
        denominator = probability.sum(dim=dimensions) + target.sum(dim=dimensions)
        dice_loss = 1.0 - ((2.0 * intersection + 1.0) / (denominator + 1.0)).mean()
        return (1.0 - self.dice_weight) * binary_cross_entropy + self.dice_weight * dice_loss


@dataclass
class BinaryConfusion:
    true_positive: int = 0
    true_negative: int = 0
    false_positive: int = 0
    false_negative: int = 0

    def update(self, logits: torch.Tensor, target: torch.Tensor, threshold: float = 0.5) -> None:
        prediction = torch.sigmoid(logits) >= threshold
        truth = target >= 0.5
        self.true_positive += int(torch.logical_and(prediction, truth).sum().item())
        self.true_negative += int(torch.logical_and(~prediction, ~truth).sum().item())
        self.false_positive += int(torch.logical_and(prediction, ~truth).sum().item())
        self.false_negative += int(torch.logical_and(~prediction, truth).sum().item())

    def metrics(self) -> dict[str, float | int | dict[str, int]]:
        tp, tn, fp, fn = (
            self.true_positive,
            self.true_negative,
            self.false_positive,
            self.false_negative,
        )

        def divide(numerator: float, denominator: float) -> float:
            return numerator / denominator if denominator else 0.0

        return {
            "confusion_matrix": {"true_positive": tp, "true_negative": tn, "false_positive": fp, "false_negative": fn},
            "accuracy": divide(tp + tn, tp + tn + fp + fn),
            "precision": divide(tp, tp + fp),
            "recall": divide(tp, tp + fn),
            "specificity": divide(tn, tn + fp),
            "iou": divide(tp, tp + fp + fn),
            "dice_f1": divide(2 * tp, 2 * tp + fp + fn),
        }
