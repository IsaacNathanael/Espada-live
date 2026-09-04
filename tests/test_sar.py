from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from espada.sar import (
    load_sar_image,
    run_synthetic_segmentation_demo,
    segment_dark_slick,
    synthetic_sar_scene,
)


def test_synthetic_sar_baseline_has_measurable_skill() -> None:
    image, truth = synthetic_sar_scene(size=256)
    mask, score, metadata = segment_dark_slick(image)
    intersection = np.logical_and(mask, truth).sum()
    union = np.logical_or(mask, truth).sum()
    assert intersection / union >= 0.35
    assert score.shape == image.shape
    assert metadata["model_type"].startswith("classical")


def test_sar_demo_exports_mask_geojson_and_model_card(tmp_path: Path) -> None:
    result = run_synthetic_segmentation_demo(tmp_path)
    assert result["status"] == "PASS"
    assert result["synthetic_evaluation"]["iou"] >= 0.35
    assert (tmp_path / "slick_observation.geojson").exists()
    assert (tmp_path / "sar_model_card.json").exists()
    assert (tmp_path / "sar_segmentation_overview.png").exists()


def test_load_sar_rejects_tiny_image(tmp_path: Path) -> None:
    path = tmp_path / "tiny.png"
    Image.fromarray(np.zeros((10, 10), dtype=np.uint8)).save(path)
    with pytest.raises(ValueError, match="32x32"):
        load_sar_image(path)
