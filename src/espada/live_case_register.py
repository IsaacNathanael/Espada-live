from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _case_stage(case_dir: Path, sar_result: dict[str, Any]) -> str:
    if (case_dir / "response/evidence_dossier.html").is_file():
        return "EVIDENCE_PACKAGE_READY"
    if (case_dir / "attribution/ranking/candidates.json").is_file():
        return "ATTRIBUTION_COMPLETE"
    if (case_dir / "attribution/drift/release_estimate.json").is_file():
        return "RECONSTRUCTION_COMPLETE"
    if (case_dir / "review/approved_slick.geojson").is_file():
        return "ANALYST_APPROVED"
    status = str(sar_result.get("status") or "").upper()
    if status == "NO_DETECTION":
        return "CLOSED_NO_DETECTION"
    if status == "REVIEW_REQUIRED":
        return "REVIEW_REQUIRED"
    if sar_result:
        return "DETECTION_COMPLETE"
    return "INGESTED"


def build_case_register(analysis_root: Path) -> dict[str, Any]:
    analysis_root = Path(analysis_root).resolve()
    cases: list[dict[str, Any]] = []
    if analysis_root.is_dir():
        directories = [path for path in analysis_root.iterdir() if path.is_dir()]
    else:
        directories = []

    for case_dir in directories:
        metadata = _read_json(case_dir / "input/sentinel1_subset_status.json")
        sar_result = _read_json(case_dir / "segmentation/sar_result.json")
        summary = _read_json(case_dir / "response/response_summary.json")
        manifest = _read_json(case_dir / "response/evidence_manifest.json")
        verification = _read_json(case_dir / "response/integrity_verification.json")
        ranking = _read_json(case_dir / "attribution/ranking/candidates.json")
        candidates = ranking.get("candidates") if isinstance(ranking.get("candidates"), list) else []
        if not candidates and isinstance(summary.get("top_candidate"), dict):
            candidates = [summary["top_candidate"]]
        top = candidates[0] if candidates else {}
        canonical_id = case_dir.name
        recorded_id = str(metadata.get("scene_id") or canonical_id)
        provenance_warnings: list[str] = []
        if recorded_id != canonical_id:
            provenance_warnings.append("Directory identifier differs from recorded Sentinel metadata.")
        file_paths = [path for path in case_dir.rglob("*") if path.is_file()]
        latest_path = max(file_paths, key=lambda path: path.stat().st_mtime) if file_paths else case_dir
        stage = _case_stage(case_dir, sar_result)
        response_status = str(summary.get("response_status") or "NOT_PACKAGED")
        integrity_status = str(
            verification.get("integrity_status")
            or manifest.get("integrity_status")
            or ("NOT_VERIFIED" if stage != "EVIDENCE_PACKAGE_READY" else "UNAVAILABLE")
        )
        cases.append(
            {
                "scene_id": canonical_id,
                "recorded_scene_id": recorded_id,
                "stage": stage,
                "acquisition_time_utc": metadata.get("acquisition_time_utc")
                or sar_result.get("observation_time_utc"),
                "updated_at_utc": _iso_mtime(latest_path),
                "platform": str(metadata.get("platform") or "Sentinel-1"),
                "polarization": metadata.get("polarization"),
                "candidate_pixel_fraction": sar_result.get("detected_pixel_fraction"),
                "operational_decision": summary.get("operational_decision"),
                "response_status": response_status,
                "permitted_action": summary.get("permitted_action"),
                "candidate_count": int(summary.get("candidate_count") or len(candidates)),
                "top_score": top.get("total_score") if isinstance(top, dict) else None,
                "verified_files": int(manifest.get("verified_files") or 0),
                "required_files": int(manifest.get("required_files") or 0),
                "missing_required_files": int(manifest.get("missing_required_files") or 0),
                "chain_digest_sha256": manifest.get("chain_digest_sha256"),
                "integrity_status": integrity_status,
                "last_verified_at_utc": verification.get("verified_at_utc"),
                "artifact_count": len(file_paths),
                "provenance_warnings": provenance_warnings,
                "milestones": {
                    "observed": (case_dir / "input/sentinel1_vv.tif").is_file(),
                    "detected": bool(sar_result),
                    "reviewed": (case_dir / "review/approved_slick.geojson").is_file(),
                    "reconstructed": (case_dir / "attribution/drift/release_estimate.json").is_file(),
                    "attributed": (case_dir / "attribution/ranking/candidates.json").is_file(),
                    "packaged": (case_dir / "response/evidence_dossier.html").is_file(),
                },
                "paths": {
                    "dossier": "response/evidence_dossier.html"
                    if (case_dir / "response/evidence_dossier.html").is_file()
                    else None,
                    "manifest": "response/evidence_manifest.json"
                    if (case_dir / "response/evidence_manifest.json").is_file()
                    else None,
                    "summary": "response/response_summary.json"
                    if (case_dir / "response/response_summary.json").is_file()
                    else None,
                    "sar_diagnostic": "segmentation/sar_segmentation_overview.png"
                    if (case_dir / "segmentation/sar_segmentation_overview.png").is_file()
                    else None,
                },
            }
        )

    cases.sort(key=lambda item: str(item.get("updated_at_utc") or ""), reverse=True)
    return {
        "status": "PASS",
        "generated_at_utc": _utc_now(),
        "case_count": len(cases),
        "sealed_count": sum(case["stage"] == "EVIDENCE_PACKAGE_READY" for case in cases),
        "review_required_count": sum(case["stage"] == "REVIEW_REQUIRED" for case in cases),
        "integrity_verified_count": sum(case["integrity_status"] == "VERIFIED" for case in cases),
        "provenance_warning_count": sum(bool(case["provenance_warnings"]) for case in cases),
        "cases": cases,
    }


