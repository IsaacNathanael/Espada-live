import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from espada.historical_ais import (
    HistoricalAISRequest,
    build_gfw_report_request,
    fetch_gfw_presence,
    gfw_entries_to_frame,
)
from espada.live_ais import AISBoundingBox


def _request() -> HistoricalAISRequest:
    return HistoricalAISRequest(
        AISBoundingBox(70.8, 17.8, 73.0, 20.0),
        datetime(2026, 8, 29, tzinfo=UTC),
        datetime(2026, 8, 30, tzinfo=UTC),
    )


def test_gfw_request_uses_presence_dataset_hourly_grid_and_closed_polygon() -> None:
    url, body = build_gfw_report_request(_request())
    query = parse_qs(urlparse(url).query)
    assert query["datasets[0]"] == ["public-global-presence:latest"]
    assert query["temporal-resolution"] == ["HOURLY"]
    assert query["group-by"] == ["MMSI"]
    envelope = json.loads(body)
    geometry = envelope["geojson"]
    assert geometry["coordinates"][0][0] == geometry["coordinates"][0][-1]


def test_gfw_entries_accept_a_top_level_list() -> None:
    frame, rejected = gfw_entries_to_frame(
        [{"date": "2026-08-29T04:00:00Z", "mmsi": "419000123", "lon": 71.4, "lat": 18.7}],
        _request().bounding_box,
    )
    assert len(frame) == 1
    assert rejected == 0


def test_gfw_dataset_version_wrapper_is_unwrapped() -> None:
    frame, rejected = gfw_entries_to_frame(
        {
            "entries": [
                {
                    "public-global-presence:v4.0": [
                        {
                            "date": "2026-08-29T04:00:00Z",
                            "mmsi": "419000123",
                            "lon": 71.4,
                            "lat": 18.7,
                        }
                    ]
                }
            ]
        },
        _request().bounding_box,
    )
    assert rejected == 0
    assert len(frame) == 1
    assert frame.iloc[0]["mmsi"] == "419000123"


def test_gfw_entries_are_converted_and_invalid_rows_are_rejected() -> None:
    frame, rejected = gfw_entries_to_frame(
        {
            "entries": [
                {
                    "date": "2026-08-29T04:00:00Z",
                    "mmsi": "419000123",
                    "shipName": "SAGAR",
                    "lon": 71.4,
                    "lat": 18.7,
                    "hours": 1,
                    "vesselId": "gfw-1",
                },
                {"date": "bad", "mmsi": "x", "lon": 999, "lat": 999},
            ]
        },
        _request().bounding_box,
    )
    assert rejected == 1
    assert len(frame) == 1
    assert frame.iloc[0]["mmsi"] == "419000123"
    assert frame.iloc[0]["source"].startswith("Global Fishing Watch")
    assert frame.attrs["rejection_reasons"] == {"missing_or_invalid_mmsi": 1}


def test_gfw_nested_entry_shape_is_supported() -> None:
    frame, rejected = gfw_entries_to_frame(
        [
            {
                "timestamp": "2026-08-29T04:00:00Z",
                "vessel": {"ssvid": "419000123", "name": "SAGAR"},
                "position": {"lon": 71.4, "lat": 18.7},
            }
        ],
        _request().bounding_box,
    )
    assert rejected == 0
    assert frame.iloc[0]["vessel_name"] == "SAGAR"


def test_fetch_writes_normalized_outputs_without_leaking_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFW_API_ACCESS_TOKEN", "top-secret-gfw")

    def fake_post(url: str, headers, body: bytes, timeout: float):
        assert headers["Authorization"] == "Bearer top-secret-gfw"
        return {
            "entries": [
                {
                    "date": "2026-08-29T04:00:00Z",
                    "mmsi": "419000123",
                    "shipName": "SAGAR",
                    "lon": 71.4,
                    "lat": 18.7,
                    "hours": 1,
                },
                {
                    "date": "2026-08-29T05:00:00Z",
                    "mmsi": "419000123",
                    "shipName": "SAGAR",
                    "lon": 71.5,
                    "lat": 18.8,
                    "hours": 1,
                },
            ]
        }

    result = fetch_gfw_presence(
        _request(),
        tmp_path,
        post_json=fake_post,
        now=datetime(2026, 9, 4, tzinfo=UTC),
    )
    normalized = pd.read_csv(tmp_path / "ais_normalized.csv", dtype={"mmsi": str})
    assert result["status"] == "PASS"
    assert result["vessels"] == 1
    assert len(normalized) == 2
    quality = json.loads((tmp_path / "ais_quality.json").read_text())
    assert quality["gap_threshold_minutes"] == 90.0
    assert quality["vessels_with_gaps_over_threshold"] == 0
    assert "top-secret-gfw" not in (tmp_path / "historical_ais_status.json").read_text()


def test_fetch_rejects_dates_inside_provider_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFW_API_ACCESS_TOKEN", "secret")
    recent = HistoricalAISRequest(
        AISBoundingBox(70.8, 17.8, 73.0, 20.0),
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 9, 3, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="96 hours"):
        fetch_gfw_presence(recent, tmp_path, now=datetime(2026, 9, 4, tzinfo=UTC))
