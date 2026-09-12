from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from espada.replay import build_forensic_replay


def test_forensic_replay_exports_gif_and_manifest(tmp_path: Path) -> None:
    case = tmp_path / "case"
    physics = case / "physics"
    physics.mkdir(parents=True)
    observed_lon = np.linspace(72.10, 72.13, 60)
    observed_lat = 18.10 + 0.004 * np.sin(np.linspace(0, 6, 60))
    origin_lon = np.tile(np.linspace(72.00, 72.03, 60), 5)
    origin_lat = np.tile(18.00 + 0.004 * np.sin(np.linspace(0, 6, 60)), 5)
    np.savez_compressed(physics / "forward_particles.npz", lon=observed_lon, lat=observed_lat)
    np.savez_compressed(physics / "reverse_endpoints.npz", lon=origin_lon, lat=origin_lat)
    (physics / "release_estimate.json").write_text(
        json.dumps({"assumed_age_hours": 12}), encoding="utf-8"
    )
    (physics / "truth.json").write_text(
        json.dumps({"release_lon": 72.01, "release_lat": 18.00}), encoding="utf-8"
    )
    ranking = {
        "candidate_count": 2,
        "top_candidate": {
            "rank": 1,
            "mmsi": "419000123",
            "vessel_name": "MV TEST",
            "total_score": 0.91,
        },
    }
    (case / "candidates.json").write_text(json.dumps(ranking), encoding="utf-8")
    times = pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC")
    ais = pd.DataFrame(
        {
            "timestamp_utc": list(times) * 2,
            "mmsi": ["419000123"] * 10 + ["419000456"] * 10,
            "vessel_name": ["MV TEST"] * 10 + ["MV DECOY"] * 10,
            "longitude": list(np.linspace(72.0, 72.12, 10)) + list(np.linspace(71.9, 71.95, 10)),
            "latitude": list(np.linspace(18.0, 18.1, 10)) + list(np.linspace(18.2, 18.25, 10)),
        }
    )
    ais.to_csv(case / "ais_tracks.csv", index=False)

    result = build_forensic_replay(
        case, tmp_path / "replay", frames=12, fps=6, particle_sample=50
    )

    assert result["status"] == "PASS"
    assert Path(result["output"]).stat().st_size > 1_000
    manifest = json.loads((tmp_path / "replay" / "replay_manifest.json").read_text())
    assert manifest["truth_reveal_policy"].endswith("final replay phase.")
    assert manifest["top_candidate"]["mmsi"] == "419000123"
