import inspect
import json
from pathlib import Path

import pandas as pd
import numpy as np

from espada.attribution import (
    _interpolate_release_position,
    _symmetric_cloud_error_km,
    assess_nomination,
    rank_candidates,
)
from espada.demo import run_demo
from espada.synthetic_ais import generate_synthetic_ais
from espada.verification import VerificationConfig, run_verification


def _physics(tmp_path: Path) -> Path:
    physics_dir = tmp_path / "physics"
    run_verification(
        physics_dir,
        VerificationConfig(particles=180, ensemble_members=4),
        check_opendrift=False,
    )
    return physics_dir


def test_ranker_cannot_receive_truth_file() -> None:
    parameters = inspect.signature(rank_candidates).parameters
    assert "truth" not in parameters
    assert "truth_path" not in parameters


def test_nomination_gate_refuses_an_ambiguous_tie() -> None:
    candidates = [
        {"mmsi": "111", "total_score": 0.82, "data_quality": 0.9, "forward_error_km": 2.0},
        {"mmsi": "222", "total_score": 0.82, "data_quality": 0.9, "forward_error_km": 2.2},
    ]
    result = assess_nomination(candidates)
    assert result["decision"] == "ABSTAIN_INSUFFICIENT_EVIDENCE"
    assert "score_margin" in result["failed_gate_ids"]
    assert result["near_tied_count"] == 2


def test_nomination_gate_allows_only_a_limited_shortlist() -> None:
    candidates = [
        {"mmsi": "111", "total_score": 0.82, "data_quality": 0.9, "forward_error_km": 2.0},
        {"mmsi": "222", "total_score": 0.65, "data_quality": 0.8, "forward_error_km": 3.0},
    ]
    result = assess_nomination(candidates)
    assert result["decision"] == "LIMITED_SHORTLIST"
    assert result["failed_gate_ids"] == []
    assert "not proof" in result["claim_boundary"].lower()


def test_shape_error_distinguishes_matching_and_displaced_clouds() -> None:
    observed_xy_km = np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    centroid = (0.0, 0.0)
    matching_lon = observed_xy_km[:, 0] / 111.195
    matching_lat = observed_xy_km[:, 1] / 111.195
    matching = _symmetric_cloud_error_km(
        matching_lon,
        matching_lat,
        observed_xy_km,
        centroid,
    )
    displaced = _symmetric_cloud_error_km(
        matching_lon + 1.0,
        matching_lat,
        observed_xy_km,
        centroid,
    )
    assert matching < 0.01
    assert displaced > 100.0


def test_release_position_interpolation_is_bounded_and_labelled() -> None:
    frame = pd.DataFrame(
        {
            "timestamp_utc": ["2026-01-01T00:00:00Z", "2026-01-01T04:00:00Z"],
            "mmsi": ["111111111", "111111111"],
            "vessel_name": ["Blind", "Blind"],
            "longitude": [10.0, 10.4],
            "latitude": [20.0, 20.2],
            "is_interpolated": [False, False],
            "source": ["test", "test"],
        }
    )
    augmented, used, gap = _interpolate_release_position(
        frame, pd.Timestamp("2026-01-01T02:00:00Z").to_pydatetime()
    )
    synthetic = augmented.loc[augmented["is_interpolated"].astype(bool)]
    assert used is True
    assert gap == 4.0
    assert abs(float(synthetic.iloc[0]["longitude"]) - 10.2) < 1e-9
    assert "scoring-only" in str(synthetic.iloc[0]["source"])


def test_synthetic_ais_has_fourteen_vessels_and_a_real_gap(tmp_path: Path) -> None:
    physics_dir = _physics(tmp_path)
    output = tmp_path / "ais.csv"
    frame = generate_synthetic_ais(physics_dir / "truth.json", output)
    assert output.exists()
    assert frame["mmsi"].nunique() == 14
    counts = frame.groupby("mmsi").size()
    assert counts.loc["419000789"] < counts.max()


def test_offline_demo_finds_known_source_without_ranker_answer_key(tmp_path: Path) -> None:
    result = run_demo(
        tmp_path,
        particles=220,
        ensemble_members=5,
        check_opendrift=False,
    )
    assert result["status"] == "PASS"
    assert result["top_candidate"]["mmsi"] == "419000123"
    saved = json.loads((tmp_path / "candidates.json").read_text(encoding="utf-8"))
    assert saved["candidate_count"] == 14
    assert pd.read_csv(tmp_path / "ais_tracks.csv")["mmsi"].nunique() == 14
    for filename in (
        "attribution_map.png",
        "candidate_ranking.png",
        "dashboard.html",
        "demo_result.json",
    ):
        assert (tmp_path / filename).stat().st_size > 0
    dashboard = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "Reverse Drift Attribution" in dashboard
    assert "MV SYNTHETIC" in dashboard
    assert "Run reverse reconstruction" in dashboard
    assert "Stress lab" in dashboard
    assert "Case Lab" in dashboard
    assert "Run operational preflight" in dashboard
    assert "Execute fresh known-source case" in dashboard
    assert "LOCAL ENGINE CONNECTED" in dashboard
    assert "ATTRIBUTION CORRECTLY WITHHELD" in dashboard
    assert "Reveal known source" in dashboard
    assert "Export evidence" in dashboard
    assert "https://" not in dashboard
