from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from espada.copernicus import (
    FORECAST_DATASET_ID,
    CopernicusRequest,
    build_subset_kwargs,
    download_subset,
    normalize_currents,
)
from espada.environment import load_cache


def _request() -> CopernicusRequest:
    return CopernicusRequest(
        start_datetime=datetime(2026, 9, 3, tzinfo=UTC),
        end_datetime=datetime(2026, 9, 5, tzinfo=UTC),
    )


def test_subset_request_is_small_and_never_contains_credentials(tmp_path: Path) -> None:
    kwargs = build_subset_kwargs(_request(), tmp_path / "currents.nc")
    assert kwargs["dataset_id"] == FORECAST_DATASET_ID
    assert kwargs["variables"] == ["uo", "vo"]
    assert np.isclose(kwargs["minimum_depth"], 0.49402499198913574)
    assert kwargs["maximum_depth"] == kwargs["minimum_depth"]
    assert kwargs["maximum_longitude"] - kwargs["minimum_longitude"] == 2.0
    assert not ({"username", "password"} & set(kwargs))


def test_download_uses_injected_official_subset_contract(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_subset(**kwargs: object) -> None:
        captured.update(kwargs)
        output = Path(str(kwargs["output_directory"])) / str(kwargs["output_filename"])
        output.write_bytes(b"netcdf-placeholder")

    output = tmp_path / "raw" / "currents.nc"
    result = download_subset(_request(), output, subsetter=fake_subset)
    assert result["status"] == "PASS"
    assert output.exists()
    assert captured["overwrite"] is True
    assert "password" not in captured


def _write_wind_cache(path: Path) -> None:
    times = pd.date_range("2026-09-03", "2026-09-05", freq="1h", tz="UTC")
    payload = {
        "schema_version": "1.0",
        "source": "test forecast wind",
        "temporal_resolution": "hourly",
        "samples": [
            {
                "time_utc": value.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "latitude": 18.7167,
                "longitude": 71.45,
                "current_east_ms": 0.0,
                "current_north_ms": 0.0,
                "wind_east_ms": 4.0 + index / 100.0,
                "wind_north_ms": -1.0,
                "source": "test forecast wind",
            }
            for index, value in enumerate(times)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_normalize_currents_selects_surface_point_and_interpolates_hourly(tmp_path: Path) -> None:
    raw = tmp_path / "currents.nc"
    cache = tmp_path / "copernicus.json"
    wind = tmp_path / "wind.json"
    _write_wind_cache(wind)
    dataset = xr.Dataset(
        {
            "uo": (
                ("time", "depth", "latitude", "longitude"),
                np.array(
                    [
                        [[[[0.10, 0.20], [0.30, 0.40]]]],
                        [[[[0.20, 0.30], [0.40, 0.50]]]],
                        [[[[0.30, 0.40], [0.50, 0.60]]]],
                    ]
                ).reshape(3, 1, 2, 2),
                {"units": "m s-1"},
            ),
            "vo": (
                ("time", "depth", "latitude", "longitude"),
                np.full((3, 1, 2, 2), -0.05),
                {"units": "m/s"},
            ),
        },
        coords={
            "time": pd.date_range("2026-09-03", periods=3, freq="1D"),
            "depth": [0.494],
            "latitude": [18.0, 18.75],
            "longitude": [71.0, 71.5],
        },
    )
    dataset.to_netcdf(raw, engine="scipy")
    result = normalize_currents(raw, cache, wind_cache_path=wind)
    assert result["status"] == "PASS"
    assert result["native_sample_count"] == 3
    assert result["normalized_sample_count"] == 49
    bundle = load_cache(cache)
    assert len(bundle.frame) == 49
    assert bundle.source.startswith("Copernicus Marine")
    assert np.isclose(bundle.frame.loc[0, "current_east_ms"], 0.4)
    assert np.isclose(bundle.frame.loc[12, "current_east_ms"], 0.45)
    assert bundle.temporal_resolution.startswith("hourly linear")


def test_single_daily_timestamp_is_not_misreported_as_missing_time(tmp_path: Path) -> None:
    raw = tmp_path / "single.nc"
    cache = tmp_path / "copernicus.json"
    wind = tmp_path / "wind.json"
    _write_wind_cache(wind)
    dataset = xr.Dataset(
        {
            "uo": (("time", "depth", "latitude", "longitude"), np.ones((1, 1, 1, 1)), {"units": "m/s"}),
            "vo": (("time", "depth", "latitude", "longitude"), np.ones((1, 1, 1, 1)), {"units": "m/s"}),
        },
        coords={"time": [pd.Timestamp("2026-09-03")], "depth": [0.494], "latitude": [18.75], "longitude": [71.5]},
    )
    dataset.to_netcdf(raw, engine="scipy")
    with pytest.raises(ValueError, match="at least two valid time steps"):
        normalize_currents(raw, cache, wind_cache_path=wind)


def test_invalid_request_is_rejected() -> None:
    with pytest.raises(ValueError, match="before"):
        CopernicusRequest(
            start_datetime=datetime(2026, 9, 5, tzinfo=UTC),
            end_datetime=datetime(2026, 9, 3, tzinfo=UTC),
        )
