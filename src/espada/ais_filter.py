from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import haversine_km
from .models import parse_utc


@dataclass(frozen=True)
class AISFilterConfig:
    """Conservative pre-ranking gates for incident-window AIS traffic."""

    release_window_hours: float = 2.5
    minimum_search_radius_km: float = 8.0
    radius_multiplier: float = 2.0
    maximum_search_radius_km: float = 100.0


def _bearing_degrees(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    y = math.sin(delta_lon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        delta_lon
    )
    return float((math.degrees(math.atan2(y, x)) + 360.0) % 360.0)


def _angular_difference(left: float, right: float) -> float:
    return float(abs((left - right + 180.0) % 360.0 - 180.0))


def _track_diagnostics(track: pd.DataFrame) -> dict[str, object]:
    ordered = track.sort_values("timestamp_utc")
    positions = len(ordered)
    coverage_hours = 0.0
    track_bearing: float | None = None
    implied_speed_knots = 0.0
    if positions > 1:
        first = ordered.iloc[0]
        last = ordered.iloc[-1]
        coverage_hours = float(
            (last["timestamp_utc"] - first["timestamp_utc"]).total_seconds() / 3600.0
        )
        distance_km = haversine_km(
            float(first["longitude"]),
            float(first["latitude"]),
            float(last["longitude"]),
            float(last["latitude"]),
        )
        if distance_km > 0.05:
            track_bearing = _bearing_degrees(
                float(first["longitude"]),
                float(first["latitude"]),
                float(last["longitude"]),
                float(last["latitude"]),
            )
        if coverage_hours > 0:
            implied_speed_knots = float(distance_km / coverage_hours / 1.852)

    reported_course: float | None = None
    if "cog" in ordered:
        course = pd.to_numeric(ordered["cog"], errors="coerce")
        course = course[(course >= 0) & (course < 360)]
        if not course.empty:
            radians = np.radians(course.to_numpy(dtype=float))
            reported_course = float(
                (math.degrees(math.atan2(np.sin(radians).mean(), np.cos(radians).mean())) + 360.0)
                % 360.0
            )

    median_sog: float | None = None
    if "sog" in ordered:
        speeds = pd.to_numeric(ordered["sog"], errors="coerce")
        speeds = speeds[(speeds >= 0) & (speeds <= 80)]
        if not speeds.empty:
            median_sog = float(speeds.median())
    motion_speed = median_sog if median_sog is not None else implied_speed_knots
    motion_state = (
        "stationary_or_anchored"
        if motion_speed < 0.8
        else "underway"
        if motion_speed >= 2.0
        else "slow_or_manoeuvring"
    )

    if track_bearing is None or reported_course is None:
        course_evidence = "not_available"
        course_delta: float | None = None
    else:
        course_delta = _angular_difference(track_bearing, reported_course)
        course_evidence = (
            "consistent" if course_delta <= 45.0 else "mixed" if course_delta <= 90.0 else "inconsistent"
        )

    maximum_gap = 0.0
    if "gap_before_minutes" in ordered:
        gaps = pd.to_numeric(ordered["gap_before_minutes"], errors="coerce")
        maximum_gap = float(gaps.max()) if gaps.notna().any() else 0.0

    return {
        "positions": positions,
        "coverage_hours": coverage_hours,
        "median_sog_knots": median_sog,
        "implied_speed_knots": implied_speed_knots,
        "motion_state": motion_state,
        "track_bearing_degrees": track_bearing,
        "reported_course_degrees": reported_course,
        "course_difference_degrees": course_delta,
        "course_evidence": course_evidence,
        "maximum_gap_minutes": maximum_gap,
        "track_continuity": "single_fix" if positions == 1 else "gapped" if maximum_gap > 90 else "continuous",
    }


def filter_ais_candidates(
    ais_path: Path,
    release_estimate_path: Path,
    output_dir: Path,
    config: AISFilterConfig = AISFilterConfig(),
) -> dict[str, object]:
    """Remove incident-irrelevant AIS traffic before candidate scoring.

    Time overlap and passage through the origin search area are hard gates. Motion,
    course and continuity are retained as analyst context and quality evidence; they
    never create a guilt score and do not exclude a nearby stationary vessel.
    """

    frame = pd.read_csv(ais_path, dtype={"mmsi": str})
    required = {"timestamp_utc", "mmsi", "longitude", "latitude"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"AIS candidate filtering is missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("AIS candidate filtering received an empty file")

    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["mmsi"] = frame["mmsi"].astype(str).str.replace(r"\.0$", "", regex=True)
    frame = frame.dropna(subset=["timestamp_utc", "longitude", "latitude"]).copy()
    if frame.empty:
        raise ValueError("AIS candidate filtering found no valid positions")
    if "vessel_name" not in frame:
        frame["vessel_name"] = "UNKNOWN"
    if "is_interpolated" not in frame:
        frame["is_interpolated"] = False

    estimate = json.loads(Path(release_estimate_path).read_text(encoding="utf-8"))
    release_time = pd.Timestamp(parse_utc(str(estimate["release_time_utc"])), tz="UTC")
    observation_time = pd.Timestamp(parse_utc(str(estimate["observation_time_utc"])), tz="UTC")
    origin = estimate["estimated_origin"]
    origin_lon = float(origin["longitude"])
    origin_lat = float(origin["latitude"])
    credible_radius = float(estimate["credible_radius_90_km"])
    search_radius = float(
        np.clip(
            credible_radius * config.radius_multiplier,
            config.minimum_search_radius_km,
            config.maximum_search_radius_km,
        )
    )

    vessel_records: list[dict[str, object]] = []
    retained_mmsi: set[str] = set()
    for mmsi, track in frame.groupby("mmsi", sort=True):
        track = track.sort_values("timestamp_utc").copy()
        diagnostics = _track_diagnostics(track)
        offsets = (
            track["timestamp_utc"] - release_time
        ).dt.total_seconds().to_numpy(dtype=float) / 3600.0
        distances = np.asarray(
            [
                haversine_km(
                    float(lon), float(lat), origin_lon, origin_lat
                )
                for lon, lat in track[["longitude", "latitude"]].to_numpy(dtype=float)
            ],
            dtype=float,
        )
        time_mask = np.abs(offsets) <= config.release_window_hours
        time_eligible = bool(time_mask.any())
        if time_eligible:
            eligible_indices = np.flatnonzero(time_mask)
            local_index = int(np.argmin(distances[eligible_indices]))
            best_index = int(eligible_indices[local_index])
            closest_release_distance = float(distances[best_index])
            closest_time_offset = float(offsets[best_index])
            closest_time = track["timestamp_utc"].iloc[best_index]
        else:
            best_index = int(np.argmin(np.abs(offsets)))
            closest_release_distance = float(distances[best_index])
            closest_time_offset = float(offsets[best_index])
            closest_time = track["timestamp_utc"].iloc[best_index]
        origin_eligible = bool(time_eligible and closest_release_distance <= search_radius)
        if not time_eligible:
            disposition = "excluded"
            reason = "outside_release_window"
        elif not origin_eligible:
            disposition = "excluded"
            reason = "outside_origin_search_area"
        else:
            disposition = "retained"
            reason = "space_time_gate_passed"
            retained_mmsi.add(str(mmsi))

        vessel_records.append(
            {
                "mmsi": str(mmsi),
                "vessel_name": str(track["vessel_name"].iloc[0] or "UNKNOWN"),
                "disposition": disposition,
                "reason": reason,
                "time_gate_passed": time_eligible,
                "origin_gate_passed": origin_eligible,
                "closest_release_distance_km": closest_release_distance,
                "closest_time_offset_hours": closest_time_offset,
                "closest_report_time_utc": closest_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                **diagnostics,
            }
        )

    retained = frame[frame["mmsi"].isin(retained_mmsi)].copy()
    retained = retained.sort_values(["mmsi", "timestamp_utc"]).reset_index(drop=True)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filtered_path = output_dir / "ais_candidates_filtered.csv"
    saved = retained.copy()
    saved["timestamp_utc"] = saved["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    saved.to_csv(filtered_path, index=False)

    time_eligible_count = sum(bool(item["time_gate_passed"]) for item in vessel_records)
    retained_records = [item for item in vessel_records if item["disposition"] == "retained"]
    excluded_records = [item for item in vessel_records if item["disposition"] == "excluded"]
    report: dict[str, object] = {
        "status": "PASS",
        "method": "conservative incident-window AIS relevance filter",
        "source_file": str(Path(ais_path).resolve()),
        "filtered_file": str(filtered_path.resolve()),
        "release_time_utc": release_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observation_time_utc": observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estimated_origin": {"longitude": origin_lon, "latitude": origin_lat},
        "credible_radius_90_km": credible_radius,
        "search_radius_km": search_radius,
        "release_window_hours": config.release_window_hours,
        "raw_vessels": int(frame["mmsi"].nunique()),
        "raw_positions": len(frame),
        "release_window_vessels": time_eligible_count,
        "origin_zone_vessels": len(retained_records),
        "retained_vessels": len(retained_records),
        "retained_positions": len(retained),
        "excluded_vessels": len(excluded_records),
        "retained": retained_records,
        "excluded": excluded_records,
        "vessels": vessel_records,
        "hard_gates": [
            f"At least one received AIS fix within ±{config.release_window_hours:g} hours of the estimated release time.",
            f"That incident-time fix lies within the {search_radius:.2f} km conservative origin search radius.",
        ],
        "context_only": [
            "Speed and motion state are retained for analyst interpretation.",
            "Reported course is compared with track geometry when both are available.",
            "Stationary vessels are not automatically excluded.",
            "AIS gaps never increase relevance or attribution score.",
        ],
        "claim_boundary": (
            "Filtering removes traffic that is incompatible with the incident's declared time and origin area. "
            "Retention means only that a track deserves comparison; it is not evidence of discharge or guilt."
        ),
    }
    (output_dir / "ais_filter_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report
