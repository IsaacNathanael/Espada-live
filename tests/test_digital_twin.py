from __future__ import annotations

import pandas as pd

from espada.digital_twin import _blind_ais, _interpolated_position


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
