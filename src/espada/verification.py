from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
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

from .coast import CoastMask
from .geo import haversine_km, local_xy_m
from .models import Forcing, format_utc
from .physics import (
    advect_diffuse_constant,
    advect_diffuse_spatial_timeseries,
    advect_diffuse_timeseries,
    run_opendrift_constant,
)
from .spatial_current import SpatialCurrentGrid


@dataclass(frozen=True)
class VerificationConfig:
    seed: int = 26143
    release_lon: float = 71.45
    release_lat: float = 18.7167
    release_time: datetime = datetime(2026, 3, 15, 2, 0, 0)
    age_hours: float = 19.0
    particles: int = 2_000
    ensemble_members: int = 20
    acceptance_error_km: float = 5.0


def infer_origins_constant(
    observed_lon: np.ndarray,
    observed_lat: np.ndarray,
    age_hours: float,
    believed_forcing: Forcing,
    *,
    seed: int,
    ensemble_members: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Infer possible origins without accepting or reading an answer key."""
    if observed_lon.size == 0 or observed_lon.shape != observed_lat.shape:
        raise ValueError("Observed longitude and latitude arrays must be non-empty and aligned")
    if age_hours <= 0 or ensemble_members <= 0:
        raise ValueError("Age and ensemble member count must be positive")

    rng = np.random.default_rng(seed)
    origin_lons: list[np.ndarray] = []
    origin_lats: list[np.ndarray] = []
    duration_seconds = age_hours * 3600.0
    for _ in range(ensemble_members):
        member_forcing = Forcing(
            current_east_ms=float(rng.normal(believed_forcing.current_east_ms, 0.06)),
            current_north_ms=float(rng.normal(believed_forcing.current_north_ms, 0.04)),
            wind_east_ms=float(rng.normal(believed_forcing.wind_east_ms, 1.2)),
            wind_north_ms=float(rng.normal(believed_forcing.wind_north_ms, 0.8)),
            windage=believed_forcing.windage,
            diffusivity_m2s=believed_forcing.diffusivity_m2s,
        )
        lon, lat = advect_diffuse_constant(
            observed_lon,
            observed_lat,
            duration_seconds,
            member_forcing,
            rng,
            reverse=True,
        )
        origin_lons.append(lon)
        origin_lats.append(lat)
    return np.concatenate(origin_lons), np.concatenate(origin_lats)


def infer_origins_timeseries(
    observed_lon: np.ndarray,
    observed_lat: np.ndarray,
    believed_series: pd.DataFrame,
    *,
    step_seconds: float,
    seed: int,
    ensemble_members: int,
    windage: float = 0.02,
    diffusivity_m2s: float = 12.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Infer origins from hourly forcing without accepting an answer key."""
    if observed_lon.size == 0 or observed_lon.shape != observed_lat.shape:
        raise ValueError("Observed longitude and latitude arrays must be non-empty and aligned")
    required = {"current_east_ms", "current_north_ms", "wind_east_ms", "wind_north_ms"}
    if required - set(believed_series.columns) or believed_series.empty:
        raise ValueError("Believed forcing series is incomplete")
    if step_seconds <= 0 or ensemble_members <= 0:
        raise ValueError("Step duration and ensemble member count must be positive")
    rng = np.random.default_rng(seed)
    origins_lon: list[np.ndarray] = []
    origins_lat: list[np.ndarray] = []
    for _ in range(ensemble_members):
        current_east = believed_series["current_east_ms"].to_numpy(dtype=float) + rng.normal(0.0, 0.05)
        current_north = believed_series["current_north_ms"].to_numpy(dtype=float) + rng.normal(0.0, 0.04)
        wind_east = believed_series["wind_east_ms"].to_numpy(dtype=float) + rng.normal(0.0, 1.0)
        wind_north = believed_series["wind_north_ms"].to_numpy(dtype=float) + rng.normal(0.0, 0.8)
        lon, lat = advect_diffuse_timeseries(
            observed_lon,
            observed_lat,
            current_east,
            current_north,
            wind_east,
            wind_north,
            step_seconds,
            rng,
            windage=windage,
            diffusivity_m2s=diffusivity_m2s,
            reverse=True,
        )
        origins_lon.append(lon)
        origins_lat.append(lat)
    return np.concatenate(origins_lon), np.concatenate(origins_lat)


def infer_origins_spatial_timeseries(
    observed_lon: np.ndarray,
    observed_lat: np.ndarray,
    believed_series: pd.DataFrame,
    current_grid: SpatialCurrentGrid,
    *,
    step_seconds: float,
    seed: int,
    ensemble_members: int,
    windage: float = 0.02,
    diffusivity_m2s: float = 12.0,
    current_multiplier: float = 1.0,
    coast_mask: CoastMask | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Infer origins while sampling Copernicus current at every particle."""
    if observed_lon.size == 0 or observed_lon.shape != observed_lat.shape:
        raise ValueError("Observed longitude and latitude arrays must be non-empty and aligned")
    required = {"time_utc", "wind_east_ms", "wind_north_ms"}
    if required - set(believed_series.columns) or believed_series.empty:
        raise ValueError("Believed spatial forcing series is incomplete")
    if step_seconds <= 0 or ensemble_members <= 0:
        raise ValueError("Step duration and ensemble member count must be positive")
    rng = np.random.default_rng(seed)
    origins_lon: list[np.ndarray] = []
    origins_lat: list[np.ndarray] = []
    for _ in range(ensemble_members):
        lon, lat = advect_diffuse_spatial_timeseries(
            observed_lon,
            observed_lat,
            believed_series["time_utc"].tolist(),
            believed_series["wind_east_ms"].to_numpy(dtype=float) + rng.normal(0.0, 1.0),
            believed_series["wind_north_ms"].to_numpy(dtype=float) + rng.normal(0.0, 0.8),
            step_seconds,
            current_grid,
            rng,
            windage=windage,
            diffusivity_m2s=diffusivity_m2s,
            current_multiplier=current_multiplier,
            current_bias_east_ms=float(rng.normal(0.0, 0.05)),
            current_bias_north_ms=float(rng.normal(0.0, 0.04)),
            reverse=True,
            coast_mask=coast_mask,
        )
        origins_lon.append(lon)
        origins_lat.append(lat)
    return np.concatenate(origins_lon), np.concatenate(origins_lat)


def _plot_forward(
    path: Path,
    release_lon: float,
    release_lat: float,
    observed_lon: np.ndarray,
    observed_lat: np.ndarray,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 6), constrained_layout=True)
    ax.scatter(observed_lon, observed_lat, s=5, alpha=0.28, color="#0B6FA4", label="Observed particles")
    ax.scatter([release_lon], [release_lat], marker="X", s=180, color="#C0392B", label="Known release")
    ax.scatter(
        [float(np.mean(observed_lon))],
        [float(np.mean(observed_lat))],
        marker="o",
        s=80,
        color="#F39C12",
        edgecolor="black",
        label="Observed centroid",
    )
    ax.set_title("Controlled forward drift verification")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(alpha=0.2)
    ax.legend()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _plot_reverse(
    path: Path,
    origin_lon: np.ndarray,
    origin_lat: np.ndarray,
    estimated_lon: float,
    estimated_lat: float,
    truth_lon: float,
    truth_lat: float,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 6), constrained_layout=True)
    density = ax.hist2d(origin_lon, origin_lat, bins=85, cmap="YlOrRd")
    fig.colorbar(density[3], ax=ax, label="Endpoint count")
    ax.scatter([truth_lon], [truth_lat], marker="X", s=180, color="#1A5276", label="Hidden truth")
    ax.scatter(
        [estimated_lon],
        [estimated_lat],
        marker="*",
        s=220,
        color="white",
        edgecolor="black",
        label="Estimated origin",
    )
    ax.set_title("Backward ensemble origin distribution")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(alpha=0.15)
    ax.legend()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _opendrift_roundtrip_error_m(config: VerificationConfig, forcing: Forcing) -> float:
    deterministic = Forcing(
        current_east_ms=forcing.current_east_ms,
        current_north_ms=forcing.current_north_ms,
        wind_east_ms=forcing.wind_east_ms,
        wind_north_ms=forcing.wind_north_ms,
        windage=forcing.windage,
        diffusivity_m2s=0.0,
    )
    forward_lon, forward_lat = run_opendrift_constant(
        config.release_lon,
        config.release_lat,
        config.release_time,
        1.0,
        deterministic,
    )
    backward_lon, backward_lat = run_opendrift_constant(
        float(forward_lon[0]),
        float(forward_lat[0]),
        config.release_time + timedelta(hours=1),
        1.0,
        deterministic,
        reverse=True,
    )
    return 1_000.0 * haversine_km(
        config.release_lon,
        config.release_lat,
        float(backward_lon[0]),
        float(backward_lat[0]),
    )


def run_verification(
    output_dir: Path,
    config: VerificationConfig | None = None,
    *,
    check_opendrift: bool = True,
    true_forcing: Forcing | None = None,
    believed_forcing: Forcing | None = None,
    forcing_provenance: dict | None = None,
    true_forcing_series: pd.DataFrame | None = None,
    believed_forcing_series: pd.DataFrame | None = None,
) -> dict:
    config = config or VerificationConfig()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    true_forcing = true_forcing or Forcing(0.35, 0.10, 5.0, -2.0, diffusivity_m2s=12.0)
    believed_forcing = believed_forcing or Forcing(0.32, 0.08, 4.5, -1.8, diffusivity_m2s=12.0)
    forcing_provenance = forcing_provenance or {
        "mode": "synthetic",
        "source": "controlled constant-field validation fixture",
    }
    rng = np.random.default_rng(config.seed)
    initial_lon = np.full(config.particles, config.release_lon)
    initial_lat = np.full(config.particles, config.release_lat)
    using_timeseries = true_forcing_series is not None or believed_forcing_series is not None
    if using_timeseries:
        if true_forcing_series is None or believed_forcing_series is None:
            raise ValueError("Both true and believed forcing series are required")
        if len(true_forcing_series) != len(believed_forcing_series) or true_forcing_series.empty:
            raise ValueError("True and believed forcing series must be non-empty and aligned")
        step_seconds = config.age_hours * 3600.0 / len(true_forcing_series)
        observed_lon, observed_lat = advect_diffuse_timeseries(
            initial_lon,
            initial_lat,
            true_forcing_series["current_east_ms"].to_numpy(dtype=float),
            true_forcing_series["current_north_ms"].to_numpy(dtype=float),
            true_forcing_series["wind_east_ms"].to_numpy(dtype=float),
            true_forcing_series["wind_north_ms"].to_numpy(dtype=float),
            step_seconds,
            rng,
            windage=true_forcing.windage,
            diffusivity_m2s=true_forcing.diffusivity_m2s,
        )
        origin_lon, origin_lat = infer_origins_timeseries(
            observed_lon,
            observed_lat,
            believed_forcing_series,
            step_seconds=step_seconds,
            seed=config.seed + 1,
            ensemble_members=config.ensemble_members,
            windage=believed_forcing.windage,
            diffusivity_m2s=believed_forcing.diffusivity_m2s,
        )
        saved_series = true_forcing_series.copy()
        saved_series = saved_series.rename(
            columns={column: f"true_{column}" for column in [
                "current_east_ms", "current_north_ms", "wind_east_ms", "wind_north_ms"
            ]}
        )
        for column in ["current_east_ms", "current_north_ms", "wind_east_ms", "wind_north_ms"]:
            saved_series[f"believed_{column}"] = believed_forcing_series[column].to_numpy()
        saved_series.to_csv(output_dir / "forcing_series.csv", index=False)
    else:
        observed_lon, observed_lat = advect_diffuse_constant(
            initial_lon,
            initial_lat,
            config.age_hours * 3600.0,
            true_forcing,
            rng,
        )
        origin_lon, origin_lat = infer_origins_constant(
            observed_lon,
            observed_lat,
            config.age_hours,
            believed_forcing,
            seed=config.seed + 1,
            ensemble_members=config.ensemble_members,
        )
    estimated_lon = float(np.mean(origin_lon))
    estimated_lat = float(np.mean(origin_lat))
    origin_error_km = haversine_km(
        config.release_lon,
        config.release_lat,
        estimated_lon,
        estimated_lat,
    )
    x, y = local_xy_m(origin_lon, origin_lat, estimated_lon, estimated_lat)
    radial_km = np.sqrt(x**2 + y**2) / 1000.0
    radius_50_km = float(np.quantile(radial_km, 0.50))
    radius_90_km = float(np.quantile(radial_km, 0.90))

    forward_plot = output_dir / "forward_slick.png"
    reverse_plot = output_dir / "reverse_probability.png"
    _plot_forward(
        forward_plot,
        config.release_lon,
        config.release_lat,
        observed_lon,
        observed_lat,
    )
    _plot_reverse(
        reverse_plot,
        origin_lon,
        origin_lat,
        estimated_lon,
        estimated_lat,
        config.release_lon,
        config.release_lat,
    )
    np.savez_compressed(output_dir / "forward_particles.npz", lon=observed_lon, lat=observed_lat)
    np.savez_compressed(output_dir / "reverse_endpoints.npz", lon=origin_lon, lat=origin_lat)

    observation_time = config.release_time + timedelta(hours=config.age_hours)
    truth = {
        "release_lon": config.release_lon,
        "release_lat": config.release_lat,
        "release_time_utc": format_utc(config.release_time),
        "observation_time_utc": format_utc(observation_time),
        "true_forcing": true_forcing.to_dict(),
        "forcing_provenance": forcing_provenance,
        "forcing_temporal_mode": "hourly time-varying" if using_timeseries else "constant",
    }
    (output_dir / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    release_estimate = {
        "release_time_utc": format_utc(config.release_time),
        "observation_time_utc": format_utc(observation_time),
        "estimated_origin": {"longitude": estimated_lon, "latitude": estimated_lat},
        "credible_radius_50_km": radius_50_km,
        "credible_radius_90_km": radius_90_km,
        "assumed_age_hours": config.age_hours,
        "believed_forcing": believed_forcing.to_dict(),
        "forcing_provenance": forcing_provenance,
        "assumption": "Release age is fixed for this first verification milestone.",
    }
    (output_dir / "release_estimate.json").write_text(
        json.dumps(release_estimate, indent=2),
        encoding="utf-8",
    )

    opendrift_error_m = None
    if check_opendrift:
        opendrift_error_m = _opendrift_roundtrip_error_m(config, true_forcing)

    acceptance = {
        "origin_error_within_threshold": origin_error_km <= config.acceptance_error_km,
        "all_arrays_finite": bool(
            np.isfinite(observed_lon).all()
            and np.isfinite(observed_lat).all()
            and np.isfinite(origin_lon).all()
            and np.isfinite(origin_lat).all()
        ),
        "expected_particle_count": observed_lon.size == config.particles,
        "expected_endpoint_count": origin_lon.size == config.particles * config.ensemble_members,
        "opendrift_roundtrip_under_5m": opendrift_error_m is None or opendrift_error_m <= 5.0,
    }
    status = "PASS" if all(acceptance.values()) else "FAIL"
    result = {
        "status": status,
        "milestone": (
            "hourly time-varying forward/reverse physics verification"
            if using_timeseries
            else "constant-field forward/reverse physics verification"
        ),
        "backend": (
            "analytic time-varying advection-diffusion with OpenDrift adapter check"
            if using_timeseries
            else "analytic constant advection-diffusion with OpenDrift adapter check"
        ),
        "forcing_provenance": forcing_provenance,
        "config": {
            **asdict(config),
            "release_time": format_utc(config.release_time),
        },
        "estimated_origin": {"longitude": estimated_lon, "latitude": estimated_lat},
        "metrics": {
            "origin_error_km": origin_error_km,
            "credible_radius_50_km": radius_50_km,
            "credible_radius_90_km": radius_90_km,
            "opendrift_roundtrip_error_m": opendrift_error_m,
        },
        "acceptance": acceptance,
        "limitations": [
            (
                "Hourly forcing is integrated in time but remains spatially uniform around the case location."
                if using_timeseries
                else "Forcing is reduced to a constant mean."
            ),
            "Point release; this is not a vessel-track slick generator.",
            "Known age; spill-age inference is not implemented.",
            "This physics-stage report does not itself perform SAR or vessel attribution.",
        ],
        "artifacts": [
            "forward_slick.png",
            "reverse_probability.png",
            "forward_particles.npz",
            "reverse_endpoints.npz",
            "truth.json",
            "release_estimate.json",
            "verification_result.json",
        ] + (["forcing_series.csv"] if using_timeseries else []),
    }
    (output_dir / "verification_result.json").write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )
    return result
