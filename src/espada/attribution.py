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
from scipy.spatial import cKDTree

from .coast import CoastMask, load_coast_mask
from .geo import haversine_km, local_xy_m
from .models import Forcing, parse_utc
from .physics import advect_diffuse_constant, advect_diffuse_spatial_timeseries
from .silence import analyze_coverage_aware_silence
from .spatial_current import SpatialCurrentGrid, load_spatial_current_grid


REQUIRED_AIS_COLUMNS = {
    "timestamp_utc",
    "mmsi",
    "vessel_name",
    "longitude",
    "latitude",
    "is_interpolated",
}


def _interpolate_release_position(
    track: pd.DataFrame, release_time: datetime, *, maximum_gap_hours: float = 6.0
) -> tuple[pd.DataFrame, bool, float | None]:
    """Add one scoring-only position when observations safely bracket release time."""
    ordered = track.copy()
    times = pd.to_datetime(ordered["timestamp_utc"], utc=True).dt.tz_localize(None)
    target = pd.Timestamp(release_time)
    if target.tzinfo is not None:
        target = target.tz_convert("UTC").tz_localize(None)
    if (times == target).any():
        return ordered, False, 0.0
    before = np.flatnonzero((times < target).to_numpy())
    after = np.flatnonzero((times > target).to_numpy())
    if not len(before) or not len(after):
        return ordered, False, None
    left_index = int(before[-1])
    right_index = int(after[0])
    gap_hours = (times.iloc[right_index] - times.iloc[left_index]).total_seconds() / 3600.0
    if gap_hours <= 0 or gap_hours > maximum_gap_hours:
        return ordered, False, gap_hours
    fraction = (target - times.iloc[left_index]).total_seconds() / (
        times.iloc[right_index] - times.iloc[left_index]
    ).total_seconds()
    row = ordered.iloc[left_index].copy()
    row["timestamp_utc"] = target.strftime("%Y-%m-%dT%H:%M:%SZ")
    row["longitude"] = float(ordered.iloc[left_index]["longitude"]) + fraction * (
        float(ordered.iloc[right_index]["longitude"])
        - float(ordered.iloc[left_index]["longitude"])
    )
    row["latitude"] = float(ordered.iloc[left_index]["latitude"]) + fraction * (
        float(ordered.iloc[right_index]["latitude"])
        - float(ordered.iloc[left_index]["latitude"])
    )
    row["is_interpolated"] = True
    if "source" in row:
        row["source"] = f"{row['source']}; scoring-only linear gap interpolation"
    augmented = pd.concat([ordered, row.to_frame().T], ignore_index=True)
    return augmented, True, gap_hours


def _observed_cloud_xy_km(
    observed_lon: np.ndarray,
    observed_lat: np.ndarray,
    observed_centroid: tuple[float, float],
    maximum_points: int = 512,
) -> np.ndarray:
    observed_x, observed_y = local_xy_m(
        observed_lon,
        observed_lat,
        observed_centroid[0],
        observed_centroid[1],
    )
    observed_xy_km = np.column_stack((observed_x, observed_y)) / 1_000.0
    if len(observed_xy_km) > maximum_points:
        sample_indices = np.linspace(0, len(observed_xy_km) - 1, maximum_points, dtype=int)
        observed_xy_km = observed_xy_km[sample_indices]
    return observed_xy_km


def _symmetric_cloud_error_km(
    predicted_lon: np.ndarray,
    predicted_lat: np.ndarray,
    observed_xy_km: np.ndarray,
    observed_centroid: tuple[float, float],
) -> float:
    predicted_x, predicted_y = local_xy_m(
        predicted_lon,
        predicted_lat,
        observed_centroid[0],
        observed_centroid[1],
    )
    predicted_xy_km = np.column_stack((predicted_x, predicted_y)) / 1_000.0
    predicted_tree = cKDTree(predicted_xy_km)
    observed_tree = cKDTree(observed_xy_km)
    predicted_to_observed = observed_tree.query(predicted_xy_km, k=1)[0]
    observed_to_predicted = predicted_tree.query(observed_xy_km, k=1)[0]
    return float(
        0.5
        * (
            np.quantile(predicted_to_observed, 0.75)
            + np.quantile(observed_to_predicted, 0.75)
        )
    )


