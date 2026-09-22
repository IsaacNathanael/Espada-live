from __future__ import annotations

import json
from pathlib import Path

from espada.live_case_register import build_case_register, verify_case_integrity
from espada.live_response import build_live_response_package


def _write_artifacts(case_dir: Path) -> None:
    paths = [
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
        "segmentation/sar_segmentation_overview.png",
        "attribution/drift/slick_reverse_analysis.png",
        "attribution/ranking/candidate_ranking.png",
        "attribution/ranking/attribution_map.png",
    ]
    for relative in paths:
        path = case_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"artifact {relative}", encoding="utf-8")


def test_case_register_tracks_incomplete_cases_and_detects_file_changes(tmp_path: Path) -> None:
    analysis_root = tmp_path / "analysis"
    sealed = analysis_root / "S1-SEALED"
    _write_artifacts(sealed)
    (sealed / "input/sentinel1_subset_status.json").write_text(
        json.dumps({"scene_id": "S1-SEALED", "acquisition_time_utc": "2026-09-01T00:00:00Z", "polarization": "VV"}),
        encoding="utf-8",
    )
    build_live_response_package(
        sealed,
        analysis={"scene_id": "S1-SEALED"},
        review={"status": "APPROVED"},
        attribution={
            "status": "COMPLETE",
            "decision": "ABSTAIN_INSUFFICIENT_EVIDENCE",
            "candidate_count": 1,
            "candidates": [{"rank": 1, "mmsi": "123", "total_score": 0.5}],
        },
        sources={},
    )

    incomplete = analysis_root / "S1-INCOMPLETE"
    (incomplete / "input").mkdir(parents=True)
    (incomplete / "segmentation").mkdir()
    (incomplete / "input/sentinel1_vv.tif").write_bytes(b"sar")
    (incomplete / "input/sentinel1_subset_status.json").write_text(
        json.dumps({"scene_id": "DIFFERENT-ID", "acquisition_time_utc": "2026-09-02T00:00:00Z"}),
        encoding="utf-8",
    )
    (incomplete / "segmentation/sar_result.json").write_text(
        json.dumps({"status": "REVIEW_REQUIRED"}), encoding="utf-8"
    )

    register = build_case_register(analysis_root)
    assert register["case_count"] == 2
    assert register["sealed_count"] == 1
    assert register["review_required_count"] == 1
    assert register["provenance_warning_count"] == 1
    incomplete_record = next(case for case in register["cases"] if case["scene_id"] == "S1-INCOMPLETE")
    assert incomplete_record["stage"] == "REVIEW_REQUIRED"
    assert incomplete_record["provenance_warnings"]

    first = verify_case_integrity(analysis_root, "S1-SEALED")
    assert first["integrity_status"] == "VERIFIED"
    assert first["matched_files"] == 16
    (sealed / "segmentation/sar_result.json").write_text("changed", encoding="utf-8")
    second = verify_case_integrity(analysis_root, "S1-SEALED")
    assert second["integrity_status"] == "TAMPER_DETECTED"
    assert second["modified_files"] == 1
