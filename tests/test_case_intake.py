from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from espada.case_intake import CaseIntakeError, create_case_workspace


def _upload(name: str, raw: bytes) -> dict[str, str]:
    return {"name": name, "data": base64.b64encode(raw).decode("ascii")}


def _environment() -> bytes:
    sample = {
        "time_utc": "2026-08-29T12:00:00Z",
        "current_east_ms": 0.1,
        "current_north_ms": -0.1,
        "wind_east_ms": 5.0,
        "wind_north_ms": 1.0,
    }
    return json.dumps({"samples": [sample, {**sample, "time_utc": "2026-08-29T13:00:00Z"}]}).encode()


def _ais() -> bytes:
    return b"timestamp_utc,mmsi,longitude,latitude\n2026-08-29T12:00:00Z,123456789,72.1,18.8\n"


def _slick() -> bytes:
    return json.dumps(
        {
            "type": "Feature",
            "properties": {
                "observation_time_utc": "2026-08-29T13:00:00Z",
                "detection_confidence": 0.91,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[72.0, 18.7], [72.2, 18.7], [72.2, 18.9], [72.0, 18.7]]],
            },
        }
    ).encode()


def test_approved_slick_intake_creates_runnable_case(tmp_path: Path) -> None:
    request = {
        "case_id": "incident_001",
        "mode": "approved_slick",
        "files": {
            "slick_geojson": _upload("slick.geojson", _slick()),
            "ais_csv": _upload("positions.csv", _ais()),
            "environment_cache": _upload("environment.json", _environment()),
        },
        "analysis": {"age_hours": 0, "candidate_ages_hours": [2, 4, 8]},
    }
    result = create_case_workspace(tmp_path, request)
    case_path = Path(result["case_file"])
    case = json.loads(case_path.read_text(encoding="utf-8"))
    assert result["status"] == "READY"
    assert case["mode"] == "approved_slick"
    assert (tmp_path / case["inputs"]["ais_csv"]).is_file()
    assert case["output_directory"] == "out/cases/incident_001"


def test_intake_rejects_invalid_ais_before_writing(tmp_path: Path) -> None:
    request = {
        "case_id": "bad_ais",
        "mode": "approved_slick",
        "files": {
            "slick_geojson": _upload("slick.geojson", _slick()),
            "ais_csv": _upload("positions.csv", b"name,value\na,b\n"),
            "environment_cache": _upload("environment.json", _environment()),
        },
    }
    with pytest.raises(CaseIntakeError, match="MMSI"):
        create_case_workspace(tmp_path, request)
    assert not (tmp_path / "out" / "case_intake" / "bad_ais").exists()


def test_sar_intake_validates_bbox_and_signature(tmp_path: Path) -> None:
    request = {
        "case_id": "sar_001",
        "mode": "sar",
        "observation_time_utc": "2026-08-29T13:00:00Z",
        "bbox": [71.2, 18.5, 71.8, 19.0],
        "files": {
            "sar_image": _upload("scene.png", b"\x89PNG\r\n\x1a\nfixture"),
            "ais_csv": _upload("positions.csv", _ais()),
            "environment_cache": _upload("environment.json", _environment()),
        },
    }
    result = create_case_workspace(tmp_path, request)
    case = json.loads(Path(result["case_file"]).read_text(encoding="utf-8"))
    assert case["bbox"] == [71.2, 18.5, 71.8, 19.0]
    assert case["analysis"]["analyst_approved"] is False


def test_existing_case_is_never_overwritten(tmp_path: Path) -> None:
    request = {
        "case_id": "preserved_001",
        "mode": "approved_slick",
        "files": {
            "slick_geojson": _upload("slick.geojson", _slick()),
            "ais_csv": _upload("positions.csv", _ais()),
            "environment_cache": _upload("environment.json", _environment()),
        },
    }
    create_case_workspace(tmp_path, request)
    with pytest.raises(CaseIntakeError, match="already exists"):
        create_case_workspace(tmp_path, request)