def _score_track(
    track: pd.DataFrame,
    release_time: datetime,
    observation_time: datetime,
    estimated_lon: float,
    estimated_lat: float,
    credible_radius_km: float,
    observed_centroid: tuple[float, float],
    observed_xy_km: np.ndarray,
    forcing: Forcing,
    expected_rows: int,
    silence_report: dict | None = None,
    spatial_current_grid: SpatialCurrentGrid | None = None,
    forcing_history: pd.DataFrame | None = None,
    spatial_current_multiplier: float = 1.0,
    coast_mask: CoastMask | None = None,
) -> dict:
    observed_track = track
    scoring_track, interpolation_used, interpolation_gap_hours = _interpolate_release_position(
        observed_track, release_time
    )
    timestamps = pd.to_datetime(scoring_track["timestamp_utc"], utc=True).dt.tz_localize(None)
    lons = scoring_track["longitude"].to_numpy(dtype=float)
    lats = scoring_track["latitude"].to_numpy(dtype=float)
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
    forward_shape_scores: list[float] = []
    forward_shape_errors: list[float] = []
    deterministic = Forcing(
        forcing.current_east_ms,
        forcing.current_north_ms,
        forcing.wind_east_ms,
        forcing.wind_north_ms,
        forcing.windage,
        0.0,
    )
    dispersive = Forcing(
        forcing.current_east_ms,
        forcing.current_north_ms,
        forcing.wind_east_ms,
        forcing.wind_north_ms,
        forcing.windage,
        max(forcing.diffusivity_m2s, 1.0),
    )
    for point_index, (lon, lat, timestamp) in enumerate(zip(lons, lats, timestamps)):
        duration_hours = (observation_time - timestamp.to_pydatetime()).total_seconds() / 3600.0
        if duration_hours <= 0:
            forward_scores.append(0.0)
            forward_errors.append(float("inf"))
            forward_shape_scores.append(0.0)
            forward_shape_errors.append(float("inf"))
            continue
        rng = np.random.default_rng(10_000 + point_index)
        local_history = None
        if spatial_current_grid is not None and forcing_history is not None:
            start_utc = pd.Timestamp(timestamp)
            start_utc = start_utc.tz_localize("UTC") if start_utc.tzinfo is None else start_utc.tz_convert("UTC")
            end_utc = pd.Timestamp(observation_time)
            end_utc = end_utc.tz_localize("UTC") if end_utc.tzinfo is None else end_utc.tz_convert("UTC")
            local_history = forcing_history[
                (forcing_history["time_utc"] >= start_utc)
                & (forcing_history["time_utc"] < end_utc)
            ]
        if local_history is not None and not local_history.empty:
            spatial_step = duration_hours * 3600.0 / len(local_history)
            predicted_lon, predicted_lat = advect_diffuse_spatial_timeseries(
                lon,
                lat,
                local_history["time_utc"].tolist(),
                local_history["wind_east_ms"].to_numpy(dtype=float),
                local_history["wind_north_ms"].to_numpy(dtype=float),
                spatial_step,
                spatial_current_grid,
                rng,
                windage=forcing.windage,
                diffusivity_m2s=0.0,
                current_multiplier=spatial_current_multiplier,
                coast_mask=coast_mask,
            )
        else:
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

        cloud_size = 128
        if local_history is not None and not local_history.empty:
            cloud_lon, cloud_lat = advect_diffuse_spatial_timeseries(
                np.full(cloud_size, lon),
                np.full(cloud_size, lat),
                local_history["time_utc"].tolist(),
                local_history["wind_east_ms"].to_numpy(dtype=float),
                local_history["wind_north_ms"].to_numpy(dtype=float),
                duration_hours * 3600.0 / len(local_history),
                spatial_current_grid,
                np.random.default_rng(20_000 + point_index),
                windage=forcing.windage,
                diffusivity_m2s=dispersive.diffusivity_m2s,
                current_multiplier=spatial_current_multiplier,
                coast_mask=coast_mask,
            )
        else:
            cloud_lon, cloud_lat = advect_diffuse_constant(
                np.full(cloud_size, lon),
                np.full(cloud_size, lat),
                duration_hours * 3600.0,
                dispersive,
                np.random.default_rng(20_000 + point_index),
            )
        shape_error_km = _symmetric_cloud_error_km(
            cloud_lon,
            cloud_lat,
            observed_xy_km,
            observed_centroid,
        )
        forward_shape_errors.append(shape_error_km)
        forward_shape_scores.append(float(np.exp(-0.5 * (shape_error_km / 8.0) ** 2)))

    forward_combined = 0.55 * np.asarray(forward_scores) + 0.45 * np.asarray(
        forward_shape_scores
    )
    joint = 0.55 * presence_by_point + 0.40 * forward_combined
    best_index = int(np.argmax(joint))
    coverage_quality = min(1.0, len(observed_track) / max(expected_rows, 1))
    interpolated_fraction = float(observed_track["is_interpolated"].astype(str).str.lower().isin({"true", "1", "yes"}).mean())
    maximum_gap = float(observed_track["gap_before_minutes"].max()) if "gap_before_minutes" in observed_track else 0.0
    sampling_interval = 0.0
    if "sampling_interval_minutes" in observed_track:
        sampling = pd.to_numeric(observed_track["sampling_interval_minutes"], errors="coerce")
        valid_sampling = sampling.loc[sampling > 0]
        sampling_interval = float(valid_sampling.median()) if not valid_sampling.empty else 0.0
    gap_threshold = max(30.0, sampling_interval * 1.5)
    gap_penalty = min(0.35, max(0.0, maximum_gap - gap_threshold) / 360.0)
    data_quality = float(np.clip(coverage_quality * (1.0 - 0.25 * interpolated_fraction - gap_penalty), 0.0, 1.0))
    interpolation_penalty = 0.015 if interpolation_used else 0.0
    total_score = float(
        np.clip(joint[best_index] + 0.05 * data_quality - interpolation_penalty, 0.0, 1.0)
    )
    silence_report = silence_report or {
        "classification": "not_evaluated",
        "significant_gaps": 0,
        "gaps_with_local_peer_reception": 0,
        "maximum_gap_minutes": maximum_gap,
        "interpretation": "Coverage-aware silence analysis was not available.",
    }
    return {
        "mmsi": str(observed_track["mmsi"].iloc[0]),
        "vessel_name": str(observed_track["vessel_name"].iloc[0]),
        "total_score": total_score,
        "presence_score": presence_score,
        "forward_consistency": float(forward_combined[best_index]),
        "forward_error_km": float(forward_errors[best_index]),
        "forward_centroid_consistency": float(forward_scores[best_index]),
        "forward_shape_consistency": float(forward_shape_scores[best_index]),
        "forward_shape_error_km": float(forward_shape_errors[best_index]),
        "data_quality": data_quality,
        "release_position_interpolated": interpolation_used,
        "interpolation_gap_hours": interpolation_gap_hours,
        "interpolation_score_penalty": interpolation_penalty,
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
            "Forward particle-cloud consistency with both the slick centroid and mapped shape.",
            (
                "Forward replay sampled particle-local Copernicus surface currents."
                if spatial_current_grid is not None
                else "Forward replay used the case-mean surface current."
            ),
            *(
                ["Forward particle paths intersecting supplied land polygons were rejected."]
                if coast_mask is not None
                else []
            ),
            "Coverage-aware AIS gap classification using simultaneous nearby peer reception.",
            (
                "Release-time position was linearly interpolated inside a bounded AIS gap and penalized."
                if interpolation_used
                else "No release-time gap interpolation was used."
            ),
        ],
        "limitations": [
            (
                "Synthetic AIS validation fixture."
                if str(observed_track.get("source", pd.Series([""])).iloc[0]).startswith("synthetic")
                else "AIS identity and reception completeness require independent verification."
            ),
            "Interpolated positions are hypotheses, not received AIS messages.",
            "Shape matching uses a point-release particle cloud and a robust symmetric nearest-neighbour distance.",
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
        observed_lon = np.asarray(forward["lon"], dtype=float)
        observed_lat = np.asarray(forward["lat"], dtype=float)
        observed_centroid = (
            float(np.mean(observed_lon)),
            float(np.mean(observed_lat)),
        )
    observed_xy_km = _observed_cloud_xy_km(
        observed_lon,
        observed_lat,
        observed_centroid,
    )
    estimate = json.loads(Path(release_estimate_path).read_text(encoding="utf-8"))
    release_time = parse_utc(estimate["release_time_utc"])
    observation_time = parse_utc(estimate["observation_time_utc"])
    estimated_lon = float(estimate["estimated_origin"]["longitude"])
    estimated_lat = float(estimate["estimated_origin"]["latitude"])
    forcing = Forcing.from_dict(estimate["believed_forcing"])
    provenance = estimate.get("forcing_provenance", {})
    grid_path = provenance.get("spatial_current_grid")
    spatial_grid = load_spatial_current_grid(Path(grid_path)) if grid_path else None
    land_path = provenance.get("land_mask")
    coast_mask = load_coast_mask(Path(land_path)) if land_path else None
    history_path = Path(release_estimate_path).parent / "forcing_history.csv"
    forcing_history = pd.read_csv(history_path) if spatial_grid and history_path.exists() else None
    if forcing_history is not None:
        forcing_history["time_utc"] = pd.to_datetime(forcing_history["time_utc"], utc=True)
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
            observed_xy_km,
            forcing,
            expected_rows,
            silence_by_mmsi.get(str(track["mmsi"].iloc[0])),
            spatial_grid,
            forcing_history,
            1.0,
            coast_mask,
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
