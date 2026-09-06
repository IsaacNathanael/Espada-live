import pytest


try:
    import torch
except (ImportError, OSError) as error:
    pytest.skip(f"optional PyTorch environment is unavailable: {error}", allow_module_level=True)

from espada.ml_model import (
    BCEDiceLoss,
    BCEFocalTverskyLoss,
    BinaryConfusion,
    ResNet34UNet,
    ResNet50UNet,
)


def test_resnet34_unet_preserves_spatial_shape() -> None:
    model = ResNet34UNet(pretrained_encoder=False).eval()
    with torch.no_grad():
        output = model(torch.zeros((1, 1, 64, 64)))
    assert output.shape == (1, 1, 64, 64)


def test_binary_metrics_use_oil_as_positive_class() -> None:
    logits = torch.tensor([[[[10.0, -10.0], [10.0, -10.0]]]])
    target = torch.tensor([[[[1.0, 0.0], [0.0, 1.0]]]])
    confusion = BinaryConfusion()
    confusion.update(logits, target)
    metrics = confusion.metrics()
    assert metrics["confusion_matrix"] == {
        "true_positive": 1,
        "true_negative": 1,
        "false_positive": 1,
        "false_negative": 1,
    }
    assert metrics["iou"] == pytest.approx(1 / 3)
    assert BCEDiceLoss()(logits, target).item() > 0
    assert BCEFocalTverskyLoss()(logits, target).item() > 0


def test_resnet50_unet_preserves_spatial_shape() -> None:
    model = ResNet50UNet(attention_decoder=True).eval()
    with torch.no_grad():
        output = model(torch.zeros((1, 1, 64, 64)))
    assert output.shape == (1, 1, 64, 64)


def test_group_normalized_decoder_avoids_small_batch_statistics() -> None:
    model = ResNet50UNet(attention_decoder=True, decoder_normalization="group")
    decoder_modules = [
        module
        for name, module in model.named_modules()
        if name.startswith(("decoder", "final"))
    ]
    assert any(isinstance(module, torch.nn.GroupNorm) for module in decoder_modules)
    assert not any(isinstance(module, torch.nn.BatchNorm2d) for module in decoder_modules)
