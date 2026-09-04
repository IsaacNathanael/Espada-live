from datetime import datetime, timedelta

import numpy as np
import pytest

from espada.geo import haversine_km
from espada.models import Forcing
from espada.physics import advect_diffuse_constant, advect_diffuse_timeseries, run_opendrift_constant


def test_analytic_forward_reverse_roundtrip_without_diffusion() -> None:
    forcing = Forcing(0.35, 0.10, 5.0, -2.0, diffusivity_m2s=0.0)
    start_lon = np.array([71.45])
    start_lat = np.array([18.7167])
    forward_lon, forward_lat = advect_diffuse_constant(
        start_lon,
        start_lat,
        3_600.0,
        forcing,
        np.random.default_rng(10),
    )
    reverse_lon, reverse_lat = advect_diffuse_constant(
        forward_lon,
        forward_lat,
        3_600.0,
        forcing,
        np.random.default_rng(11),
        reverse=True,
    )
    error_m = 1_000.0 * haversine_km(
        float(start_lon[0]),
        float(start_lat[0]),
        float(reverse_lon[0]),
        float(reverse_lat[0]),
    )
    assert error_m < 0.1


def test_time_varying_forward_reverse_roundtrip_without_diffusion() -> None:
    components = {
        "current_east_ms": np.array([0.1, 0.3, -0.05]),
        "current_north_ms": np.array([-0.1, 0.05, 0.2]),
        "wind_east_ms": np.array([3.0, 6.0, 4.0]),
        "wind_north_ms": np.array([1.0, -2.0, 0.5]),
    }
    forward = advect_diffuse_timeseries(
        71.45, 18.7167, **components, step_seconds=3600.0,
        rng=np.random.default_rng(20), diffusivity_m2s=0.0,
    )
    backward = advect_diffuse_timeseries(
        forward[0], forward[1], **components, step_seconds=3600.0,
        rng=np.random.default_rng(21), diffusivity_m2s=0.0, reverse=True,
    )
    error_m = 1000 * haversine_km(71.45, 18.7167, float(backward[0][0]), float(backward[1][0]))
    assert error_m < 0.1


def test_time_series_matches_constant_for_identical_hourly_values() -> None:
    forcing = Forcing(0.2, -0.05, 5.0, 1.0, diffusivity_m2s=0.0)
    constant = advect_diffuse_constant(
        71.45, 18.7167, 3 * 3600.0, forcing, np.random.default_rng(30)
    )
    series = advect_diffuse_timeseries(
        71.45, 18.7167,
        np.full(3, forcing.current_east_ms), np.full(3, forcing.current_north_ms),
        np.full(3, forcing.wind_east_ms), np.full(3, forcing.wind_north_ms),
        3600.0, np.random.default_rng(30), windage=forcing.windage, diffusivity_m2s=0.0,
    )
    error_m = 1000 * haversine_km(float(constant[0][0]), float(constant[1][0]), float(series[0][0]), float(series[1][0]))
    assert error_m < 0.01


@pytest.mark.integration
def test_opendrift_forward_reverse_roundtrip_under_five_metres() -> None:
    forcing = Forcing(0.35, 0.10, 5.0, -2.0, diffusivity_m2s=0.0)
    start = datetime(2026, 3, 15, 2, 0, 0)
    forward_lon, forward_lat = run_opendrift_constant(
        71.45,
        18.7167,
        start,
        1.0,
        forcing,
    )
    reverse_lon, reverse_lat = run_opendrift_constant(
        float(forward_lon[0]),
        float(forward_lat[0]),
        start + timedelta(hours=1),
        1.0,
        forcing,
        reverse=True,
    )
    error_m = 1_000.0 * haversine_km(
        71.45,
        18.7167,
        float(reverse_lon[0]),
        float(reverse_lat[0]),
    )
    assert error_m < 5.0
