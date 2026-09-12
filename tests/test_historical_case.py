from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import shapefile

from espada.historical_case import (
    WAKASHIO_MMSI,
    build_blinded_candidates,
    build_unosat_slick,
    evaluate_blind_ranking,
)


def _source_shapefile(path: Path) -> Path:
    writer = shapefile.Writer(str(path))
    writer.field("SensorID", "C")
    writer.field("Confidence", "C")
    writer.field("Area_m2", "N", decimal=1)
    writer.field("Field_Vali", "C")
    writer.poly([[[57.73, -20.44], [57.75, -20.44], [57.75, -20.42], [57.73, -20.44]]])
    writer.record("Sentinel-2", "High", 231436.0, "Not validated")
    writer.close()
    return path


def _gfw(path: Path) -> Path:
    pd.DataFrame(
        {
            "timestamp_utc": ["2020-08-06T05:00:00Z"],
            "mmsi": ["111111111"],
            "vessel_name": ["UNKNOWN"],
            "longitude": [57.65],
            "latitude": [-20.50],
            "is_interpolated": [False],
            "source": ["Global Fishing Watch"],
            "sampling_interval_minutes": [60.0],
        }
    ).to_csv(path, index=False)
    return path


def test_prepare_keeps_truth_separate_and_discloses_missing_gfw_target(tmp_path: Path) -> None:
    slick = build_unosat_slick(_source_shapefile(tmp_path / "oil.shp"), tmp_path / "slick.geojson")
    candidates = build_blinded_candidates(
        _gfw(tmp_path / "gfw.csv"),
        tmp_path / "blinded.csv",
        tmp_path / "truth.json",
    )
    blinded_text = (tmp_path / "blinded.csv").read_text(encoding="utf-8")
    assert slick["status"] == "PASS"
    assert candidates["target_absent_from_gfw"] is True
    assert WAKASHIO_MMSI not in blinded_text
    assert "Official casualty-file position reconstruction" in blinded_text


def test_reveal_reports_target_rank(tmp_path: Path) -> None:
    candidates = build_blinded_candidates(
        _gfw(tmp_path / "gfw.csv"),
        tmp_path / "blinded.csv",
        tmp_path / "truth.json",
    )
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    ranking = {
        "candidates": [
            {"rank": 1, "mmsi": truth["target_candidate_id"], "vessel_name": "Blind", "total_score": 0.8, "forward_error_km": 1.2},
            {"rank": 2, "mmsi": "CAND-OTHER", "vessel_name": "Blind", "total_score": 0.4, "forward_error_km": 7.0},
        ]
    }
    ranking_path = tmp_path / "ranking.json"
    ranking_path.write_text(json.dumps(ranking), encoding="utf-8")
    result = evaluate_blind_ranking(ranking_path, tmp_path / "truth.json", tmp_path / "report")
    assert candidates["status"] == "PASS"
    assert result["top_1_pass"] is True
    assert "MV Wakashio ranked #1" in result["answer"]
    assert (tmp_path / "report" / "historical_evaluation_report.html").exists()
