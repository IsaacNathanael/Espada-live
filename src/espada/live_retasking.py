from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coordinate_pairs(value: object) -> Iterable[tuple[float, float]]:
    if not isinstance(value, list):
        return
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
        return
    for child in value:
        yield from _coordinate_pairs(child)


def _origin_bbox(origin_zone: dict[str, Any], release: dict[str, Any]) -> list[float] | None:
    points: list[tuple[float, float]] = []
    for feature in origin_zone.get("features", []):
        if isinstance(feature, dict):
            geometry = feature.get("geometry")
            if isinstance(geometry, dict):
                points.extend(_coordinate_pairs(geometry.get("coordinates")))
    if points:
        longitudes = [point[0] for point in points]
        latitudes = [point[1] for point in points]
        return [
            round(min(longitudes), 6),
            round(min(latitudes), 6),
            round(max(longitudes), 6),
            round(max(latitudes), 6),
        ]
    estimated = release.get("estimated_origin")
    if not isinstance(estimated, dict):
        return None
    try:
        longitude = float(estimated["longitude"])
        latitude = float(estimated["latitude"])
        radius_km = max(1.0, float(release.get("credible_radius_90_km") or 1.0))
    except (KeyError, TypeError, ValueError):
        return None
    latitude_delta = radius_km / 111.0
    longitude_delta = latitude_delta / max(0.2, abs(math.cos(math.radians(latitude))))
    return [
        round(longitude - longitude_delta, 6),
        round(latitude - latitude_delta, 6),
        round(longitude + longitude_delta, 6),
        round(latitude + latitude_delta, 6),
    ]


def _unknown_identity(candidate: dict[str, Any]) -> bool:
    name = str(candidate.get("vessel_name") or "").strip().upper()
    return not name or name in {"UNKNOWN", "UNVERIFIED", "N/A"}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_live_retasking_plan(run_root: Path) -> dict[str, Any]:
    run_root = Path(run_root).resolve()
    return _read_json(run_root / "follow_up/evidence_acquisition_plan.json")


