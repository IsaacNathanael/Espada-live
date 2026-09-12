import json
from pathlib import Path

from espada.dossier import generate_evidence_dossier


def test_dossier_keeps_ranking_and_safety_language(tmp_path: Path) -> None:
    case = tmp_path / "case"
    for name in ("drift", "ais", "ranking"):
        (case / name).mkdir(parents=True)
    (case / "case_alignment.json").write_text(
        json.dumps({"status": "PASS", "checks": {"sources_align": True}}),
        encoding="utf-8",
    )
    (case / "ranking" / "candidates.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "candidate_count": 1,
                "top_candidate": {
                    "rank": 1,
                    "mmsi": "419000123",
                    "vessel_name": "MV TEST",
                    "total_score": 0.9,
                    "presence_score": 0.8,
                    "forward_error_km": 1.2,
                    "data_quality": 1.0,
                },
                "candidates": [],
            }
        ),
        encoding="utf-8",
    )

    result = generate_evidence_dossier(case, tmp_path / "dossier")

    page = Path(result["dossier"]).read_text(encoding="utf-8")
    bundle = json.loads(Path(result["evidence_bundle"]).read_text(encoding="utf-8"))
    assert result["case_status"] == "READY_FOR_ANALYST_REVIEW"
    assert bundle["ranking"]["top_candidate"]["mmsi"] == "419000123"
    assert "not a finding" in page
    assert "MV TEST" in page
    assert "Coverage-aware AIS silence test" in page
    assert "Silence classification never increases" in page


def test_dossier_includes_historical_proof_and_integrity(tmp_path: Path) -> None:
    case = tmp_path / "case"
    for name in ("drift", "ais", "ranking", "evaluation", "sensitivity", "decision"):
        (case / name).mkdir(parents=True)
    (case / "case_alignment.json").write_text(
        json.dumps({"status": "PASS", "checks": {"sources_align": True}}),
        encoding="utf-8",
    )
    (case / "ranking" / "candidates.json").write_text(
        json.dumps({"status": "PASS", "candidate_count": 5, "top_candidate": {"mmsi": "CAND-1", "total_score": 0.77}, "candidates": []}),
        encoding="utf-8",
    )
    (case / "evaluation" / "historical_evaluation.json").write_text(
        json.dumps({"answer": "Yes — MV Wakashio ranked #1 of 5.", "rank": 1, "candidate_count": 5, "comparative_score": 0.77, "forward_error_km": 0.74, "target": {"vessel_name": "MV Wakashio", "imo": "9337119"}}),
        encoding="utf-8",
    )
    (case / "sensitivity" / "sensitivity_report.json").write_text(
        json.dumps({"verdict": "ROBUST SHORTLIST", "top_1_rate": 0.69, "top_3_rate": 1.0, "worst_rank": 2}),
        encoding="utf-8",
    )
    (case / "decision" / "decision_gate.json").write_text(
        json.dumps({"decision": "PRIORITY_ANALYST_REVIEW", "recommended_action": "Escalate to a human investigator.", "checks": [{"gate": "incident_alignment", "status": "PASS"}]}),
        encoding="utf-8",
    )

    result = generate_evidence_dossier(case, tmp_path / "dossier")

    page = Path(result["dossier"]).read_text(encoding="utf-8")
    bundle = json.loads(Path(result["evidence_bundle"]).read_text(encoding="utf-8"))
    assert result["sensitivity_verdict"] == "ROBUST SHORTLIST"
    assert "MV Wakashio ranked #1 of 5" in page
    assert "ROBUST SHORTLIST" in page
    assert "Evidence integrity register" in page
    assert "PRIORITY ANALYST REVIEW" in page
    assert len(bundle["artifact_integrity"]["candidate_ranking"]["sha256"]) == 64
