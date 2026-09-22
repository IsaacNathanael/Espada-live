from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from espada.live_reanalysis import load_live_reanalysis, run_live_reanalysis


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _case(tmp_path: Path) -> tuple[Path, Path]:
    run_root = tmp_path / "S1-CASE"
    plan_digest = "a" * 64
    _write_json(
        run_root / "follow_up/evidence_acquisition_plan.json",
        {
            "schema": "espada.live-evidence-acquisition-plan.v1",
            "plan_digest_sha256": plan_digest,
            "blocking_gate": "CANDIDATE_SEPARATION",
            "task_count": 1,
            "tasks": [{"id": "REQ-AIS-DIRECT"}],
        },
    )
    payload_text = (
        "timestamp,mmsi,vessel_name,lon,lat,source\n"
        "2026-09-04T05:30:00Z,111000111,ALPHA,103.91,1.04,Coastal VTS\n"
        "2026-09-04T06:30:00Z,111000111,ALPHA,103.92,1.05,Coastal VTS\n"
    )
    payload = run_root / "follow_up/intake/payloads/RET-001.txt"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(payload_text.encode("utf-8"))
    receipt = {
        "receipt_id": "RET-001",
        "request_id": "REQ-AIS-DIRECT",
        "request_title": "Request direct AIS",
        "evidence_type": "OBSERVED",
        "resolves": ["CANDIDATE_SEPARATION"],
        "provider": "Coastal VTS",
        "payload_sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        "payload_file": payload.relative_to(run_root).as_posix(),
        "plan_digest_sha256": plan_digest,
        "validation_status": "PASS",
        "admission_status": "ADMITTED",
        "coverage_start_utc": "2026-09-04T05:00:00Z",
        "coverage_end_utc": "2026-09-04T08:00:00Z",
        "covered_mmsi": ["111000111"],
    }
    _write_json(
        run_root / "follow_up/intake/evidence_intake_register.json",
        {"schema": "espada.live-evidence-intake.v1", "receipts": [receipt]},
    )
    ais = pd.DataFrame(
        [
            ["2026-09-04T04:00:00Z", "111000111", "ALPHA", 103.80, 1.00, False, 0, "baseline"],
            ["2026-09-04T05:30:00Z", "111000111", "ALPHA", 103.81, 1.01, True, 90, "baseline"],
            ["2026-09-04T06:00:00Z", "111000111", "ALPHA", 103.82, 1.02, True, 30, "baseline"],
            ["2026-09-04T06:00:00Z", "222000222", "BRAVO", 103.95, 1.06, False, 0, "baseline"],
        ],
        columns=[
            "timestamp_utc", "mmsi", "vessel_name", "longitude", "latitude",
            "is_interpolated", "gap_before_minutes", "source",
        ],
    )
    ais_path = run_root / "attribution/ais/ais_normalized.csv"
    ais_path.parent.mkdir(parents=True, exist_ok=True)
    ais.to_csv(ais_path, index=False)
    baseline_candidates = [
        {
            "rank": 1,
            "mmsi": "222000222",
            "vessel_name": "BRAVO",
            "total_score": 0.76,
            "data_quality": 0.9,
            "forward_error_km": 2.0,
        },
        {
            "rank": 2,
            "mmsi": "111000111",
            "vessel_name": "ALPHA",
            "total_score": 0.74,
            "data_quality": 0.5,
            "forward_error_km": 3.0,
        },
    ]
    _write_json(
        run_root / "attribution/ranking/candidates.json",
        {"status": "PASS", "candidates": baseline_candidates},
    )
    for relative in (
        "attribution/drift/reverse_endpoints.npz",
        "attribution/drift/release_estimate.json",
        "attribution/drift/forward_particles.npz",
    ):
        path = run_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    return run_root, payload


