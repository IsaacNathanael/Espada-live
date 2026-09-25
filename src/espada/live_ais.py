from __future__ import annotations

import asyncio
import json
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import pandas as pd


AISSTREAM_ENDPOINT = "wss://stream.aisstream.io/v0/stream"
POSITION_MESSAGE_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
)
CANONICAL_COLUMNS = (
    "timestamp_utc",
    "mmsi",
    "vessel_name",
    "longitude",
    "latitude",
    "sog",
    "cog",
    "is_interpolated",
    "source",
    "timestamp_source",
)


@dataclass(frozen=True)
class AISBoundingBox:
    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float

    def __post_init__(self) -> None:
        if not -180.0 <= self.min_longitude <= 180.0:
            raise ValueError("minimum longitude must be between -180 and 180")
        if not -180.0 <= self.max_longitude <= 180.0:
            raise ValueError("maximum longitude must be between -180 and 180")
        if not -90.0 <= self.min_latitude <= 90.0:
            raise ValueError("minimum latitude must be between -90 and 90")
        if not -90.0 <= self.max_latitude <= 90.0:
            raise ValueError("maximum latitude must be between -90 and 90")
        if self.min_longitude >= self.max_longitude:
            raise ValueError("minimum longitude must be smaller than maximum longitude")
        if self.min_latitude >= self.max_latitude:
            raise ValueError("minimum latitude must be smaller than maximum latitude")

    def aisstream_box(self) -> list[list[float]]:
        # AISStream documents each box as north-west then south-east [latitude, longitude].
        return [
            [self.max_latitude, self.min_longitude],
            [self.min_latitude, self.max_longitude],
        ]

    def to_dict(self) -> dict[str, float]:
        return {
            "min_longitude": self.min_longitude,
            "min_latitude": self.min_latitude,
            "max_longitude": self.max_longitude,
            "max_latitude": self.max_latitude,
        }


def _normalize_mmsi(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float) and not value.is_integer():
            return None
        normalized = str(int(value)) if isinstance(value, (int, float)) else str(value).strip()
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if len(normalized) == 9 and normalized.isdigit() else None


def build_subscription(
    api_key: str,
    bounding_box: AISBoundingBox,
    *,
    mmsi: list[str] | None = None,
) -> dict[str, object]:
    if not api_key.strip():
        raise ValueError("AISStream API key is empty")
    subscription: dict[str, object] = {
        "APIKey": api_key,
        "BoundingBoxes": [bounding_box.aisstream_box()],
        "FilterMessageTypes": list(POSITION_MESSAGE_TYPES),
    }
    if mmsi:
        normalized = [_normalize_mmsi(value) for value in mmsi]
        if any(value is None for value in normalized):
            raise ValueError("each MMSI filter must be a nine-digit number")
        unique = list(dict.fromkeys(value for value in normalized if value is not None))
        if len(unique) > 200:
            raise ValueError("AISStream accepts at most 200 MMSI filters per subscription")
        subscription["FiltersShipMMSI"] = unique
    return subscription


def _parse_time(value: object, fallback: datetime) -> tuple[datetime, str]:
    if isinstance(value, str) and value.strip():
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
        if not pd.isna(parsed):
            return parsed.to_pydatetime(), "provider"
    return fallback, "received"


