from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .ais import normalize_ais_csv
from .live_ais import AISBoundingBox, _normalize_mmsi


GFW_REPORT_ENDPOINT = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
GFW_PRESENCE_DATASET = "public-global-presence:latest"
GFW_DELAY_HOURS = 96
GFW_MAX_WINDOW_DAYS = 7
JsonPayload = Mapping[str, Any] | list[object]
JsonPoster = Callable[[str, Mapping[str, str], bytes, float], JsonPayload]


def parse_utc_datetime(value: str) -> datetime:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid UTC date/time: {value}")
    return parsed.to_pydatetime().astimezone(UTC)


@dataclass(frozen=True)
class HistoricalAISRequest:
    bounding_box: AISBoundingBox
    start_datetime: datetime
    end_datetime: datetime
    dataset: str = GFW_PRESENCE_DATASET

    def __post_init__(self) -> None:
        start = self.start_datetime
        end = self.end_datetime
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("historical AIS dates must include a timezone")
        if start >= end:
            raise ValueError("historical AIS start must be before end")
        if end - start > timedelta(days=GFW_MAX_WINDOW_DAYS):
            raise ValueError(
                f"historical AIS window must be {GFW_MAX_WINDOW_DAYS} days or shorter"
            )
        if not self.dataset.strip():
            raise ValueError("Global Fishing Watch dataset is required")

    def date_range(self) -> str:
        def format_utc(value: datetime) -> str:
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        return f"{format_utc(self.start_datetime)},{format_utc(self.end_datetime)}"


def _polygon_for_box(box: AISBoundingBox) -> dict[str, object]:
    coordinates = [[
        [box.min_longitude, box.min_latitude],
        [box.max_longitude, box.min_latitude],
        [box.max_longitude, box.max_latitude],
        [box.min_longitude, box.max_latitude],
        [box.min_longitude, box.min_latitude],
    ]]
    return {"type": "Polygon", "coordinates": coordinates}


def build_gfw_report_request(request: HistoricalAISRequest) -> tuple[str, bytes]:
    """Build the documented 4Wings vessel-presence report request."""
    query = urlencode(
        {
            "spatial-resolution": "HIGH",
            "temporal-resolution": "HOURLY",
            "spatial-aggregation": "false",
            "group-by": "MMSI",
            "datasets[0]": request.dataset,
            "date-range": request.date_range(),
            "format": "JSON",
        }
    )
    body = json.dumps({"geojson": _polygon_for_box(request.bounding_box)}).encode("utf-8")
    return f"{GFW_REPORT_ENDPOINT}?{query}", body


