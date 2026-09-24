from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from espada.ais_filter import filter_ais_candidates


def _write_estimate(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "release_time_utc": "2026-08-29T12:00:00Z",
                "observation_time_utc": "2026-08-29T18:00:00Z",
                "estimated_origin": {"longitude": 72.0, "latitude": 18.8},
                "credible_radius_90_km": 4.0,
            }
        ),
        encoding="utf-8",
    )
    return path


def _rows() -> list[dict[str, object]]:
    return [
        {
            "timestamp_utc": "2026-08-29T11:00:00Z",
            "mmsi": "111111111",
            "vessel_name": "NEAR UNDERWAY",
            "longitude": 71.96,
            "latitude": 18.80,
            "sog": 7.0,
            "cog": 90.0,
            "is_interpolated": False,
            "gap_before_minutes": 0.0,
        },
        {
            "timestamp_utc": "2026-08-29T13:00:00Z",
            "mmsi": "111111111",
            "vessel_name": "NEAR UNDERWAY",
            "longitude": 72.04,
            "latitude": 18.80,
            "sog": 7.2,
            "cog": 90.0,
            "is_interpolated": False,
            "gap_before_minutes": 120.0,
        },
        {
            "timestamp_utc": "2026-08-29T12:00:00Z",
            "mmsi": "222222222",
            "vessel_name": "NEAR STATIONARY",
            "longitude": 72.01,
            "latitude": 18.80,
            "sog": 0.2,
            "cog": 270.0,
            "is_interpolated": False,
            "gap_before_minutes": 0.0,
        },
        {
            "timestamp_utc": "2026-08-29T12:00:00Z",
            "mmsi": "333333333",
            "vessel_name": "FAR TRAFFIC",
            "longitude": 73.0,
            "latitude": 19.5,
            "sog": 9.0,
            "cog": 20.0,
            "is_interpolated": False,
            "gap_before_minutes": 0.0,
        },
        {
            "timestamp_utc": "2026-08-29T17:30:00Z",
            "mmsi": "444444444",
            "vessel_name": "LATE TRAFFIC",
            "longitude": 72.0,
            "latitude": 18.8,
            "sog": 4.0,
            "cog": 180.0,
            "is_interpolated": False,
            "gap_before_minutes": 0.0,
        },
    ]


def test_filter_keeps_only_incident_relevant_tracks(tmp_path: Path) -> None:
    ais = tmp_path / "ais.csv"
    pd.DataFrame(_rows()).to_csv(ais, index=False)
    report = filter_ais_candidates(
        ais,
        _write_estimate(tmp_path / "release.json"),
        tmp_path / "filtered",
    )

    assert report["raw_vessels"] == 4
    assert report["release_window_vessels"] == 3
    assert report["retained_vessels"] == 2
    assert report["excluded_vessels"] == 2
    assert {item["mmsi"] for item in report["retained"]} == {
        "111111111",
        "222222222",
    }
    reasons = {item["mmsi"]: item["reason"] for item in report["excluded"]}
    assert reasons["333333333"] == "outside_origin_search_area"
    assert reasons["444444444"] == "outside_release_window"
    filtered = pd.read_csv(report["filtered_file"], dtype={"mmsi": str})
    assert set(filtered["mmsi"]) == {"111111111", "222222222"}
    assert (tmp_path / "filtered" / "ais_filter_report.json").exists()


def test_direction_and_stationary_state_are_context_not_hard_exclusions(tmp_path: Path) -> None:
    ais = tmp_path / "ais.csv"
    pd.DataFrame(_rows()).to_csv(ais, index=False)
    report = filter_ais_candidates(
        ais,
        _write_estimate(tmp_path / "release.json"),
        tmp_path / "filtered",
    )
    retained = {item["mmsi"]: item for item in report["retained"]}
    assert retained["111111111"]["course_evidence"] == "consistent"
    assert retained["222222222"]["motion_state"] == "stationary_or_anchored"
    assert retained["222222222"]["disposition"] == "retained"
    assert "Stationary vessels are not automatically excluded." in report["context_only"]
    assert "not evidence of discharge or guilt" in report["claim_boundary"]
