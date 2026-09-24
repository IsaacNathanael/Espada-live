from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

from espada.environment import synthetic_environment
from espada.geo import write_polygon_geojson
from espada.slick import analyze_slick, load_slick


def _slick(path: Path, **properties: object) -> Path:
    write_polygon_geojson(
        path,
        Polygon([(71.50, 18.72), (71.53, 18.72), (71.53, 18.75), (71.50, 18.75)]),
        {
            "observation_time_utc": "2026-03-15T21:00:00Z",
            "detection_confidence": 0.88,
            "source": "test detector",
            **properties,
        },
    )
    return path


def test_slick_contract_rejects_missing_confidence(tmp_path: Path) -> None:
    path = tmp_path / "slick.geojson"
    write_polygon_geojson(
        path,
        Polygon([(71.5, 18.7), (71.6, 18.7), (71.6, 18.8)]),
        {"observation_time_utc": "2026-03-15T21:00:00Z"},
    )
    with pytest.raises(ValueError, match="detection_confidence"):
        load_slick(path)


def test_slick_contract_rejects_pending_detector_candidate(tmp_path: Path) -> None:
    path = _slick(tmp_path / "candidate.geojson", review_status="pending")
    with pytest.raises(ValueError, match="analyst approval"):
        load_slick(path)


def test_slick_geojson_runs_backward_inference(tmp_path: Path) -> None:
    environment = synthetic_environment(tmp_path / "unused.json")
    records = environment.frame.copy()
    records["time_utc"] = records["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cache = tmp_path / "environment.json"
    cache.write_text(
        json.dumps(
            {
                "source": environment.source,
                "temporal_resolution": "hourly",
                "samples": records.to_dict(orient="records"),
            }
        ),
        encoding="utf-8",
    )
    result = analyze_slick(
        _slick(tmp_path / "slick.geojson"),
        cache,
        tmp_path / "analysis",
        age_hours=12,
        particles=120,
        ensemble_members=3,
    )
    assert result["status"] == "PASS"
    assert result["environment"]["forcing_steps"] == 12
    assert result["input"]["area_km2"] > 0
    assert (tmp_path / "analysis" / "slick_reverse_analysis.png").exists()
    assert (tmp_path / "analysis" / "release_estimate.json").exists()
    assert (tmp_path / "analysis" / "forward_particles.npz").exists()
    assert (tmp_path / "analysis" / "forward_replay_particles.npz").exists()
    assert (tmp_path / "analysis" / "drift_validation.json").exists()
    closure = result["forward_closure"]
    assert closure["status"] == "COMPUTED"
    assert closure["particles_retained"] == 120
    assert closure["centroid_error_km"] >= 0
    assert closure["cloud_shape_error_km"] >= 0
    assert 0 <= closure["fraction_inside_observed_polygon"] <= 1
    with np.load(tmp_path / "analysis" / "forward_particles.npz") as observed, np.load(
        tmp_path / "analysis" / "forward_replay_particles.npz"
    ) as replay:
        assert not np.array_equal(observed["lon"], replay["lon"])
