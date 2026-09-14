from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from espada.challenge import (
    _age_grid,
    _apply_evidence_stress,
    _commit_truth,
    validate_config,
)


def test_challenge_config_merges_defaults_and_builds_age_grid() -> None:
    config = validate_config(
        {
            "schema_version": "1.0",
            "satellite_delay_hours": 10,
            "inference": {
                "minimum_age_hours": 6,
                "maximum_age_hours": 12,
                "age_step_hours": 2,
            },
        }
    )
    assert _age_grid(config["inference"]) == (6.0, 8.0, 10.0, 12.0)
    assert config["truth"]["windage"] == 0.02


def test_challenge_rejects_excessive_release_duration() -> None:
    with pytest.raises(ValueError, match="between 0 and 180"):
        validate_config(
            {
                "schema_version": "1.0",
                "satellite_delay_hours": 4,
                "release_duration_minutes": 240,
            }
        )


def test_evidence_stress_preserves_minimum_tracks_and_marks_decoy() -> None:
    timestamps = pd.date_range("2020-01-01T00:00:00Z", periods=5, freq="h")
    frame = pd.concat(
        [
            pd.DataFrame(
                {
                    "timestamp_utc": timestamps,
                    "mmsi": vessel,
                    "vessel_name": vessel,
                    "longitude": np.linspace(index, index + 0.1, len(timestamps)),
                    "latitude": np.linspace(10.0, 10.1, len(timestamps)),
                    "source": "test",
                    "is_interpolated": False,
                }
            )
            for index, vessel in enumerate(("980000001", "980000002", "980000003"))
        ],
        ignore_index=True,
    )
    stressed, notes = _apply_evidence_stress(
        frame,
        "980000001",
        timestamps[2],
        (0.05, 10.05),
        {
            "random_ais_dropout_fraction": 0.8,
            "source_blackout_hours": 2.0,
            "position_noise_m": 0.0,
            "spoofed_decoy_tracks": 1,
        },
        np.random.default_rng(3),
    )
    assert stressed.groupby("mmsi").size().min() >= 2
    assert stressed["source"].str.contains("spoofing decoy").any()
    assert any("blackout" in note for note in notes)


def test_truth_commitment_detects_changes() -> None:
    truth = {"source_id": "980000001", "release_age_hours": 12.0}
    commitment, canonical = _commit_truth(truth)
    assert hashlib.sha256(canonical.encode()).hexdigest() == commitment["commitment"]
    changed = json.dumps({**truth, "release_age_hours": 14.0}, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(changed.encode()).hexdigest() != commitment["commitment"]
