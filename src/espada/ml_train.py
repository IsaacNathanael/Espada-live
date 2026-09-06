from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from .ml_metrics import ProbabilityHistogram
from .ml_model import BCEDiceLoss, BinaryConfusion, ModelConfig, ResNet34UNet


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 26143
    epochs: int = 40
    batch_size: int = 4
    patch_size: int = 384
    training_stride: int = 256
    validation_stride: int = 384
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    positive_weight: float = 4.0
    negative_patch_ratio: float = 3.0
    hard_negative_fraction: float = 0.65
    minimum_oil_pixels: int = 64
    patience: int = 10
    pretrained_encoder: bool = True
    attention_decoder: bool = True


def tile_positions(length: int, patch_size: int, stride: int) -> list[int]:
    if length < patch_size:
        raise ValueError(f"Scene dimension {length} is smaller than patch size {patch_size}")
    positions = list(range(0, length - patch_size + 1, stride))
    final = length - patch_size
    if positions[-1] != final:
        positions.append(final)
    return positions


class SarPatchDataset(Dataset):
    def __init__(
        self,
        dataset_root: Path,
        manifest_path: Path,
        split: str,
        *,
        patch_size: int,
        stride: int,
        seed: int,
        minimum_oil_pixels: int = 64,
        negative_patch_ratio: float = 2.0,
        hard_negative_fraction: float = 0.0,
        augment: bool = False,
        limit: int | None = None,
    ) -> None:
        self.dataset_root = Path(dataset_root)
        self.patch_size = patch_size
        self.augment = augment
        self._cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        with Path(manifest_path).open("r", encoding="utf-8-sig", newline="") as stream:
            self.scenes = [row for row in csv.DictReader(stream) if row["split"] == split]
        if not self.scenes:
            raise ValueError(f"Manifest contains no scenes for split '{split}'")

        positive: list[tuple[int, int, int]] = []
        background: list[tuple[tuple[int, int, int], float]] = []
        all_patches: list[tuple[int, int, int]] = []
        for scene_index, scene in enumerate(self.scenes):
            with Image.open(self.dataset_root / scene["mask_path"]) as mask_file:
                mask = np.asarray(mask_file, dtype=np.float32)
            image = None
            if split == "train" and hard_negative_fraction:
                with Image.open(self.dataset_root / scene["image_path"]) as image_file:
                    image = np.asarray(image_file, dtype=np.float32).copy()
            for y in tile_positions(mask.shape[0], patch_size, stride):
                for x in tile_positions(mask.shape[1], patch_size, stride):
                    item = (scene_index, x, y)
                    all_patches.append(item)
                    oil_pixels = int(np.count_nonzero(mask[y : y + patch_size, x : x + patch_size]))
                    if oil_pixels >= minimum_oil_pixels:
                        positive.append(item)
                    elif oil_pixels == 0:
                        hardness = (
                            float(image[y : y + patch_size, x : x + patch_size].mean())
                            if image is not None
                            else 0.0
                        )
                        background.append((item, hardness))

        if split == "train":
            if not positive:
                raise ValueError("Training split contains no positive oil patches")
            rng = random.Random(seed)
            negative_count = min(len(background), round(len(positive) * negative_patch_ratio))
            hard_count = min(negative_count, round(negative_count * hard_negative_fraction))
            ranked_background = sorted(background, key=lambda item: item[1])
            hard_background = [item for item, _ in ranked_background[:hard_count]]
            remaining_background = [item for item, _ in ranked_background[hard_count:]]
            rng.shuffle(remaining_background)
            selected_background = hard_background + remaining_background[: negative_count - hard_count]
            self.patches = positive + selected_background
            rng.shuffle(self.patches)
        else:
            self.patches = all_patches
        if limit is not None:
            self.patches = self.patches[:limit]
        if not self.patches:
            raise ValueError(f"No patches were generated for split '{split}'")

    def __len__(self) -> int:
        return len(self.patches)

    def _scene_arrays(self, scene_index: int) -> tuple[np.ndarray, np.ndarray]:
        scene = self.scenes[scene_index]
        scene_id = scene["scene_id"]
        if scene_id not in self._cache:
            with Image.open(self.dataset_root / scene["image_path"]) as image_file:
                image = np.asarray(image_file, dtype=np.float32).copy()
            with Image.open(self.dataset_root / scene["mask_path"]) as mask_file:
                mask = np.asarray(mask_file, dtype=np.float32).copy()
            self._cache[scene_id] = image, mask
        return self._cache[scene_id]

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        scene_index, x, y = self.patches[index]
        image, mask = self._scene_arrays(scene_index)
        size = self.patch_size
        image_patch = image[y : y + size, x : x + size].copy()
        mask_patch = mask[y : y + size, x : x + size].copy()
        image_patch = np.clip((image_patch + 35.0) / 40.0, 0.0, 1.0)
        image_tensor = torch.from_numpy(image_patch).unsqueeze(0)
        mask_tensor = torch.from_numpy(mask_patch).unsqueeze(0)
        if self.augment:
            if torch.rand(()) < 0.5:
                image_tensor = torch.flip(image_tensor, dims=(-1,))
                mask_tensor = torch.flip(mask_tensor, dims=(-1,))
            if torch.rand(()) < 0.5:
                image_tensor = torch.flip(image_tensor, dims=(-2,))
                mask_tensor = torch.flip(mask_tensor, dims=(-2,))
            rotations = int(torch.randint(0, 4, ()).item())
            image_tensor = torch.rot90(image_tensor, rotations, dims=(-2, -1))
            mask_tensor = torch.rot90(mask_tensor, rotations, dims=(-2, -1))
            gain = 0.9 + 0.2 * torch.rand(())
            offset = -0.05 + 0.1 * torch.rand(())
            image_tensor = torch.clamp(image_tensor * gain + offset, 0.0, 1.0)
            if torch.rand(()) < 0.35:
                image_tensor = torch.clamp(
                    image_tensor + 0.015 * torch.randn_like(image_tensor), 0.0, 1.0
                )
        return image_tensor.contiguous(), mask_tensor.contiguous()


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _run_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    loss_function: torch.nn.Module,
    device: torch.device,
    *,
    optimizer: torch.optim.Optimizer | None,
    scaler: torch.amp.GradScaler,
) -> tuple[float, dict]:
    training = optimizer is not None
    model.train(training)
    confusion = BinaryConfusion()
    probability_histogram = ProbabilityHistogram(bins=500)
    loss_sum = 0.0
    batches = 0
    for image, mask in loader:
        image = image.to(device, non_blocking=True)
        mask = mask.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training):
            with torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
                logits = model(image)
                loss = loss_function(logits, mask)
            if training:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
        confusion.update(logits.detach(), mask)
        probability_histogram.update(
            torch.sigmoid(logits.detach()).float().cpu().numpy(),
            mask.detach().float().cpu().numpy(),
        )
        loss_sum += float(loss.detach().item())
        batches += 1
    metrics = confusion.metrics()
    metrics["loss"] = loss_sum / max(batches, 1)
    metrics["average_precision"] = probability_histogram.average_precision()
    return float(metrics["loss"]), metrics


