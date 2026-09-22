from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .live_reanalysis import evaluate_candidate_decision


DISPOSITIONS = {
    "KEEP_OPEN_ACQUIRE_EVIDENCE": {
        "case_state": "OPEN",
        "label": "Continue evidence collection",
        "message": "The case remains open for the next evidence cycle.",
    },
    "CLOSE_INCONCLUSIVE": {
        "case_state": "CLOSED_INCONCLUSIVE",
        "label": "Close as inconclusive",
        "message": "The investigation is closed without nominating a vessel.",
    },
    "REFER_AUTHORIZED_REVIEW": {
        "case_state": "REFERRED_FOR_REVIEW",
        "label": "Refer for authorized review",
        "message": "The evidence package is referred for accountable human review.",
    },
}


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_summary(candidate: object) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    return {
        "mmsi": candidate.get("mmsi"),
        "vessel_name": candidate.get("vessel_name"),
        "total_score": candidate.get("total_score"),
        "data_quality": candidate.get("data_quality"),
        "forward_error_km": candidate.get("forward_error_km"),
    }


def _baseline_version(run_root: Path) -> dict[str, Any] | None:
    ranking_path = run_root / "attribution/ranking/candidates.json"
    ranking = _read_json(ranking_path)
    if not ranking:
        return None
    candidates = [item for item in ranking.get("candidates", []) if isinstance(item, dict)]
    outcome = evaluate_candidate_decision(candidates)
    recorded_at = datetime.fromtimestamp(ranking_path.stat().st_mtime, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "version": "BASELINE",
        "label": "Original attribution",
        "completed_at_utc": recorded_at,
        "input_digest_sha256": _sha256(ranking_path),
        "decision": outcome["decision"],
        "candidate_count": len(candidates),
        "top_candidate": _candidate_summary(candidates[0] if candidates else None),
        "score_margin": outcome["score_margin"],
        "gates": outcome["gates"],
        "record_path": "attribution/ranking/candidates.json",
        "immutable": True,
    }


def _reanalysis_versions(run_root: Path) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for path in sorted((run_root / "reanalysis").glob("v[0-9][0-9][0-9]/reanalysis_result.json")):
        result = _read_json(path)
        rerun = result.get("rerun") if isinstance(result.get("rerun"), dict) else {}
        if result.get("status") != "COMPLETE":
            continue
        versions.append(
            {
                "version": result.get("version") or path.parent.name,
                "label": f"Evidence rerun {result.get('version') or path.parent.name}",
                "completed_at_utc": result.get("completed_at_utc"),
                "input_digest_sha256": result.get("input_digest_sha256") or _sha256(path),
                "decision": rerun.get("decision") or "ABSTAIN_INSUFFICIENT_EVIDENCE",
                "candidate_count": int(rerun.get("candidate_count") or 0),
                "top_candidate": _candidate_summary(rerun.get("top_candidate")),
                "score_margin": float(rerun.get("score_margin") or 0.0),
                "gates": list(rerun.get("gates") or []),
                "record_path": path.relative_to(run_root).as_posix(),
                "immutable": True,
            }
        )
    return versions


def decision_versions(run_root: Path) -> list[dict[str, Any]]:
    """Return the immutable baseline and every completed rerun in evidence order."""
    run_root = Path(run_root).resolve()
    versions: list[dict[str, Any]] = []
    baseline = _baseline_version(run_root)
    if baseline:
        versions.append(baseline)
    versions.extend(_reanalysis_versions(run_root))
    previous: dict[str, Any] | None = None
    for version in versions:
        if previous is None:
            version["change"] = {
                "decision_changed": False,
                "top_candidate_changed": False,
                "score_margin_change": 0.0,
                "summary": "Original preserved attribution result.",
            }
        else:
            prior_top = previous.get("top_candidate") or {}
            current_top = version.get("top_candidate") or {}
            decision_changed = previous.get("decision") != version.get("decision")
            candidate_changed = prior_top.get("mmsi") != current_top.get("mmsi")
            margin_change = float(version.get("score_margin") or 0.0) - float(
                previous.get("score_margin") or 0.0
            )
            changes = []
            if candidate_changed:
                changes.append("top candidate changed")
            if decision_changed:
                changes.append("safety decision changed")
            if not changes:
                changes.append("candidate and decision remained stable")
            version["change"] = {
                "decision_changed": decision_changed,
                "top_candidate_changed": candidate_changed,
                "score_margin_change": margin_change,
                "summary": "; ".join(changes).capitalize() + ".",
            }
        previous = version
    return versions


def _event_files(run_root: Path) -> list[Path]:
    return sorted((run_root / "closure/events").glob("CLS-*.json"))