def build_live_retasking_plan(run_root: Path) -> dict[str, Any]:
    """Translate an evidence-safe abstention into auditable, non-dispatched requests."""
    run_root = Path(run_root).resolve()
    response = _read_json(run_root / "response/response_summary.json")
    ranking = _read_json(run_root / "attribution/ranking/candidates.json")
    release = _read_json(run_root / "attribution/drift/release_estimate.json")
    origin_zone = _read_json(run_root / "attribution/origin_zone.geojson")
    metadata = _read_json(run_root / "input/sentinel1_subset_status.json")
    candidates = ranking.get("candidates")
    if not response:
        raise FileNotFoundError("A sealed response summary is required before follow-up planning")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Candidate ranking evidence is required before follow-up planning")

    normalized_candidates = [item for item in candidates if isinstance(item, dict)]
    normalized_candidates.sort(key=lambda item: int(item.get("rank") or 999_999))
    top = normalized_candidates[0]
    runner = normalized_candidates[1] if len(normalized_candidates) > 1 else {}
    top_score = float(top.get("total_score") or 0.0)
    runner_score = float(runner.get("total_score") or 0.0)
    score_margin = top_score - runner_score
    tied = [
        item for item in normalized_candidates
        if abs(float(item.get("total_score") or 0.0) - top_score) < 1e-9
    ]
    lead_candidates = tied if len(tied) > 1 else normalized_candidates[:3]
    lead_mmsis = [str(item.get("mmsi") or "") for item in lead_candidates if item.get("mmsi")]
    release_time = _parse_utc(release.get("release_time_utc") or response.get("release_time_utc"))
    observation_time = _parse_utc(
        release.get("observation_time_utc")
        or response.get("observation_time_utc")
        or metadata.get("acquisition_time_utc")
    )
    if release_time is None or observation_time is None:
        raise RuntimeError("Release and observation timestamps are required for follow-up planning")
    request_start = release_time - timedelta(minutes=90)
    request_end = release_time + timedelta(minutes=90)
    imagery_start = release_time - timedelta(hours=3)
    imagery_end = observation_time + timedelta(hours=6)
    bbox = _origin_bbox(origin_zone, release)

    gate_specs = [
        {
            "id": "TOP_SCORE",
            "label": "Top comparative score",
            "direction": "minimum",
            "observed": top_score,
            "required": 0.40,
            "unit": "fraction",
            "passed": top_score >= 0.40,
        },
        {
            "id": "CANDIDATE_SEPARATION",
            "label": "Lead over runner-up",
            "direction": "minimum",
            "observed": score_margin,
            "required": 0.05,
            "unit": "fraction",
            "passed": score_margin >= 0.05,
        },
        {
            "id": "TRACK_QUALITY",
            "label": "Top-track data quality",
            "direction": "minimum",
            "observed": float(top.get("data_quality") or 0.0),
            "required": 0.40,
            "unit": "fraction",
            "passed": float(top.get("data_quality") or 0.0) >= 0.40,
        },
        {
            "id": "FORWARD_ERROR",
            "label": "Forward-replay error",
            "direction": "maximum",
            "observed": float(top.get("forward_error_km") or 999.0),
            "required": 8.0,
            "unit": "km",
            "passed": float(top.get("forward_error_km") or 999.0) <= 8.0,
        },
    ]
    failed_gates = [gate for gate in gate_specs if not gate["passed"]]
    blocking_gate = failed_gates[0]["id"] if failed_gates else "INDEPENDENT_CORROBORATION"

    tasks: list[dict[str, Any]] = []
    if blocking_gate == "CANDIDATE_SEPARATION" or any(
        bool(item.get("release_position_interpolated")) for item in lead_candidates
    ):
        tasks.append(
            {
                "id": "REQ-AIS-DIRECT",
                "priority": "P1",
                "evidence_type": "OBSERVED",
                "title": "Request direct AIS around the release window",
                "objective": "Replace interpolated release positions and separate the tied leading tracks.",
                "resolves": ["CANDIDATE_SEPARATION", "RELEASE_POSITION_INTERPOLATION"],
                "request_to": "Terrestrial or commercial AIS provider / coastal VTS",
                "request_scope": {
                    "mmsi": lead_mmsis,
                    "start_utc": _format_utc(request_start),
                    "end_utc": _format_utc(request_end),
                    "bbox_wgs84": bbox,
                    "fields": ["mmsi", "timestamp_utc", "longitude", "latitude", "sog", "cog", "source_receiver"],
                },
                "acceptance_criteria": [
                    "Provider-origin timestamps and coordinates are retained.",
                    "Receiver or coverage metadata accompanies the positions.",
                    "The release-time position is directly observed rather than interpolated.",
                ],
                "dispatch_status": "DRAFT_NOT_SENT",
            }
        )
    if any(_unknown_identity(item) for item in lead_candidates):
        tasks.append(
            {
                "id": "REQ-IDENTITY",
                "priority": "P1",
                "evidence_type": "CORROBORATIVE",
                "title": "Verify the leading vessel identities",
                "objective": "Map each leading MMSI to a vessel identity valid on the incident date.",
                "resolves": ["IDENTITY_UNVERIFIED"],
                "request_to": "Flag-state registry, IMO/GISIS, port authority or verified registry provider",
                "request_scope": {
                    "mmsi": lead_mmsis,
                    "as_of_utc": _format_utc(release_time),
                    "fields": ["mmsi", "imo", "vessel_name", "flag", "vessel_type", "ownership_as_of_incident"],
                },
                "acceptance_criteria": [
                    "Identity source and retrieval time are recorded.",
                    "MMSI-to-IMO mapping is valid at the incident time.",
                    "Historical name or ownership changes are preserved, not overwritten.",
                ],
                "dispatch_status": "DRAFT_NOT_SENT",
            }
        )
    tasks.append(
        {
            "id": "REQ-EARTH-OBSERVATION",
            "priority": "P2",
            "evidence_type": "INDEPENDENT_OBSERVATION",
            "title": "Search for an independent slick observation",
            "objective": "Test whether the SAR dark signature persists across another sensor or acquisition.",
            "resolves": ["SAR_LOOKALIKE_UNCERTAINTY"],
            "request_to": "Sentinel-1/2, Landsat, commercial SAR or optical archive",
            "request_scope": {
                "start_utc": _format_utc(imagery_start),
                "end_utc": _format_utc(imagery_end),
                "bbox_wgs84": metadata.get("bbox") or bbox,
                "preferred_observations": ["dual-polarisation SAR", "cloud-free optical", "thermal or hyperspectral if available"],
            },
            "acceptance_criteria": [
                "Acquisition metadata and original product identifier are retained.",
                "Positive and negative observations are both recorded.",
                "Co-location is assessed against the approved slick geometry, not only its centroid.",
            ],
            "dispatch_status": "DRAFT_NOT_SENT",
        }
    )
    tasks.append(
        {
            "id": "REQ-OPERATIONAL-RECORDS",
            "priority": "P2",
            "evidence_type": "CORROBORATIVE",
            "title": "Request independent movement and discharge records",
            "objective": "Check whether non-AIS records distinguish the leading vessels without treating silence as guilt.",
            "resolves": ["INDEPENDENT_CORROBORATION"],
            "request_to": "Port authority, coastal VTS, operator and environmental response authority",
            "request_scope": {
                "mmsi": lead_mmsis,
                "start_utc": _format_utc(request_start - timedelta(hours=2)),
                "end_utc": _format_utc(request_end + timedelta(hours=2)),
                "records": ["VTS radar track", "port call", "bunker or cargo log", "incident report", "sampling chain of custody"],
            },
            "acceptance_criteria": [
                "Records have an attributable source and incident-time validity.",
                "Contradictory and exculpatory evidence is preserved.",
                "Any sample comparison retains laboratory method and chain of custody.",
            ],
            "dispatch_status": "DRAFT_NOT_SENT",
        }
    )

    plan = {
        "schema": "espada.live-evidence-acquisition-plan.v1",
        "status": "PASS",
        "plan_status": "REQUEST_PACKAGE_READY",
        "dispatch_status": "NOT_SENT",
        "external_side_effects": False,
        "generated_at_utc": _utc_now(),
        "scene_id": str(response.get("scene_id") or run_root.name),
        "operational_decision": response.get("operational_decision"),
        "trigger": response.get("rationale"),
        "blocking_gate": blocking_gate,
        "decision_gates": gate_specs,
        "failed_gate_count": len(failed_gates),
        "tied_candidate_count": len(tied),
        "tied_candidates": [
            {
                "rank": item.get("rank"),
                "mmsi": str(item.get("mmsi") or ""),
                "vessel_name": item.get("vessel_name"),
                "score": item.get("total_score"),
                "release_position_interpolated": bool(item.get("release_position_interpolated")),
            }
            for item in tied
        ],
        "release_window_utc": {
            "start": _format_utc(request_start),
            "centre": _format_utc(release_time),
            "end": _format_utc(request_end),
        },
        "observation_time_utc": _format_utc(observation_time),
        "origin_zone_bbox_wgs84": bbox,
        "task_count": len(tasks),
        "p1_task_count": sum(task["priority"] == "P1" for task in tasks),
        "tasks": tasks,
        "claim_boundary": (
            "This plan identifies evidence gaps and drafts acquisition scopes. It does not send "
            "requests, retask a satellite, verify a vessel identity or change the attribution decision."
        ),
    }
    digest_payload = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    plan["plan_digest_sha256"] = hashlib.sha256(digest_payload).hexdigest()

    follow_up = run_root / "follow_up"
    plan_path = follow_up / "evidence_acquisition_plan.json"
    csv_path = follow_up / "evidence_requests.csv"
    _atomic_json(plan_path, plan)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "priority", "request_id", "title", "evidence_type", "request_to", "objective",
                "resolves", "start_utc", "end_utc", "bbox_wgs84", "mmsi", "acceptance_criteria",
                "dispatch_status",
            ],
        )
        writer.writeheader()
        for task in tasks:
            scope = task["request_scope"]
            writer.writerow(
                {
                    "priority": task["priority"],
                    "request_id": task["id"],
                    "title": task["title"],
                    "evidence_type": task["evidence_type"],
                    "request_to": task["request_to"],
                    "objective": task["objective"],
                    "resolves": "; ".join(task["resolves"]),
                    "start_utc": scope.get("start_utc") or scope.get("as_of_utc"),
                    "end_utc": scope.get("end_utc"),
                    "bbox_wgs84": json.dumps(scope.get("bbox_wgs84")) if scope.get("bbox_wgs84") else "",
                    "mmsi": "; ".join(scope.get("mmsi") or []),
                    "acceptance_criteria": " | ".join(task["acceptance_criteria"]),
                    "dispatch_status": task["dispatch_status"],
                }
            )
    return {**plan, "plan_path": plan_path, "requests_csv_path": csv_path}
