from __future__ import annotations

import json
from pathlib import Path

import pytest

from espada.live_closure import load_case_closure, record_case_disposition


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _case(tmp_path: Path) -> Path:
    run_root = tmp_path / "S1-CLOSURE"
    _write_json(
        run_root / "attribution/ranking/candidates.json",
        {
            "status": "PASS",
            "candidates": [
                {
                    "rank": 1,
                    "mmsi": "111000111",
                    "vessel_name": "ALPHA",
                    "total_score": 0.76,
                    "data_quality": 0.90,
                    "forward_error_km": 2.0,
                },
                {
                    "rank": 2,
                    "mmsi": "222000222",
                    "vessel_name": "BRAVO",
                    "total_score": 0.74,
                    "data_quality": 0.88,
                    "forward_error_km": 2.2,
                },
            ],
        },
    )
    return run_root


def _submission(disposition: str, version: str = "BASELINE") -> dict[str, object]:
    return {
        "selected_version": version,
        "disposition": disposition,
        "reviewer_role": "Senior maritime analyst",
        "rationale": "The recorded evidence and safety gates support this workflow disposition.",
        "acknowledged": True,
    }


def test_closure_records_append_only_disposition_chain(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    ready = load_case_closure(run_root)
    assert ready["status"] == "READY"
    assert ready["latest_version"] == "BASELINE"
    assert ready["versions"][0]["decision"] == "ABSTAIN_INSUFFICIENT_EVIDENCE"

    closed = record_case_disposition(run_root, _submission("CLOSE_INCONCLUSIVE"))
    assert closed["status"] == "RECORDED"
    assert closed["case_state"] == "CLOSED_INCONCLUSIVE"
    first_hash = closed["current_disposition"]["event_sha256"]
    assert closed["current_disposition"]["previous_event_sha256"] is None

    reopened = record_case_disposition(
        run_root, _submission("KEEP_OPEN_ACQUIRE_EVIDENCE")
    )
    assert reopened["case_state"] == "OPEN"
    assert reopened["current_disposition"]["previous_event_sha256"] == first_hash
    assert reopened["event_count"] == 2
    assert len(list((run_root / "closure/events").glob("CLS-*.json"))) == 2


def test_new_evidence_supersedes_old_closure_and_requires_latest_version(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    record_case_disposition(run_root, _submission("CLOSE_INCONCLUSIVE"))
    _write_json(
        run_root / "reanalysis/v001/reanalysis_result.json",
        {
            "status": "COMPLETE",
            "version": "v001",
            "completed_at_utc": "2026-09-22T12:00:00Z",
            "input_digest_sha256": "b" * 64,
            "rerun": {
                "decision": "LIMITED_SHORTLIST",
                "candidate_count": 2,
                "score_margin": 0.14,
                "top_candidate": {
                    "mmsi": "222000222",
                    "vessel_name": "BRAVO",
                    "total_score": 0.86,
                    "data_quality": 0.94,
                    "forward_error_km": 1.8,
                },
                "gates": [],
            },
        },
    )

    superseded = load_case_closure(run_root)
    assert superseded["status"] == "SUPERSEDED"
    assert superseded["latest_version"] == "v001"
    assert superseded["versions"][-1]["change"]["top_candidate_changed"] is True

    with pytest.raises(ValueError, match="latest evidence version"):
        record_case_disposition(run_root, _submission("REFER_AUTHORIZED_REVIEW"))
    referred = record_case_disposition(
        run_root, _submission("REFER_AUTHORIZED_REVIEW", "v001")
    )
    assert referred["status"] == "RECORDED"
    assert referred["case_state"] == "REFERRED_FOR_REVIEW"


def test_closure_requires_rationale_and_boundary_confirmation(tmp_path: Path) -> None:
    run_root = _case(tmp_path)
    missing_confirmation = _submission("CLOSE_INCONCLUSIVE")
    missing_confirmation["acknowledged"] = False
    with pytest.raises(ValueError, match="claim boundary"):
        record_case_disposition(run_root, missing_confirmation)

    short_rationale = _submission("CLOSE_INCONCLUSIVE")
    short_rationale["rationale"] = "Too short"
    with pytest.raises(ValueError, match="Rationale"):
        record_case_disposition(run_root, short_rationale)
