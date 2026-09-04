import inspect
import json
from pathlib import Path

import pytest

from espada.verification import (
    VerificationConfig,
    infer_origins_constant,
    run_verification,
)


def _small_config() -> VerificationConfig:
    return VerificationConfig(
        seed=26143,
        particles=200,
        ensemble_members=4,
        acceptance_error_km=5.0,
    )


def test_inference_signature_cannot_receive_truth_file() -> None:
    parameters = inspect.signature(infer_origins_constant).parameters
    assert "truth" not in parameters
    assert "truth_path" not in parameters


def test_verification_generates_expected_outputs(tmp_path: Path) -> None:
    result = run_verification(tmp_path, _small_config(), check_opendrift=False)
    assert result["status"] == "PASS"
    for filename in result["artifacts"]:
        artifact = tmp_path / filename
        assert artifact.exists(), filename
        assert artifact.stat().st_size > 0, filename
    saved = json.loads((tmp_path / "verification_result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "PASS"
    assert saved["metrics"]["origin_error_km"] <= 5.0


def test_verification_is_reproducible(tmp_path: Path) -> None:
    first = run_verification(tmp_path / "first", _small_config(), check_opendrift=False)
    second = run_verification(tmp_path / "second", _small_config(), check_opendrift=False)
    assert first["estimated_origin"] == second["estimated_origin"]
    assert first["metrics"] == second["metrics"]
    assert first["acceptance"] == second["acceptance"]


def test_invalid_inference_input_is_rejected() -> None:
    import numpy as np

    with pytest.raises(ValueError):
        infer_origins_constant(
            np.array([]),
            np.array([]),
            19.0,
            _small_config_forcing(),
            seed=1,
            ensemble_members=2,
        )


def _small_config_forcing():
    from espada.models import Forcing

    return Forcing(0.32, 0.08, 4.5, -1.8, diffusivity_m2s=12.0)
