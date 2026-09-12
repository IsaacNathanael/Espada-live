from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "espada-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .geo import haversine_km, local_xy_m
from .models import Forcing, parse_utc
from .physics import advect_diffuse_constant
from .silence import analyze_coverage_aware_silence


REQUIRED_AIS_COLUMNS = {
    "timestamp_utc",
    "mmsi",
    "vessel_name",
    "longitude",
    "latitude",
    "is_interpolated",
}


def _score_track(
    track: pd.DataFrame,
    release_time: datetime,
    observation_time: datetime,
    estimated_lon: float,
    estimated_lat: float,
    credible_radius_km: float,
    observed_centroid: tuple[float, float],
    forcing: Forcing,
    expected_rows: int,
    silence_report: dict | None = None,
) -> dict:
    timestamps = pd.to_datetime(track["timestamp_utc"], utc=True).dt.tz_localize(None)
    lons = track["longitude"].to_numpy(dtype=float)
    lats = track["latitude"].to_numpy(dtype=float)
    x, y = local_xy_m(lons, lats, estimated_lon, estimated_lat)
    spatial_km = np.sqrt(x**2 + y**2) / 1000.0
    time_hours = np.abs((timestamps - release_time).dt.total_seconds().to_numpy() / 3600.0)
    radius = max(credible_radius_km, 1.0)
    presence_by_point = np.exp(-0.5 * (spatial_km / radius) ** 2) * np.exp(
        -0.5 * (time_hours / 1.25) ** 2
    )
    presence_score = float(np.max(presence_by_point))

    forward_scores: list[float] = []
    forward_errors: list[float] = []
    deterministic = Forcing(
        forcing.current_east_ms,
        forcing.current_north_ms,
        forcing.wind_east_ms,
        forcing.wind_north_ms,
        forcing.windage,
        0.0,
    )
    rng = np.random.default_rng(100)
    for lon, lat, timestamp in zip(lons, lats, timestamps):
        duration_hours = (observation_time - timestamp.to_pydatetime()).total_seconds() / 3600.0
        if duration_hours <= 0:
            forward_scores.append(0.0)
            forward_errors.append(float("inf"))
            continue
        predicted_lon, predicted_lat = advect_diffuse_constant(
            lon,
            lat,
            duration_hours * 3600.0,
            deterministic,
            rng,
        )
        error_km = haversine_km(
            float(predicted_lon[0]),
            float(predicted_lat[0]),
            observed_centroid[0],
            observed_centroid[1],
        )
        forward_errors.append(error_km)
        forward_scores.append(float(np.exp(-0.5 * (error_km / 8.0) ** 2)))

    joint = 0.55 * presence_by_point + 0.40 * np.asarray(forward_scores)
    best_index = int(np.argmax(joint))
    coverage_quality = min(1.0, len(track) / max(expected_rows, 1))
    interpolated_fraction = float(track["is_interpolated"].astype(str).str.lower().isin({"true", "1", "yes"}).mean())
    maximum_gap = float(track["gap_before_minutes"].max()) if "gap_before_minutes" in track else 0.0
    sampling_interval = 0.0
    if "sampling_interval_minutes" in track:
        sampling = pd.to_numeric(track["sampling_interval_minutes"], errors="coerce")
        valid_sampling = sampling.loc[sampling > 0]
        sampling_interval = float(valid_sampling.median()) if not valid_sampling.empty else 0.0
    gap_threshold = max(30.0, sampling_interval * 1.5)
    gap_penalty = min(0.35, max(0.0, maximum_gap - gap_threshold) / 360.0)
    data_quality = float(np.clip(coverage_quality * (1.0 - 0.25 * interpolated_fraction - gap_penalty), 0.0, 1.0))
    total_score = float(np.clip(joint[best_index] + 0.05 * data_quality, 0.0, 1.0))
    silence_report = silence_report or {
        "classification": "not_evaluated",
        "significant_gaps": 0,
        "gaps_with_local_peer_reception": 0,
        "maximum_gap_minutes": maximum_gap,
        "interpretation": "Coverage-aware silence analysis was not available.",
    }
    return {
        "mmsi": str(track["mmsi"].iloc[0]),
        "vessel_name": str(track["vessel_name"].iloc[0]),
        "total_score": total_score,
        "presence_score": presence_score,
        "forward_consistency": float(forward_scores[best_index]),
        "forward_error_km": float(forward_errors[best_index]),
        "data_quality": data_quality,
        "gap_threshold_minutes": gap_threshold,
        "silence_classification": silence_report["classification"],
        "significant_gaps": silence_report["significant_gaps"],
        "gaps_with_local_peer_reception": silence_report[
            "gaps_with_local_peer_reception"
        ],
        "silence_interpretation": silence_report["interpretation"],
        "best_match_time_utc": timestamps.iloc[best_index].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence": [
            "Space-time proximity to the inferred release distribution.",
            "Forward trajectory consistency with the observed slick centroid.",
            "Coverage-aware AIS gap classification using simultaneous nearby peer reception.",
        ],
        "limitations": [
            (
                "Synthetic AIS validation fixture."
                if str(track.get("source", pd.Series([""])).iloc[0]).startswith("synthetic")
                else "AIS identity and reception completeness require independent verification."
            ),
            "Point-release centroid comparison; slick-shape comparison is not yet implemented.",
            "AIS gaps do not receive a deliberate-behaviour bonus.",
        ],
    }