def _post_json(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
) -> JsonPayload:
    http_request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(http_request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(
            f"Global Fishing Watch returned HTTP {exc.code}; check the token, dates and area"
        ) from exc
    except URLError as exc:
        raise RuntimeError("Global Fishing Watch could not be reached") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Global Fishing Watch returned an unreadable response") from exc
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("Global Fishing Watch returned an unexpected response")
    return payload


def _report_entries(payload: JsonPayload) -> list[dict[str, Any]]:
    """Flatten both documented rows and dataset-keyed 4Wings responses."""
    rows: list[dict[str, Any]] = []
    row_location_fields = {"lat", "latitude", "lon", "longitude", "position"}

    def visit(value: object, report_dataset: str | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, report_dataset)
            return
        if not isinstance(value, dict):
            return
        if row_location_fields.intersection(value):
            row = dict(value)
            if report_dataset:
                row.setdefault("reportDataset", report_dataset)
            rows.append(row)
            return
        if "entries" in value:
            visit(value["entries"], report_dataset)
        for key, nested in value.items():
            if str(key).startswith("public-"):
                visit(nested, str(key))

    visit(payload)
    return rows


def gfw_entries_to_frame(
    payload: JsonPayload,
    bounding_box: AISBoundingBox,
) -> tuple[pd.DataFrame, int]:
    entries = _report_entries(payload)
    rows: list[dict[str, object]] = []
    rejected = 0
    rejection_reasons: dict[str, int] = {}
    response_fields: set[str] = set()

    def reject(reason: str) -> None:
        nonlocal rejected
        rejected += 1
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    for entry in entries:
        if not isinstance(entry, dict):
            reject("entry_not_an_object")
            continue
        response_fields.update(str(key) for key in entry)
        vessel = entry.get("vessel") if isinstance(entry.get("vessel"), dict) else {}
        position = entry.get("position") if isinstance(entry.get("position"), dict) else {}
        mmsi = _normalize_mmsi(
            entry.get("mmsi")
            or entry.get("MMSI")
            or entry.get("ssvid")
            or vessel.get("ssvid")
            or vessel.get("mmsi")
        )
        try:
            longitude = float(entry.get("lon", entry.get("longitude", position.get("lon"))))
            latitude = float(entry.get("lat", entry.get("latitude", position.get("lat"))))
        except (TypeError, ValueError):
            reject("missing_or_invalid_coordinates")
            continue
        timestamp_source = "report_bin"
        timestamp = pd.to_datetime(entry.get("date"), utc=True, errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.to_datetime(
                entry.get("timestamp")
                or entry.get("entryTimestamp")
                or entry.get("entry_timestamp"),
                utc=True,
                errors="coerce",
            )
            timestamp_source = "region_entry"
        inside = (
            bounding_box.min_longitude <= longitude <= bounding_box.max_longitude
            and bounding_box.min_latitude <= latitude <= bounding_box.max_latitude
        )
        if mmsi is None:
            reject("missing_or_invalid_mmsi")
            continue
        if pd.isna(timestamp):
            reject("missing_or_invalid_timestamp")
            continue
        if not inside:
            reject("outside_requested_box")
            continue
        rows.append(
            {
                "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mmsi": mmsi,
                "vessel_name": str(
                    entry.get("shipName")
                    or entry.get("ship_name")
                    or entry.get("vesselName")
                    or vessel.get("name")
                    or "UNKNOWN"
                ),
                "longitude": longitude,
                "latitude": latitude,
                "is_interpolated": False,
                "source": "Global Fishing Watch AIS vessel presence (hourly grid)",
                "timestamp_source": timestamp_source,
                "gfw_vessel_id": entry.get("vesselId", entry.get("vessel_id")),
                "presence_hours": entry.get("hours"),
            }
        )
    columns = [
        "timestamp_utc",
        "mmsi",
        "vessel_name",
        "longitude",
        "latitude",
        "is_interpolated",
        "source",
        "timestamp_source",
        "gfw_vessel_id",
        "presence_hours",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame.attrs["rejection_reasons"] = rejection_reasons
    frame.attrs["response_entry_fields"] = sorted(response_fields)
    return frame, rejected


def fetch_gfw_presence(
    request: HistoricalAISRequest,
    output_dir: Path,
    *,
    token_env: str = "GFW_API_ACCESS_TOKEN",
    timeout_seconds: float = 120.0,
    post_json: JsonPoster | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Download delayed hourly AIS presence and normalize it for ESPADA."""
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"set the {token_env} environment variable before downloading AIS history")
    current = now or datetime.now(UTC)
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    latest_available = current - timedelta(hours=GFW_DELAY_HOURS)
    if request.end_datetime.astimezone(UTC) > latest_available:
        latest_text = latest_available.strftime("%Y-%m-%dT%H:%M:%SZ")
        raise ValueError(
            "Global Fishing Watch vessel presence is delayed by about 96 hours; "
            f"choose an end time no later than {latest_text}"
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    url, body = build_gfw_report_request(request)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-EN",
        "User-Agent": "ESPADA-RDA/0.2",
    }
    payload = (post_json or _post_json)(url, headers, body, timeout_seconds)
    frame, rejected = gfw_entries_to_frame(payload, request.bounding_box)
    response_entries = len(_report_entries(payload))
    source_path = output_dir / "gfw_presence.csv"
    frame.to_csv(source_path, index=False)

    status: dict[str, object] = {
        "status": "NO_DATA" if frame.empty else "PASS",
        "provider": "Global Fishing Watch",
        "dataset": request.dataset,
        "data_product": "AIS Vessel Presence",
        "temporal_resolution": "hourly",
        "spatial_resolution": "0.01 degree grid-cell centres",
        "requested_date_range_utc": request.date_range(),
        "bounding_box": request.bounding_box.to_dict(),
        "response_entries": response_entries,
        "positions_accepted": len(frame),
        "entries_rejected": rejected,
        "rejection_reasons": frame.attrs.get("rejection_reasons", {}),
        "response_entry_fields": frame.attrs.get("response_entry_fields", []),
        "vessels": int(frame["mmsi"].nunique()) if not frame.empty else 0,
        "source_file": str(source_path.resolve()),
        "limitations": [
            "This is delayed historical evidence, not live AIS; availability ends about 96 hours ago.",
            "Positions are hourly samples placed at 0.01-degree grid-cell centres, not raw AIS messages.",
            "AIS reception and identity data can be incomplete, duplicated, spoofed or misclassified.",
            "Global Fishing Watch data is for non-commercial use and must be attributed.",
            "Candidate ranking is investigative support and never a determination of guilt.",
        ],
    }
    if not frame.empty:
        quality = normalize_ais_csv(
            source_path,
            output_dir,
            normalized_path=output_dir / "ais_normalized.csv",
        )
        status["normalized_file"] = quality["normalized_file"]
        status["quality_report"] = str((output_dir / "ais_quality.json").resolve())
    status_path = output_dir / "historical_ais_status.json"
    status["status_file"] = str(status_path.resolve())
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status
