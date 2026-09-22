from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MAX_PAYLOAD_BYTES = 20_000


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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


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


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _bbox(value: object) -> list[float] | None:
    values: list[object]
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        return None
    if len(values) != 4:
        return None
    try:
        parsed = [float(item) for item in values]
    except (TypeError, ValueError):
        return None
    if parsed[0] >= parsed[2] or parsed[1] >= parsed[3]:
        return None
    if not (-180 <= parsed[0] <= 180 and -180 <= parsed[2] <= 180):
        return None
    if not (-90 <= parsed[1] <= 90 and -90 <= parsed[3] <= 90):
        return None
    return [round(item, 6) for item in parsed]


def _covers_bbox(returned: list[float] | None, requested: object) -> bool:
    target = _bbox(requested)
    if target is None:
        return True
    if returned is None:
        return False
    return (
        returned[0] <= target[0]
        and returned[1] <= target[1]
        and returned[2] >= target[2]
        and returned[3] >= target[3]
    )


def _check(check_id: str, label: str, passed: bool, detail: str) -> dict[str, object]:
    return {"id": check_id, "label": label, "passed": bool(passed), "detail": detail}


def _load_plan(run_root: Path) -> dict[str, Any]:
    plan = _read_json(run_root / "follow_up/evidence_acquisition_plan.json")
    if not plan:
        raise FileNotFoundError("Build a follow-up evidence plan before recording a return")
    return plan


def _register_path(run_root: Path) -> Path:
    return run_root / "follow_up/intake/evidence_intake_register.json"


def _load_receipts(run_root: Path) -> list[dict[str, Any]]:
    register = _read_json(_register_path(run_root))
    receipts = register.get("receipts", [])
    return [item for item in receipts if isinstance(item, dict)] if isinstance(receipts, list) else []


