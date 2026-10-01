"""Guardrails for the portable historical replay supplied to judges."""

from __future__ import annotations

import json
from pathlib import Path

from espada.live_operations_server import LiveOperationsHandler


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "operator" / "historical_case"


def test_historical_case_matches_saved_wakashio_evidence() -> None:
    published = json.loads((SITE / "case-data.json").read_text(encoding="utf-8"))
    saved_ranking = json.loads(
        (ROOT / "out/wakashio/ranking/candidates.json").read_text(encoding="utf-8")
    )
    saved_slick = json.loads(
        (ROOT / "out/wakashio/prepared/slick.geojson").read_text(encoding="utf-8")
    )
    assert published["mode"] == "RECORDED HISTORICAL REPLAY — NOT LIVE"
    assert published["slick"] == saved_slick
    assert published["provider"]["rows"] == 72
    assert sum(len(vessel["positions"]) for vessel in published["vessels"]) == 72
    assert len(published["vessels"]) == saved_ranking["candidate_count"] == 5
    assert published["ranking"][0]["mmsi"] == published["source_vessel"]["mmsi"]
    assert published["ranking"][0]["score"] == saved_ranking["top_candidate"]["total_score"]
    assert published["slick_source"]["field_validation"] == "Not yet field validated"


def test_historical_case_is_separate_from_live_api_and_uses_all_five_pages() -> None:
    script = (SITE / "case.js").read_text(encoding="utf-8")
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
    assert "time >= data.observation_time_utc" in script
    assert "No recent AIS report" in script
    for filename in ("index.html", "detection.html", "investigation.html", "cases.html", "case-file.html"):
        page = (SITE / filename).read_text(encoding="utf-8")
        assert 'src="case-data.js"' in page
        assert 'src="case.js"' in page
        assert LiveOperationsHandler._static_route_allowed(f"/operator/historical_case/{filename}")
    assert not LiveOperationsHandler._static_route_allowed("/operator/historical_case/../.env")
    assert (SITE / "assets/slick_reverse_analysis.png").is_file()
