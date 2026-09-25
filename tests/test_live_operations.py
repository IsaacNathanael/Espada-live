from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from espada.live_operations import LiveOperationsEngine, LiveRegion, freshness_label
from espada.live_operations_server import DEFAULT_LIVE_REGION


def _prepare_reviewable_case(
    engine: LiveOperationsEngine,
    *,
    scene_id: str = "SCENE-1",
    acquisition_time_utc: str = "2026-09-01T00:00:00Z",
) -> Path:
    run_root = engine.output_root / "analysis" / scene_id
    input_dir = run_root / "input"
    segmentation = run_root / "segmentation"
    input_dir.mkdir(parents=True, exist_ok=True)
    segmentation.mkdir(parents=True, exist_ok=True)
    (input_dir / "selected_scene_manifest.json").write_text(
        json.dumps({"scene_id": scene_id, "acquisition_time_utc": acquisition_time_utc}),
        encoding="utf-8",
    )
    (input_dir / "sentinel1_subset_status.json").write_text(
        json.dumps({"scene_id": scene_id, "acquisition_time_utc": acquisition_time_utc}),
        encoding="utf-8",
    )
    (segmentation / "sar_result.json").write_text(
        json.dumps({"status": "REVIEW_REQUIRED", "observation_time_utc": acquisition_time_utc}),
        encoding="utf-8",
    )
    physics = {
        "status": "PLAUSIBLE_DARK_SIGNATURE",
        "contrast_gate_passed": True,
        "wind_gate_passed": True,
    }
    (segmentation / "physics_screen.json").write_text(json.dumps(physics), encoding="utf-8")
    (segmentation / "slick_candidate.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "review_status": "pending",
                            "observation_time_utc": acquisition_time_utc,
                            "detection_confidence": 0.8,
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[71.0, 18.0], [71.1, 18.0], [71.1, 18.1], [71.0, 18.0]]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    engine._state["analysis"] = {
        "status": "REVIEW_REQUIRED",
        "scene_id": scene_id,
        "acquisition_time_utc": acquisition_time_utc,
        "provenance_verified": True,
        "physics_screen": physics,
    }
    return run_root


def test_freshness_labels_live_delayed_stale_and_missing() -> None:
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    assert freshness_label(now.isoformat(), now=now)["label"] == "LIVE"
    assert freshness_label((now - timedelta(minutes=20)).isoformat(), now=now)["label"] == "DELAYED"
    assert freshness_label((now - timedelta(hours=2)).isoformat(), now=now)["label"] == "STALE"
    assert freshness_label(None, now=now)["label"] == "NO DATA"


def test_default_live_watch_is_the_compact_east_singapore_offshore_sector() -> None:
    assert DEFAULT_LIVE_REGION.name == "East Singapore Offshore Watch"
    assert DEFAULT_LIVE_REGION.bbox == (104.02, 1.20, 104.23, 1.31)


def test_live_provider_caches_are_isolated_by_watch_boundary(tmp_path: Path) -> None:
    east = LiveOperationsEngine(
        tmp_path,
        LiveRegion("East", 104.02, 1.20, 104.23, 1.31),
    )
    harbour = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Harbour", 103.50, 1.00, 104.20, 1.55),
    )
    assert east.ais_cache != harbour.ais_cache
    assert east.environment_cache != harbour.environment_cache


