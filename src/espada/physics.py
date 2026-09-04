from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

import numpy as np

from .geo import local_xy_m, lonlat_from_local_m
from .models import Forcing


def advect_diffuse_constant(
    lon: np.ndarray | float,
    lat: np.ndarray | float,
    duration_seconds: np.ndarray | float,
    forcing: Forcing,
    rng: np.random.Generator,
    *,
    reverse: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Analytic constant-field advection/diffusion test backend.

    Backtracking reverses advection but keeps diffusion dispersive, matching the
    physically meaningful OpenDrift convention.
    """
    lon_array = np.atleast_1d(np.asarray(lon, dtype=float))
    lat_array = np.atleast_1d(np.asarray(lat, dtype=float))
    seconds = np.broadcast_to(np.asarray(duration_seconds, dtype=float), lon_array.shape)
    ref_lon = float(np.mean(lon_array))
    ref_lat = float(np.mean(lat_array))
    x, y = local_xy_m(lon_array, lat_array, ref_lon, ref_lat)
    sign = -1.0 if reverse else 1.0
    sigma = np.sqrt(2.0 * forcing.diffusivity_m2s * np.maximum(seconds, 0.0))
    x = x + sign * forcing.drift_east_ms * seconds + rng.normal(0.0, sigma)
    y = y + sign * forcing.drift_north_ms * seconds + rng.normal(0.0, sigma)
    return lonlat_from_local_m(x, y, ref_lon, ref_lat)


def advect_diffuse_timeseries(
    lon: np.ndarray | float,
    lat: np.ndarray | float,
    current_east_ms: np.ndarray,
    current_north_ms: np.ndarray,
    wind_east_ms: np.ndarray,
    wind_north_ms: np.ndarray,
    step_seconds: np.ndarray | float,
    rng: np.random.Generator,
    *,
    windage: float = 0.02,
    diffusivity_m2s: float = 12.0,
    reverse: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Integrate spatially uniform but time-varying forcing over each interval."""
    components = [
        np.asarray(current_east_ms, dtype=float),
        np.asarray(current_north_ms, dtype=float),
        np.asarray(wind_east_ms, dtype=float),
        np.asarray(wind_north_ms, dtype=float),
    ]
    if any(values.ndim != 1 for values in components):
        raise ValueError("Forcing series must be one-dimensional")
    lengths = {len(values) for values in components}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise ValueError("Forcing series must be non-empty and aligned")
    steps = np.broadcast_to(np.asarray(step_seconds, dtype=float), components[0].shape)
    if not np.isfinite(np.concatenate([*components, steps])).all() or np.any(steps <= 0):
        raise ValueError("Forcing values must be finite and step durations positive")

    lon_array = np.atleast_1d(np.asarray(lon, dtype=float))
    lat_array = np.atleast_1d(np.asarray(lat, dtype=float))
    if lon_array.shape != lat_array.shape or lon_array.size == 0:
        raise ValueError("Longitude and latitude must be non-empty and aligned")
    ref_lon = float(np.mean(lon_array))
    ref_lat = float(np.mean(lat_array))
    x, y = local_xy_m(lon_array, lat_array, ref_lon, ref_lat)
    east_velocity = components[0] + windage * components[2]
    north_velocity = components[1] + windage * components[3]
    sign = -1.0 if reverse else 1.0
    x = x + sign * float(np.sum(east_velocity * steps))
    y = y + sign * float(np.sum(north_velocity * steps))
    total_seconds = float(np.sum(steps))
    sigma = np.sqrt(2.0 * diffusivity_m2s * total_seconds)
    x = x + rng.normal(0.0, sigma, size=lon_array.shape)
    y = y + rng.normal(0.0, sigma, size=lat_array.shape)
    return lonlat_from_local_m(x, y, ref_lon, ref_lat)


def run_opendrift_constant(
    lon: Sequence[float] | float,
    lat: Sequence[float] | float,
    start_time: datetime,
    duration_hours: float,
    forcing: Forcing,
    *,
    reverse: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """OpenDrift constant-reader adapter used for parity and real-backend checks."""
    from opendrift.models.oceandrift import OceanDrift
    from opendrift.readers import reader_constant

    lon_array = np.atleast_1d(np.asarray(lon, dtype=float))
    lat_array = np.atleast_1d(np.asarray(lat, dtype=float))
    if lon_array.size == 1 and lat_array.size > 1:
        lon_array = np.repeat(lon_array, lat_array.size)
    if lat_array.size == 1 and lon_array.size > 1:
        lat_array = np.repeat(lat_array, lon_array.size)
    model = OceanDrift(loglevel=50)
    model.add_reader(
        reader_constant.Reader(
            {
                "x_sea_water_velocity": forcing.current_east_ms,
                "y_sea_water_velocity": forcing.current_north_ms,
                "x_wind": forcing.wind_east_ms,
                "y_wind": forcing.wind_north_ms,
                "horizontal_diffusivity": forcing.diffusivity_m2s,
                "land_binary_mask": 0,
            }
        )
    )
    model.seed_elements(
        lon=lon_array,
        lat=lat_array,
        number=lon_array.size,
        time=start_time,
        wind_drift_factor=forcing.windage,
    )
    step = -300 if reverse else 300
    model.run(
        duration=timedelta(hours=duration_hours),
        time_step=step,
        time_step_output=-3600 if reverse else 3600,
    )
    return np.asarray(model.result.lon[:, -1]), np.asarray(model.result.lat[:, -1])
