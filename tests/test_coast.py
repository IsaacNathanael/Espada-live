from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from espada.coast import load_coast_mask
from espada.physics import advect_diffuse_spatial_timeseries
from espada.spatial_current import SpatialCurrentGrid


def _land(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [[71.005, 17.0], [73.0, 17.0], [73.0, 20.0], [71.005, 20.0], [71.005, 17.0]]
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _narrow_island(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [[71.004, 18.0], [71.006, 18.0], [71.006, 19.0], [71.004, 19.0], [71.004, 18.0]]
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_coast_mask_rejects_particle_step_onto_land(tmp_path: Path) -> None:
    coast = load_coast_mask(_land(tmp_path / "land.geojson"))
    times = pd.to_datetime(["2026-03-15T00:00:00Z", "2026-03-15T01:00:00Z"])
    grid = SpatialCurrentGrid(
        times_ns=np.asarray(times.asi8, dtype=np.int64),
        latitudes=np.array([18.0, 19.0]),
        longitudes=np.array([71.0, 72.0]),
        east_ms=np.full((2, 2, 2), 0.5),
        north_ms=np.zeros((2, 2, 2)),
        source="test",
        path=tmp_path / "currents.nc",
    )
    free_lon, _ = advect_diffuse_spatial_timeseries(
        np.array([71.0]),
        np.array([18.5]),
        [times[0]],
        np.array([0.0]),
        np.array([0.0]),
        3600.0,
        grid,
        np.random.default_rng(1),
        windage=0.0,
        diffusivity_m2s=0.0,
    )
    blocked_lon, blocked_lat = advect_diffuse_spatial_timeseries(
        np.array([71.0]),
        np.array([18.5]),
        [times[0]],
        np.array([0.0]),
        np.array([0.0]),
        3600.0,
        grid,
        np.random.default_rng(1),
        windage=0.0,
        diffusivity_m2s=0.0,
        coast_mask=coast,
    )
    assert free_lon[0] > 71.005
    assert blocked_lon[0] == 71.0
    assert blocked_lat[0] == 18.5


def test_coast_mask_blocks_step_across_narrow_island(tmp_path: Path) -> None:
    coast = load_coast_mask(_narrow_island(tmp_path / "island.geojson"))
    blocked = coast.blocks_step(
        np.array([71.0, 71.0]),
        np.array([18.5, 17.5]),
        np.array([71.01, 71.01]),
        np.array([18.5, 17.5]),
    )
    assert blocked.tolist() == [True, False]


def test_spatial_advection_cannot_jump_across_narrow_island(tmp_path: Path) -> None:
    coast = load_coast_mask(_narrow_island(tmp_path / "island.geojson"))
    times = pd.to_datetime(["2026-03-15T00:00:00Z", "2026-03-15T01:00:00Z"])
    grid = SpatialCurrentGrid(
        times_ns=np.asarray(times.asi8, dtype=np.int64),
        latitudes=np.array([18.0, 19.0]),
        longitudes=np.array([71.0, 72.0]),
        east_ms=np.full((2, 2, 2), 0.31),
        north_ms=np.zeros((2, 2, 2)),
        source="test",
        path=tmp_path / "currents.nc",
    )
    free_lon, _ = advect_diffuse_spatial_timeseries(
        np.array([71.0]),
        np.array([18.5]),
        [times[0]],
        np.array([0.0]),
        np.array([0.0]),
        3600.0,
        grid,
        np.random.default_rng(1),
        windage=0.0,
        diffusivity_m2s=0.0,
    )
    blocked_lon, blocked_lat = advect_diffuse_spatial_timeseries(
        np.array([71.0]),
        np.array([18.5]),
        [times[0]],
        np.array([0.0]),
        np.array([0.0]),
        3600.0,
        grid,
        np.random.default_rng(1),
        windage=0.0,
        diffusivity_m2s=0.0,
        coast_mask=coast,
    )
    assert free_lon[0] > 71.006
    assert blocked_lon[0] == 71.0
    assert blocked_lat[0] == 18.5
