from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "espada-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import MultiPoint, Polygon

from .geo import write_polygon_geojson
from .sar_input import (
    _repair_float_byte_order,
    load_sar_bytes,
    load_sar_image,
    sar_to_decibels,
)


def preprocess_sar(image: np.ndarray) -> tuple[np.ndarray, str]:
    working, transform = sar_to_decibels(image)
    valid_working = working[np.isfinite(working)]
    low, high = np.percentile(valid_working, [1.0, 99.0])
    if high <= low:
        raise ValueError("SAR input has insufficient intensity variation")
    normalized = np.clip((working - low) / (high - low), 0.0, 1.0)
    return ndimage.gaussian_filter(normalized, sigma=1.1), transform


def _filter_components(mask: np.ndarray) -> tuple[np.ndarray, list[dict[str, float | int]]]:
    labels, count = ndimage.label(mask)
    cleaned = np.zeros_like(mask, dtype=bool)
    components: list[dict[str, float | int]] = []
    total = mask.size
    min_pixels = max(24, int(total * 0.0007))
    max_pixels = int(total * 0.30)
    for label_id in range(1, count + 1):
        rows, columns = np.where(labels == label_id)
        area = len(rows)
        if area < min_pixels or area > max_pixels:
            continue
        height = int(rows.max() - rows.min() + 1)
        width = int(columns.max() - columns.min() + 1)
        elongation = max(height, width) / max(1, min(height, width))
        if elongation < 1.15 and area > total * 0.05:
            continue
        cleaned[labels == label_id] = True
        components.append(
            {
                "label": label_id,
                "pixels": area,
                "area_fraction": area / total,
                "elongation": float(elongation),
            }
        )
    components.sort(key=lambda item: int(item["pixels"]), reverse=True)
    return cleaned, components


def segment_dark_slick(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    normalized, transform = preprocess_sar(image)
    background = ndimage.gaussian_filter(normalized, sigma=max(6.0, min(image.shape) / 24.0))
    darkness = np.clip(background - normalized, 0.0, None)
    median = float(np.median(darkness))
    mad = float(np.median(np.abs(darkness - median)))
    threshold = max(0.055, median + 2.8 * 1.4826 * mad)
    mask = darkness > threshold
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)))
    mask = ndimage.binary_closing(mask, structure=np.ones((7, 7)))
    mask = ndimage.binary_fill_holes(mask)
    mask, components = _filter_components(mask)
    score = np.clip(darkness / max(threshold * 1.8, 1e-8), 0.0, 1.0)
    metadata = {
        "method": "adaptive dark-anomaly segmentation",
        "model_type": "classical computer-vision baseline; not machine learning",
        "preprocessing": transform,
        "threshold": threshold,
        "detected_components": len(components),
        "components": components,
    }
    return mask, score, metadata


