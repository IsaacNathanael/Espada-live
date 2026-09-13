from __future__ import annotations

import json
from pathlib import Path

import pytest

from espada.system_validation import build_system_scorecard


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _fixtures(root: Path, *, princess_abstains: bool = True) -> None:
    _write(
        root / "out/evaluation/evaluation_summary.json",
        {
            "status": "PASS",
            "config": {"cases": 24},
            "overall": {
                "top1_accuracy": 0.71,
                "top3_accuracy": 1.0,
                "median_origin_error_km": 3.7,
                "p90_origin_error_km": 7.5,
            },
            "acceptance": {"top3": True, "median_error": True},
        },
    )
    _write(
        root / "out/wakashio/evaluation/historical_evaluation.json",
        {"rank": 1, "candidate_count": 5, "answer": "Yes — MV Wakashio ranked #1 of 5."},
    )
    _write(
        root / "out/wakashio/ranking/candidates.json",
        {
            "top_candidate": {
                "total_score": 0.78,
                "forward_error_km": 0.74,
                "forward_shape_error_km": 0.51,
            }
        },
    )
    _write(
        root / "out/wakashio/decision/decision_gate.json",
        {
            "decision": "PRIORITY_ANALYST_REVIEW",
            "checks": [{"gate": "forward_shape_replay", "status": "PASS"}],
        },
    )
    _write(
        root / "out/incidents/princess_empress_2023/run_official/ranking/candidates.json",
        {
            "candidate_count": 52,
            "top_candidate": {
                "total_score": 0.88,
                "forward_shape_error_km": 6.54,
                "data_quality": 0.2,
            },
        },
    )
    _write(
        root / "out/incidents/princess_empress_2023/run_official/decision/decision_gate.json",
        {
            "decision": (
                "ABSTAIN_INSUFFICIENT_EVIDENCE" if princess_abstains else "PRIORITY_ANALYST_REVIEW"
            ),
            "checks": [
                {"gate": "forward_shape_replay", "status": "WARN"},
                {"gate": "track_data_quality", "status": "STOP" if princess_abstains else "PASS"},
            ],
        },
    )


def test_system_scorecard_passes_three_distinct_validation_layers(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    result = build_system_scorecard(tmp_path, tmp_path / "scorecard")
    assert result["status"] == "PASS"
    assert all(result["checks"].values())
    assert (tmp_path / "scorecard/system_scorecard.html").stat().st_size > 0
    assert "not an external blind trial" in result["claim_boundary"]


def test_system_scorecard_fails_if_unsafe_case_is_escalated(tmp_path: Path) -> None:
    _fixtures(tmp_path, princess_abstains=False)
    result = build_system_scorecard(tmp_path, tmp_path / "scorecard")
    assert result["status"] == "FAIL"
    assert result["checks"]["evidence_limited_abstention"] is False


def test_system_scorecard_requires_all_artifacts(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Required validation artifact"):
        build_system_scorecard(tmp_path, tmp_path / "scorecard")