def _summary(run_root: Path, plan: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    current_digest = str(plan.get("plan_digest_sha256") or "")
    current = [item for item in receipts if item.get("plan_digest_sha256") == current_digest]
    staged = [item for item in current if str(item.get("admission_status")) == "STAGED"]
    admitted = [item for item in current if str(item.get("admission_status")) == "ADMITTED"]
    rejected = [item for item in current if str(item.get("admission_status")) == "REJECTED"]
    blocking_gate = str(plan.get("blocking_gate") or "")
    resolving = [
        item
        for item in admitted
        if blocking_gate and blocking_gate in list(item.get("resolves") or [])
    ]
    if resolving:
        status = "REANALYSIS_READY"
        message = "Admitted evidence addresses the blocking gate; attribution may be rerun by an analyst."
    elif current:
        status = "RETURNS_RECORDED"
        message = "Evidence returns are recorded, but none yet makes the case eligible for reanalysis."
    else:
        status = "NO_RETURNS"
        message = "No follow-up evidence has been returned to this case."
    return {
        "schema": "espada.live-evidence-intake.v1",
        "status": status,
        "message": message,
        "plan_digest_sha256": current_digest,
        "blocking_gate": blocking_gate,
        "request_count": int(plan.get("task_count") or len(plan.get("tasks") or [])),
        "return_count": len(current),
        "staged_count": len(staged),
        "admitted_count": len(admitted),
        "rejected_count": len(rejected),
        "stale_return_count": len(receipts) - len(current),
        "reanalysis_eligible": bool(resolving),
        "attribution_decision_unchanged": True,
        "receipts": current,
        "register_url_path": "follow_up/intake/evidence_intake_register.json",
        "claim_boundary": (
            "Evidence admission makes a case eligible for reanalysis only. It does not identify a vessel, "
            "alter the preserved attribution decision or prove a discharge."
        ),
    }


def load_evidence_intake(run_root: Path) -> dict[str, Any]:
    run_root = Path(run_root).resolve()
    try:
        plan = _load_plan(run_root)
    except FileNotFoundError:
        return {
            "status": "NOT_READY",
            "message": "Build a follow-up evidence plan before recording returns.",
            "receipts": [],
            "reanalysis_eligible": False,
            "attribution_decision_unchanged": True,
        }
    return _summary(run_root, plan, _load_receipts(run_root))


def stage_evidence_return(run_root: Path, submission: dict[str, Any]) -> dict[str, Any]:
    """Record an evidence return locally and test it against the requested scope."""
    run_root = Path(run_root).resolve()
    plan = _load_plan(run_root)
    request_id = str(submission.get("request_id") or "").strip()
    tasks = [item for item in plan.get("tasks", []) if isinstance(item, dict)]
    task = next((item for item in tasks if str(item.get("id")) == request_id), None)
    if task is None:
        raise ValueError("Select a request from the current evidence plan")

    provider = str(submission.get("provider") or "").strip()
    source_reference = str(submission.get("source_reference") or "").strip()
    payload_text = str(submission.get("payload_text") or "")
    encoded = payload_text.encode("utf-8")
    if not provider:
        raise ValueError("Evidence provider is required")
    if not source_reference:
        raise ValueError("A source reference or product identifier is required")
    if not payload_text.strip():
        raise ValueError("Paste the returned evidence data before staging it")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"Evidence payload exceeds the {MAX_PAYLOAD_BYTES:,}-byte intake limit")

    scope = task.get("request_scope") if isinstance(task.get("request_scope"), dict) else {}
    requested_start = _parse_utc(scope.get("start_utc"))
    requested_end = _parse_utc(scope.get("end_utc"))
    returned_start = _parse_utc(submission.get("coverage_start_utc"))
    returned_end = _parse_utc(submission.get("coverage_end_utc"))
    returned_bbox = _bbox(submission.get("bbox_wgs84"))
    requested_mmsi = set(_string_list(scope.get("mmsi")))
    returned_mmsi = set(_string_list(submission.get("covered_mmsi")))
    incident_valid = bool(submission.get("incident_time_valid", False))
    content_digest = hashlib.sha256(encoded).hexdigest()
    receipts = _load_receipts(run_root)
    if any(item.get("payload_sha256") == content_digest for item in receipts):
        raise ValueError("This exact evidence payload is already recorded")

    checks = [
        _check("PROVIDER", "Attributable provider", bool(provider), provider or "Missing provider"),
        _check("SOURCE_REFERENCE", "Source or product reference", bool(source_reference), source_reference or "Missing reference"),
        _check("PAYLOAD", "Evidence payload preserved", bool(payload_text.strip()), f"{len(encoded):,} bytes"),
    ]
    if requested_start or requested_end:
        time_passed = bool(
            returned_start
            and returned_end
            and returned_start <= (requested_start or returned_start)
            and returned_end >= (requested_end or returned_end)
        )
        checks.append(
            _check(
                "TIME_COVERAGE",
                "Requested time covered",
                time_passed,
                "Returned coverage contains the requested interval" if time_passed else "Coverage does not contain the requested interval",
            )
        )
    if scope.get("bbox_wgs84"):
        spatial_passed = _covers_bbox(returned_bbox, scope.get("bbox_wgs84"))
        checks.append(
            _check(
                "SPATIAL_COVERAGE",
                "Requested area covered",
                spatial_passed,
                "Returned bounds contain the requested area" if spatial_passed else "Returned bounds do not contain the requested area",
            )
        )
    if requested_mmsi:
        identifiers_passed = requested_mmsi.issubset(returned_mmsi)
        missing = sorted(requested_mmsi - returned_mmsi)
        checks.append(
            _check(
                "IDENTIFIERS",
                "Requested vessel identifiers covered",
                identifiers_passed,
                "All requested MMSIs are present" if identifiers_passed else f"Missing MMSI: {', '.join(missing)}",
            )
        )
    if request_id == "REQ-IDENTITY":
        checks.append(
            _check(
                "INCIDENT_VALIDITY",
                "Identity valid at incident time",
                incident_valid,
                "Incident-time validity confirmed" if incident_valid else "Incident-time validity is not confirmed",
            )
        )

    validation_passed = all(bool(item["passed"]) for item in checks)
    staged_at = _utc_now()
    receipt_id = f"RET-{staged_at.replace(':', '').replace('-', '')}-{content_digest[:8]}"
    intake_root = run_root / "follow_up/intake"
    payload_path = intake_root / "payloads" / f"{receipt_id}.txt"
    receipt_path = intake_root / "receipts" / f"{receipt_id}.json"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_bytes(encoded)
    receipt = {
        "schema": "espada.evidence-return-receipt.v1",
        "receipt_id": receipt_id,
        "request_id": request_id,
        "request_title": task.get("title"),
        "evidence_type": task.get("evidence_type"),
        "priority": task.get("priority"),
        "resolves": list(task.get("resolves") or []),
        "provider": provider,
        "source_reference": source_reference,
        "staged_at_utc": staged_at,
        "coverage_start_utc": submission.get("coverage_start_utc") or None,
        "coverage_end_utc": submission.get("coverage_end_utc") or None,
        "bbox_wgs84": returned_bbox,
        "covered_mmsi": sorted(returned_mmsi),
        "incident_time_valid": incident_valid,
        "payload_sha256": content_digest,
        "payload_bytes": len(encoded),
        "payload_file": payload_path.relative_to(run_root).as_posix(),
        "plan_digest_sha256": plan.get("plan_digest_sha256"),
        "validation_status": "PASS" if validation_passed else "GAPS",
        "validation_checks": checks,
        "admission_status": "STAGED",
        "external_side_effects": False,
        "attribution_decision_unchanged": True,
    }
    _atomic_json(receipt_path, receipt)
    receipts.append(receipt)
    summary = _summary(run_root, plan, receipts)
    summary["updated_at_utc"] = staged_at
    _atomic_json(_register_path(run_root), summary)
    return {**summary, "staged_receipt": receipt, "register_path": _register_path(run_root)}


