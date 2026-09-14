from __future__ import annotations

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from espada.coast import CoastMask
from espada.digital_twin import _blind_ais, _interpolated_position, _simulate_slick


class _EastwardCurrentGrid:
    def sample(self, timestamps, longitude, latitude):
        size = np.asarray(longitude).size
        return np.full(size, 30.0), np.zeros(size)


def test_digital_twin_blinds_real_identifiers_deterministically() -> None:
    frame = pd.DataFrame(
        {
            "mmsi": ["123456789", "123456789", "987654321"],
            "vessel_name": ["A", "A", "B"],
        }
    )
    blinded, mapping = _blind_ais(frame)
    assert blinded["mmsi"].nunique() == 2
    assert all(len(value) == 9 and value.startswith("98") for value in mapping.values())
    assert "123456789" not in blinded.to_csv(index=False)
    assert set(blinded["vessel_name"]) == {f"Blinded {value}" for value in mapping.values()}


def test_digital_twin_interpolates_source_position() -> None:
    track = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2018-10-07T10:00:00Z", "2018-10-07T12:00:00Z"], utc=True
            ),
            "longitude": [9.0, 9.2],
            "latitude": [43.0, 43.1],
        }
    )
    lon, lat = _interpolated_position(track, pd.Timestamp("2018-10-07T11:00:00Z"))
    assert abs(lon - 9.1) < 1e-9
    assert abs(lat - 43.05) < 1e-9


def test_digital_twin_truth_generation_respects_land_mask(tmp_path) -> None:
    timestamps = pd.date_range("2020-01-01T00:00:00Z", periods=5, freq="h")
    track = pd.DataFrame(
        {
            "timestamp_utc": timestamps,
            "longitude": np.zeros(5),
            "latitude": np.zeros(5),
        }
    )
    environment = pd.DataFrame(
        {
            "time_utc": timestamps[:4],
            "wind_east_ms": np.zeros(4),
            "wind_north_ms": np.zeros(4),
        }
    )
    coast = CoastMask(
        geometry=Polygon([(0.2, -0.1), (0.3, -0.1), (0.3, 0.1), (0.2, 0.1)]),
        path=tmp_path / "land.geojson",
    )
    lon, lat, _ = _simulate_slick(
        track,
        environment,
        timestamps[0],
        timestamps[-1],
        np.random.default_rng(7),
        particles=300,
        windage=0.0,
        diffusivity_m2s=0.0,
        spatial_current_grid=_EastwardCurrentGrid(),
        coast_mask=coast,
    )
    assert np.all(lon < 0.2)
    assert np.allclose(lat, 0.0)
