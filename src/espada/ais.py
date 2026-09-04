from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "espada-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .geo import haversine_km


ALIASES = {
    "timestamp_utc": {"timestamputc", "timestamp", "basedatetime", "datetime", "time", "positiontimestamp"},
    "mmsi": {"mmsi", "shipmmsi"},
    "vessel_name": {"vesselname", "shipname", "name"},
    "longitude": {"longitude", "lon", "long"},
    "latitude": {"latitude", "lat"},
    "sog": {"sog", "speedoverground", "speed"},
    "cog": {"cog", "courseoverground", "course"},
    "is_interpolated": {"isinterpolated", "interpolated"},
    "source": {"source", "datasource"},
    "sampling_interval_minutes": {"samplingintervalminutes", "samplingminutes"},
}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _column_mapping(columns: list[object]) -> dict[object, str]:
    mapping: dict[object, str] = {}
    claimed: set[str] = set()
    for original in columns:
        normalized = _key(original)
        for canonical, aliases in ALIASES.items():
            if canonical not in claimed and normalized in aliases:
                mapping[original] = canonical
                claimed.add(canonical)
                break
    return mapping


def _quality_by_vessel(
    frame: pd.DataFrame, gap_threshold_minutes: float
) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    for mmsi, track in frame.groupby("mmsi", sort=True):
        track = track.sort_values("timestamp_utc")
        times = track["timestamp_utc"]
        gaps = times.diff().dt.total_seconds().div(60.0)
        suspicious = 0
        implied_speeds: list[float] = []
        for index in range(1, len(track)):
            hours = (times.iloc[index] - times.iloc[index - 1]).total_seconds() / 3600.0
            if hours <= 0:
                continue
            distance_km = haversine_km(
                float(track["longitude"].iloc[index - 1]),
                float(track["latitude"].iloc[index - 1]),
                float(track["longitude"].iloc[index]),
                float(track["latitude"].iloc[index]),
            )
            speed_knots = distance_km / hours / 1.852
            implied_speeds.append(speed_knots)
            suspicious += int(speed_knots > 60.0)
        maximum_gap = float(gaps.max()) if gaps.notna().any() else 0.0
        coverage_hours = (
            float((times.max() - times.min()).total_seconds() / 3600.0) if len(track) > 1 else 0.0
        )
        reports.append(
            {
                "mmsi": str(mmsi),
                "vessel_name": str(track["vessel_name"].iloc[0]),
                "positions": len(track),
                "coverage_hours": coverage_hours,
                "maximum_gap_minutes": maximum_gap,
                "gaps_over_threshold": int((gaps > gap_threshold_minutes).sum()),
                "suspicious_jumps_over_60_knots": suspicious,
                "maximum_implied_speed_knots": max(implied_speeds, default=0.0),
            }
        )
    return reports


