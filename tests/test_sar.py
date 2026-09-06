from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from espada.sar import (
    _repair_float_byte_order,
    load_sar_image,
    run_segmentation,
    run_synthetic_segmentation_demo,
    sar_to_decibels,
    segment_dark_slick,
    synthetic_sar_scene,
)


def test_sar_to_decibels_converts_linear_power_and_preserves_db() -> None:
    linear = np.array([[0.001, 0.01], [0.1, 1.0]], dtype=np.float32)
    converted, source_scale = sar_to_decibels(linear)
    assert source_scale == "linear intensity converted to decibels"
    assert np.allclose(converted, 10.0 * np.log10(linear), atol=1e-5)

    decibels = np.array([[-30.0, -20.0], [-10.0, 0.0]], dtype=np.float32)
    preserved, source_scale = sar_to_decibels(decibels)
    assert source_scale == "input treated as decibel or display intensity"
    assert np.array_equal(preserved, decibels)


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


def test_precomputed_ml_no_detection_needs_no_review(tmp_path: Path) -> None:
    import json

    image = np.linspace(0.001, 0.2, 128 * 128, dtype=np.float32).reshape(128, 128)
    bundle = tmp_path / "prediction.npz"
    metadata = {
        "method": "test ML segmentation",
        "model_type": "deep-learning binary oil-candidate segmentation",
        "threshold": 0.25,
    }
    np.savez_compressed(
        bundle,
        probability=np.full(image.shape, 0.1, dtype=np.float32),
        metadata_json=np.asarray(json.dumps(metadata)),
    )
    result = run_segmentation(
        image,
        tmp_path / "result",
        source="test SAR",
        observation_time_utc="2026-08-25T01:02:37Z",
        bbox=(71.25, 18.55, 71.65, 18.90),
        prediction_bundle=bundle,
    )
    assert result["status"] == "NO_DETECTION"
    assert result["review_status"] == "not_required"
    assert "no detection" in result["geojson_status"]


def test_load_sar_rejects_tiny_image(tmp_path: Path) -> None:
    path = tmp_path / "tiny.png"
    Image.fromarray(np.zeros((10, 10), dtype=np.uint8)).save(path)
    with pytest.raises(ValueError, match="32x32"):
        load_sar_image(path)
