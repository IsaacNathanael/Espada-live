from __future__ import annotations

import json
from pathlib import Path

from espada.judge_demo import build_judge_demo


def test_judge_demo_links_every_evidence_view(tmp_path: Path) -> None:
    scorecard = tmp_path / "out/system_validation/system_scorecard.json"
    scorecard.parent.mkdir(parents=True)
    scorecard.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    wakashio = tmp_path / "out/wakashio/evaluation/historical_evaluation.json"
    wakashio.parent.mkdir(parents=True)
    wakashio.write_text(
        json.dumps({"answer": "Yes — MV Wakashio ranked #1 of 5."}), encoding="utf-8"
    )
    output = tmp_path / "out/judge_demo/index.html"
    result = build_judge_demo(tmp_path, output)
    document = output.read_text(encoding="utf-8")
    assert result["status"] == "PASS"
    assert result["views"] == 6
    assert "System scorecard" in document
    assert "Forensic replay" in document
    assert "Safe abstention" in document
    assert "Interactive laboratory" in document
    assert all(token in document for token in ("href=", "READY", "not guilt probabilities"))
