from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from espada.operations_dashboard import build_operations_dashboard


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_operations_dashboard_is_self_contained_and_interactive(tmp_path: Path) -> None:
    case = tmp_path / "out/external_validation/corsica_2018"
    run = tmp_path / "out/challenge"
    candidate = {
        "mmsi": "999000001", "vessel_name": "Blinded 999000001", "rank": 1,
        "total_score": .9, "presence_score": .8, "forward_consistency": .85,
        "forward_error_km": 1.0, "forward_shape_error_km": 1.2, "data_quality": .7,
        "best_match_time_utc": "2018-10-07T19:00:00Z", "silence_classification": "no_significant_gap",
    }
    _write(run / "ranking/candidates.json", {"candidate_count": 1, "candidates": [candidate]})
    _write(run / "decision/decision_gate.json", {"decision": "PRIORITY_ANALYST_REVIEW"})
    _write(run / "drift/release_estimate.json", {"observation_time_utc": "2018-10-08T05:00:00Z", "release_time_utc": "2018-10-07T19:00:00Z", "estimated_origin": {"longitude": 9.3, "latitude": 43.3}, "credible_radius_90_km": 4.0})
    _write(run / "synthetic_slick.geojson", {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[9.2,43.2],[9.4,43.2],[9.4,43.4],[9.2,43.2]]]}}]})
    _write(run / "challenge_result.json", {"status": "PASS", "outcome": "KNOWN_SOURCE_RECOVERED", "truth_reveal": {"source_id": "999000001"}, "measured_result": {"source_rank": 1, "candidate_count": 1}})
    _write(run / "scenario_used.json", {"name": "test challenge"})
    _write(case / "environment/environment.json", {"source": "test forcing", "samples": [{"time_utc": "2018-10-07T19:00:00Z", "wind_east_ms": 2, "wind_north_ms": 1, "current_east_ms": .1, "current_north_ms": .05}]})
    _write(case / "sar_input/sentinel1_subset_status.json", {"bbox": [9,43,10,44], "acquisition_time_utc": "2018-10-08T05:00:00Z"})
    _write(tmp_path / "out/digital_twin/digital_twin_summary.json", {"top_3_rate": .83})
    Image.new("L", (20, 20), 80).save(case / "sar_input/sentinel1_vv_quicklook.png")
    (run / "drift").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(run / "drift/reverse_endpoints.npz", lon=np.array([9.3,9.31]), lat=np.array([43.3,43.31]))
    (run / "ais").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"timestamp_utc": ["2018-10-07T19:00:00Z","2018-10-08T05:00:00Z"], "mmsi": ["999000001"]*2, "vessel_name": ["Blinded 999000001"]*2, "longitude": [9.3,9.35], "latitude": [43.3,43.31]}).to_csv(run / "ais/ais_normalized.csv", index=False)
    output = tmp_path / "out/operations_dashboard/index.html"
    result = build_operations_dashboard(tmp_path, output, dossier_href="evidence_dossier.html")
    page = output.read_text(encoding="utf-8")
    assert result["status"] == "PASS"
    assert "Run attribution" in page and "Candidate vessels" in page
    assert "Investigation workspace" in page
    assert "SCENARIO LAB" in page and "DATA PROVENANCE" in page
    assert 'id="labAge" type="range" min="0" max="4" step="1" value="2"' in page
    assert '"dossier":"evidence_dossier.html"' in page
    assert "data:image/png;base64," in page
    assert "function run()" in page and "function select(id)" in page
    assert "showCount=3" in page
    assert "CONTROLLED DIGITAL TWIN" in page
    assert "AWAITING ANALYSIS" in page
    assert "LEAK BEGINS" in page
    assert "Raw SAR" in page
    assert "Fictional scenario alias" in page
    assert "Co-located AIS reports" in page
    assert "Observed route span" in page
    assert 'class="hull"' in page
    assert (output.parent / "alignment_report.json").exists()
    alignment = json.loads((output.parent / "alignment_report.json").read_text())
    assert "sourceTrackReleaseMatch" in alignment
    assert result["source"] == "sealed challenge"
    assert "culprit" not in page.lower()
