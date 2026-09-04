from pathlib import Path

import pandas as pd
import pytest

from espada.ais import normalize_ais_csv


def test_ais_aliases_are_normalized_and_bad_rows_removed(tmp_path: Path) -> None:
    source = tmp_path / "vendor.csv"
    pd.DataFrame(
        [
            {"BaseDateTime": "2026-09-03T00:00:00Z", "MMSI": "419000123", "LAT": 18.7, "LON": 71.4, "VesselName": "TEST"},
            {"BaseDateTime": "2026-09-03T00:10:00Z", "MMSI": "419000123", "LAT": 18.71, "LON": 71.41, "VesselName": "TEST"},
            {"BaseDateTime": "2026-09-03T01:10:00Z", "MMSI": "419000123", "LAT": 18.72, "LON": 71.42, "VesselName": "TEST"},
            {"BaseDateTime": "bad", "MMSI": "x", "LAT": 999, "LON": 999, "VesselName": "BAD"},
        ]
    ).to_csv(source, index=False)
    report = normalize_ais_csv(source, tmp_path / "out")
    normalized = pd.read_csv(tmp_path / "out" / "ais_normalized.csv", dtype={"mmsi": str})
    assert report["status"] == "PASS"
    assert report["invalid_rows_removed"] == 1
    assert report["vessel_count"] == 1
    assert report["gap_threshold_minutes"] == 30.0
    assert report["vessels_with_gaps_over_threshold"] == 1
    assert set(["timestamp_utc", "mmsi", "longitude", "latitude", "gap_before_minutes"]) <= set(normalized)


def test_ais_duplicate_timestamp_is_removed(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.csv"
    rows = [
        {"timestamp_utc": "2026-09-03T00:00:00Z", "mmsi": "419000123", "longitude": 71.4, "latitude": 18.7},
        {"timestamp_utc": "2026-09-03T00:00:00Z", "mmsi": "419000123", "longitude": 71.5, "latitude": 18.8},
    ]
    pd.DataFrame(rows).to_csv(source, index=False)
    report = normalize_ais_csv(source, tmp_path / "out")
    assert report["duplicate_rows_removed"] == 1
    assert report["valid_rows"] == 1


def test_ais_missing_required_column_fails_clearly(tmp_path: Path) -> None:
    source = tmp_path / "missing.csv"
    pd.DataFrame([{"MMSI": "419000123", "LAT": 18.7}]).to_csv(source, index=False)
    with pytest.raises(ValueError, match="missing required columns"):
        normalize_ais_csv(source, tmp_path / "out")
