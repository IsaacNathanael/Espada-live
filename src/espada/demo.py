from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from .attribution import write_attribution_outputs
from .ais import normalize_ais_csv
from .dashboard import generate_dashboard
from .environment import load_environment, write_environment_outputs
from .models import Forcing
from .synthetic_ais import generate_synthetic_ais
from .slick import write_slick_from_particles
from .verification import VerificationConfig, run_verification


def run_demo(
    output_dir: Path,
    *,
    seed: int = 26143,
    particles: int = 2_000,
    ensemble_members: int = 20,
    check_opendrift: bool = True,
    environment_mode: str = "synthetic",
    environment_cache: Path | None = None,
) -> dict:
    """Run the complete offline validation path and evaluate it after inference."""
    output_dir = Path(output_dir)
    physics_dir = output_dir / "physics"
    environment_cache = Path(environment_cache or output_dir / "environment_cache.json")
    environment = load_environment(environment_mode, environment_cache)
    environment_result = write_environment_outputs(environment, output_dir / "environment")

    config = VerificationConfig(
        seed=seed,
        particles=particles,
        ensemble_members=ensemble_members,
    )
    true_forcing = None
    believed_forcing = None
    provenance = {
        "mode": environment.active_mode,
        "source": environment.source,
        "sample_count": len(environment.frame),
        "temporal_resolution": environment.temporal_resolution,
    }
    if environment.active_mode in {"live", "cache"}:
        release_index = min(3, len(environment.frame) - 1)
        end_index = min(release_index + int(config.age_hours), len(environment.frame))
        true_series = environment.frame.iloc[release_index:end_index].copy().reset_index(drop=True)
        true_forcing = Forcing(
            current_east_ms=float(true_series["current_east_ms"].mean()),
            current_north_ms=float(true_series["current_north_ms"].mean()),
            wind_east_ms=float(true_series["wind_east_ms"].mean()),
            wind_north_ms=float(true_series["wind_north_ms"].mean()),
            diffusivity_m2s=12.0,
        )
        believed_forcing = Forcing(
            current_east_ms=true_forcing.current_east_ms * 0.95 + 0.005,
            current_north_ms=true_forcing.current_north_ms * 0.95 - 0.003,
            wind_east_ms=true_forcing.wind_east_ms * 0.94,
            wind_north_ms=true_forcing.wind_north_ms * 0.94,
            windage=true_forcing.windage,
            diffusivity_m2s=true_forcing.diffusivity_m2s,
        )
        believed_series = true_series.copy()
        believed_series["current_east_ms"] = true_series["current_east_ms"] * 0.95 + 0.005
        believed_series["current_north_ms"] = true_series["current_north_ms"] * 0.95 - 0.003
        believed_series["wind_east_ms"] = true_series["wind_east_ms"] * 0.94
        believed_series["wind_north_ms"] = true_series["wind_north_ms"] * 0.94
        release_time = (
            environment.frame["time_utc"].iloc[release_index].to_pydatetime().replace(tzinfo=None)
        )
        config = VerificationConfig(
            seed=seed,
            release_time=release_time,
            particles=particles,
            ensemble_members=ensemble_members,
        )
        provenance["temporal_mode"] = f"time-varying ({environment.temporal_resolution})"
    else:
        true_series = None
        believed_series = None
    physics = run_verification(
        physics_dir,
        config,
        check_opendrift=check_opendrift,
        true_forcing=true_forcing,
        believed_forcing=believed_forcing,
        forcing_provenance=provenance,
        true_forcing_series=true_series,
        believed_forcing_series=believed_series,
    )
    with np.load(physics_dir / "forward_particles.npz") as observed:
        write_slick_from_particles(
            output_dir / "slick_observation.geojson",
            observed["lon"],
            observed["lat"],
            observation_time_utc=(config.release_time + timedelta(hours=config.age_hours)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )

    raw_ais_path = output_dir / "ais_tracks_raw.csv"
    ais_path = output_dir / "ais_tracks.csv"
    generate_synthetic_ais(
        physics_dir / "truth.json",
        raw_ais_path,
        seed=seed,
    )
    ais_quality = normalize_ais_csv(
        raw_ais_path,
        output_dir / "ais",
        normalized_path=ais_path,
    )
    attribution = write_attribution_outputs(
        output_dir,
        ais_path,
        physics_dir / "reverse_endpoints.npz",
        physics_dir / "release_estimate.json",
        physics_dir / "forward_particles.npz",
    )

    # The answer key is opened only here, after inference, to score the test.
    truth = json.loads((physics_dir / "truth.json").read_text(encoding="utf-8"))
    expected_mmsi = "419000123"
    actual_mmsi = attribution["top_candidate"]["mmsi"]
    acceptance = {
        "physics_passed": physics["status"] == "PASS",
        "fourteen_vessels_ranked": attribution["candidate_count"] == 14,
        "known_synthetic_source_ranked_first": actual_mmsi == expected_mmsi,
    }
    result = {
        "status": "PASS" if all(acceptance.values()) else "FAIL",
        "milestone": "offline reverse-drift and AIS attribution validation",
        "acceptance": acceptance,
        "environment": environment_result,
        "ais_quality": {
            "valid_rows": ais_quality["valid_rows"],
            "vessel_count": ais_quality["vessel_count"],
            "vessels_with_gaps_over_30_minutes": ais_quality["vessels_with_gaps_over_30_minutes"],
            "suspicious_jumps_over_60_knots": ais_quality["suspicious_jumps_over_60_knots"],
        },
        "top_candidate": attribution["top_candidate"],
        "evaluation_only": {
            "expected_mmsi": expected_mmsi,
            "release_time_utc": truth["release_time_utc"],
        },
        "warning": "Synthetic validation only. A ranked candidate is not a finding of guilt.",
        "artifacts": [
            "attribution_map.png",
            "candidate_ranking.png",
            "candidates.json",
            "ais_tracks.csv",
            "ais_tracks_raw.csv",
            "ais/ais_quality.json",
            "ais/ais_quality.png",
            "physics/forward_slick.png",
            "physics/reverse_probability.png",
            "environment/environment_status.json",
            "environment/environment_timeseries.png",
            "dashboard.html",
            "slick_observation.geojson",
            "demo_result.json",
        ],
    }
    (output_dir / "demo_result.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
    generate_dashboard(output_dir)
    return result
