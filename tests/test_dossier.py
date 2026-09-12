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