def test_versioned_reanalysis_replaces_only_declared_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _case(tmp_path)
    captured: dict[str, pd.DataFrame] = {}

    def fake_outputs(output_dir: Path, ais_path: Path, *_: Path) -> dict[str, object]:
        captured["ais"] = pd.read_csv(ais_path, dtype={"mmsi": str})
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "candidate_ranking.png").write_bytes(b"chart")
        (output_dir / "attribution_map.png").write_bytes(b"map")
        return {
            "candidates": [
                {
                    "rank": 1,
                    "mmsi": "111000111",
                    "vessel_name": "ALPHA",
                    "total_score": 0.86,
                    "data_quality": 0.95,
                    "forward_error_km": 1.5,
                },
                {
                    "rank": 2,
                    "mmsi": "222000222",
                    "vessel_name": "BRAVO",
                    "total_score": 0.72,
                    "data_quality": 0.90,
                    "forward_error_km": 2.0,
                },
            ]
        }

    monkeypatch.setattr("espada.live_reanalysis.write_attribution_outputs", fake_outputs)
    readiness = load_live_reanalysis(run_root)
    assert readiness["status"] == "READY"
    result = run_live_reanalysis(run_root)
    assert result["status"] == "COMPLETE"
    assert result["version"] == "v001"
    assert result["comparison"]["top_candidate_changed"] is True
    assert result["original_attribution_unchanged"] is True
    merged = captured["ais"]
    alpha = merged.loc[merged["mmsi"] == "111000111"]
    assert len(alpha) == 3
    assert 103.81 not in alpha["longitude"].tolist()
    assert 103.91 in alpha["longitude"].tolist()
    assert result["merge_audit"]["baseline_rows_replaced"] == 2

    repeated = run_live_reanalysis(run_root)
    assert repeated["version"] == "v001"
    assert repeated["reused_existing_version"] is True


def test_reanalysis_refuses_tampered_admitted_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, payload = _case(tmp_path)
    payload.write_text("changed after admission", encoding="utf-8")
    monkeypatch.setattr(
        "espada.live_reanalysis.write_attribution_outputs",
        lambda *_args, **_kwargs: pytest.fail("ranker must not run after a hash failure"),
    )
    with pytest.raises(RuntimeError, match="payload hash failed"):
        run_live_reanalysis(run_root)


def test_new_admitted_evidence_creates_the_next_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, _ = _case(tmp_path)

    def fake_outputs(output_dir: Path, *_args: Path) -> dict[str, object]:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "candidate_ranking.png").write_bytes(b"chart")
        (output_dir / "attribution_map.png").write_bytes(b"map")
        return {
            "candidates": [
                {
                    "rank": 1,
                    "mmsi": "111000111",
                    "vessel_name": "ALPHA",
                    "total_score": 0.86,
                    "data_quality": 0.95,
                    "forward_error_km": 1.5,
                },
                {
                    "rank": 2,
                    "mmsi": "222000222",
                    "vessel_name": "BRAVO",
                    "total_score": 0.72,
                    "data_quality": 0.90,
                    "forward_error_km": 2.0,
                },
            ]
        }

    monkeypatch.setattr("espada.live_reanalysis.write_attribution_outputs", fake_outputs)
    first = run_live_reanalysis(run_root)
    assert first["version"] == "v001"

    new_payload_text = (
        "timestamp,mmsi,vessel_name,lon,lat,source\n"
        "2026-09-04T07:30:00Z,111000111,ALPHA,103.93,1.06,Coastal VTS\n"
    )
    new_payload = run_root / "follow_up/intake/payloads/RET-002.txt"
    new_payload.write_text(new_payload_text, encoding="utf-8")
    register_path = run_root / "follow_up/intake/evidence_intake_register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    second_receipt = {
        **register["receipts"][0],
        "receipt_id": "RET-002",
        "payload_sha256": hashlib.sha256(new_payload.read_bytes()).hexdigest(),
        "payload_file": new_payload.relative_to(run_root).as_posix(),
    }
    register["receipts"].append(second_receipt)
    _write_json(register_path, register)

    readiness = load_live_reanalysis(run_root)
    assert readiness["status"] == "READY"
    assert readiness["next_version"] == "v002"
    second = run_live_reanalysis(run_root)
    assert second["version"] == "v002"