def _history(run_root: Path) -> list[dict[str, Any]]:
    events = [_read_json(path) for path in _event_files(run_root)]
    return [
        {
            "event_id": item.get("event_id"),
            "recorded_at_utc": item.get("recorded_at_utc"),
            "selected_version": item.get("selected_version"),
            "disposition": item.get("disposition"),
            "disposition_label": item.get("disposition_label"),
            "case_state": item.get("case_state"),
            "reviewer_role": item.get("reviewer_role"),
            "event_sha256": item.get("event_sha256"),
        }
        for item in events
        if item.get("event_id")
    ]


def load_case_closure(run_root: Path) -> dict[str, Any]:
    run_root = Path(run_root).resolve()
    versions = decision_versions(run_root)
    if not versions:
        return {
            "schema": "espada.case-closure.v1",
            "status": "NOT_READY",
            "message": "Complete candidate attribution before recording a case disposition.",
            "versions": [],
            "history": [],
            "allowed_dispositions": DISPOSITIONS,
        }
    latest = versions[-1]
    current = _read_json(run_root / "closure/current.json")
    history = _history(run_root)
    if not current:
        status = "READY"
        case_state = "AWAITING_DISPOSITION"
        message = "The latest evidence version is ready for an accountable disposition."
    elif (
        current.get("selected_version") != latest.get("version")
        or current.get("selected_input_digest_sha256") != latest.get("input_digest_sha256")
    ):
        status = "SUPERSEDED"
        case_state = "REVIEW_REQUIRED"
        message = "New evidence superseded the recorded disposition; review the latest version."
    else:
        status = "RECORDED"
        case_state = str(current.get("case_state") or "AWAITING_DISPOSITION")
        message = str(current.get("message") or "The latest disposition is recorded.")
    return {
        "schema": "espada.case-closure.v1",
        "status": status,
        "message": message,
        "case_state": case_state,
        "latest_version": latest.get("version"),
        "latest_input_digest_sha256": latest.get("input_digest_sha256"),
        "versions": versions,
        "version_count": len(versions),
        "current_disposition": current or None,
        "history": history,
        "event_count": len(history),
        "allowed_dispositions": DISPOSITIONS,
        "claim_boundary": (
            "A disposition records an investigative workflow decision. It does not prove a discharge, "
            "declare guilt or authorize enforcement."
        ),
    }


def record_case_disposition(run_root: Path, submission: dict[str, Any]) -> dict[str, Any]:
    """Append an analyst disposition and update the current pointer without deleting history."""
    run_root = Path(run_root).resolve()
    closure = load_case_closure(run_root)
    versions = closure.get("versions") or []
    if not versions:
        raise RuntimeError(str(closure.get("message") or "Case closure is not ready"))
    latest = versions[-1]
    selected_version = str(submission.get("selected_version") or "").strip().upper()
    if selected_version != str(latest.get("version") or "").upper():
        raise ValueError("Disposition must reference the latest evidence version")
    disposition = str(submission.get("disposition") or "").strip().upper()
    if disposition not in DISPOSITIONS:
        raise ValueError("Select a permitted case disposition")
    reviewer_role = " ".join(str(submission.get("reviewer_role") or "").split())
    rationale = " ".join(str(submission.get("rationale") or "").split())
    if not 3 <= len(reviewer_role) <= 80:
        raise ValueError("Reviewer role must be between 3 and 80 characters")
    if not 20 <= len(rationale) <= 1000:
        raise ValueError("Rationale must be between 20 and 1000 characters")
    if submission.get("acknowledged") is not True:
        raise ValueError("Confirm the claim boundary before recording a disposition")

    previous = _read_json(run_root / "closure/current.json")
    previous_hash = previous.get("event_sha256") if previous else None
    policy = DISPOSITIONS[disposition]
    recorded_at = _utc_now()
    event_id = f"CLS-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
    event = {
        "schema": "espada.case-disposition-event.v1",
        "event_id": event_id,
        "recorded_at_utc": recorded_at,
        "selected_version": latest.get("version"),
        "selected_input_digest_sha256": latest.get("input_digest_sha256"),
        "selected_decision": latest.get("decision"),
        "selected_top_candidate": latest.get("top_candidate"),
        "disposition": disposition,
        "disposition_label": policy["label"],
        "case_state": policy["case_state"],
        "message": policy["message"],
        "reviewer_role": reviewer_role,
        "rationale": rationale,
        "previous_event_sha256": previous_hash,
        "external_side_effects": False,
        "automatic_accusation": False,
        "claim_boundary": (
            "This record documents an analyst workflow disposition only. It is not a guilt finding, "
            "discharge proof or enforcement authorization."
        ),
    }
    event["event_sha256"] = _canonical_digest(event)
    event_path = run_root / "closure/events" / f"{event_id}.json"
    _atomic_json(event_path, event)
    _atomic_json(run_root / "closure/current.json", event)
    return {
        **load_case_closure(run_root),
        "recorded_event": event,
        "event_path": event_path,
        "current_path": run_root / "closure/current.json",
    }