def test_empty_snapshot_never_invents_vessels_or_slick(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    snapshot = engine.snapshot()
    assert snapshot["truth_policy"]["synthetic_fallback"] is False
    assert snapshot["ais"]["positions"] == []
    assert snapshot["ais"]["vessel_count"] == 0
    assert snapshot["pipeline"]["slick_detection_ready"] is False


def test_snapshot_returns_only_received_ais_rows(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    engine.ais_cache.parent.mkdir(parents=True, exist_ok=True)
    observed = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pd.DataFrame(
        [
            {
                "timestamp_utc": observed,
                "mmsi": "123456789",
                "vessel_name": "PROVIDER VESSEL",
                "longitude": 72.1,
                "latitude": 18.9,
                "sog": 8.2,
                "cog": 121.0,
                "source": "AISStream live",
            }
        ]
    ).to_csv(engine.ais_cache, index=False)
    snapshot = engine.snapshot()
    assert snapshot["ais"]["vessel_count"] == 1
    assert snapshot["ais"]["positions"][0]["mmsi"] == "123456789"
    assert snapshot["ais"]["positions"][0]["source"] == "AISStream live"
    assert snapshot["ais"]["positions"][0]["motion_state"] == "underway"
    assert snapshot["ais"]["underway_count"] == 1
    assert snapshot["ais"]["stationary_count"] == 0
    assert snapshot["ais"]["window_minutes"] == 10.0


def test_current_vessel_snapshot_deduplicates_filters_and_expires_rows(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
        ais_snapshot_minutes=10,
    )
    engine.ais_cache.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    pd.DataFrame(
        [
            {
                "timestamp_utc": (now - timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mmsi": "111111111",
                "vessel_name": "STALE",
                "longitude": 72.0,
                "latitude": 18.8,
                "sog": 7.0,
                "cog": 90.0,
            },
            {
                "timestamp_utc": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mmsi": "222222222",
                "vessel_name": "MOVING OLD",
                "longitude": 72.0,
                "latitude": 18.8,
                "sog": 4.0,
                "cog": 80.0,
            },
            {
                "timestamp_utc": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mmsi": "222222222",
                "vessel_name": "MOVING LATEST",
                "longitude": 72.1,
                "latitude": 18.9,
                "sog": 8.0,
                "cog": 100.0,
            },
            {
                "timestamp_utc": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mmsi": "333333333",
                "vessel_name": "ANCHORED",
                "longitude": 72.2,
                "latitude": 18.7,
                "sog": 0.2,
                "cog": 0.0,
            },
            {
                "timestamp_utc": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mmsi": "444444444",
                "vessel_name": "OUTSIDE",
                "longitude": 75.0,
                "latitude": 18.7,
                "sog": 9.0,
                "cog": 45.0,
            },
        ]
    ).to_csv(engine.ais_cache, index=False)

    ais = engine.snapshot()["ais"]
    assert ais["vessel_count"] == 2
    assert ais["position_count"] == 3
    assert ais["underway_count"] == 1
    assert ais["stationary_count"] == 1
    assert ais["unknown_motion_count"] == 0
    assert {row["mmsi"] for row in ais["positions"]} == {"222222222", "333333333"}
    moving = next(row for row in ais["positions"] if row["mmsi"] == "222222222")
    assert moving["vessel_name"] == "MOVING LATEST"
    assert moving["longitude"] == 72.1


def test_operator_page_is_separate_and_calls_live_api() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_operations/index.html").read_text(encoding="utf-8")
    assert "/api/live/snapshot" in page
    assert "No synthetic fallback" in page
    assert "docs/prototype" not in page


def test_live_command_truth_panel_uses_provider_state_without_demo_data() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    engine = (root / "src/espada/live_operations.py").read_text(encoding="utf-8")
    assert "Observed" in page
    assert "Catalogue" in page
    assert "Modelled" in page
    assert "Missing data stays missing" in page
    assert "/api/live/snapshot" in script
    assert "/api/live/refresh" in script
    assert "demoVessels" not in script
    assert "fallbackPositions" not in script


def test_live_command_map_separates_live_and_incident_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="evidenceMap"' in page
    assert "One map. Two scales. Honest timelines." in page
    assert 'id="overviewModeButton"' in page
    assert "Singapore Strait overview" in script
    assert "ais-observed-interpolation" in script
    assert ".duration(12000)" in script
    assert "initializeLazyComponents" in script
    assert "IntersectionObserver" in script
    assert 'href="demo.html"' in page
    assert "No AIS positions received" in page
    assert "snapshot.coastline_url" in script
    assert "snapshot.sources?.sentinel?.footprints_url" in script
    assert "snapshot.review?.approved_slick_url" in script
    assert "snapshot.attribution?.origin_zone_url" in script
    assert "Present-day AIS is intentionally hidden here." in script
    assert "visiblePositions.length === 0" in script
    assert 'data-layer="stationary"' in page
    assert "CURRENT VESSELS" in page
    assert "rolling live window" in script


def test_controlled_judges_demo_is_integrated_and_explicitly_synthetic() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/demo.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/demo.js").read_text(encoding="utf-8")
    assert "CONTROLLED EXERCISE" in page
    assert "Synthetic truth is known and disclosed" in page
    assert "NOT LIVE EVIDENCE" in page
    assert "GROUND TRUTH SEALED" in page
    assert "MERIDIAN-7" in page
    assert "KNOWN SOURCE RECOVERED" in page
    assert "BEGIN CONTROLLED RUN" in page
    assert "startDemo" in script
    assert "previousStage" in script
    assert "nextStage" in script
    assert "restartDemo" in script


def test_singapore_watch_exposes_wider_context_without_widening_filter(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("East Singapore Offshore Watch", 104.02, 1.20, 104.23, 1.31),
    )
    snapshot = engine.snapshot()
    assert snapshot["region"]["bbox"] == [104.02, 1.20, 104.23, 1.31]
    assert snapshot["map_context"]["bbox"] == [103.55, 0.98, 104.32, 1.52]
    assert snapshot["map_context"]["name"] == "Singapore Strait overview"


def test_live_command_detection_workbench_preserves_evidence_gates() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="detection-workbench"' in page
    assert "Inspect the pixels before trusting the polygon." in page
    assert "Three independent gates" in page
    assert "Segmentation model" in page
    assert "Physics screen" in page
    assert "Analyst decision" in page
    assert "/api/live/analyze-latest-sar" in script
    assert "/api/live/review" in script
    assert "analysis.physics_screen" in script
    assert "Approval allows reverse-drift analysis" in page
    assert 'id="sarIntegrity"' in page
    assert "provenance_verified" in script
    assert "approvalReady" in script
    assert "operatorMessage" in script


def test_sar_queue_freezes_the_selected_scene_provenance(tmp_path: Path, monkeypatch) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    catalog = engine.output_root / "sentinel" / "sentinel1_catalog.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    scene = {
        "id": "S1-EXACT",
        "acquisition_time_utc": "2026-09-10T11:16:44Z",
        "platform": "sentinel-1d",
        "polarizations": ["VV", "VH"],
        "has_vv": True,
        "target_point_covered": True,
    }
    catalog.write_text(
        json.dumps({"recommended_scene": scene, "scenes": [scene]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine, "_analysis_worker", lambda: None)
    queued = engine.start_latest_sar_analysis("S1-EXACT")
    assert queued["scene_id"] == "S1-EXACT"
    assert queued["selected_scene"]["acquisition_time_utc"] == scene["acquisition_time_utc"]
    assert len(queued["catalog_sha256"]) == 64


def test_analyst_cannot_approve_without_a_complete_physics_pass(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    engine._state["analysis"] = {
        "status": "REVIEW_REQUIRED",
        "scene_id": "SCENE-AMBIGUOUS",
        "physics_screen": {
            "status": "AMBIGUOUS",
            "contrast_gate_passed": False,
            "wind_gate_passed": True,
        },
    }
    with pytest.raises(RuntimeError, match="Approval is locked"):
        engine.review_candidate("APPROVE", age_hours=12)


def test_live_command_reverse_drift_uses_computed_uncertainty_without_fake_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="reverse-drift"' in page
    assert "Trace uncertainty backward—not blame." in page
    assert "INCIDENT HANDOFF · COMPONENT 04" in page
    assert 'id="incidentSeal"' in page
    assert 'id="handoffRecordLink"' in page
    assert "90% credible radius" in page
    assert "Forward replay" in page
    assert "Forward centroid closure" in page
    assert "Forward cloud error" in page
    assert "not a particle trajectory" in page
    assert "/api/live/build-attribution" in script
    assert "origin_particles" in script
    assert "forward_replay_particles" in script
    assert "forward_closure" in script
    assert "drift_status" in script
    assert "origin_zone_url" in script
    assert "drift-displacement" in script
    assert "handoff_verified" in script
    assert "handoffReady" in script
    assert "slick_geometry_sha256" in script
    assert "particle trajectory" not in script


def test_live_command_candidate_attribution_exposes_scores_and_abstention_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    engine = (root / "src/espada/live_operations.py").read_text(encoding="utf-8")
    assert 'id="candidate-attribution"' in page
    assert "Rank the evidence. Respect the refusal." in page
    assert "comparative, not probability" in page
    assert "Lead ≥ 5 points" in page
    assert "At least 2 tracks" in page
    assert "Scores prioritize analyst review" in page
    assert "Direction and AIS silence add <b>0%</b>" in page
    assert "candidate_tracks_url" in script
    assert "forward_consistency" in script
    assert "silence_classification" in script
    assert "nomination_assessment" in engine
    assert "candidateTrackDirection" in script
    assert "AIS gaps are contextual evidence only" in page


def test_live_command_ais_filter_is_separate_transparent_and_direction_safe() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    engine = (root / "src/espada/live_operations.py").read_text(encoding="utf-8")
    assert 'id="ais-filter"' in page
    assert "HISTORICAL AIS FILTER · COMPONENT 06" in page
    assert "Remove traffic that could not be involved." in page
    assert "RELEASE-WINDOW OVERLAP" in page
    assert "ORIGIN-AREA PASSAGE" in page
    assert "DIRECTION IS CONTEXT" in page
    assert 'data-ais-filter-view="retained"' in page
    assert 'data-ais-filter-view="excluded"' in page
    assert "renderAisFilter" in script
    assert "course_evidence" in script
    assert "ais_filter_report.json" in engine
    assert "filter_ais_candidates" in engine
    assert "ABSTAIN_NO_RELEVANT_AIS" in engine


def test_live_command_response_workspace_builds_an_auditable_safe_output() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="respond-workspace"' in page
    assert "Package the evidence. Control the consequence." in page
    assert "Automatic vessel accusation" in page
    assert "Six recorded transitions" in page
    assert "/api/live/build-response" in script
    assert "chain_digest_sha256" in script
    assert "SAFE ABSTENTION READY" in script


def test_live_command_case_register_exposes_history_and_integrity_recheck() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="case-register"' in page
    assert "Every run remains inspectable." in page
    assert "PROVENANCE WARNINGS" in page
    assert "Verify package integrity" in page
    assert "/api/live/verify-case" in script
    assert "provenance_warnings" in script
    assert "computed_chain_digest_sha256" in script


def test_live_command_retasking_drafts_scoped_requests_without_dispatch() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="evidence-planner"' in page
    assert "Turn abstention into a precise evidence request." in page
    assert "Decision-gate diagnosis" in page
    assert "EXTERNAL DISPATCH" in page
    assert "does not contact providers" in page
    assert "/api/live/build-evidence-plan" in script
    assert "decision_gates" in script
    assert "acceptance_criteria" in script


def test_live_command_intake_preserves_and_gates_returned_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="evidence-intake"' in page
    assert "Admit new evidence—never overwrite the old case." in page
    assert "ATTRIBUTION STATE" in page
    assert "rerun is always explicit" in page
    assert "/api/live/stage-evidence-return" in script
    assert "/api/live/review-evidence-return" in script
    assert "attribution remains frozen" in script


def test_live_command_reanalysis_versions_new_evidence_without_overwrite() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="controlled-reanalysis"' in page
    assert "Re-run the evidence—not history." in page
    assert "ORIGINAL RECORD" in page
    assert "IMMUTABLE" in page
    assert "/api/live/start-reanalysis" in script
    assert "original case is locked" in script


def test_live_command_closure_compares_versions_and_records_disposition() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_command/index.html").read_text(encoding="utf-8")
    script = (root / "operator/live_command/app.js").read_text(encoding="utf-8")
    assert 'id="decision-closure"' in page
    assert "Close the workflow. Preserve the uncertainty." in page
    assert "Close as inconclusive" in page
    assert "Refer for authorized review" in page
    assert "/api/live/record-case-disposition" in script
    assert "latest evidence version" not in page
    assert "prior events and evidence versions remain unchanged" in script.lower()


def test_completed_analysis_survives_server_restart(tmp_path: Path) -> None:
    region = LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0)
    first = LiveOperationsEngine(tmp_path, region)
    first._state["analysis"] = {  # durable job result written exactly like the worker
        "status": "NO_DETECTION",
        "scene_id": "REAL-SCENE-1",
        "message": "No candidate exceeded the frozen threshold.",
    }
    first._write_state()
    restarted = LiveOperationsEngine(tmp_path, region)
    assert restarted.snapshot()["analysis"]["status"] == "NO_DETECTION"
    assert restarted.snapshot()["analysis"]["scene_id"] == "REAL-SCENE-1"
    assert restarted.snapshot()["review"]["status"] == "NOT_REQUIRED"


def test_refresh_failure_preserves_last_verified_provider_payload(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    verified_at = "2026-09-16T12:00:00Z"
    engine._update_source(
        "environment",
        status="PASS",
        last_success_utc=verified_at,
        current={"current_speed_ms": 0.18, "wind_speed_ms": 6.4},
    )
    engine._update_source(
        "sentinel",
        status="PASS",
        last_success_utc=verified_at,
        scenes=[{"id": "S1-VERIFIED"}],
    )

    engine._record_source_failure(
        "environment", OSError("temporary socket denial"), attempted="2026-09-16T12:10:00Z"
    )
    engine._record_source_failure(
        "sentinel", OSError("temporary socket denial"), attempted="2026-09-16T12:10:00Z"
    )

    snapshot = engine.snapshot()
    assert snapshot["sources"]["environment"]["status"] == "STALE"
    assert snapshot["sources"]["environment"]["current"]["current_speed_ms"] == 0.18
    assert snapshot["sources"]["sentinel"]["status"] == "STALE"
    assert snapshot["sources"]["sentinel"]["scenes"][0]["id"] == "S1-VERIFIED"
    assert snapshot["pipeline"]["environment_ready"] is True
    assert snapshot["pipeline"]["sentinel_catalog_ready"] is True


def test_analyst_approval_is_required_and_persisted(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    with pytest.raises(RuntimeError, match="Approve"):
        engine.start_attribution()

    scene_id = "SCENE-1"
    run_root = _prepare_reviewable_case(engine, scene_id=scene_id)
    review = engine.review_candidate("APPROVE", age_hours=12)
    approved = run_root / "review" / "approved_slick.geojson"
    handoff_path = run_root / "review" / "incident_handoff.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert review["status"] == "APPROVED"
    assert review["assumed_age_hours"] == 12
    assert review["estimated_release_time_utc"] == "2026-08-31T12:00:00Z"
    assert review["handoff_status"] == "SEALED"
    assert review["handoff_verified"] is True
    assert len(review["handoff_digest_sha256"]) == 64
    assert approved.exists()
    assert handoff_path.exists()
    assert handoff["record_digest_sha256"] == review["handoff_digest_sha256"]
    assert handoff["artifacts"]["approved_slick"]["sha256"] == review["slick_geometry_sha256"]
    assert '"review_status": "analyst_approved"' in approved.read_text(encoding="utf-8")
    assert engine.snapshot()["pipeline"]["analyst_review_ready"] is True
    assert engine.snapshot()["pipeline"]["incident_handoff_ready"] is True


def test_reverse_drift_refuses_a_tampered_sealed_handoff(tmp_path: Path) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    run_root = _prepare_reviewable_case(engine)
    engine.review_candidate("APPROVE", age_hours=12)
    approved = run_root / "review" / "approved_slick.geojson"
    approved.write_text(approved.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="integrity failed"):
        engine.start_attribution()


def test_reverse_drift_accepts_only_the_verified_handoff(tmp_path: Path, monkeypatch) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Test region", 70.8, 17.8, 73.0, 20.0),
    )
    _prepare_reviewable_case(engine)
    review = engine.review_candidate("APPROVE", age_hours=12)
    monkeypatch.setattr(engine, "_attribution_worker", lambda: None)

    queued = engine.start_attribution()
    assert queued["status"] == "QUEUED"
    assert queued["handoff_digest_sha256"] == review["handoff_digest_sha256"]


def test_coastline_cache_is_regenerated_when_region_changes(tmp_path: Path, monkeypatch) -> None:
    engine = LiveOperationsEngine(
        tmp_path,
        LiveRegion("Singapore", 103.5, 1.0, 104.2, 1.55),
    )
    engine.coast_path.parent.mkdir(parents=True, exist_ok=True)
    engine.coast_path.write_text(
        json.dumps(
            {
                "type": "Feature",
                "properties": {"requested_bbox": [70.8, 17.8, 73.0, 20.0]},
                "geometry": {"type": "Polygon", "coordinates": []},
            }
        ),
        encoding="utf-8",
    )
    archive = tmp_path / "data" / "cache" / "natural_earth" / "ne_10m_land.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"cached")
    seen: dict[str, object] = {"bboxes": []}

    def fake_clip(source, destination, bbox, *, padding_degrees):
        seen["bboxes"].append(tuple(bbox))

    monkeypatch.setattr("espada.live_operations.clip_land_archive", fake_clip)
    engine._prepare_coastline()
    assert engine.region.bbox in seen["bboxes"]
    assert engine.map_context_bbox in seen["bboxes"]
