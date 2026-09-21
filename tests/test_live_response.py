from __future__ import annotations

import json
from pathlib import Path

from espada.live_response import build_live_response_package


def test_response_package_hashes_the_case_and_preserves_safe_abstention(tmp_path: Path) -> None:
    required = [
        "input/sentinel1_vv.tif",
        "input/sentinel1_subset_status.json",
        "segmentation/sar_result.json",
        "segmentation/physics_screen.json",
        "review/approved_slick.geojson",
        "attribution/environment/environment.json",
        "attribution/drift/release_estimate.json",
        "attribution/origin_zone.geojson",
        "attribution/ais/ais_normalized.csv",
        "attribution/ais/ais_quality.json",
        "attribution/ranking/candidates.json",
        "attribution/candidate_tracks.geojson",
    ]
    optional = [
        "segmentation/sar_segmentation_overview.png",
        "attribution/drift/slick_reverse_analysis.png",
        "attribution/ranking/candidate_ranking.png",
        "attribution/ranking/attribution_map.png",
    ]
    for relative in required + optional:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"verified artifact: {relative}".encode())

    attribution = {
        "status": "COMPLETE",
        "decision": "ABSTAIN_INSUFFICIENT_EVIDENCE",
        "candidate_count": 2,
        "score_margin": 0.0,
        "release_time_utc": "2026-09-01T00:00:00Z",
        "observation_time_utc": "2026-09-01T12:00:00Z",
        "credible_radius_90_km": 4.2,
        "estimated_origin": [103.8, 1.2],
        "candidates": [
            {"rank": 1, "mmsi": "111000111", "total_score": 0.72, "forward_error_km": 2.1, "data_quality": 0.3},
            {"rank": 2, "mmsi": "222000222", "total_score": 0.72, "forward_error_km": 2.6, "data_quality": 0.3},
        ],
    }
    result = build_live_response_package(
        tmp_path,
        analysis={"scene_id": "S1-CASE-001"},
        review={"status": "APPROVED", "reviewed_at_utc": "2026-09-01T13:00:00Z", "assumed_age_hours": 12},
        attribution=attribution,
        sources={"ais": {"provider": "GFW", "status": "PASS"}},
    )

    assert result["status"] == "PASS"
    assert result["response_status"] == "SAFE_ABSTENTION_READY"
    assert result["verified_files"] == 16
    assert result["required_files"] == 12
    assert result["missing_required_files"] == 0
    assert len(result["chain_digest_sha256"]) == 64
    manifest = json.loads(result["manifest_path"].read_text(encoding="utf-8"))
    assert manifest["integrity_status"] == "VERIFIED"
    assert all(item["status"] == "VERIFIED" for item in manifest["artifacts"])
    dossier = result["dossier_path"].read_text(encoding="utf-8")
    assert "No vessel nominated." in dossier
    assert "CLAIM BOUNDARY" in dossier
