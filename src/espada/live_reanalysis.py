from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .ais import normalize_ais_csv
from .attribution import write_attribution_outputs
from .live_evidence_intake import load_evidence_intake


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


def _parse_utc(value: object) -> pd.Timestamp | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else parsed


def _decision(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {
            "decision": "ABSTAIN_INSUFFICIENT_EVIDENCE",
            "score_margin": 0.0,
            "gates": [],
        }
    top = candidates[0]
    runner_score = float(candidates[1].get("total_score", 0.0)) if len(candidates) > 1 else 0.0
    margin = float(top.get("total_score", 0.0)) - runner_score
    gates = [
        {
            "id": "TOP_SCORE",
            "label": "Top comparative score",
            "passed": float(top.get("total_score", 0.0)) >= 0.40,
            "value": float(top.get("total_score", 0.0)),
            "threshold": 0.40,
        },
        {
            "id": "CANDIDATE_SEPARATION",
            "label": "Lead over runner-up",
            "passed": margin >= 0.05,
            "value": margin,
            "threshold": 0.05,
        },
        {
            "id": "TRACK_QUALITY",
            "label": "Top-track data quality",
            "passed": float(top.get("data_quality", 0.0)) >= 0.40,
            "value": float(top.get("data_quality", 0.0)),
            "threshold": 0.40,
        },
        {
            "id": "FORWARD_ERROR",
            "label": "Forward-replay error",
            "passed": float(top.get("forward_error_km", 999.0)) <= 8.0,
            "value": float(top.get("forward_error_km", 999.0)),
            "threshold": 8.0,
        },
    ]
    return {
        "decision": (
            "LIMITED_SHORTLIST"
            if len(candidates) >= 2 and all(bool(gate["passed"]) for gate in gates)
            else "ABSTAIN_INSUFFICIENT_EVIDENCE"
        ),
        "score_margin": margin,
        "gates": gates,
    }


def _baseline(run_root: Path) -> dict[str, Any]:
    ranking = _read_json(run_root / "attribution/ranking/candidates.json")
    candidates = [item for item in ranking.get("candidates", []) if isinstance(item, dict)]
    outcome = _decision(candidates)
    return {
        "candidate_count": len(candidates),
        "top_candidate": candidates[0] if candidates else None,
        **outcome,
    }


def _result_files(run_root: Path) -> list[Path]:
    root = run_root / "reanalysis"
    if not root.is_dir():
        return []
    return sorted(root.glob("v[0-9][0-9][0-9]/reanalysis_result.json"))


def _public_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"result_path", "ranking_chart_path", "attribution_map_path", "merged_ais_path"}
    }


def load_live_reanalysis(run_root: Path) -> dict[str, Any]:
    run_root = Path(run_root).resolve()
    intake = load_evidence_intake(run_root)
    completed = [_read_json(path) for path in _result_files(run_root)]
    completed = [item for item in completed if item.get("status") == "COMPLETE"]
    if completed:
        latest = completed[-1]
        eligible = [
            item
            for item in intake.get("receipts", [])
            if item.get("admission_status") == "ADMITTED"
            and intake.get("blocking_gate") in list(item.get("resolves") or [])
        ]
        direct_ais = [item for item in eligible if item.get("request_id") == "REQ-AIS-DIRECT"]
        if intake.get("status") == "REANALYSIS_READY" and direct_ais:
            current_digest = _input_digest(run_root, direct_ais)
            if current_digest != latest.get("input_digest_sha256"):
                next_number = max(int(str(item.get("version", "v000"))[1:]) for item in completed) + 1
                return {
                    "schema": "espada.live-reanalysis.v1",
                    "status": "READY",
                    "message": "New admitted direct AIS is available for another versioned rerun.",
                    "version_count": len(completed),
                    "next_version": f"v{next_number:03d}",
                    "eligible_receipts": eligible,
                    "baseline": _baseline(run_root),
                    "previous_version": latest.get("version"),
                    "original_attribution_unchanged": True,
                }
        return {
            **latest,
            "status": "COMPLETE",
            "message": "Recovered the latest versioned reanalysis result.",
            "version_count": len(completed),
            "versions": [
                {
                    "version": item.get("version"),
                    "completed_at_utc": item.get("completed_at_utc"),
                    "decision": item.get("rerun", {}).get("decision"),
                    "top_mmsi": item.get("rerun", {}).get("top_candidate", {}).get("mmsi"),
                }
                for item in completed
            ],
        }
    if intake.get("status") != "REANALYSIS_READY":
        return {
            "schema": "espada.live-reanalysis.v1",
            "status": "NOT_READY",
            "message": "Admit evidence that resolves the blocking gate before reanalysis.",
            "version_count": 0,
            "eligible_receipts": [],
            "baseline": _baseline(run_root),
            "original_attribution_unchanged": True,
        }
    receipts = [
        item
        for item in intake.get("receipts", [])
        if item.get("admission_status") == "ADMITTED"
        and intake.get("blocking_gate") in list(item.get("resolves") or [])
    ]
    direct_ais = [item for item in receipts if item.get("request_id") == "REQ-AIS-DIRECT"]
    return {
        "schema": "espada.live-reanalysis.v1",
        "status": "READY" if direct_ais else "BLOCKED_UNSUPPORTED_EVIDENCE",
        "message": (
            "Admitted direct AIS can be replayed through the frozen attribution pipeline."
            if direct_ais
            else "The admitted evidence cannot yet be translated into the attribution input contract."
        ),
        "version_count": 0,
        "next_version": "v001",
        "eligible_receipts": receipts,
        "baseline": _baseline(run_root),
        "original_attribution_unchanged": True,
    }


