from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .sar_input import load_sar_image, sar_to_decibels


def evaluate_dark_signature(
    image_db: np.ndarray,
    mask: np.ndarray,
    *,
    wind_speed_ms: float | None = None,
    minimum_component_pixels: int = 12,
    dark_contrast_db: float = -1.0,
) -> dict[str, object]:
    """Screen a model mask for basic oil-like physical plausibility.

    The screen checks whether predicted regions are darker than a surrounding
    annulus and whether the acquisition wind is within a broad interpretable
    range. It is deliberately a gate, not an oil classifier.
    """
    image = np.asarray(image_db, dtype=float)
    candidate = np.asarray(mask, dtype=bool)
    if image.ndim != 2 or candidate.shape != image.shape:
        raise ValueError("SAR image and candidate mask must be aligned 2-D arrays")
    valid = np.isfinite(image)
    candidate &= valid
    labels, component_count = ndimage.label(candidate)
    components: list[dict[str, object]] = []
    for component_id in range(1, component_count + 1):
        region = labels == component_id
        area = int(region.sum())
        if area < minimum_component_pixels:
            continue
        outer = ndimage.binary_dilation(region, iterations=12)
        inner = ndimage.binary_dilation(region, iterations=2)
        ring = outer & ~inner & valid & ~candidate
        if int(ring.sum()) < 20:
            continue
        candidate_mean = float(np.mean(image[region]))
        background_mean = float(np.mean(image[ring]))
        contrast = candidate_mean - background_mean
        components.append(
            {
                "component_id": component_id,
                "area_pixels": area,
                "candidate_mean_db": round(candidate_mean, 4),
                "local_background_mean_db": round(background_mean, 4),
                "local_contrast_db": round(contrast, 4),
                "darker_than_background": bool(contrast <= dark_contrast_db),
            }
        )
    total_assessed_area = sum(int(item["area_pixels"]) for item in components)
    dark_area = sum(
        int(item["area_pixels"])
        for item in components
        if bool(item["darker_than_background"])
    )
    dark_fraction = dark_area / total_assessed_area if total_assessed_area else 0.0
    weighted_contrast = (
        float(
            np.average(
                [float(item["local_contrast_db"]) for item in components],
                weights=[int(item["area_pixels"]) for item in components],
            )
        )
        if components
        else None
    )
    contrast_pass = bool(components and dark_fraction >= 0.60)
    wind_pass = wind_speed_ms is None or 1.5 <= float(wind_speed_ms) <= 12.0
    if not components or dark_fraction < 0.35 or not wind_pass:
        status = "LOOKALIKE_RISK"
    elif contrast_pass:
        status = "PLAUSIBLE_DARK_SIGNATURE"
    else:
        status = "AMBIGUOUS"
    return {
        "status": status,
        "method": "local SAR backscatter contrast + broad acquisition-wind plausibility",
        "components_detected": int(component_count),
        "components_assessed": len(components),
        "assessed_area_pixels": total_assessed_area,
        "dark_area_fraction": round(dark_fraction, 6),
        "weighted_local_contrast_db": (
            round(weighted_contrast, 4) if weighted_contrast is not None else None
        ),
        "dark_contrast_threshold_db": dark_contrast_db,
        "wind_speed_ms": None if wind_speed_ms is None else round(float(wind_speed_ms), 4),
        "contrast_gate_passed": contrast_pass,
        "wind_gate_passed": wind_pass,
        "components": components,
        "interpretation": (
            "Candidate regions are predominantly darker than their surroundings and are not rejected by the broad wind gate."
            if status == "PLAUSIBLE_DARK_SIGNATURE"
            else "The candidate has physical lookalike indicators and should not proceed without stronger review."
            if status == "LOOKALIKE_RISK"
            else "Physical evidence is mixed; analyst review remains mandatory."
        ),
        "limitations": [
            "Dark backscatter is necessary for many oil slicks but is not specific to oil.",
            "The local contrast and wind limits are transparent screening heuristics, not a calibrated oil probability.",
            "Low-wind zones, rain cells, biogenic films and processing artefacts may still pass this screen.",
        ],
    }


def evaluate_sar_files(
    image_path: Path,
    mask_path: Path,
    output_path: Path,
    *,
    wind_speed_ms: float | None = None,
) -> dict[str, object]:
    image_db, transform = sar_to_decibels(load_sar_image(image_path))
    mask = np.asarray(Image.open(mask_path).convert("L")) > 0
    result = evaluate_dark_signature(image_db, mask, wind_speed_ms=wind_speed_ms)
    result["input_transform"] = transform
    result["image"] = str(Path(image_path).resolve())
    result["mask"] = str(Path(mask_path).resolve())
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(output_path)
    return result

