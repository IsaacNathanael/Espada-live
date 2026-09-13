from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from espada.counterfactual_validation import (
    prepare_counterfactual_ais,
    reveal_counterfactual_result,
)


def test_counterfactual_track_is_disclosed_and_identity_is_sealed(tmp_path: Path) -> None:
    background = tmp_path / "background.csv"
    pd.DataFrame(
        {
            "timestamp_utc": ["2018-10-07T19:00:00Z"],
            "mmsi": ["247000001"],
            "vessel_name": ["UNKNOWN"],
            "longitude": [9.1],
            "latitude": [43.1],
            "is_interpolated": [False],
            "source": ["Global Fishing Watch AIS vessel presence"],
        }
    ).to_csv(background, index=False)
    estimate = tmp_path / "estimate.json"
    estimate.write_text(
        json.dumps(
            {
                "release_time_utc": "2018-10-07T19:00:00Z",
                "observation_time_utc": "2018-10-08T05:00:00Z",
                "estimated_origin": {"longitude": 9.3, "latitude": 43.3},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "hybrid.csv"
    registry = tmp_path / "sealed.json"
    result = prepare_counterfactual_ais(background, estimate, output, registry)
    frame = pd.read_csv(output, dtype={"mmsi": str})
    truth = json.loads(registry.read_text(encoding="utf-8"))
    synthetic = frame.loc[frame["mmsi"] == truth["candidate_id"]]
    assert result["real_background_vessels"] == 1
    assert len(synthetic) == 17
    assert synthetic["source"].str.startswith("synthetic counterfactual").all()
    assert "CONTROLLED SYNTHETIC SOURCE" not in output.read_text(encoding="utf-8")


def test_counterfactual_truth_is_revealed_only_from_existing_ranking(tmp_path: Path) -> None:
    registry = tmp_path / "sealed.json"
    registry.write_text(
        json.dumps(
            {
                "candidate_id": "991234567",
                "claim_boundary": "Synthetic ranking test only.",
            }
        ),
        encoding="utf-8",
    )
    ranking = tmp_path / "ranking.json"
    ranking.write_text(
        json.dumps(
            {
                "candidate_count": 2,
                "candidates": [
                    {"rank": 1, "mmsi": "991234567", "total_score": 0.9, "forward_error_km": 1.0}
                ],
            }
        ),
        encoding="utf-8",
    )
    result = reveal_counterfactual_result(ranking, registry, tmp_path / "result.json")
    assert result["status"] == "PASS"
    assert result["top_1"] is True
    assert result["target_rank"] == 1