def _input_digest(run_root: Path, receipts: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for relative in (
        "attribution/ais/ais_normalized.csv",
        "attribution/drift/reverse_endpoints.npz",
        "attribution/drift/release_estimate.json",
        "attribution/drift/forward_particles.npz",
        "attribution/ranking/candidates.json",
    ):
        path = run_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Required baseline evidence is missing: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    for receipt in sorted(receipts, key=lambda item: str(item.get("receipt_id"))):
        digest.update(str(receipt.get("receipt_id")).encode("utf-8"))
        digest.update(str(receipt.get("payload_sha256")).encode("ascii"))
    return digest.hexdigest()


def _verified_payload(run_root: Path, receipt: dict[str, Any]) -> Path:
    path = (run_root / str(receipt.get("payload_file") or "")).resolve()
    try:
        path.relative_to(run_root)
    except ValueError as error:
        raise RuntimeError("An admitted payload path escapes the case record") from error
    if not path.is_file():
        raise FileNotFoundError(f"Admitted payload is missing: {receipt.get('receipt_id')}")
    if _sha256(path) != receipt.get("payload_sha256"):
        raise RuntimeError(f"Admitted payload hash failed: {receipt.get('receipt_id')}")
    return path


def _merge_direct_ais(
    run_root: Path,
    version_root: Path,
    receipts: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    baseline_path = run_root / "attribution/ais/ais_normalized.csv"
    baseline = pd.read_csv(baseline_path, dtype={"mmsi": str})
    baseline["timestamp_utc"] = pd.to_datetime(baseline["timestamp_utc"], utc=True, errors="coerce")
    baseline = baseline.loc[baseline["timestamp_utc"].notna()].copy()
    baseline_rows = len(baseline)
    removed_rows = 0
    incoming_frames: list[pd.DataFrame] = []
    receipt_audit: list[dict[str, Any]] = []

    for receipt in receipts:
        payload = _verified_payload(run_root, receipt)
        receipt_dir = version_root / "evidence" / str(receipt["receipt_id"])
        normalized = normalize_ais_csv(
            payload,
            receipt_dir,
            normalized_path=receipt_dir / "ais_normalized.csv",
        )
        incoming = pd.read_csv(normalized["normalized_file"], dtype={"mmsi": str})
        incoming["timestamp_utc"] = pd.to_datetime(incoming["timestamp_utc"], utc=True, errors="coerce")
        incoming = incoming.loc[incoming["timestamp_utc"].notna()].copy()
        incoming_frames.append(incoming)

        covered = {str(item) for item in receipt.get("covered_mmsi", [])}
        start = _parse_utc(receipt.get("coverage_start_utc"))
        end = _parse_utc(receipt.get("coverage_end_utc"))
        mask = baseline["mmsi"].astype(str).isin(covered)
        if start is not None:
            mask &= baseline["timestamp_utc"] >= start
        if end is not None:
            mask &= baseline["timestamp_utc"] <= end
        replaced = int(mask.sum())
        removed_rows += replaced
        baseline = baseline.loc[~mask].copy()
        receipt_audit.append(
            {
                "receipt_id": receipt.get("receipt_id"),
                "payload_sha256": receipt.get("payload_sha256"),
                "provider": receipt.get("provider"),
                "normalized_rows": int(normalized["valid_rows"]),
                "baseline_rows_replaced": replaced,
                "covered_mmsi": sorted(covered),
                "coverage_start_utc": receipt.get("coverage_start_utc"),
                "coverage_end_utc": receipt.get("coverage_end_utc"),
            }
        )

    combined = pd.concat([baseline, *incoming_frames], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(["mmsi", "timestamp_utc"], keep="last")
    combined = combined.sort_values(["mmsi", "timestamp_utc"]).reset_index(drop=True)
    combined["timestamp_utc"] = combined["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    merged_path = version_root / "ais/ais_normalized.csv"
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(merged_path, index=False)
    audit = {
        "schema": "espada.ais-evidence-merge.v1",
        "baseline_file": baseline_path.relative_to(run_root).as_posix(),
        "baseline_rows": baseline_rows,
        "baseline_rows_replaced": removed_rows,
        "provider_rows_added": sum(len(frame) for frame in incoming_frames),
        "merged_rows": len(combined),
        "receipts": receipt_audit,
        "rule": "Admitted provider positions replace baseline rows for the same MMSI inside the declared coverage interval; all other baseline evidence is retained.",
    }
    _atomic_json(version_root / "ais/merge_audit.json", audit)
    return merged_path, audit


def run_live_reanalysis(run_root: Path) -> dict[str, Any]:
    """Rerun the frozen ranker with admitted direct AIS in a new immutable version."""
    run_root = Path(run_root).resolve()
    readiness = load_live_reanalysis(run_root)
    if readiness.get("status") == "COMPLETE":
        intake = load_evidence_intake(run_root)
        readiness = {
            "status": "READY" if intake.get("status") == "REANALYSIS_READY" else "NOT_READY",
            "message": intake.get("message"),
            "eligible_receipts": [
                item
                for item in intake.get("receipts", [])
                if item.get("admission_status") == "ADMITTED"
                and intake.get("blocking_gate") in list(item.get("resolves") or [])
            ],
        }
    if readiness.get("status") != "READY":
        raise RuntimeError(str(readiness.get("message") or "Reanalysis is not ready"))
    receipts = [
        item
        for item in readiness.get("eligible_receipts", [])
        if item.get("request_id") == "REQ-AIS-DIRECT"
    ]
    input_digest = _input_digest(run_root, receipts)
    for result_path in _result_files(run_root):
        existing = _read_json(result_path)
        if existing.get("input_digest_sha256") == input_digest and existing.get("status") == "COMPLETE":
            return {
                **existing,
                "reused_existing_version": True,
                "result_path": result_path,
                "ranking_chart_path": result_path.parent / "ranking/candidate_ranking.png",
                "attribution_map_path": result_path.parent / "ranking/attribution_map.png",
                "merged_ais_path": result_path.parent / "ais/ais_normalized.csv",
            }

    existing_versions = [int(path.parent.name[1:]) for path in _result_files(run_root)]
    version_number = max(existing_versions, default=0) + 1
    version = f"v{version_number:03d}"
    version_root = run_root / "reanalysis" / version
    merged_ais, merge_audit = _merge_direct_ais(run_root, version_root, receipts)
    ranking_dir = version_root / "ranking"
    ranking = write_attribution_outputs(
        ranking_dir,
        merged_ais,
        run_root / "attribution/drift/reverse_endpoints.npz",
        run_root / "attribution/drift/release_estimate.json",
        run_root / "attribution/drift/forward_particles.npz",
    )
    candidates = [item for item in ranking.get("candidates", []) if isinstance(item, dict)]
    rerun = {
        "candidate_count": len(candidates),
        "top_candidate": candidates[0] if candidates else None,
        "candidates": candidates[:12],
        **_decision(candidates),
    }
    baseline = _baseline(run_root)
    baseline_top = baseline.get("top_candidate") or {}
    rerun_top = rerun.get("top_candidate") or {}
    result = {
        "schema": "espada.live-reanalysis-result.v1",
        "status": "COMPLETE",
        "version": version,
        "completed_at_utc": _utc_now(),
        "input_digest_sha256": input_digest,
        "used_receipts": [
            {
                "receipt_id": item.get("receipt_id"),
                "provider": item.get("provider"),
                "payload_sha256": item.get("payload_sha256"),
            }
            for item in receipts
        ],
        "baseline": baseline,
        "rerun": rerun,
        "comparison": {
            "top_candidate_changed": baseline_top.get("mmsi") != rerun_top.get("mmsi"),
            "decision_changed": baseline.get("decision") != rerun.get("decision"),
            "score_margin_change": float(rerun.get("score_margin", 0.0)) - float(baseline.get("score_margin", 0.0)),
            "baseline_top_mmsi": baseline_top.get("mmsi"),
            "rerun_top_mmsi": rerun_top.get("mmsi"),
        },
        "merge_audit": merge_audit,
        "original_attribution_unchanged": True,
        "external_side_effects": False,
        "claim_boundary": (
            "This is a versioned investigative rerun. It does not overwrite the original result, "
            "convert a score into guilt probability or authorize an enforcement action."
        ),
    }
    result_path = version_root / "reanalysis_result.json"
    _atomic_json(result_path, result)
    return {
        **_public_summary(result),
        "result_path": result_path,
        "ranking_chart_path": ranking_dir / "candidate_ranking.png",
        "attribution_map_path": ranking_dir / "attribution_map.png",
        "merged_ais_path": merged_ais,
    }