def parse_aisstream_message(
    payload: str | bytes | dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> dict[str, object] | None:
    """Convert one AISStream envelope to Espada's canonical AIS row."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    message_type = str(payload.get("MessageType", ""))
    if message_type not in POSITION_MESSAGE_TYPES:
        return None
    metadata = payload.get("MetaData")
    message = payload.get("Message")
    metadata = metadata if isinstance(metadata, dict) else {}
    message = message if isinstance(message, dict) else {}
    body = message.get(message_type)
    body = body if isinstance(body, dict) else {}

    mmsi = _normalize_mmsi(metadata.get("MMSI", body.get("UserID")))
    if mmsi is None:
        return None
    longitude = metadata.get("Longitude", body.get("Longitude"))
    latitude = metadata.get("Latitude", body.get("Latitude"))
    try:
        longitude = float(longitude)
        latitude = float(latitude)
    except (TypeError, ValueError):
        return None
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        return None

    received = received_at or datetime.now(UTC)
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    else:
        received = received.astimezone(UTC)
    provider_time = next(
        (
            metadata[key]
            for key in ("time_utc", "Time_utc", "Timestamp", "timestamp_utc")
            if key in metadata
        ),
        None,
    )
    timestamp, timestamp_source = _parse_time(provider_time, received)
    vessel_name = str(metadata.get("ShipName") or metadata.get("ship_name") or "UNKNOWN").strip()
    row: dict[str, object] = {
        "timestamp_utc": timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mmsi": mmsi,
        "vessel_name": vessel_name or "UNKNOWN",
        "longitude": longitude,
        "latitude": latitude,
        "sog": body.get("Sog"),
        "cog": body.get("Cog"),
        "is_interpolated": False,
        "source": "AISStream live",
        "timestamp_source": timestamp_source,
    }
    return row


class RollingAISCache:
    def __init__(self, path: Path, *, window_hours: float = 72.0, max_rows: int = 250_000):
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")
        self.path = Path(path)
        self.window_hours = float(window_hours)
        self.max_rows = int(max_rows)
        self._rows: list[dict[str, object]] = []
        if self.path.exists():
            try:
                existing = pd.read_csv(self.path, dtype={"mmsi": str})
                self._rows.extend(existing.to_dict("records"))
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                pass

    def add(self, row: dict[str, object]) -> None:
        self._rows.append({name: row.get(name) for name in CANONICAL_COLUMNS})

    def frame(self, *, now: datetime | None = None) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        else:
            current = current.astimezone(UTC)
        frame = pd.DataFrame(self._rows)
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp_utc", "mmsi", "longitude", "latitude"])
        cutoff = current - timedelta(hours=self.window_hours)
        frame = frame.loc[frame["timestamp_utc"] >= cutoff].copy()
        frame = frame.sort_values("timestamp_utc")
        frame = frame.drop_duplicates(["mmsi", "timestamp_utc"], keep="last")
        if len(frame) > self.max_rows:
            frame = frame.iloc[-self.max_rows :].copy()
        frame["timestamp_utc"] = frame["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        for name in CANONICAL_COLUMNS:
            if name not in frame:
                frame[name] = None
        return frame.loc[:, CANONICAL_COLUMNS].reset_index(drop=True)

    def flush(self, *, now: datetime | None = None) -> pd.DataFrame:
        frame = self.frame(now=now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(self.path)
        self._rows = frame.to_dict("records")
        return frame


def _write_status(path: Path, status: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
    temporary.replace(path)


async def capture_aisstream(
    bounding_box: AISBoundingBox,
    output_dir: Path,
    cache_path: Path,
    *,
    duration_seconds: float = 300.0,
    window_hours: float = 72.0,
    max_messages: int | None = None,
    mmsi: list[str] | None = None,
    api_key_env: str = "AISSTREAM_API_KEY",
    endpoint: str = AISSTREAM_ENDPOINT,
    save_raw: bool = False,
    connect_factory: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, object]:
    """Capture live positions with bounded reconnects and a rolling on-disk cache."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError(f"set the {api_key_env} environment variable before starting live AIS")
    if connect_factory is None:
        try:
            from websockets.asyncio.client import connect as websocket_connect
        except ImportError as exc:
            raise RuntimeError(
                "live AIS requires the optional dependency; install the project with .[realtime]"
            ) from exc
        connect_factory = websocket_connect

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = RollingAISCache(cache_path, window_hours=window_hours)
    status_path = output_dir / "live_ais_status.json"
    raw_path = output_dir / "aisstream_raw.jsonl"
    subscription = build_subscription(api_key, bounding_box, mmsi=mmsi)
    started = datetime.now(UTC)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_seconds
    received = 0
    accepted = 0
    rejected = 0
    connections = 0
    confirmations = 0
    warnings: list[str] = []
    backoff_seconds = 1.0

    while loop.time() < deadline and (max_messages is None or accepted < max_messages):
        try:
            connections += 1
            async with connect_factory(
                endpoint,
                compression="deflate",
                ping_interval=20,
                ping_timeout=20,
                max_queue=4096,
            ) as websocket:
                await websocket.send(json.dumps(subscription))
                backoff_seconds = 1.0
                while loop.time() < deadline and (max_messages is None or accepted < max_messages):
                    remaining = max(0.1, deadline - loop.time())
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=min(30.0, remaining))
                    except TimeoutError:
                        continue
                    received += 1
                    raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    try:
                        envelope = json.loads(raw_text)
                    except json.JSONDecodeError:
                        rejected += 1
                        continue
                    if envelope.get("MessageType") == "SubscriptionConfirmation":
                        confirmations += 1
                        continue
                    row = parse_aisstream_message(envelope)
                    if row is None:
                        rejected += 1
                        continue
                    cache.add(row)
                    accepted += 1
                    if save_raw:
                        with raw_path.open("a", encoding="utf-8") as handle:
                            handle.write(raw_text + "\n")
                    if accepted % 50 == 0:
                        cache.flush()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # Network failures are reported and retried until the deadline.
            warning = f"connection {connections} ended: {type(exc).__name__}: {exc}"
            if warning not in warnings:
                warnings.append(warning)
            # A provider rate limit is a deliberate refusal, not a transient empty
            # sea. Reconnecting several times inside the same capture window only
            # extends the block and can hide the real fault behind a NO_DATA state.
            if "HTTP 429" in warning or "status code 429" in warning.lower():
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            delay = min(backoff_seconds + random.uniform(0.0, 0.5), remaining, 30.0)
            await asyncio.sleep(delay)
            backoff_seconds = min(backoff_seconds * 2.0, 30.0)

    frame = cache.flush()
    finished = datetime.now(UTC)
    rate_limited = any("HTTP 429" in warning for warning in warnings)
    connection_failed = bool(warnings and accepted == 0 and confirmations == 0 and received == 0)
    status = {
        "status": "PASS" if accepted > 0 else "ERROR" if connection_failed else "NO_DATA",
        "error_kind": "RATE_LIMITED" if rate_limited else "CONNECTION_FAILED" if connection_failed else None,
        "provider": "AISStream",
        "endpoint": endpoint,
        "bounding_box": bounding_box.to_dict(),
        "started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at_utc": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_duration_seconds": duration_seconds,
        "connections": connections,
        "subscription_confirmations": confirmations,
        "messages_received": received,
        "positions_accepted": accepted,
        "messages_rejected_or_non_position": rejected,
        "cached_positions": len(frame),
        "cached_vessels": int(frame["mmsi"].nunique()) if not frame.empty else 0,
        "cache_file": str(Path(cache_path).resolve()),
        "raw_messages_saved": bool(save_raw),
        "warnings": warnings,
        "limitations": [
            "AIS reception is incomplete and the provider offers no replay or uptime guarantee.",
            "A live AIS position is self-reported evidence, not proof of vessel identity or conduct.",
            "The API key is read from an environment variable and is never written to status or cache files.",
        ],
    }
    _write_status(status_path, status)
    return status