def rank_candidates(
    ais_path: Path,
    reverse_endpoints_path: Path,
    release_estimate_path: Path,
    forward_particles_path: Path,
) -> tuple[list[dict], pd.DataFrame, np.ndarray, np.ndarray, tuple[float, float]]:
    """Rank candidates from inference artifacts. No answer-key path is accepted."""
    ais = pd.read_csv(ais_path, dtype={"mmsi": str})
    missing = REQUIRED_AIS_COLUMNS - set(ais.columns)
    if missing:
        raise ValueError(f"AIS file is missing required columns: {sorted(missing)}")
    if ais.empty:
        raise ValueError("AIS file is empty")

    with np.load(reverse_endpoints_path) as reverse:
        origin_lon = np.asarray(reverse["lon"], dtype=float)
        origin_lat = np.asarray(reverse["lat"], dtype=float)
    with np.load(forward_particles_path) as forward:
        observed_centroid = (
            float(np.mean(forward["lon"])),
            float(np.mean(forward["lat"])),
        )
    estimate = json.loads(Path(release_estimate_path).read_text(encoding="utf-8"))
    release_time = parse_utc(estimate["release_time_utc"])
    observation_time = parse_utc(estimate["observation_time_utc"])
    estimated_lon = float(estimate["estimated_origin"]["longitude"])
    estimated_lat = float(estimate["estimated_origin"]["latitude"])
    forcing = Forcing.from_dict(estimate["believed_forcing"])
    expected_rows = int(ais.groupby("mmsi").size().max())

    silence = analyze_coverage_aware_silence(ais)
    silence_by_mmsi = {item["mmsi"]: item for item in silence["vessels"]}
    candidates = [
        _score_track(
            track,
            release_time,
            observation_time,
            estimated_lon,
            estimated_lat,
            float(estimate["credible_radius_90_km"]),
            observed_centroid,
            forcing,
            expected_rows,
            silence_by_mmsi.get(str(track["mmsi"].iloc[0])),
        )
        for _, track in ais.groupby("mmsi", sort=False)
    ]
    candidates.sort(key=lambda item: item["total_score"], reverse=True)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return candidates, ais, origin_lon, origin_lat, observed_centroid, silence


def _plot_attribution(
    path: Path,
    candidates: list[dict],
    ais: pd.DataFrame,
    origin_lon: np.ndarray,
    origin_lat: np.ndarray,
    observed_centroid: tuple[float, float],
) -> None:
    top_mmsi = candidates[0]["mmsi"]
    fig, ax = plt.subplots(figsize=(10, 7), constrained_layout=True)
    ax.hist2d(origin_lon, origin_lat, bins=80, cmap="YlOrRd", alpha=0.72)
    for mmsi, track in ais.groupby("mmsi"):
        is_top = str(mmsi) == top_mmsi
        ax.plot(
            track["longitude"],
            track["latitude"],
            color="#F39C12" if is_top else "#7F8C8D",
            linewidth=3.0 if is_top else 1.0,
            alpha=1.0 if is_top else 0.48,
            label=f"Top candidate: {track['vessel_name'].iloc[0]}" if is_top else None,
        )
    ax.scatter(
        [observed_centroid[0]],
        [observed_centroid[1]],
        marker="o",
        s=95,
        color="#0B6FA4",
        edgecolor="white",
        label="Observed slick centroid",
    )
    ax.set_title("AIS attribution over inferred origin probability")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(alpha=0.18)
    ax.legend()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _plot_ranking(path: Path, candidates: list[dict]) -> None:
    top = list(reversed(candidates[:5]))
    names = [item["vessel_name"] for item in top]
    values = [100.0 * item["total_score"] for item in top]
    colors = ["#F39C12" if item["rank"] == 1 else "#2E86AB" for item in top]
    fig, ax = plt.subplots(figsize=(9, 5.5), constrained_layout=True)
    bars = ax.barh(names, values, color=colors)
    ax.bar_label(bars, fmt="%.1f%%", padding=5)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Candidate score")
    ax.set_title("Ranked vessel candidates")
    ax.grid(axis="x", alpha=0.2)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_attribution_outputs(
    output_dir: Path,
    ais_path: Path,
    reverse_endpoints_path: Path,
    release_estimate_path: Path,
    forward_particles_path: Path,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates, ais, origin_lon, origin_lat, observed_centroid, silence = rank_candidates(
        ais_path,
        reverse_endpoints_path,
        release_estimate_path,
        forward_particles_path,
    )
    synthetic = bool(ais["source"].astype(str).str.startswith("synthetic").all()) if "source" in ais else False
    result = {
        "status": "PASS",
        "candidate_count": len(candidates),
        "top_candidate": candidates[0],
        "candidates": candidates,
        "silence_analysis": silence,
        "warning": (
            "Synthetic validation output; candidate ranking is not a finding of guilt."
            if synthetic
            else "AIS import output; identity and data completeness require verification, and ranking is not a finding of guilt."
        ),
    }
    (output_dir / "candidates.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (output_dir / "ais_silence_analysis.json").write_text(
        json.dumps(silence, indent=2), encoding="utf-8"
    )
    _plot_attribution(
        output_dir / "attribution_map.png",
        candidates,
        ais,
        origin_lon,
        origin_lat,
        observed_centroid,
    )
    _plot_ranking(output_dir / "candidate_ranking.png", candidates)
    return result
