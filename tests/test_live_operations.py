from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from espada.live_operations import LiveOperationsEngine, LiveRegion, freshness_label


def test_freshness_labels_live_delayed_stale_and_missing() -> None:
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    assert freshness_label(now.isoformat(), now=now)["label"] == "LIVE"
    assert freshness_label((now - timedelta(minutes=20)).isoformat(), now=now)["label"] == "DELAYED"
    assert freshness_label((now - timedelta(hours=2)).isoformat(), now=now)["label"] == "STALE"
    assert freshness_label(None, now=now)["label"] == "NO DATA"


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


def test_operator_page_is_separate_and_calls_live_api() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "operator/live_operations/index.html").read_text(encoding="utf-8")
    assert "/api/live/snapshot" in page
    assert "No synthetic fallback" in page
    assert "docs/prototype" not in page


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
    segmentation = engine.output_root / "analysis" / scene_id / "segmentation"
    segmentation.mkdir(parents=True)
    (segmentation / "slick_candidate.geojson").write_text(
        """{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"review_status":"pending","observation_time_utc":"2026-09-01T00:00:00Z","detection_confidence":0.8},"geometry":{"type":"Polygon","coordinates":[[[71.0,18.0],[71.1,18.0],[71.1,18.1],[71.0,18.0]]]}}]}""",
        encoding="utf-8",
    )
    engine._state["analysis"] = {
        "status": "REVIEW_REQUIRED",
        "scene_id": scene_id,
        "physics_screen": {"status": "PLAUSIBLE_DARK_SIGNATURE"},
    }
    review = engine.review_candidate("APPROVE", age_hours=12)
    approved = engine.output_root / "analysis" / scene_id / "review" / "approved_slick.geojson"
    assert review["status"] == "APPROVED"
    assert review["assumed_age_hours"] == 12
    assert approved.exists()
    assert '"review_status": "analyst_approved"' in approved.read_text(encoding="utf-8")
    assert engine.snapshot()["pipeline"]["analyst_review_ready"] is True


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
    seen: dict[str, object] = {}

    def fake_clip(source, destination, bbox, *, padding_degrees):
        seen["bbox"] = tuple(bbox)

    monkeypatch.setattr("espada.live_operations.clip_land_archive", fake_clip)
    engine._prepare_coastline()
    assert seen["bbox"] == engine.region.bbox
