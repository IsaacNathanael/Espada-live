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


def load_sar_image(path: Path) -> np.ndarray:
    with Image.open(path) as source:
        image = np.asarray(source, dtype=float)
    if image.ndim == 3:
        image = image[..., :3].mean(axis=2)
    if image.ndim != 2 or min(image.shape) < 32:
        raise ValueError("SAR input must be a two-dimensional image of at least 32x32 pixels")
    finite = np.isfinite(image)
    if not finite.any():
        raise ValueError("SAR input has no finite pixels")
    image[~finite] = float(np.nanmedian(image[finite]))
    return image


def preprocess_sar(image: np.ndarray) -> tuple[np.ndarray, str]:
    image = np.asarray(image, dtype=float)
    positive = image[image > 0]
    dynamic_ratio = float(np.percentile(positive, 99) / max(np.percentile(positive, 5), 1e-12)) if positive.size else 1.0
    if image.min() >= 0 and dynamic_ratio > 20:
        floor = max(float(np.percentile(positive, 1)) * 0.2, 1e-12)
        working = 10.0 * np.log10(np.maximum(image, floor))
        transform = "linear intensity converted to decibels"
    else:
        working = image
        transform = "input treated as decibel or display intensity"
    low, high = np.percentile(working, [1.0, 99.0])
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
) -> dict[str, object]:
    mask, score, metadata = segment_dark_slick(image)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    display = preprocess_sar(image)[0]
    Image.fromarray(np.uint8(display * 255), mode="L").save(output_dir / "sar_input.png")
    Image.fromarray(np.uint8(mask) * 255, mode="L").save(output_dir / "slick_mask.png")
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), constrained_layout=True)
    axes[0].imshow(display, cmap="gray")
    axes[0].set_title("Preprocessed SAR")
    axes[1].imshow(score, cmap="magma", vmin=0, vmax=1)
    axes[1].set_title("Dark-anomaly score")
    axes[2].imshow(display, cmap="gray")
    axes[2].contour(mask, levels=[0.5], colors=["#ff9d24"], linewidths=1.4)
    axes[2].set_title("Candidate slick boundary")
    for axis in axes:
        axis.axis("off")
    fig.savefig(output_dir / "sar_segmentation_overview.png", dpi=180)
    plt.close(fig)
    evaluation = _pixel_metrics(mask, truth_mask) if truth_mask is not None else None
    if truth_mask is not None:
        Image.fromarray(np.uint8(truth_mask) * 255, mode="L").save(output_dir / "truth_mask.png")
    artifacts = ["sar_input.png", "slick_mask.png", "sar_segmentation_overview.png"]
    geojson_status = "not requested; supply a WGS84 bounding box"
    if bbox is not None:
        polygon = _mask_polygon(mask, bbox)
        confidence = float(np.mean(score[mask])) if mask.any() else 0.0
        write_polygon_geojson(
            output_dir / "slick_observation.geojson",
            polygon,
            {
                "observation_time_utc": observation_time_utc,
                "detection_confidence": confidence,
                "source": source,
                "segmentation_method": metadata["method"],
            },
        )
        artifacts.append("slick_observation.geojson")
        geojson_status = "written"
    result = {
        "status": "PASS" if mask.any() else "NO_DETECTION",
        "source": source,
        "observation_time_utc": observation_time_utc,
        "image_shape": list(image.shape),
        "detected_pixel_fraction": float(mask.mean()),
        "segmentation": metadata,
        "synthetic_evaluation": evaluation,
        "geojson_status": geojson_status,
        "limitations": [
            "Dark-lookalikes such as low wind, rain cells and sensor artefacts can cause false positives.",
            "This baseline must be replaced or complemented by a held-out evaluated ResNet34 U-Net.",
            "A WGS84 bounding box is required because raster georeferencing is not inferred by this lightweight adapter.",
        ],
        "artifacts": artifacts + ["sar_result.json", "sar_model_card.json"],
    }
    (output_dir / "sar_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    model_card = {
        "name": "Espada adaptive SAR dark-anomaly baseline",
        "version": "0.1",
        "type": "classical computer vision (not ML)",
        "intended_use": "fallback candidate-mask generation for analyst review",
        "not_for": "autonomous pollution attribution or guilt determination",
        "evaluation": evaluation,
        "planned_upgrade": "ResNet34-based U-Net trained and tested on separated labelled Sentinel-1 scenes",
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
