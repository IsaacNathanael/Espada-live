from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from espada.physics import advect_diffuse_spatial_timeseries
from espada.spatial_current import load_spatial_current_grid


def _grid(path: Path) -> Path:
    east = np.zeros((2, 1, 2, 2), dtype=float)
    east[:, 0, :, 0] = 0.1
    east[:, 0, :, 1] = 0.5
    dataset = xr.Dataset(
        {
            "uo": (("time", "depth", "latitude", "longitude"), east, {"units": "m/s"}),
            "vo": (
                ("time", "depth", "latitude", "longitude"),
                np.zeros_like(east),
                {"units": "m/s"},
            ),
        },
        coords={
            "time": pd.date_range("2026-03-15T00:00:00", periods=2, freq="1h"),
            "depth": [0.494],
            "latitude": [18.0, 19.0],
            "longitude": [71.0, 72.0],
        },
        attrs={"title": "test Copernicus surface currents"},
    )
    dataset.to_netcdf(path, engine="netcdf4")
    return path


def test_grid_interpolates_current_at_each_particle_position(tmp_path: Path) -> None:
    grid = load_spatial_current_grid(_grid(tmp_path / "currents.nc"))
    east, north = grid.sample(
        np.array([71.0, 71.5, 72.0]),
        np.array([18.5, 18.5, 18.5]),
        "2026-03-15T00:30:00Z",
    )
    assert np.allclose(east, [0.1, 0.3, 0.5])
    assert np.allclose(north, 0.0)
    assert grid.covers(
        pd.Timestamp("2026-03-15T00:00:00Z"), pd.Timestamp("2026-03-15T01:00:00Z")
    )


def test_particles_follow_different_local_currents(tmp_path: Path) -> None:
    grid = load_spatial_current_grid(_grid(tmp_path / "currents.nc"))
    start_lon = np.array([71.0, 72.0])
    result_lon, result_lat = advect_diffuse_spatial_timeseries(
        start_lon,
        np.array([18.5, 18.5]),
        [pd.Timestamp("2026-03-15T00:00:00Z")],
        np.array([0.0]),
        np.array([0.0]),
        3600.0,
        grid,
        np.random.default_rng(10),
        windage=0.0,
        diffusivity_m2s=0.0,
    )
    displacement = result_lon - start_lon
    assert np.allclose(result_lat, 18.5)
    assert displacement[1] > 4.9 * displacement[0]


def test_current_multiplier_changes_spatial_displacement(tmp_path: Path) -> None:
    grid = load_spatial_current_grid(_grid(tmp_path / "currents.nc"))
    common = dict(
        lon=np.array([71.5]),
        lat=np.array([18.5]),
        timestamps=[pd.Timestamp("2026-03-15T00:00:00Z")],
        wind_east_ms=np.array([0.0]),
        wind_north_ms=np.array([0.0]),
        step_seconds=3600.0,
        current_grid=grid,
        windage=0.0,
        diffusivity_m2s=0.0,
    )
    normal_lon, _ = advect_diffuse_spatial_timeseries(
        **common, rng=np.random.default_rng(1), current_multiplier=1.0
    )
    doubled_lon, _ = advect_diffuse_spatial_timeseries(
        **common, rng=np.random.default_rng(1), current_multiplier=2.0
    )
    assert np.allclose(doubled_lon - 71.5, 2.0 * (normal_lon - 71.5))
