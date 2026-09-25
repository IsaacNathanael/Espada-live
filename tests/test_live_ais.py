import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from espada.live_ais import (
    AISBoundingBox,
    RollingAISCache,
    build_subscription,
    capture_aisstream,
    parse_aisstream_message,
)


def test_aisstream_subscription_uses_documented_box_order() -> None:
    box = AISBoundingBox(71.2, 18.5, 71.8, 19.0)
    subscription = build_subscription("secret", box, mmsi=["419000123"])
    assert subscription["BoundingBoxes"] == [[[19.0, 71.2], [18.5, 71.8]]]
    assert subscription["FiltersShipMMSI"] == ["419000123"]
    assert "PositionReport" in subscription["FilterMessageTypes"]


def test_aisstream_box_rejects_invalid_extent() -> None:
    with pytest.raises(ValueError, match="smaller"):
        AISBoundingBox(72.0, 18.5, 71.8, 19.0)


def test_position_report_is_converted_to_canonical_row() -> None:
    envelope = {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": 368207620,
            "ShipName": "EXAMPLE VESSEL",
            "Latitude": 25.7617,
            "Longitude": -80.1918,
            "time_utc": "2026-09-04T05:00:00Z",
        },
        "Message": {"PositionReport": {"UserID": 368207620, "Sog": 12.4, "Cog": 86.7}},
    }
    row = parse_aisstream_message(envelope)
    assert row is not None
    assert row["mmsi"] == "368207620"
    assert row["timestamp_utc"] == "2026-09-04T05:00:00Z"
    assert row["timestamp_source"] == "provider"
    assert row["source"] == "AISStream live"


def test_position_report_uses_receive_time_when_provider_time_is_absent() -> None:
    row = parse_aisstream_message(
        {
            "MessageType": "PositionReport",
            "MetaData": {"MMSI": 368207620, "Latitude": 25.7, "Longitude": -80.1},
            "Message": {"PositionReport": {"Sog": 2.0, "Cog": 20.0}},
        },
        received_at=datetime(2026, 9, 4, 5, 0, tzinfo=UTC),
    )
    assert row is not None
    assert row["timestamp_utc"] == "2026-09-04T05:00:00Z"
    assert row["timestamp_source"] == "received"


def test_rolling_cache_deduplicates_and_removes_old_positions(tmp_path: Path) -> None:
    cache = RollingAISCache(tmp_path / "ais.csv", window_hours=2)
    base = {
        "mmsi": "368207620",
        "vessel_name": "TEST",
        "longitude": -80.1,
        "latitude": 25.7,
        "sog": 3.0,
        "cog": 20.0,
        "is_interpolated": False,
        "source": "AISStream live",
        "timestamp_source": "provider",
    }
    cache.add({**base, "timestamp_utc": "2026-09-04T01:00:00Z"})
    cache.add({**base, "timestamp_utc": "2026-09-04T04:00:00Z", "longitude": -80.2})
    cache.add({**base, "timestamp_utc": "2026-09-04T04:00:00Z", "longitude": -80.3})
    frame = cache.flush(now=datetime(2026, 9, 4, 5, 0, tzinfo=UTC))
    assert len(frame) == 1
    assert frame.iloc[0]["longitude"] == pytest.approx(-80.3)


def test_capture_writes_sanitized_status_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    position = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 368207620, "Latitude": 25.7, "Longitude": -80.1},
        "Message": {"PositionReport": {"Sog": 3.0, "Cog": 20.0}},
    }

    class FakeSocket:
        def __init__(self) -> None:
            self.messages = [json.dumps(position)]
            self.subscription = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def send(self, value: str) -> None:
            self.subscription = value

        async def recv(self) -> str:
            return self.messages.pop(0)

    monkeypatch.setenv("AISSTREAM_API_KEY", "top-secret")
    result = asyncio.run(
        capture_aisstream(
            AISBoundingBox(-80.2, 25.6, -80.0, 25.8),
            tmp_path / "out",
            tmp_path / "cache.csv",
            duration_seconds=1,
            max_messages=1,
            connect_factory=lambda *args, **kwargs: FakeSocket(),
        )
    )
    assert result["status"] == "PASS"
    assert result["positions_accepted"] == 1
    assert (tmp_path / "cache.csv").exists()
    status_text = (tmp_path / "out" / "live_ais_status.json").read_text(encoding="utf-8")
    assert "top-secret" not in status_text


def test_capture_reports_provider_rate_limit_as_error_without_reconnect_storm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RateLimitedSocket:
        async def __aenter__(self):
            raise RuntimeError("server rejected WebSocket connection: HTTP 429")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setenv("AISSTREAM_API_KEY", "top-secret")
    result = asyncio.run(
        capture_aisstream(
            AISBoundingBox(104.02, 1.20, 104.23, 1.31),
            tmp_path / "out",
            tmp_path / "cache.csv",
            duration_seconds=5,
            connect_factory=lambda *args, **kwargs: RateLimitedSocket(),
        )
    )
    assert result["status"] == "ERROR"
    assert result["error_kind"] == "RATE_LIMITED"
    assert result["connections"] == 1
