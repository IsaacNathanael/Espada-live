from __future__ import annotations

import json
from pathlib import Path

from espada.external_validation import evaluate_registered_case


def _write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_missing_registered_source_is_coverage_failure_and_safe_abstention(tmp_path: Path) -> None:
    registry = _write(
        tmp_path / "registry.json",
        {
            "case_id": "case",
            "evaluation_class": "holdout",
            "sealed_target": {"vessel_name": "Source", "mmsi": "999"},
            "incident": {"release_longitude": 9.0, "release_latitude": 43.0},
            "acceptance": {"target_rank_at_most": 3, "origin_error_km_at_most": 15},
        },
    )
    ranking = _write(
        tmp_path / "ranking.json",
        {"candidate_count": 1, "candidates": [{"mmsi": "111", "rank": 1}]},
    )
    release = _write(
        tmp_path / "release.json",
        {"estimated_origin": {"longitude": 9.01, "latitude": 43.0}, "assumed_age_hours": 10},
    )
    decision = _write(tmp_path / "decision.json", {"decision": "ABSTAIN_INSUFFICIENT_EVIDENCE"})
    result = evaluate_registered_case(
        registry, ranking, release, decision, tmp_path / "result.json"
    )
    assert result["status"] == "FAIL"
    assert result["failure_class"] == "INPUT_EVIDENCE_COVERAGE"
    assert result["checks"]["reverse_origin_error"] is True
    assert result["checks"]["evidence_gate_safety"] is True
    assert result["source_recovery"]["rank"] is None


def test_registered_source_can_pass(tmp_path: Path) -> None:
    registry = _write(
        tmp_path / "registry.json",
        {
            "case_id": "case",
            "evaluation_class": "holdout",
            "sealed_target": {"vessel_name": "Source", "mmsi": "999"},
            "incident": {"release_longitude": 9.0, "release_latitude": 43.0},
            "acceptance": {"target_rank_at_most": 3, "origin_error_km_at_most": 15},
        },
    )
    ranking = _write(
        tmp_path / "ranking.json",
        {"candidate_count": 2, "candidates": [{"mmsi": "999", "rank": 1}]},
    )
    release = _write(
        tmp_path / "release.json",
        {"estimated_origin": {"longitude": 9.01, "latitude": 43.0}, "assumed_age_hours": 10},
    )
    decision = _write(tmp_path / "decision.json", {"decision": "PRIORITY_ANALYST_REVIEW"})
    result = evaluate_registered_case(
        registry, ranking, release, decision, tmp_path / "result.json"
    )
    assert result["status"] == "PASS"
    assert result["source_recovery"]["rank"] == 1