def normalize_ais_csv(
    input_path: Path,
    output_dir: Path,
    *,
    normalized_path: Path | None = None,
) -> dict[str, object]:
    input_path = Path(input_path)
    raw = pd.read_csv(input_path, dtype=str)
    if raw.empty:
        raise ValueError("AIS CSV is empty")
    raw = raw.rename(columns=_column_mapping(list(raw.columns)))
    required = {"timestamp_utc", "mmsi", "longitude", "latitude"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"AIS CSV is missing required columns: {sorted(missing)}")
    total_rows = len(raw)
    frame = raw.copy()
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["mmsi"] = frame["mmsi"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    valid = (
        frame["timestamp_utc"].notna()
        & frame["longitude"].between(-180, 180)
        & frame["latitude"].between(-90, 90)
        & frame["mmsi"].str.fullmatch(r"\d{9}")
    )
    frame = frame.loc[valid].copy()
    invalid_rows = total_rows - len(frame)
    if frame.empty:
        raise ValueError("AIS CSV has no valid positions after timestamp, MMSI and coordinate checks")
    duplicate_rows = int(frame.duplicated(["mmsi", "timestamp_utc"]).sum())
    frame = frame.drop_duplicates(["mmsi", "timestamp_utc"], keep="last")
    if "vessel_name" not in frame:
        frame["vessel_name"] = "UNKNOWN"
    frame["vessel_name"] = frame["vessel_name"].fillna("UNKNOWN").replace("", "UNKNOWN")
    if "is_interpolated" not in frame:
        frame["is_interpolated"] = False
    else:
        frame["is_interpolated"] = frame["is_interpolated"].astype(str).str.lower().isin(
            {"true", "1", "yes"}
        )
    if "source" not in frame:
        frame["source"] = f"CSV import: {input_path.name}"
    frame["source"] = frame["source"].fillna(f"CSV import: {input_path.name}")
    sampling_interval_minutes = 0.0
    if "sampling_interval_minutes" in frame:
        sampling = pd.to_numeric(frame["sampling_interval_minutes"], errors="coerce")
        valid_sampling = sampling.loc[sampling > 0]
        if not valid_sampling.empty:
            sampling_interval_minutes = float(valid_sampling.median())
        frame["sampling_interval_minutes"] = sampling
    elif frame["source"].astype(str).str.startswith("Global Fishing Watch").all():
        # GFW Vessel Presence is explicitly sampled at one position per vessel per hour.
        sampling_interval_minutes = 60.0
        frame["sampling_interval_minutes"] = sampling_interval_minutes
    gap_threshold_minutes = max(30.0, sampling_interval_minutes * 1.5)
    frame = frame.sort_values(["mmsi", "timestamp_utc"]).reset_index(drop=True)
    frame["gap_before_minutes"] = frame.groupby("mmsi")["timestamp_utc"].diff().dt.total_seconds().div(60.0).fillna(0.0)
    vessel_reports = _quality_by_vessel(frame, gap_threshold_minutes)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = frame.copy()
    saved["timestamp_utc"] = saved["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    columns = [
        "timestamp_utc", "mmsi", "vessel_name", "longitude", "latitude",
        "is_interpolated", "gap_before_minutes", "source",
    ] + [name for name in ("sog", "cog", "sampling_interval_minutes") if name in saved]
    normalized_path = Path(normalized_path or output_dir / "ais_normalized.csv")
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    saved[columns].to_csv(normalized_path, index=False)
    gap_vessels = sum(report["gaps_over_threshold"] > 0 for report in vessel_reports)
    suspicious_jumps = sum(int(report["suspicious_jumps_over_60_knots"]) for report in vessel_reports)
    report = {
        "status": "PASS",
        "source_file": str(input_path.resolve()),
        "normalized_file": str(normalized_path.resolve()),
        "input_rows": total_rows,
        "valid_rows": len(saved),
        "invalid_rows_removed": invalid_rows,
        "duplicate_rows_removed": duplicate_rows,
        "vessel_count": int(saved["mmsi"].nunique()),
        "sampling_interval_minutes": sampling_interval_minutes or None,
        "gap_threshold_minutes": gap_threshold_minutes,
        "vessels_with_gaps_over_threshold": gap_vessels,
        "suspicious_jumps_over_60_knots": suspicious_jumps,
        "time_start_utc": saved["timestamp_utc"].min(),
        "time_end_utc": saved["timestamp_utc"].max(),
        "vessels": vessel_reports,
        "limitations": [
            "CSV normalization validates structure and plausibility; it does not prove AIS authenticity.",
            "AIS silence can result from reception gaps, equipment failure, regulation or deliberate action.",
            "Candidate ranking is investigative support and never a determination of guilt.",
        ],
        "artifacts": [str(normalized_path), "ais_quality.json", "ais_quality.png"],
    }
    (output_dir / "ais_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sorted_reports = sorted(vessel_reports, key=lambda item: int(item["positions"]), reverse=True)
    fig, axis = plt.subplots(figsize=(10, max(4.8, len(sorted_reports) * 0.32)), constrained_layout=True)
    names = [str(item["vessel_name"]) for item in sorted_reports]
    positions = [int(item["positions"]) for item in sorted_reports]
    colors = ["#F49A24" if item["gaps_over_threshold"] else "#087F7B" for item in sorted_reports]
    bars = axis.barh(names[::-1], positions[::-1], color=colors[::-1])
    axis.bar_label(bars, padding=4, fontsize=8)
    axis.set_xlabel("Validated AIS positions")
    axis.set_title(
        f"AIS coverage quality · orange indicates a gap over {gap_threshold_minutes:g} minutes"
    )
    axis.grid(axis="x", alpha=0.2)
    fig.savefig(output_dir / "ais_quality.png", dpi=180)
    plt.close(fig)
    return report
