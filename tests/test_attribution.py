import inspect
import json
from pathlib import Path

import pandas as pd

from espada.attribution import rank_candidates
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
    assert "https://" not in dashboard
