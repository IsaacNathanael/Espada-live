from __future__ import annotations

import numpy as np

FIXED_MINMAX = "fixed_minmax"
SCENE_CENTERED_S1_VV = "scene_centered_s1_vv"
CLIP_MIN_DB = -35.0
CLIP_MAX_DB = 5.0
REFERENCE_OCEAN_MEDIAN_DB = -22.0
SSL4EO_S1_VV_MEAN_DB = -12.59
SSL4EO_S1_VV_STD_DB = 5.26


def prepare_scene_db(image: np.ndarray, mode: str) -> np.ndarray:
    scene = np.asarray(image, dtype=np.float32).copy()
    finite = np.isfinite(scene)
    if not finite.any():
        raise ValueError("SAR scene contains no finite pixels")
    median = float(np.median(scene[finite]))
    scene[~finite] = median
    if mode == SCENE_CENTERED_S1_VV:
        scene += REFERENCE_OCEAN_MEDIAN_DB - median
    elif mode != FIXED_MINMAX:
        raise ValueError(f"Unsupported SAR normalization mode: {mode}")
    return np.clip(scene, CLIP_MIN_DB, CLIP_MAX_DB)


def db_to_unit(image_db: np.ndarray) -> np.ndarray:
    return np.clip((image_db - CLIP_MIN_DB) / (CLIP_MAX_DB - CLIP_MIN_DB), 0.0, 1.0)


def unit_to_db(image: np.ndarray) -> np.ndarray:
    return np.asarray(image, dtype=np.float32) * (CLIP_MAX_DB - CLIP_MIN_DB) + CLIP_MIN_DB


def model_input_from_db(image_db: np.ndarray, mode: str) -> np.ndarray:
    if mode == FIXED_MINMAX:
        return db_to_unit(image_db).astype(np.float32, copy=False)
    if mode == SCENE_CENTERED_S1_VV:
        return ((image_db - SSL4EO_S1_VV_MEAN_DB) / SSL4EO_S1_VV_STD_DB).astype(
            np.float32, copy=False
        )
    raise ValueError(f"Unsupported SAR normalization mode: {mode}")


def normalization_metadata(mode: str) -> dict[str, float | str]:
    metadata: dict[str, float | str] = {
        "mode": mode,
        "clip_min_db": CLIP_MIN_DB,
        "clip_max_db": CLIP_MAX_DB,
    }
    if mode == SCENE_CENTERED_S1_VV:
        metadata.update(
            {
                "reference_ocean_median_db": REFERENCE_OCEAN_MEDIAN_DB,
                "ssl4eo_s1_vv_mean_db": SSL4EO_S1_VV_MEAN_DB,
                "ssl4eo_s1_vv_std_db": SSL4EO_S1_VV_STD_DB,
            }
        )
    return metadata
