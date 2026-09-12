from __future__ import annotations

from pathlib import Path

import pandas as pd
import shapefile

from espada.environment import synthetic_environment
from espada.historical_case import build_blinded_candidates, build_unosat_slick
from espada.sensitivity import run_historical_sensitivity


def test_sensitivity_writes_complete_report(tmp_path: Path) -> None:
    source = tmp_path / "oil.shp"
    writer = shapefile.Writer(str(source))
    writer.field("SensorID", "C")
    writer.field("Confidence", "C")
    writer.field("Area_m2", "N", decimal=1)
    writer.field("Field_Vali", "C")
    writer.poly([[[57.73, -20.44], [57.75, -20.44], [57.75, -20.42], [57.73, -20.44]]])
    writer.record("Sentinel-2", "High", 231436.0, "Not validated")
    writer.close()
    slick = tmp_path / "slick.geojson"
    build_unosat_slick(source, slick)

    gfw = tmp_path / "gfw.csv"
    pd.DataFrame(
        {
            "timestamp_utc": ["2020-08-06T05:00:00Z", "2020-08-06T06:00:00Z"],
            "mmsi": ["111111111", "111111111"],
            "vessel_name": ["UNKNOWN", "UNKNOWN"],
            "longitude": [57.65, 57.65],
            "latitude": [-20.50, -20.50],
            "is_interpolated": [False, False],
            "source": ["Global Fishing Watch", "Global Fishing Watch"],
            "sampling_interval_minutes": [60.0, 60.0],
        }
    ).to_csv(gfw, index=False)
    prepared = tmp_path / "prepared"
    build_blinded_candidates(gfw, prepared / "candidates.csv", prepared / "truth.json")

    environment = synthetic_environment(tmp_path / "unused.json")
    frame = environment.frame.copy()
    frame["time_utc"] = pd.date_range("2020-08-05T00:00:00Z", periods=len(frame), freq="1h")
    environment_cache = tmp_path / "environment.json"
    environment_cache.write_text(
        pd.Series(
            {
                "source": "test forcing",
                "temporal_resolution": "hourly",
                "samples": frame.assign(time_utc=frame["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_dict(orient="records"),
            }
        ).to_json(),
        encoding="utf-8",
    )
    result = run_historical_sensitivity(
        slick,
        environment_cache,
        prepared / "candidates.csv",
        prepared / "truth.json",
        tmp_path / "result",
        ages_hours=(1.5,),
        current_multipliers=(1.0,),
        windages=(0.02,),
        particles=120,
        ensemble_members=2,
    )
    assert result["status"] == "PASS"
    assert result["scenarios"] == 1
    assert (tmp_path / "result" / "sensitivity_report.html").exists()
    assert (tmp_path / "result" / "sensitivity_rank_matrix.png").exists()
    assert (tmp_path / "result" / "sensitivity_origin_envelope.png").exists()
    assert "documented_source_inside_90pct_radius" in result["assumption_aware_origin"]
