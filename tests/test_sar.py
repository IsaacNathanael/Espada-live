from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from espada.sar import (
    _repair_float_byte_order,
    load_sar_image,
    run_segmentation,
    run_synthetic_segmentation_demo,
    segment_dark_slick,
    synthetic_sar_scene,
)


def test_big_endian_float_tiff_values_are_repaired_before_analysis() -> None:
    expected = np.linspace(0.0, 0.3, 64, dtype=np.float32).reshape(8, 8)
    corrupted = expected.byteswap()
    repaired = _repair_float_byte_order(corrupted, b"MM")
    assert np.allclose(repaired, expected)


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


def test_real_segmentation_requires_review_before_observation_export(tmp_path: Path) -> None:
    image, _ = synthetic_sar_scene(size=128)
    result = run_segmentation(
        image,
        tmp_path,
        source="real Sentinel-1 test fixture",
        observation_time_utc="2026-08-25T01:02:37Z",
        bbox=(71.25, 18.55, 71.65, 18.90),
    )
    assert result["status"] == "REVIEW_REQUIRED"
    assert (tmp_path / "slick_candidate.geojson").exists()
    assert not (tmp_path / "slick_observation.geojson").exists()


def test_load_sar_rejects_tiny_image(tmp_path: Path) -> None:
    path = tmp_path / "tiny.png"
    Image.fromarray(np.zeros((10, 10), dtype=np.uint8)).save(path)
    with pytest.raises(ValueError, match="32x32"):
        load_sar_image(path)
