from __future__ import annotations

import numpy as np

from espada.sar_physics_gate import evaluate_dark_signature


def _candidate_fixture(value: float) -> tuple[np.ndarray, np.ndarray]:
    image = np.full((96, 96), -18.0, dtype=float)
    mask = np.zeros_like(image, dtype=bool)
    mask[38:58, 34:62] = True
    image[mask] = value
    return image, mask


def test_dark_candidate_passes_plausibility_screen() -> None:
    image, mask = _candidate_fixture(-23.0)
    result = evaluate_dark_signature(image, mask, wind_speed_ms=5.3)
    assert result["status"] == "PLAUSIBLE_DARK_SIGNATURE"
    assert result["weighted_local_contrast_db"] < -4.0
    assert result["wind_gate_passed"] is True


def test_bright_candidate_is_flagged_as_lookalike() -> None:
    image, mask = _candidate_fixture(-12.0)
    result = evaluate_dark_signature(image, mask, wind_speed_ms=5.3)
    assert result["status"] == "LOOKALIKE_RISK"
    assert result["contrast_gate_passed"] is False


def test_dead_calm_wind_is_flagged_even_for_dark_region() -> None:
    image, mask = _candidate_fixture(-23.0)
    result = evaluate_dark_signature(image, mask, wind_speed_ms=0.4)
    assert result["status"] == "LOOKALIKE_RISK"
    assert result["wind_gate_passed"] is False

