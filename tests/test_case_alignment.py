import json
from pathlib import Path

import pandas as pd
from shapely.geometry import Polygon

from espada.case_alignment import validate_case_alignment
from espada.geo import write_polygon_geojson


def _write_case_inputs(tmp_path: Path, ais_time: str) -> tuple[Path, Path, Path]:
    slick = tmp_path / "slick.geojson"
    write_polygon_geojson(
        slick,
        Polygon([(71.4, 18.7), (71.5, 18.7), (71.5, 18.8), (71.4, 18.8)]),
        {
            "observation_time_utc": "2026-08-30T15:00:00Z",
            "detection_confidence": 0.9,
        },
    )
    times = pd.date_range("2026-08-29T18:00:00Z", "2026-08-30T15:00:00Z", freq="1h")
    environment = tmp_path / "environment.json"
    environment.write_text(
        json.dumps(
            {
                "source": "test Copernicus currents + wind",
                "temporal_resolution": "hourly",
                "samples": [
                    {
                        "time_utc": value.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "latitude": 18.7,
                        "longitude": 71.45,
                        "current_east_ms": 0.1,
                        "current_north_ms": -0.02,
                        "wind_east_ms": 4.0,
                        "wind_north_ms": 1.0,
                        "source": "test",
                    }
                    for value in times
                ],
            }
        ),
        encoding="utf-8",
    )
    ais = tmp_path / "ais.csv"
    pd.DataFrame(
        [
            {
                "timestamp_utc": ais_time,
                "mmsi": "419000123",
                "longitude": 71.45,
                "latitude": 18.72,
            }
        ]
    ).to_csv(ais, index=False)
    return slick, environment, ais


def test_aligned_case_passes_and_reports_evidence(tmp_path: Path) -> None:
    slick, environment, ais = _write_case_inputs(tmp_path, "2026-08-29T20:00:00Z")
    output = tmp_path / "alignment.json"
    result = validate_case_alignment(
        slick, environment, ais, output, age_hours=19, max_ais_offset_hours=2
    )
    assert result["status"] == "PASS"
    assert result["ais"]["vessels_near_release_time"] == 1
    assert output.exists()


def test_mismatched_ais_fails_before_attribution(tmp_path: Path) -> None:
    slick, environment, ais = _write_case_inputs(tmp_path, "2026-08-20T20:00:00Z")
    result = validate_case_alignment(
        slick, environment, ais, tmp_path / "alignment.json", age_hours=19
    )
    assert result["status"] == "FAIL"
    assert result["checks"]["ais_has_positions_near_release_time"] is False