def review_evidence_return(
    run_root: Path,
    receipt_id: str,
    decision: str,
    analyst_note: str,
) -> dict[str, Any]:
    """Admit or reject a staged return without changing the preserved attribution result."""
    run_root = Path(run_root).resolve()
    plan = _load_plan(run_root)
    decision = decision.strip().upper()
    if decision not in {"ADMIT", "REJECT"}:
        raise ValueError("Evidence decision must be ADMIT or REJECT")
    note = analyst_note.strip()
    if len(note) < 8:
        raise ValueError("Record a short analyst note explaining the evidence decision")
    receipts = _load_receipts(run_root)
    receipt = next((item for item in receipts if item.get("receipt_id") == receipt_id), None)
    if receipt is None:
        raise FileNotFoundError("The selected evidence receipt does not exist")
    if receipt.get("plan_digest_sha256") != plan.get("plan_digest_sha256"):
        raise RuntimeError("The evidence receipt belongs to an older request plan")
    if decision == "ADMIT" and receipt.get("validation_status") != "PASS":
        raise RuntimeError("Evidence with unresolved scope or provenance gaps cannot be admitted")
    if receipt.get("admission_status") != "STAGED":
        raise RuntimeError("The evidence receipt has already been reviewed")
    if decision == "ADMIT":
        payload_path = (run_root / str(receipt.get("payload_file") or "")).resolve()
        try:
            payload_path.relative_to(run_root)
        except ValueError as error:
            raise RuntimeError("The preserved evidence payload path is outside the case record") from error
        if not payload_path.is_file():
            raise RuntimeError("The preserved evidence payload is missing")
        preserved_digest = hashlib.sha256(payload_path.read_bytes()).hexdigest()
        if preserved_digest != receipt.get("payload_sha256"):
            raise RuntimeError("The preserved evidence payload no longer matches its recorded hash")

    receipt["admission_status"] = "ADMITTED" if decision == "ADMIT" else "REJECTED"
    receipt["reviewed_at_utc"] = _utc_now()
    receipt["analyst_note"] = note
    receipt["attribution_decision_unchanged"] = True
    receipt_path = run_root / "follow_up/intake/receipts" / f"{receipt_id}.json"
    _atomic_json(receipt_path, receipt)
    summary = _summary(run_root, plan, receipts)
    summary["updated_at_utc"] = receipt["reviewed_at_utc"]
    summary["reviewed_receipt_id"] = receipt_id
    _atomic_json(_register_path(run_root), summary)
    return {**summary, "reviewed_receipt": receipt, "register_path": _register_path(run_root)}