def verify_case_integrity(analysis_root: Path, scene_id: str) -> dict[str, Any]:
    analysis_root = Path(analysis_root).resolve()
    requested = str(scene_id or "").strip()
    safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in requested)
    if not requested or safe_id != requested:
        raise ValueError("Invalid case identifier")
    case_dir = (analysis_root / safe_id).resolve()
    if analysis_root not in case_dir.parents or not case_dir.is_dir():
        raise FileNotFoundError("The requested case does not exist")
    manifest_path = case_dir / "response/evidence_manifest.json"
    manifest = _read_json(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("This case has no evidence manifest to verify")

    records: list[dict[str, Any]] = []
    chain_rows: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("relative_path") or "")
        artifact_path = (case_dir / relative).resolve()
        if case_dir not in artifact_path.parents:
            raise RuntimeError("Evidence manifest contains an unsafe artifact path")
        expected = item.get("sha256")
        computed = _sha256(artifact_path)
        if computed is None:
            status = "MISSING"
        elif expected == computed:
            status = "MATCH"
        else:
            status = "MODIFIED"
        if computed:
            chain_rows.append(f"{item.get('id')}|{relative}|{computed}")
        records.append(
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "required": bool(item.get("required")),
                "relative_path": relative,
                "status": status,
                "expected_sha256": expected,
                "computed_sha256": computed,
            }
        )

    computed_chain = hashlib.sha256("\n".join(chain_rows).encode("utf-8")).hexdigest()
    expected_chain = manifest.get("chain_digest_sha256")
    missing_required = sum(
        record["required"] and record["status"] == "MISSING" for record in records
    )
    modified = sum(record["status"] == "MODIFIED" for record in records)
    matched = sum(record["status"] == "MATCH" for record in records)
    if modified or (not missing_required and computed_chain != expected_chain):
        integrity_status = "TAMPER_DETECTED"
    elif missing_required:
        integrity_status = "INCOMPLETE"
    else:
        integrity_status = "VERIFIED"
    result = {
        "status": "PASS",
        "scene_id": safe_id,
        "integrity_status": integrity_status,
        "verified_at_utc": _utc_now(),
        "matched_files": matched,
        "modified_files": modified,
        "missing_required_files": missing_required,
        "expected_chain_digest_sha256": expected_chain,
        "computed_chain_digest_sha256": computed_chain,
        "artifacts": records,
    }
    output_path = case_dir / "response/integrity_verification.json"
    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(output_path)
    return result
