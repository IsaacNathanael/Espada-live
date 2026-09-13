from __future__ import annotations

import json
from pathlib import Path

from espada.decision import evaluate_case_decision


def _write_case(root: Path, *, aligned: bool = True, separated: bool = True, sensitivity: bool = True, time_search: bool = True, shape_error: float | None = 0.5) -> None:
    (root / "ranking").mkdir(parents=True)
    (root / "sensitivity").mkdir(parents=True)
    (root / "time_search").mkdir(parents=True)
    (root / "case_alignment.json").write_text(json.dumps({"status": "PASS" if aligned else "FAIL"}), encoding="utf-8")
    second = 0.40 if separated else 0.76
    candidates = [
        {"rank": 1, "mmsi": "CAND-1", "total_score": 0.78, "forward_error_km": 0.74, "forward_shape_error_km": shape_error, "data_quality": 0.49},
        {"rank": 2, "mmsi": "CAND-2", "total_score": second, "forward_error_km": 2.0, "forward_shape_error_km": 1.0, "data_quality": 0.8},
    ]
    if shape_error is None:
        candidates[0].pop("forward_shape_error_km")
    (root / "ranking" / "candidates.json").write_text(json.dumps({"status": "PASS", "candidate_count": 2, "top_candidate": candidates[0], "candidates": candidates}), encoding="utf-8")
    if sensitivity:
        (root / "sensitivity" / "sensitivity_report.json").write_text(json.dumps({"top_3_rate": 1.0, "worst_rank": 2}), encoding="utf-8")
    if time_search:
        (root / "time_search" / "release_time_search.json").write_text(json.dumps({"top_candidate_id": "CAND-1", "top_candidate_rank_1_rate": 0.8, "top_candidate_top_3_rate": 1.0}), encoding="utf-8")


def test_decision_escalates_strong_robust_shortlist(tmp_path: Path) -> None:
    _write_case(tmp_path)
    result = evaluate_case_decision(tmp_path, tmp_path / "decision")
    assert result["decision"] == "PRIORITY_ANALYST_REVIEW"
    assert result["candidate"]["score_margin"] == 0.38
    assert (tmp_path / "decision" / "decision_gate.html").exists()


def test_decision_abstains_when_candidates_are_not_separated(tmp_path: Path) -> None:
    _write_case(tmp_path, separated=False)
    result = evaluate_case_decision(tmp_path, tmp_path / "decision")
    assert result["decision"] == "ABSTAIN_INSUFFICIENT_EVIDENCE"
    assert any(item["gate"] == "candidate_separation" and item["status"] == "STOP" for item in result["checks"])


def test_decision_limits_case_without_sensitivity_run(tmp_path: Path) -> None:
    _write_case(tmp_path, sensitivity=False, time_search=False)
    result = evaluate_case_decision(tmp_path, tmp_path / "decision")
    assert result["decision"] == "LIMITED_SHORTLIST"


def test_decision_abstains_when_shape_replay_is_poor(tmp_path: Path) -> None:
    _write_case(tmp_path, shape_error=12.0)
    result = evaluate_case_decision(tmp_path, tmp_path / "decision")
    assert result["decision"] == "ABSTAIN_INSUFFICIENT_EVIDENCE"
    assert any(
        item["gate"] == "forward_shape_replay" and item["status"] == "STOP"
        for item in result["checks"]
    )


def test_decision_abstains_when_shape_metric_is_missing(tmp_path: Path) -> None:
    _write_case(tmp_path, shape_error=None)
    result = evaluate_case_decision(tmp_path, tmp_path / "decision")
    assert result["decision"] == "ABSTAIN_INSUFFICIENT_EVIDENCE"
