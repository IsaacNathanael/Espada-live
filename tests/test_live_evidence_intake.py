from __future__ import annotations

import json
from pathlib import Path

import pytest

from espada.live_evidence_intake import (
    load_evidence_intake,
    review_evidence_return,
    stage_evidence_return,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _case(tmp_path: Path) -> Path:
    run_root = tmp_path / "S1-CASE"
    _write_json(
        run_root / "follow_up/evidence_acquisition_plan.json",
        {
            "schema": "espada.live-evidence-acquisition-plan.v1",
            "status": "PASS",
            "plan_status": "REQUEST_PACKAGE_READY",
            "plan_digest_sha256": "a" * 64,
            "blocking_gate": "CANDIDATE_SEPARATION",
            "task_count": 1,
            "tasks": [
                {
                    "id": "REQ-AIS-DIRECT",
                    "priority": "P1",
                    "evidence_type": "OBSERVED",
                    "title": "Request direct AIS around the release window",
                    "resolves": ["CANDIDATE_SEPARATION", "RELEASE_POSITION_INTERPOLATION"],
                    "request_scope": {
                        "start_utc": "2026-09-04T05:17:45Z",
                        "end_utc": "2026-09-04T08:17:45Z",
                        "bbox_wgs84": [103.8, 1.0, 104.0, 1.1],
                        "mmsi": ["111000111", "222000222"],
                    },
                }
            ],
        },
    )
    return run_root


def test_return_with_scope_gap_is_preserved_but_cannot_be_admitted(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    staged = stage_evidence_return(
        run_root,
        {
            "request_id": "REQ-AIS-DIRECT",
            "provider": "Coastal VTS",
            "source_reference": "VTS-2026-0904-A",
            "coverage_start_utc": "2026-09-04T05:17:45Z",
            "coverage_end_utc": "2026-09-04T08:17:45Z",
            "bbox_wgs84": [103.8, 1.0, 104.0, 1.1],
            "covered_mmsi": ["111000111"],
            "payload_text": "timestamp,mmsi,lon,lat\n2026-09-04T06:47:45Z,111000111,103.9,1.05",
        },
    )
    receipt = staged["staged_receipt"]
    assert receipt["validation_status"] == "GAPS"
    assert receipt["admission_status"] == "STAGED"
    assert receipt["attribution_decision_unchanged"] is True
    assert any(
        check["id"] == "IDENTIFIERS" and check["passed"] is False
        for check in receipt["validation_checks"]
    )
    assert (run_root / receipt["payload_file"]).is_file()
    with pytest.raises(RuntimeError, match="cannot be admitted"):
        review_evidence_return(run_root, receipt["receipt_id"], "ADMIT", "Scope reviewed; one MMSI is absent.")


def test_valid_return_can_be_admitted_without_changing_attribution(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    staged = stage_evidence_return(
        run_root,
        {
            "request_id": "REQ-AIS-DIRECT",
            "provider": "Coastal VTS",
            "source_reference": "VTS-2026-0904-B",
            "coverage_start_utc": "2026-09-04T05:00:00Z",
            "coverage_end_utc": "2026-09-04T08:30:00Z",
            "bbox_wgs84": [103.7, 0.9, 104.1, 1.2],
            "covered_mmsi": ["111000111", "222000222"],
            "payload_text": (
                "timestamp,mmsi,lon,lat\n"
                "2026-09-04T06:47:45Z,111000111,103.9,1.05\n"
                "2026-09-04T06:47:45Z,222000222,103.92,1.04"
            ),
        },
    )
    receipt = staged["staged_receipt"]
    assert receipt["validation_status"] == "PASS"
    assert len(receipt["payload_sha256"]) == 64

    admitted = review_evidence_return(
        run_root,
        receipt["receipt_id"],
        "ADMIT",
        "Provider, scope and payload were reviewed.",
    )
    assert admitted["status"] == "REANALYSIS_READY"
    assert admitted["reanalysis_eligible"] is True
    assert admitted["attribution_decision_unchanged"] is True
    assert admitted["admitted_count"] == 1
    recovered = load_evidence_intake(run_root)
    assert recovered["receipts"][0]["admission_status"] == "ADMITTED"
    assert recovered["receipts"][0]["analyst_note"] == "Provider, scope and payload were reviewed."


def test_duplicate_payload_is_rejected(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    submission = {
        "request_id": "REQ-AIS-DIRECT",
        "provider": "Coastal VTS",
        "source_reference": "VTS-2026-0904-C",
        "coverage_start_utc": "2026-09-04T05:00:00Z",
        "coverage_end_utc": "2026-09-04T08:30:00Z",
        "bbox_wgs84": [103.7, 0.9, 104.1, 1.2],
        "covered_mmsi": ["111000111", "222000222"],
        "payload_text": "same provider payload",
    }
    stage_evidence_return(run_root, submission)
    with pytest.raises(ValueError, match="already recorded"):
        stage_evidence_return(run_root, submission)


def test_tampered_payload_cannot_be_admitted(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    staged = stage_evidence_return(
        run_root,
        {
            "request_id": "REQ-AIS-DIRECT",
            "provider": "Coastal VTS",
            "source_reference": "VTS-2026-0904-D",
            "coverage_start_utc": "2026-09-04T05:00:00Z",
            "coverage_end_utc": "2026-09-04T08:30:00Z",
            "bbox_wgs84": [103.7, 0.9, 104.1, 1.2],
            "covered_mmsi": ["111000111", "222000222"],
            "payload_text": "provider-origin evidence",
        },
    )
    receipt = staged["staged_receipt"]
    (run_root / receipt["payload_file"]).write_text("altered evidence", encoding="utf-8")
    with pytest.raises(RuntimeError, match="recorded hash"):
        review_evidence_return(
            run_root,
            receipt["receipt_id"],
            "ADMIT",
            "Provider, scope and payload were reviewed.",
        )