def train_model(
    dataset_root: Path,
    manifest_path: Path,
    output_dir: Path,
    config: TrainingConfig | None = None,
    *,
    max_train_patches: int | None = None,
    max_validation_patches: int | None = None,
) -> dict:
    config = config or TrainingConfig()
    _set_seed(config.seed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_data = SarPatchDataset(
        dataset_root,
        manifest_path,
        "train",
        patch_size=config.patch_size,
        stride=config.training_stride,
        seed=config.seed,
        minimum_oil_pixels=config.minimum_oil_pixels,
        negative_patch_ratio=config.negative_patch_ratio,
        hard_negative_fraction=config.hard_negative_fraction,
        augment=True,
        limit=max_train_patches,
    )
    validation_data = SarPatchDataset(
        dataset_root,
        manifest_path,
        "validation",
        patch_size=config.patch_size,
        stride=config.validation_stride,
        seed=config.seed,
        augment=False,
        limit=max_validation_patches,
    )
    train_loader = DataLoader(
        train_data,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )
    validation_loader = DataLoader(
        validation_data,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )

    model = ResNet34UNet(
        pretrained_encoder=config.pretrained_encoder,
        attention_decoder=config.attention_decoder,
    ).to(device)
    loss_function = BCEDiceLoss(positive_weight=config.positive_weight).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=2, min_lr=1e-6
    )
    scaler = torch.amp.GradScaler(device.type, enabled=device.type == "cuda")
    history: list[dict] = []
    best_average_precision = -1.0
    stale_epochs = 0
    checkpoint_path = output_dir / "resnet34_unet_best.pt"

    for epoch in range(1, config.epochs + 1):
        _, training_metrics = _run_epoch(
            model, train_loader, loss_function, device, optimizer=optimizer, scaler=scaler
        )
        with torch.no_grad():
            _, validation_metrics = _run_epoch(
                model,
                validation_loader,
                loss_function,
                device,
                optimizer=None,
                scaler=scaler,
            )
        row = {
            "epoch": epoch,
            "training_loss": training_metrics["loss"],
            "training_iou": training_metrics["iou"],
            "training_dice_f1": training_metrics["dice_f1"],
            "training_average_precision": training_metrics["average_precision"],
            "validation_loss": validation_metrics["loss"],
            "validation_iou": validation_metrics["iou"],
            "validation_dice_f1": validation_metrics["dice_f1"],
            "validation_precision": validation_metrics["precision"],
            "validation_recall": validation_metrics["recall"],
            "validation_average_precision": validation_metrics["average_precision"],
            "learning_rate": float(optimizer.param_groups[0]["lr"]),
        }
        history.append(row)
        print(json.dumps(row), flush=True)
        scheduler.step(float(validation_metrics["average_precision"]))
        if float(validation_metrics["average_precision"]) > best_average_precision:
            best_average_precision = float(validation_metrics["average_precision"])
            stale_epochs = 0
            torch.save(
                {
                    "format_version": 1,
                    "model_state_dict": model.state_dict(),
                    "model_config": ModelConfig(
                        pretrained_encoder=config.pretrained_encoder,
                        attention_decoder=config.attention_decoder,
                    ).to_dict(),
                    "training_config": asdict(config),
                    "epoch": epoch,
                    "threshold": 0.5,
                    "validation_metrics": validation_metrics,
                    "selection_metric": "validation_average_precision",
                    "normalization": {"clip_min_db": -35.0, "clip_max_db": 5.0},
                    "dataset_doi": "10.5281/zenodo.4672426",
                },
                checkpoint_path,
            )
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break

    history_path = output_dir / "training_history.csv"
    with history_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    best = max(history, key=lambda item: item["validation_average_precision"])
    result = {
        "status": "PASS",
        "run_type": "smoke" if max_train_patches else "full_training",
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "architecture": "attention-gated ResNet34 U-Net",
        "training_patches": len(train_data),
        "validation_patches": len(validation_data),
        "epochs_completed": len(history),
        "checkpoint_selection": "highest validation average precision (threshold-independent)",
        "best_validation": best,
        "checkpoint": str(checkpoint_path.resolve()),
        "limitations": [
            "Validation is acquisition-group isolated, but the dataset is small and region-specific.",
            "Patch validation is for model selection; final claims require untouched full-scene test evaluation.",
            "Oil-versus-lookalike confusion requires analyst review in operational use.",
        ],
    }
    (output_dir / "training_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train ESPADA's ResNet34 U-Net")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--no-pretrained", action="store_true")
    parser.add_argument("--max-train-patches", type=int)
    parser.add_argument("--max-validation-patches", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = TrainingConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        pretrained_encoder=not args.no_pretrained,
    )
    try:
        result = train_model(
            args.dataset_root,
            args.manifest,
            args.out,
            config,
            max_train_patches=args.max_train_patches,
            max_validation_patches=args.max_validation_patches,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
