from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .environment import load_cache
from .slick import load_slick


def validate_case_alignment(
    slick_path: Path,
    environment_cache: Path,
    ais_path: Path,
    output_path: Path,
    *,
    age_hours: float,
    max_ais_offset_hours: float = 2.0,
) -> dict[str, object]:
    """Verify that every case input covers the same incident window."""
    if age_hours <= 0 or max_ais_offset_hours <= 0:
        raise ValueError("age_hours and max_ais_offset_hours must be positive")
    slick = load_slick(slick_path)
    environment = load_cache(environment_cache)
    ais = pd.read_csv(ais_path, dtype={"mmsi": str})
    required = {"timestamp_utc", "mmsi", "longitude", "latitude"}
    missing = required - set(ais.columns)
    if missing:
        raise ValueError(f"AIS file is missing required columns: {sorted(missing)}")
    ais["timestamp_utc"] = pd.to_datetime(ais["timestamp_utc"], utc=True, errors="coerce")
    ais = ais.dropna(subset=["timestamp_utc", "mmsi", "longitude", "latitude"])
    if ais.empty:
        raise ValueError("AIS file has no valid timestamped positions")

    observation_time = slick.observation_time
    release_time = observation_time - timedelta(hours=age_hours)
    forcing = environment.frame.sort_values("time_utc")
    history = forcing[
        (forcing["time_utc"] >= release_time) & (forcing["time_utc"] < observation_time)
    ]
    if len(forcing) > 1:
        cadence_hours = float(
            forcing["time_utc"].diff().dt.total_seconds().dropna().median() / 3600.0
        )
    else:
        cadence_hours = 1.0
    cadence_hours = max(cadence_hours, 1.0 / 60.0)
    environment_covers_start = bool(
        not history.empty
        and history["time_utc"].min() <= release_time + timedelta(hours=1.5 * cadence_hours)
    )
    environment_covers_end = bool(
        not history.empty
        and history["time_utc"].max()
        >= observation_time - timedelta(hours=1.5 * cadence_hours)
    )

    ais_start = release_time - timedelta(hours=max_ais_offset_hours)
    ais_end = release_time + timedelta(hours=max_ais_offset_hours)
    release_evidence = ais[
        (ais["timestamp_utc"] >= ais_start) & (ais["timestamp_utc"] <= ais_end)
    ]
    checks = {
        "environment_has_at_least_two_steps": len(history) >= 2,
        "environment_covers_release_time": environment_covers_start,
        "environment_covers_observation_time": environment_covers_end,
        "ais_has_positions_near_release_time": not release_evidence.empty,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    report: dict[str, object] = {
        "status": status,
        "incident_window": {
            "estimated_release_time_utc": release_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "observation_time_utc": observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "assumed_age_hours": age_hours,
        },
        "checks": checks,
        "environment": {
            "source": environment.source,
            "time_start_utc": forcing["time_utc"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_end_utc": forcing["time_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "steps_inside_incident_window": len(history),
            "sampling_interval_hours": cadence_hours,
        },
        "ais": {
            "time_start_utc": ais["timestamp_utc"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_end_utc": ais["timestamp_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "positions": len(ais),
            "vessels": int(ais["mmsi"].nunique()),
            "positions_near_release_time": len(release_evidence),
            "vessels_near_release_time": int(release_evidence["mmsi"].nunique()),
            "allowed_release_time_offset_hours": max_ais_offset_hours,
        },
        "interpretation": (
            "All sources cover the same incident window; processing may continue."
            if status == "PASS"
            else "Inputs are not time-aligned. Processing must stop until the failed checks are corrected."
        ),
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_file"] = str(output_path.resolve())
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
