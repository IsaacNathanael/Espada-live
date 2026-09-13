from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "espada-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .attribution import rank_candidates
from .decision import POLICY
from .environment import load_cache
from .geo import haversine_km
from .physics import advect_diffuse_spatial_timeseries, advect_diffuse_timeseries
from .slick import analyze_slick, write_slick_from_particles
from .spatial_current import SpatialCurrentGrid, load_spatial_current_grid


CONDITIONS = (
    "nominal",
    "source dropout",
    "position noise",
    "windage mismatch",
    "diffusion mismatch",
    "combined stress",
)


def _blind_id(mmsi: str) -> str:
    digest = hashlib.sha256(f"espada-digital-twin-v1:{mmsi}".encode()).hexdigest()
    return f"98{int(digest[:12], 16) % 10_000_000:07d}"


def _blind_ais(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    result = frame.copy()
    original = result["mmsi"].astype(str)
    mapping = {mmsi: _blind_id(mmsi) for mmsi in original.unique()}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Pseudonym collision; change the digital-twin namespace")
    result["mmsi"] = original.map(mapping)
    result["vessel_name"] = result["mmsi"].map(lambda value: f"Blinded {value}")
    return result, mapping


def _interpolated_position(track: pd.DataFrame, timestamp: pd.Timestamp) -> tuple[float, float]:
    ordered = track.sort_values("timestamp_utc")
    seconds = ordered["timestamp_utc"].map(lambda value: value.timestamp()).to_numpy(dtype=float)
    target = timestamp.timestamp()
    return (
        float(np.interp(target, seconds, ordered["longitude"].to_numpy(dtype=float))),
        float(np.interp(target, seconds, ordered["latitude"].to_numpy(dtype=float))),
    )


def _select_trials(ais: pd.DataFrame, observation_time: pd.Timestamp, count: int) -> list[dict]:
    options: list[dict] = []
    ages = (8.0, 10.0, 12.0, 16.0, 20.0, 24.0)
    for mmsi, track in ais.groupby("mmsi"):
        if len(track) < 3:
            continue
        for age in ages:
            release_time = observation_time - pd.Timedelta(hours=age)
            distances = (track["timestamp_utc"] - release_time).abs()
            index = distances.idxmin()
            if distances.loc[index] <= pd.Timedelta(hours=1.1):
                options.append(
                    {
                        "mmsi": str(mmsi),
                        "release_time": release_time,
                        "age_hours": age,
                        "track_rows": len(track),
                        "time_offset_minutes": distances.loc[index].total_seconds() / 60.0,
                    }
                )
                break
    options.sort(key=lambda item: (-item["track_rows"], item["time_offset_minutes"]))
    if len(options) < count:
        raise ValueError(f"Only {len(options)} sufficiently observed source tracks are available")
    return options[:count]


def _simulate_slick(
    track: pd.DataFrame,
    environment: pd.DataFrame,
    release_time: pd.Timestamp,
    observation_time: pd.Timestamp,
    rng: np.random.Generator,
    *,
    particles: int,
    windage: float,
    diffusivity_m2s: float,
    spatial_current_grid: SpatialCurrentGrid | None = None,
) -> tuple[np.ndarray, np.ndarray, list[tuple[float, float]]]:
    release_corridor: list[tuple[float, float]] = []
    all_lon: list[np.ndarray] = []
    all_lat: list[np.ndarray] = []
    # Three cohorts approximate a 90-minute continuous release.
    for delay_hours in (0.0, 0.75, 1.5):
        cohort_time = release_time + pd.Timedelta(hours=delay_hours)
        history = environment.loc[
            (environment["time_utc"] >= cohort_time)
            & (environment["time_utc"] < observation_time)
        ]
        if len(history) < 2:
            raise ValueError("Environmental cache does not cover the digital-twin interval")
        source_lon, source_lat = _interpolated_position(track, cohort_time)
        release_corridor.append((source_lon, source_lat))
        cohort_size = particles // 3
        duration_seconds = (observation_time - cohort_time).total_seconds()
        step_seconds = duration_seconds / len(history)
        if spatial_current_grid is not None:
            lon, lat = advect_diffuse_spatial_timeseries(
                np.full(cohort_size, source_lon),
                np.full(cohort_size, source_lat),
                history["time_utc"].tolist(),
                history["wind_east_ms"].to_numpy(),
                history["wind_north_ms"].to_numpy(),
                step_seconds,
                spatial_current_grid,
                rng,
                windage=windage,
                diffusivity_m2s=diffusivity_m2s,
            )
        else:
            lon, lat = advect_diffuse_timeseries(
                np.full(cohort_size, source_lon),
                np.full(cohort_size, source_lat),
                history["current_east_ms"].to_numpy(),
                history["current_north_ms"].to_numpy(),
                history["wind_east_ms"].to_numpy(),
                history["wind_north_ms"].to_numpy(),
                step_seconds,
                rng,
                windage=windage,
                diffusivity_m2s=diffusivity_m2s,
            )
        all_lon.append(lon)
        all_lat.append(lat)
    return np.concatenate(all_lon), np.concatenate(all_lat), release_corridor


def _disturb_ais(
    frame: pd.DataFrame,
    target_id: str,
    release_time: pd.Timestamp,
    condition: str,
    rng: np.random.Generator,
) -> pd.DataFrame:
    result = frame.copy()
    if condition in {"source dropout", "combined stress"}:
        separation = (result["timestamp_utc"] - release_time).abs()
        remove = result["mmsi"].eq(target_id) & (separation <= pd.Timedelta(hours=1.1))
        result = result.loc[~remove].copy()
    if condition in {"position noise", "combined stress"}:
        # Roughly 100-250 m of position noise at this latitude.
        sigma = 0.001 if condition == "position noise" else 0.0025
        result["longitude"] = result["longitude"].astype(float) + rng.normal(0, sigma, len(result))
        result["latitude"] = result["latitude"].astype(float) + rng.normal(0, sigma, len(result))
    return result


def _plot(path: Path, cases: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    colors = ["#087f7b" if rank == 1 else "#f49a24" if rank <= 3 else "#d05a4a" for rank in cases["source_rank"]]
    axes[0].bar(cases["case"], cases["source_rank"], color=colors)
    axes[0].axhline(3, color="#d05a4a", linestyle="--", label="Top-3 boundary")
    axes[0].invert_yaxis()
    axes[0].set_ylabel("Hidden source rank (lower is better)")
    axes[0].set_title("Known-source recovery in real traffic")
    axes[0].legend(frameon=False)
    bars = axes[1].bar(cases["case"], cases["origin_error_km"], color="#347fa0")
    axes[1].bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
    axes[1].axhline(15, color="#d05a4a", linestyle="--", label="15 km acceptance")
    axes[1].set_ylabel("Reverse-origin error (km)")
    axes[1].set_title("Physics recovery under model mismatch")
    axes[1].legend(frameon=False)
    for axis in axes:
        axis.tick_params(axis="x", rotation=25)
        axis.grid(axis="y", alpha=0.2)
    fig.suptitle("ESPADA digital twin · synthetic spills, real forcing and real traffic")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def run_digital_twin_suite(
    ais_path: Path,
    environment_path: Path,
    output_dir: Path,
    *,
    cases: int = 6,
    particles: int = 1500,
    seed: int = 26143,
    spatial_current_grid: Path | None = None,
) -> dict[str, object]:
    if not 1 <= cases <= len(CONDITIONS) or particles < 300:
        raise ValueError("Use 1-6 cases and at least 300 particles")
    ais = pd.read_csv(ais_path, dtype={"mmsi": str})
    required = {"timestamp_utc", "mmsi", "longitude", "latitude", "is_interpolated"}
    missing = required - set(ais.columns)
    if missing:
        raise ValueError(f"AIS file is missing columns: {sorted(missing)}")
    ais["timestamp_utc"] = pd.to_datetime(ais["timestamp_utc"], utc=True)
    environment_bundle = load_cache(environment_path)
    environment = environment_bundle.frame
    current_grid = (
        load_spatial_current_grid(spatial_current_grid) if spatial_current_grid else None
    )
    observation_time = min(ais["timestamp_utc"].max(), environment["time_utc"].max()) - pd.Timedelta(hours=2)
    trials = _select_trials(ais, observation_time, cases)
    if current_grid:
        earliest_release = min(trial["release_time"] for trial in trials)
        if not current_grid.covers(earliest_release, observation_time):
            raise ValueError("Spatial current grid does not cover the digital-twin trials")
    blinded, mapping = _blind_ais(ais)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    for index, (trial, condition) in enumerate(zip(trials, CONDITIONS), start=1):
        rng = np.random.default_rng(seed + index * 101)
        source_track = ais.loc[ais["mmsi"].astype(str) == trial["mmsi"]]
        windage = 0.03 if condition in {"windage mismatch", "combined stress"} else 0.02
        diffusivity = 20.0 if condition in {"diffusion mismatch", "combined stress"} else 12.0
        observed_lon, observed_lat, release_corridor = _simulate_slick(
            source_track,
            environment,
            trial["release_time"],
            observation_time,
            rng,
            particles=particles,
            windage=windage,
            diffusivity_m2s=diffusivity,
            spatial_current_grid=current_grid,
        )
        case_dir = output_dir / f"case-{index:02d}"
        slick_path = write_slick_from_particles(
            case_dir / "synthetic_slick.geojson",
            observed_lon,
            observed_lat,
            observation_time_utc=observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            source="CONTROLLED DIGITAL TWIN; synthetic spill generated with real forcing",
        )
        target_id = mapping[trial["mmsi"]]
        candidate_ais = _disturb_ais(blinded, target_id, trial["release_time"], condition, rng)
        candidate_ais["timestamp_utc"] = candidate_ais["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        candidate_path = case_dir / "pseudonymized_real_traffic.csv"
        candidate_ais.to_csv(candidate_path, index=False)
        analysis = analyze_slick(
            slick_path,
            environment_path,
            case_dir / "drift",
            age_hours=trial["age_hours"],
            particles=800,
            ensemble_members=8,
            seed=seed + index * 101 + 1,
            spatial_current_grid=spatial_current_grid,
        )
        candidates, *_ = rank_candidates(
            candidate_path,
            case_dir / "drift" / "reverse_endpoints.npz",
            case_dir / "drift" / "release_estimate.json",
            case_dir / "drift" / "forward_particles.npz",
        )
        match = next(item for item in candidates if item["mmsi"] == target_id)
        top = candidates[0]
        runner_up = candidates[1]
        score_margin = float(top["total_score"] - runner_up["total_score"])
        priority = POLICY["priority_review"]
        numeric_priority_ready = bool(
            float(top["total_score"]) >= priority["minimum_top_score"]
            and score_margin >= priority["minimum_score_margin"]
            and float(top["forward_error_km"]) <= priority["maximum_forward_error_km"]
            and float(top["forward_shape_error_km"])
            <= priority["maximum_forward_shape_error_km"]
            and float(top["data_quality"]) >= priority["minimum_data_quality"]
        )
        estimate = analysis["estimated_origin"]
        origin_error = min(
            haversine_km(
                point[0], point[1], float(estimate["longitude"]), float(estimate["latitude"])
            )
            for point in release_corridor
        )
        corridor_center = (
            float(np.mean([point[0] for point in release_corridor])),
            float(np.mean([point[1] for point in release_corridor])),
        )
        centroid_error = haversine_km(
            corridor_center[0],
            corridor_center[1],
            float(estimate["longitude"]),
            float(estimate["latitude"]),
        )
        rows.append(
            {
                "case": f"Twin {index}",
                "condition": condition,
                "age_hours": trial["age_hours"],
                "real_candidates": int(ais["mmsi"].nunique()),
                "source_track_rows_before_stress": trial["track_rows"],
                "source_rank": int(match["rank"]),
                "top_1": int(match["rank"]) == 1,
                "top_3": int(match["rank"]) <= 3,
                "origin_error_km": origin_error,
                "release_corridor_centroid_error_km": centroid_error,
                "forward_error_km": float(match["forward_error_km"]),
                "comparative_score": float(match["total_score"]),
                "top_score_margin": score_margin,
                "numeric_priority_gates_passed": numeric_priority_ready,
                "unsafe_false_priority": numeric_priority_ready and int(match["rank"]) != 1,
                "truth_windage": windage,
                "truth_diffusivity_m2s": diffusivity,
            }
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(output_dir / "digital_twin_cases.csv", index=False)
    top_3_rate = float(frame["top_3"].mean())
    false_priorities = int(frame["unsafe_false_priority"].sum())
    summary = {
        "status": "PASS" if top_3_rate >= 0.8 and false_priorities == 0 else "REVIEW",
        "evaluation": "controlled digital-twin suite",
        "data_composition": {
            "slicks": "synthetic continuous-release particle simulations",
            "currents": environment_bundle.source,
            "current_sampling": (
                "particle-local bilinear Copernicus grid"
                if current_grid
                else "time-varying current at one analysis point"
            ),
            "traffic": f"pseudonymized real GFW background; {int(ais['mmsi'].nunique())} vessels",
        },
        "cases": len(frame),
        "top_1_rate": float(frame["top_1"].mean()),
        "top_3_rate": top_3_rate,
        "safety": {
            "unsafe_false_priority_count": false_priorities,
            "passed": false_priorities == 0,
            "interpretation": "A wrong leading candidate must not pass the numeric priority-review gates.",
        },
        "origin_error_definition": "minimum distance from estimated origin to the 90-minute true release corridor",
        "median_origin_error_km": float(frame["origin_error_km"].median()),
        "p90_origin_error_km": float(frame["origin_error_km"].quantile(0.9)),
        "anti_leakage": "The ranker receives pseudonymized tracks and never receives the selected source identity.",
        "claim_boundary": (
            "This is a realistic controlled simulation, not real-spill attribution accuracy, "
            "not an external blind trial and not evidence against any vessel."
        ),
        "artifacts": ["digital_twin_summary.json", "digital_twin_cases.csv", "digital_twin_overview.png"],
    }
    (output_dir / "digital_twin_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _plot(output_dir / "digital_twin_overview.png", frame)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run realistic ESPADA digital-twin cases")
    parser.add_argument("--ais", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--spatial-current-grid", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=6)
    parser.add_argument("--particles", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=26143)
    args = parser.parse_args()
    result = run_digital_twin_suite(
        args.ais,
        args.environment,
        args.out,
        cases=args.cases,
        particles=args.particles,
        seed=args.seed,
        spatial_current_grid=args.spatial_current_grid,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
