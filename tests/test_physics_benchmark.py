from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from espada.physics_benchmark import (
    _load_wind_series,
    restore_trajectory_order,
    summarize_endpoint_parity,
)


def test_parity_summary_passes_when_every_endpoint_is_within_limit() -> None:
    result = summarize_endpoint_parity(
        np.array([1.0, 2.0, 3.0]),
        np.array([4.0, 5.0, 6.0]),
        10.0,
    )
    assert result["trajectories"] == 6
    assert result["combined_maximum_separation_m"] == 6.0
    assert result["all_within_acceptance"] is True


def test_parity_summary_fails_when_one_endpoint_exceeds_limit() -> None:
    result = summarize_endpoint_parity(
        np.array([1.0, 12.0]),
        np.array([2.0, 3.0]),
        10.0,
    )
    assert result["all_within_acceptance"] is False


def test_trajectory_order_is_restored_when_backend_reverses_particles() -> None:
    end_lon, end_lat = restore_trajectory_order(
        np.array([9.1, 9.2, 9.3]),
        np.array([43.1, 43.2, 43.3]),
        np.array([9.3, 9.2, 9.1]),
        np.array([43.3, 43.2, 43.1]),
        np.array([8.3, 8.2, 8.1]),
        np.array([42.3, 42.2, 42.1]),
    )
    assert np.allclose(end_lon, [8.1, 8.2, 8.3])
    assert np.allclose(end_lat, [42.1, 42.2, 42.3])


def test_wind_series_is_interpolated_to_benchmark_steps(tmp_path) -> None:
    start = datetime(2018, 10, 7, 19, tzinfo=timezone.utc)
    path = tmp_path / "wind.json"
    path.write_text(
        json.dumps(
            {
                "source": "test wind",
                "samples": [
                    {"time_utc": start.isoformat(), "wind_east_ms": 0.0, "wind_north_ms": 2.0},
                    {
                        "time_utc": (start + timedelta(hours=1)).isoformat(),
                        "wind_east_ms": 4.0,
                        "wind_north_ms": 6.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    east, north, source = _load_wind_series(
        path, [start, start + timedelta(minutes=30), start + timedelta(hours=1)]
    )
    assert np.allclose(east, [0.0, 2.0, 4.0])
    assert np.allclose(north, [2.0, 4.0, 6.0])
    assert source == "test wind"


@pytest.mark.parametrize(
    ("forward", "reverse", "limit"),
    [
        (np.array([]), np.array([]), 10.0),
        (np.array([1.0]), np.array([1.0, 2.0]), 10.0),
        (np.array([np.nan]), np.array([1.0]), 10.0),
        (np.array([1.0]), np.array([1.0]), 0.0),
    ],
)
def test_parity_summary_rejects_invalid_inputs(forward, reverse, limit) -> None:
    with pytest.raises(ValueError):
        summarize_endpoint_parity(forward, reverse, limit)
