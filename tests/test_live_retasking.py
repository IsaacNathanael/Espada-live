from __future__ import annotations

import csv
import json
from pathlib import Path

from espada.live_retasking import build_live_retasking_plan, load_live_retasking_plan


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_retasking_plan_turns_a_tie_into_scoped_unsigned_requests(tmp_path: Path) -> None:
    run_root = tmp_path / "S1-CASE"
    _write_json(
        run_root / "response/response_summary.json",
        {
            "scene_id": "S1-CASE",
            "operational_decision": "ABSTAIN_INSUFFICIENT_EVIDENCE",
            "rationale": "Two candidates are tied; no vessel is nominated.",
            "release_time_utc": "2026-09-04T06:47:45Z",
            "observation_time_utc": "2026-09-04T22:47:45Z",
        },
    )
    candidates = [
        {
            "rank": 1,
            "mmsi": "111000111",
            "vessel_name": "UNKNOWN",
            "total_score": 0.91,
            "data_quality": 0.85,
            "forward_error_km": 2.5,
            "release_position_interpolated": True,
        },
        {
            "rank": 2,
            "mmsi": "222000222",
            "vessel_name": "UNKNOWN",
            "total_score": 0.91,
            "data_quality": 0.82,
            "forward_error_km": 2.8,
            "release_position_interpolated": True,
        },
    ]
    _write_json(run_root / "attribution/ranking/candidates.json", {"candidates": candidates})
    _write_json(
        run_root / "attribution/drift/release_estimate.json",
        {
            "release_time_utc": "2026-09-04T06:47:45Z",
            "observation_time_utc": "2026-09-04T22:47:45Z",
            "estimated_origin": {"longitude": 103.9, "latitude": 1.03},
            "credible_radius_90_km": 5.0,
        },
    )
    _write_json(
        run_root / "attribution/origin_zone.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[103.8, 1.0], [104.0, 1.0], [104.0, 1.1], [103.8, 1.0]]],
                    },
                }
            ],
        },
    )
    _write_json(
        run_root / "input/sentinel1_subset_status.json",
        {"bbox": [103.5, 0.9, 104.2, 1.4], "acquisition_time_utc": "2026-09-04T22:47:45Z"},
    )

    result = build_live_retasking_plan(run_root)

    assert result["status"] == "PASS"
    assert result["plan_status"] == "REQUEST_PACKAGE_READY"
    assert result["dispatch_status"] == "NOT_SENT"
    assert result["external_side_effects"] is False
    assert result["blocking_gate"] == "CANDIDATE_SEPARATION"
    assert result["tied_candidate_count"] == 2
    assert result["origin_zone_bbox_wgs84"] == [103.8, 1.0, 104.0, 1.1]
    assert result["release_window_utc"] == {
        "start": "2026-09-04T05:17:45Z",
        "centre": "2026-09-04T06:47:45Z",
        "end": "2026-09-04T08:17:45Z",
    }
    assert result["tasks"][0]["id"] == "REQ-AIS-DIRECT"
    assert result["tasks"][0]["request_scope"]["mmsi"] == ["111000111", "222000222"]
    assert result["tasks"][0]["dispatch_status"] == "DRAFT_NOT_SENT"
    assert len(result["plan_digest_sha256"]) == 64
    assert result["plan_path"].is_file()
    assert result["requests_csv_path"].is_file()

    recovered = load_live_retasking_plan(run_root)
    assert recovered["plan_digest_sha256"] == result["plan_digest_sha256"]
    with result["requests_csv_path"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == result["task_count"]
    assert rows[0]["dispatch_status"] == "DRAFT_NOT_SENT"
    assert "111000111" in rows[0]["mmsi"]
