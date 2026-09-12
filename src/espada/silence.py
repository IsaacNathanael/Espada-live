from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SilenceConfig:
    minimum_gap_minutes: float = 30.0
    cadence_multiplier: float = 1.5
    peer_radius_km: float = 50.0
    minimum_peer_vessels: int = 2
    minimum_peer_reports: int = 3


def _dataset_cadence_minutes(frame: pd.DataFrame) -> float:
    diffs = (
        frame.sort_values(["mmsi", "timestamp_utc"])
        .groupby("mmsi")["timestamp_utc"]
        .diff()
        .dt.total_seconds()
        .div(60.0)
    )
    valid = diffs[(diffs > 0) & np.isfinite(diffs)]
    return float(valid.median()) if not valid.empty else 0.0


def _peer_support(
    frame: pd.DataFrame,
    *,
    target_mmsi: str,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    radius_km: float,
) -> tuple[int, int]:
    peers = frame[
        (frame["mmsi"].astype(str) != target_mmsi)
        & (frame["timestamp_utc"] > start_time)
        & (frame["timestamp_utc"] < end_time)
    ].copy()
    if peers.empty:
        return 0, 0
    duration = max((end_time - start_time).total_seconds(), 1.0)
    fraction = (peers["timestamp_utc"] - start_time).dt.total_seconds().to_numpy() / duration
    expected_lon = start_lon + fraction * (end_lon - start_lon)
    expected_lat = start_lat + fraction * (end_lat - start_lat)
    peer_lon = peers["longitude"].to_numpy(dtype=float)
    peer_lat = peers["latitude"].to_numpy(dtype=float)
    mean_lat = np.radians((expected_lat + peer_lat) * 0.5)
    dx = (peer_lon - expected_lon) * np.cos(mean_lat) * 111.320
    dy = (peer_lat - expected_lat) * 110.574
    nearby = peers.loc[np.sqrt(dx * dx + dy * dy) <= radius_km]
    return len(nearby), int(nearby["mmsi"].astype(str).nunique())


def analyze_coverage_aware_silence(
    ais: pd.DataFrame,
    config: SilenceConfig = SilenceConfig(),
) -> dict[str, object]:
    """Classify AIS gaps using simultaneous nearby peer reception.

    Peer reception is evidence that the data source was receiving traffic in the
    local space-time window. It cannot establish why the target vessel is absent,
    and the resulting classification must never be used as a guilt bonus.
    """
    required = {"timestamp_utc", "mmsi", "longitude", "latitude"}
    missing = required - set(ais.columns)
    if missing:
        raise ValueError(f"AIS silence analysis is missing columns: {sorted(missing)}")
    frame = ais.copy()
    frame["mmsi"] = frame["mmsi"].astype(str)
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame = frame.dropna(subset=["timestamp_utc", "longitude", "latitude"])
    cadence = _dataset_cadence_minutes(frame)
    declared = pd.Series(dtype=float)
    if "sampling_interval_minutes" in frame:
        declared = pd.to_numeric(frame["sampling_interval_minutes"], errors="coerce")
        declared = declared[declared > 0]
    declared_cadence = float(declared.median()) if not declared.empty else 0.0
    effective_cadence = declared_cadence or cadence
    gap_threshold = max(
        config.minimum_gap_minutes,
        effective_cadence * config.cadence_multiplier,
    )

    vessel_reports: list[dict[str, object]] = []
    for mmsi, track in frame.groupby("mmsi", sort=True):
        track = track.sort_values("timestamp_utc").reset_index(drop=True)
        gaps: list[dict[str, object]] = []
        for index in range(1, len(track)):
            start = track.iloc[index - 1]
            end = track.iloc[index]
            duration_minutes = float(
                (end["timestamp_utc"] - start["timestamp_utc"]).total_seconds() / 60.0
            )
            if duration_minutes <= gap_threshold:
                continue
            peer_reports, peer_vessels = _peer_support(
                frame,
                target_mmsi=str(mmsi),
                start_time=start["timestamp_utc"],
                end_time=end["timestamp_utc"],
                start_lon=float(start["longitude"]),
                start_lat=float(start["latitude"]),
                end_lon=float(end["longitude"]),
                end_lat=float(end["latitude"]),
                radius_km=config.peer_radius_km,
            )
            coverage_supported = bool(
                peer_vessels >= config.minimum_peer_vessels
                and peer_reports >= config.minimum_peer_reports
            )
            gaps.append(
                {
                    "start_utc": start["timestamp_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_utc": end["timestamp_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duration_minutes": duration_minutes,
                    "nearby_peer_reports": peer_reports,
                    "nearby_peer_vessels": peer_vessels,
                    "local_reception_supported": coverage_supported,
                }
            )
        supported = sum(bool(item["local_reception_supported"]) for item in gaps)
        if not gaps:
            classification = "no_significant_gap"
        elif supported == len(gaps):
            classification = "vessel_specific_gap_with_peer_coverage"
        elif supported == 0:
            classification = "coverage_unresolved"
        else:
            classification = "mixed_gap_evidence"
        vessel_reports.append(
            {
                "mmsi": str(mmsi),
                "vessel_name": str(track.get("vessel_name", pd.Series(["UNKNOWN"])).iloc[0]),
                "classification": classification,
                "significant_gaps": len(gaps),
                "gaps_with_local_peer_reception": supported,
                "maximum_gap_minutes": max(
                    (float(item["duration_minutes"]) for item in gaps), default=0.0
                ),
                "gaps": gaps,
                "interpretation": (
                    "Nearby vessels were still being received during at least one gap. "
                    "This makes a broad local reception outage less likely, but does not establish intent."
                    if supported
                    else "No nearby peer reception establishes coverage during the gap; its cause is unresolved."
                    if gaps
                    else "No gap exceeded the source-aware threshold."
                ),
            }
        )
    return {
        "status": "PASS",
        "method": "local peer-reception coverage proxy",
        "sampling_interval_minutes": effective_cadence or None,
        "gap_threshold_minutes": gap_threshold,
        "peer_radius_km": config.peer_radius_km,
        "vessels_analyzed": len(vessel_reports),
        "vessels_with_significant_gaps": sum(
            item["significant_gaps"] > 0 for item in vessel_reports
        ),
        "vessels_with_peer_supported_gaps": sum(
            item["gaps_with_local_peer_reception"] > 0 for item in vessel_reports
        ),
        "vessels": vessel_reports,
        "safety_rule": "Silence classification never increases the attribution score.",
        "limitations": [
            "Peer reception is a coverage proxy, not a calibrated receiver-coverage map.",
            "A vessel-specific gap can result from equipment, regulation, filtering or data-provider effects.",
            "Silence is never proof of deliberate disabling, discharge, identity or guilt.",
        ],
    }
