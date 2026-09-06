import numpy as np
import pytest


try:
    import albumentations  # noqa: F401
    import torch
except (ImportError, OSError) as error:
    pytest.skip(f"optional V6 ML environment is unavailable: {error}", allow_module_level=True)

from espada.ml_evaluate import infer_full_scene
from espada.ml_train import _albumentations_pipeline


class _PointwiseModel(torch.nn.Module):
    def forward(self, image: torch.Tensor) -> torch.Tensor:
        return image


def test_flip4_tta_preserves_equivariant_predictions() -> None:
    image = np.linspace(-35.0, 5.0, 64 * 64, dtype=np.float32).reshape(64, 64)
    model = _PointwiseModel().eval()
    device = torch.device("cpu")
    plain = infer_full_scene(
        model, image, device, patch_size=64, stride=64, batch_size=1
    )
    augmented = infer_full_scene(
        model,
        image,
        device,
        patch_size=64,
        stride=64,
        batch_size=1,
        tta_mode="flip4",
    )
    assert np.allclose(plain, augmented, atol=1e-6)


def test_sar_v6_augmentation_keeps_mask_aligned_and_binary() -> None:
    image = np.zeros((128, 128, 1), dtype=np.float32)
    mask = np.zeros((128, 128), dtype=np.float32)
    image[35:90, 45:100, 0] = 1.0
    mask[35:90, 45:100] = 1.0
    transformed = _albumentations_pipeline(26143, "sar_v6")(
        image=image, mask=mask
    )
    assert transformed["image"].shape == image.shape
    assert transformed["mask"].shape == mask.shape
    assert set(np.unique(transformed["mask"])).issubset({0.0, 1.0})
