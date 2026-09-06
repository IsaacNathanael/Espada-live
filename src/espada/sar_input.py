from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image


def _repair_float_byte_order(image: np.ndarray, marker: bytes) -> np.ndarray:
    if marker != b"MM" or image.dtype.kind != "f":
        return image
    swapped = image.byteswap()
    original_reasonable = float(np.mean(np.isfinite(image) & (np.abs(image) <= 1e6)))
    swapped_reasonable = float(np.mean(np.isfinite(swapped) & (np.abs(swapped) <= 1e6)))
    return swapped if swapped_reasonable > original_reasonable + 0.1 else image


def _load_sar_source(source: Image.Image, marker: bytes) -> np.ndarray:
    image = _repair_float_byte_order(np.asarray(source), marker).astype(float, copy=True)
    if image.ndim == 3:
        image = image[..., :3].mean(axis=2)
    if image.ndim != 2 or min(image.shape) < 32:
        raise ValueError("SAR input must be a two-dimensional image of at least 32x32 pixels")
    finite = np.isfinite(image)
    if not finite.any():
        raise ValueError("SAR input has no finite pixels")
    image[~finite] = float(np.nanmedian(image[finite]))
    return image


def load_sar_image(path: Path) -> np.ndarray:
    path = Path(path)
    with path.open("rb") as stream:
        marker = stream.read(2)
    with Image.open(path) as source:
        return _load_sar_source(source, marker)


def load_sar_bytes(content: bytes) -> np.ndarray:
    with Image.open(BytesIO(content)) as source:
        return _load_sar_source(source, content[:2])


def sar_to_decibels(image: np.ndarray) -> tuple[np.ndarray, str]:
    """Normalize input representation while preserving calibrated dB values."""
    image = np.asarray(image, dtype=float)
    finite = np.isfinite(image)
    positive = image[finite & (image > 0)]
    dynamic_ratio = (
        float(np.percentile(positive, 99) / max(np.percentile(positive, 5), 1e-12))
        if positive.size
        else 1.0
    )
    looks_like_linear_power = bool(
        positive.size
        and np.nanmin(image) >= 0
        and (dynamic_ratio > 20 or np.percentile(positive, 99) <= 10)
    )
    invalid = ~finite
    if looks_like_linear_power:
        invalid |= image <= 0
        floor = max(float(np.percentile(positive, 1)) * 0.2, 1e-12)
        working = 10.0 * np.log10(np.maximum(image, floor))
        transform = "linear intensity converted to decibels"
    else:
        working = image.copy()
        transform = "input treated as decibel or display intensity"
    valid_working = working[~invalid]
    if not valid_working.size:
        raise ValueError("SAR input has no valid analysis pixels")
    working[invalid] = float(np.median(valid_working))
    return working.astype(np.float32), transform
