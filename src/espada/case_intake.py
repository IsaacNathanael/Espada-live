from __future__ import annotations

import base64
import binascii
import csv
import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 48 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")

FILE_RULES = {
    "ais_csv": {".csv"},
    "environment_cache": {".json"},
    "slick_geojson": {".geojson", ".json"},
    "sar_image": {".tif", ".tiff", ".png"},
    "spatial_current_grid": {".nc", ".nc4", ".netcdf"},
    "land_mask": {".geojson", ".json"},
}


class CaseIntakeError(ValueError):
    """A request was rejected before it could become an attribution case."""


def _safe_case_id(value: object) -> str:
    case_id = str(value or "").strip()
    if not CASE_ID_PATTERN.fullmatch(case_id):
        raise CaseIntakeError(
            "Case ID must contain 2-64 letters, numbers, underscores or hyphens."
        )
    return case_id


def _finite_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise CaseIntakeError(f"{label} must be a number.") from error
    if number != number or number in {float("inf"), float("-inf")}:
        raise CaseIntakeError(f"{label} must be finite.")
    return number


def _utc_timestamp(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CaseIntakeError(f"{label} is required.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise CaseIntakeError(f"{label} is not a valid ISO date-time.") from error
    if parsed.tzinfo is None:
        raise CaseIntakeError(f"{label} must include a timezone.")
    return parsed.isoformat().replace("+00:00", "Z")


def _decode_upload(field: str, upload: object) -> tuple[str, bytes]:
    if not isinstance(upload, dict):
        raise CaseIntakeError(f"Missing upload: {field}.")
    name = Path(str(upload.get("name") or "")).name
    if not name or name in {".", ".."}:
        raise CaseIntakeError(f"{field} has an invalid filename.")
    suffix = Path(name).suffix.lower()
    if suffix not in FILE_RULES[field]:
        allowed = ", ".join(sorted(FILE_RULES[field]))
        raise CaseIntakeError(f"{field} must use one of: {allowed}.")
    encoded = str(upload.get("data") or "")
    if "," in encoded and encoded.lstrip().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise CaseIntakeError(f"{field} is not a valid file upload.") from error
    if not raw:
        raise CaseIntakeError(f"{field} is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise CaseIntakeError(f"{field} exceeds the 48 MB local intake limit.")
    return name, raw


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaseIntakeError(f"{label} is not valid UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise CaseIntakeError(f"{label} must contain a JSON object.")
    return value


def _validate_ais(raw: bytes) -> None:
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as error:
        raise CaseIntakeError("AIS CSV must be UTF-8 text.") from error
    if len(lines) < 2:
        raise CaseIntakeError("AIS CSV must contain a header and at least one position.")
    header = next(csv.reader([lines[0]]), [])
    normalized = {re.sub(r"[^a-z0-9]", "", item.strip().lower()) for item in header}
    required_aliases = [
        {"mmsi", "shipmmsi"},
        {"timestamputc", "timestamp", "basedatetime", "datetime", "time", "positiontimestamp"},
        {"longitude", "lon", "long"},
        {"latitude", "lat"},
    ]
    if any(not (aliases & normalized) for aliases in required_aliases):
        raise CaseIntakeError(
            "AIS CSV needs MMSI, timestamp, longitude and latitude columns."
        )


def _validate_environment(raw: bytes) -> None:
    value = _json_object(raw, "Environment cache")
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise CaseIntakeError("Environment cache needs at least two time samples.")
    required = {
        "time_utc",
        "current_east_ms",
        "current_north_ms",
        "wind_east_ms",
        "wind_north_ms",
    }
    if any(not isinstance(sample, dict) or not required.issubset(sample) for sample in samples):
        raise CaseIntakeError("Environment samples are missing required wind/current fields.")


def _geometry_types(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    if value.get("type") == "FeatureCollection":
        return {
            str(item.get("geometry", {}).get("type", ""))
            for item in value.get("features", [])
            if isinstance(item, dict)
        }
    if value.get("type") == "Feature":
        return {str(value.get("geometry", {}).get("type", ""))}
    return {str(value.get("type", ""))}


def _validate_geojson(raw: bytes, label: str, *, require_slick_properties: bool) -> None:
    value = _json_object(raw, label)
    if not (_geometry_types(value) & {"Polygon", "MultiPolygon"}):
        raise CaseIntakeError(f"{label} needs a Polygon or MultiPolygon geometry.")
    if not require_slick_properties:
        return
    feature = value
    if value.get("type") == "FeatureCollection":
        features = value.get("features") or []
        feature = features[0] if features else {}
    properties = feature.get("properties", {}) if isinstance(feature, dict) else {}
    if not properties.get("observation_time_utc"):
        raise CaseIntakeError("Slick GeoJSON properties need observation_time_utc.")
    _utc_timestamp(properties.get("observation_time_utc"), "Slick observation_time_utc")
    confidence = _finite_number(properties.get("detection_confidence"), "detection_confidence")
    if not 0 <= confidence <= 1:
        raise CaseIntakeError("detection_confidence must be between 0 and 1.")


def _validate_sar(raw: bytes, suffix: str) -> None:
    is_png = raw.startswith(b"\x89PNG\r\n\x1a\n")
    is_tiff = raw.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"))
    if suffix == ".png" and not is_png:
        raise CaseIntakeError("SAR image filename says PNG, but its file signature does not.")
    if suffix in {".tif", ".tiff"} and not is_tiff:
        raise CaseIntakeError("SAR image filename says TIFF, but its file signature does not.")


def create_case_workspace(project_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Validate uploaded evidence and atomically create one runnable case workspace."""
    project_root = Path(project_root).resolve()
    case_id = _safe_case_id(request.get("case_id"))
    mode = str(request.get("mode") or "").strip()
    if mode not in {"approved_slick", "sar"}:
        raise CaseIntakeError("Mode must be approved_slick or sar.")
    uploads = request.get("files")
    if not isinstance(uploads, dict):
        raise CaseIntakeError("No evidence files were provided.")

    required = ["ais_csv", "environment_cache"]
    required.append("slick_geojson" if mode == "approved_slick" else "sar_image")
    optional = [name for name in ("spatial_current_grid", "land_mask") if uploads.get(name)]
    decoded: dict[str, tuple[str, bytes]] = {
        name: _decode_upload(name, uploads.get(name)) for name in required + optional
    }
    if sum(len(raw) for _, raw in decoded.values()) > MAX_TOTAL_BYTES:
        raise CaseIntakeError("Combined uploads exceed the 96 MB local intake limit.")

    _validate_ais(decoded["ais_csv"][1])
    _validate_environment(decoded["environment_cache"][1])
    if mode == "approved_slick":
        _validate_geojson(
            decoded["slick_geojson"][1], "Slick GeoJSON", require_slick_properties=True
        )
    else:
        sar_name, sar_raw = decoded["sar_image"]
        _validate_sar(sar_raw, Path(sar_name).suffix.lower())
    if "land_mask" in decoded:
        _validate_geojson(decoded["land_mask"][1], "Land mask", require_slick_properties=False)

    analysis = request.get("analysis") if isinstance(request.get("analysis"), dict) else {}
    age_hours = _finite_number(analysis.get("age_hours", 0), "age_hours")
    if age_hours < 0 or age_hours > 720:
        raise CaseIntakeError("age_hours must be between 0 and 720.")
    candidate_ages = analysis.get(
        "candidate_ages_hours", [1.5, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24]
    )
    if not isinstance(candidate_ages, list) or not candidate_ages:
        raise CaseIntakeError("candidate_ages_hours must be a non-empty list.")
    candidate_ages = [_finite_number(value, "candidate age") for value in candidate_ages]
    if any(value <= 0 or value > 720 for value in candidate_ages):
        raise CaseIntakeError("Candidate ages must be greater than 0 and at most 720 hours.")

    bbox: list[float] | None = None
    observation_time: str | None = None
    if mode == "sar":
        bbox_values = request.get("bbox")
        if not isinstance(bbox_values, list) or len(bbox_values) != 4:
            raise CaseIntakeError("SAR mode needs [min lon, min lat, max lon, max lat].")
        bbox = [_finite_number(value, "bounding-box coordinate") for value in bbox_values]
        if not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
            raise CaseIntakeError("SAR bounding box is outside WGS84 or has reversed bounds.")
        observation_time = _utc_timestamp(
            request.get("observation_time_utc"), "observation_time_utc"
        )

    intake_root = project_root / "out" / "case_intake"
    final_dir = intake_root / case_id
    if final_dir.exists():
        raise CaseIntakeError(
            f"Case '{case_id}' already exists. Choose a new Case ID to preserve its evidence."
        )
    staging = intake_root / f".{case_id}.staging-{uuid.uuid4().hex}"
    inputs = staging / "inputs"
    inputs.mkdir(parents=True, exist_ok=False)
    try:
        relative_inputs: dict[str, str] = {}
        evidence: list[dict[str, object]] = []
        for field, (original_name, raw) in decoded.items():
            stored_name = f"{field}{Path(original_name).suffix.lower()}"
            path = inputs / stored_name
            path.write_bytes(raw)
            relative = (final_dir / "inputs" / stored_name).relative_to(project_root).as_posix()
            relative_inputs[field] = relative
            evidence.append(
                {"field": field, "original_name": original_name, "stored_path": relative, "bytes": len(raw)}
            )

        case: dict[str, Any] = {
            "schema_version": 1,
            "case_id": case_id,
            "mode": mode,
            "inputs": relative_inputs,
            "analysis": {
                "age_hours": age_hours,
                "candidate_ages_hours": candidate_ages,
            },
            "output_directory": f"out/cases/{case_id}",
        }
        if mode == "sar":
            case["observation_time_utc"] = observation_time
            case["bbox"] = bbox
            case["analysis"].update(
                {
                    "analyst_approved": bool(analysis.get("analyst_approved", False)),
                    "use_classical_fallback": bool(
                        analysis.get("use_classical_fallback", False)
                    ),
                }
            )
        (staging / "case.json").write_text(json.dumps(case, indent=2), encoding="utf-8")
        intake = {
            "status": "READY",
            "case_id": case_id,
            "mode": mode,
            "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "evidence": evidence,
            "safety": "Uploaded evidence was validated; attribution has not run yet.",
        }
        (staging / "intake_manifest.json").write_text(
            json.dumps(intake, indent=2), encoding="utf-8"
        )
        staging.replace(final_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "status": "READY",
        "case_id": case_id,
        "mode": mode,
        "case_file": str((final_dir / "case.json").resolve()),
        "case_file_url": f"/out/case_intake/{case_id}/case.json",
        "run_endpoint": "/api/run-case",
        "message": "Evidence passed intake checks. The case is ready to run.",
    }