def segment_ml_slick(
    image: np.ndarray,
    checkpoint_path: Path,
    calibration_path: Path,
    *,
    batch_size: int = 4,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Run the calibrated V5 network on a prepared or linear-power SAR raster."""
    import torch

    from .ml_evaluate import file_sha256, infer_full_scene
    from .ml_model import build_segmentation_model
    from .ml_preprocess import FIXED_MINMAX

    checkpoint_path = Path(checkpoint_path)
    calibration_path = Path(calibration_path)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    checkpoint_digest = file_sha256(checkpoint_path)
    if calibration.get("checkpoint_sha256") != checkpoint_digest:
        raise ValueError("Threshold calibration belongs to a different model checkpoint")
    model_config = checkpoint.get("model_config", {})
    model = build_segmentation_model(model_config, pretrained_encoder=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device).eval()
    image_db, input_transform = sar_to_decibels(image)
    patch_size = int(checkpoint.get("training_config", {}).get("patch_size", 256))
    normalization_mode = str(checkpoint.get("normalization", {}).get("mode", FIXED_MINMAX))
    tta_mode = str(calibration.get("test_time_augmentation", "none"))
    probability = infer_full_scene(
        model,
        image_db,
        device,
        patch_size=patch_size,
        stride=max(patch_size * 3 // 4, 1),
        batch_size=batch_size,
        normalization_mode=normalization_mode,
        tta_mode=tta_mode,
    )
    threshold = float(calibration["selected_threshold"])
    augmentation_profile = str(
        checkpoint.get("training_config", {}).get("augmentation_profile", "v5")
    )
    model_generation = "V6" if augmentation_profile == "sar_v6" else "V5"
    mask = probability >= threshold
    labels, component_count = ndimage.label(mask)
    component_sizes = sorted(
        (int(value) for value in ndimage.sum(mask, labels, range(1, component_count + 1))),
        reverse=True,
    )
    architecture = str(model_config.get("architecture", "segmentation model"))
    if model_config.get("attention_decoder"):
        architecture = f"attention-gated {architecture}"
    metadata = {
        "method": f"calibrated {model_generation} SAR semantic segmentation",
        "model_type": "deep-learning binary oil-candidate segmentation",
        "model_generation": model_generation,
        "architecture": architecture,
        "checkpoint_epoch": int(checkpoint.get("epoch", 0)),
        "checkpoint_sha256": checkpoint_digest,
        "threshold": threshold,
        "threshold_source": "validation-only IoU calibration",
        "test_time_augmentation": tta_mode,
        "preprocessing": input_transform,
        "normalization": checkpoint.get("normalization", {}),
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "detected_components": component_count,
        "largest_component_pixels": component_sizes[:10],
        "probability_summary": {
            "minimum": float(probability.min()),
            "mean": float(probability.mean()),
            "maximum": float(probability.max()),
        },
    }
    return mask, probability, metadata


def segment_precomputed_slick(
    prediction_bundle: Path,
    expected_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Load a GPU-produced probability map in a non-PyTorch reporting process."""
    with np.load(Path(prediction_bundle), allow_pickle=False) as bundle:
        probability = np.asarray(bundle["probability"], dtype=np.float32)
        metadata = json.loads(str(bundle["metadata_json"].item()))
    if probability.shape != expected_shape:
        raise ValueError(
            f"Prediction shape {probability.shape} does not match SAR image {expected_shape}"
        )
    if not np.all(np.isfinite(probability)):
        raise ValueError("Prediction bundle contains invalid probability values")
    if float(probability.min()) < 0.0 or float(probability.max()) > 1.0:
        raise ValueError("Prediction probabilities must be within [0, 1]")
    threshold = float(metadata["threshold"])
    mask = probability >= threshold
    labels, component_count = ndimage.label(mask)
    component_sizes = sorted(
        (int(value) for value in ndimage.sum(mask, labels, range(1, component_count + 1))),
        reverse=True,
    )
    metadata["detected_components"] = component_count
    metadata["largest_component_pixels"] = component_sizes[:10]
    metadata["prediction_bundle"] = str(Path(prediction_bundle).resolve())
    return mask, probability, metadata


def synthetic_sar_scene(size: int = 384, seed: int = 26143) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[-1:1:complex(size), -1:1:complex(size)]
    centerline = 0.14 * np.sin(3.2 * x) - 0.10 * x
    width = 0.055 + 0.035 * (x + 1.0) / 2.0
    along = np.exp(-((x + 0.05) / 0.68) ** 6)
    soft_slick = np.exp(-((y - centerline) / width) ** 2) * along
    truth = soft_slick > 0.34
    ocean = 0.72 + 0.07 * x - 0.04 * y + 0.035 * np.sin(8 * x + 3 * y)
    speckle = rng.gamma(shape=5.0, scale=0.2, size=(size, size))
    image = ocean * speckle * (1.0 - 0.68 * soft_slick)
    image += rng.normal(0.0, 0.012, image.shape)
    return np.clip(image, 1e-5, None), truth


def _pixel_metrics(prediction: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    prediction = prediction.astype(bool)
    truth = truth.astype(bool)
    intersection = int(np.logical_and(prediction, truth).sum())
    union = int(np.logical_or(prediction, truth).sum())
    predicted = int(prediction.sum())
    actual = int(truth.sum())
    return {
        "iou": intersection / max(union, 1),
        "dice": 2 * intersection / max(predicted + actual, 1),
        "precision": intersection / max(predicted, 1),
        "recall": intersection / max(actual, 1),
    }


def _mask_polygon(mask: np.ndarray, bbox: tuple[float, float, float, float]) -> Polygon:
    labels, count = ndimage.label(mask)
    if count == 0:
        raise ValueError("No slick component was detected; GeoJSON was not fabricated")
    sizes = ndimage.sum(mask, labels, range(1, count + 1))
    selected = labels == int(np.argmax(sizes) + 1)
    boundary = np.logical_and(selected, ~ndimage.binary_erosion(selected))
    rows, columns = np.where(boundary)
    step = max(1, len(rows) // 2_000)
    rows = rows[::step]
    columns = columns[::step]
    min_lon, min_lat, max_lon, max_lat = bbox
    longitude = min_lon + columns / max(mask.shape[1] - 1, 1) * (max_lon - min_lon)
    latitude = max_lat - rows / max(mask.shape[0] - 1, 1) * (max_lat - min_lat)
    polygon = MultiPoint(np.column_stack((longitude, latitude))).convex_hull
    if not isinstance(polygon, Polygon) or not polygon.is_valid:
        raise ValueError("Detected slick could not form a valid polygon")
    return polygon


def run_segmentation(
    image: np.ndarray,
    output_dir: Path,
    *,
    source: str,
    observation_time_utc: str,
    bbox: tuple[float, float, float, float] | None = None,
    truth_mask: np.ndarray | None = None,
    analyst_approved: bool = False,
    model_checkpoint: Path | None = None,
    calibration_path: Path | None = None,
    prediction_bundle: Path | None = None,
    inference_batch_size: int = 4,
) -> dict[str, object]:
    if model_checkpoint is not None and prediction_bundle is not None:
        raise ValueError("Choose a model checkpoint or a precomputed prediction, not both")
    if prediction_bundle is not None:
        mask, score, metadata = segment_precomputed_slick(prediction_bundle, image.shape)
    elif model_checkpoint is not None:
        if calibration_path is None:
            raise ValueError("ML segmentation requires a validation calibration file")
        mask, score, metadata = segment_ml_slick(
            image,
            model_checkpoint,
            calibration_path,
            batch_size=inference_batch_size,
        )
    else:
        mask, score, metadata = segment_dark_slick(image)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_polygon in ("slick_candidate.geojson", "slick_observation.geojson"):
        stale_path = output_dir / stale_polygon
        if stale_path.exists():
            stale_path.unlink()
    display = preprocess_sar(image)[0]
    Image.fromarray(np.uint8(display * 255), mode="L").save(output_dir / "sar_input.png")
    Image.fromarray(np.uint8(mask) * 255, mode="L").save(output_dir / "slick_mask.png")
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), constrained_layout=True)
    axes[0].imshow(display, cmap="gray")
    axes[0].set_title("Preprocessed SAR")
    using_ml = model_checkpoint is not None or prediction_bundle is not None
    score_max = (
        max(float(metadata["threshold"]), float(np.percentile(score, 99.9)))
        if using_ml
        else 1.0
    )
    axes[1].imshow(score, cmap="magma", vmin=0, vmax=score_max)
    axes[1].set_title(
        f"Oil probability (threshold {float(metadata['threshold']):.3f})"
        if using_ml
        else "Dark-anomaly score"
    )
    axes[2].imshow(display, cmap="gray")
    if mask.any():
        axes[2].contour(mask, levels=[0.5], colors=["#ff9d24"], linewidths=1.4)
    axes[2].set_title("Candidate slick boundary" if mask.any() else "No candidate above threshold")
    for axis in axes:
        axis.axis("off")
    fig.savefig(output_dir / "sar_segmentation_overview.png", dpi=180)
    plt.close(fig)
    evaluation = _pixel_metrics(mask, truth_mask) if truth_mask is not None else None
    if truth_mask is not None:
        Image.fromarray(np.uint8(truth_mask) * 255, mode="L").save(output_dir / "truth_mask.png")
    artifacts = ["sar_input.png", "slick_mask.png", "sar_segmentation_overview.png"]
    if prediction_bundle is not None:
        artifacts.append(Path(prediction_bundle).name)
    geojson_status = "not requested; supply a WGS84 bounding box"
    review_status = "not_applicable" if truth_mask is not None else "pending"
    if bbox is not None and mask.any():
        polygon = _mask_polygon(mask, bbox)
        confidence = float(np.mean(score[mask])) if mask.any() else 0.0
        approved = truth_mask is not None or analyst_approved
        polygon_name = "slick_observation.geojson" if approved else "slick_candidate.geojson"
        review_status = "synthetic_truth" if truth_mask is not None else (
            "analyst_approved" if analyst_approved else "pending"
        )
        write_polygon_geojson(
            output_dir / polygon_name,
            polygon,
            {
                "observation_time_utc": observation_time_utc,
                "detection_confidence": confidence,
                "source": source,
                "segmentation_method": metadata["method"],
                "review_status": review_status,
            },
        )
        artifacts.append(polygon_name)
        geojson_status = "approved observation written" if approved else "candidate written; analyst review required"
    if not mask.any():
        status = "NO_DETECTION"
        review_status = "not_required"
        geojson_status = "not created; no detection above the calibrated threshold"
    elif truth_mask is not None or analyst_approved:
        status = "PASS"
    else:
        status = "REVIEW_REQUIRED"
    review_flags: list[str] = []
    component_count = int(
        metadata.get("detected_components", len(metadata.get("components", [])))
    )
    if truth_mask is None and component_count > 20:
        review_flags.append("Many disconnected dark regions were detected; sea-state lookalikes are likely.")
    if truth_mask is None and float(mask.mean()) > 0.08:
        review_flags.append("Detected coverage is unusually broad for one slick candidate.")
    model_generation = str(metadata.get("model_generation", "V5")) if using_ml else None
    result = {
        "status": status,
        "source": source,
        "observation_time_utc": observation_time_utc,
        "image_shape": list(image.shape),
        "detected_pixel_fraction": float(mask.mean()),
        "segmentation": metadata,
        "review_status": review_status,
        "review_flags": review_flags,
        "synthetic_evaluation": evaluation,
        "geojson_status": geojson_status,
        "limitations": [
            "Dark-lookalikes such as low wind, rain cells and sensor artefacts can cause false positives.",
            (
                f"{model_generation} has development-replay evidence but still requires an external blind test."
                if using_ml
                else "The classical fallback is not the evaluated V5 neural model."
            ),
            "Real imagery never becomes an attribution input until a human approves the candidate polygon.",
            "A WGS84 bounding box is required because raster georeferencing is not inferred by this lightweight adapter.",
        ],
        "artifacts": artifacts + ["sar_result.json", "sar_model_card.json"],
    }
    (output_dir / "sar_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    model_card = {
        "name": (
            f"ESPADA calibrated {model_generation} SAR segmentation"
            if using_ml
            else "Espada adaptive SAR dark-anomaly baseline"
        ),
        "version": "0.6" if model_generation == "V6" else ("0.5" if using_ml else "0.1"),
        "type": metadata["model_type"],
        "intended_use": "candidate-mask generation for analyst review",
        "not_for": "autonomous pollution attribution or guilt determination",
        "evaluation": evaluation,
        "checkpoint": (
            str(Path(model_checkpoint).resolve())
            if model_checkpoint is not None
            else metadata.get("checkpoint")
        ),
        "calibration": (
            str(Path(calibration_path).resolve())
            if calibration_path is not None
            else metadata.get("calibration")
        ),
        "prediction_bundle": str(Path(prediction_bundle).resolve()) if prediction_bundle is not None else None,
        "evidence": (
            "V5 development replay: IoU 59.6%, Dice 74.7%, precision 68.2%, recall 82.5%"
            if model_generation == "V5"
            else (
                "V6 development replay: IoU 61.3%, Dice 76.0%, precision 71.7%, recall 80.9%, AP 77.4%"
                if model_generation == "V6"
                else "Synthetic baseline only"
            )
        ),
        "evidence_limit": (
            "Previously examined four-scene replay; external blind evaluation remains required"
            if using_ml
            else "Not a learned oil-versus-lookalike model"
        ),
    }
    (output_dir / "sar_model_card.json").write_text(
        json.dumps(model_card, indent=2), encoding="utf-8"
    )
    return result


def run_synthetic_segmentation_demo(output_dir: Path, seed: int = 26143) -> dict[str, object]:
    image, truth = synthetic_sar_scene(seed=seed)
    return run_segmentation(
        image,
        output_dir,
        source="synthetic Sentinel-1-like evaluation scene",
        observation_time_utc=datetime(2026, 9, 3, 22, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        bbox=(71.20, 18.50, 71.80, 19.00),
        truth_mask=truth,
    )
