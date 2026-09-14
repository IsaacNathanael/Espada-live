from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .geo import haversine_km
from .physics import advect_diffuse_spatial_timeseries
from .spatial_current import SpatialCurrentGrid, load_spatial_current_grid


@dataclass(frozen=True)
class PhysicsBenchmarkConfig:
    start_time: datetime
    duration_hours: float = 10.0
    step_minutes: float = 60.0
    maximum_endpoint_separation_m: float = 250.0


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _seed_points(grid: SpatialCurrentGrid) -> tuple[np.ndarray, np.ndarray]:
    """Use five deterministic interior points so no trajectory starts on an edge."""
    min_lon, min_lat, max_lon, max_lat = grid.bounds
    width = max_lon - min_lon
    height = max_lat - min_lat
    fractions = np.asarray(
        [
            [0.35, 0.35],
            [0.50, 0.35],
            [0.65, 0.50],
            [0.50, 0.65],
            [0.35, 0.65],
        ],
        dtype=float,
    )
    return min_lon + width * fractions[:, 0], min_lat + height * fractions[:, 1]


def _step_count(config: PhysicsBenchmarkConfig) -> int:
    count = config.duration_hours * 60.0 / config.step_minutes
    rounded = int(round(count))
    if rounded < 1 or not np.isclose(count, rounded):
        raise ValueError("Duration must be an exact multiple of the step interval")
    return rounded


def _espada_trajectory(
    grid: SpatialCurrentGrid,
    lon: np.ndarray,
    lat: np.ndarray,
    config: PhysicsBenchmarkConfig,
    *,
    reverse: bool,
) -> tuple[np.ndarray, np.ndarray]:
    count = _step_count(config)
    step = timedelta(minutes=config.step_minutes)
    if reverse:
        # Backward Euler evaluates each interval at its later endpoint.
        timestamps = [config.start_time + step * (index + 1) for index in range(count)]
    else:
        timestamps = [config.start_time + step * index for index in range(count)]
    zeros = np.zeros(count, dtype=float)
    return advect_diffuse_spatial_timeseries(
        lon,
        lat,
        timestamps,
        zeros,
        zeros,
        config.step_minutes * 60.0,
        grid,
        np.random.default_rng(26143),
        windage=0.0,
        diffusivity_m2s=0.0,
        reverse=reverse,
    )


def _opendrift_trajectory(
    current_file: Path,
    lon: np.ndarray,
    lat: np.ndarray,
    config: PhysicsBenchmarkConfig,
    *,
    reverse: bool,
) -> tuple[np.ndarray, np.ndarray]:
    from opendrift.models.oceandrift import OceanDrift
    from opendrift.readers import reader_constant, reader_netCDF_CF_generic

    model = OceanDrift(loglevel=50)
    model.add_reader(reader_netCDF_CF_generic.Reader(str(current_file)))
    model.add_reader(
        reader_constant.Reader(
            {
                "x_wind": 0.0,
                "y_wind": 0.0,
                "horizontal_diffusivity": 0.0,
                "land_binary_mask": 0,
            }
        )
    )
    model.set_config("drift:advection_scheme", "euler")
    seed_time = config.start_time + timedelta(hours=config.duration_hours) if reverse else config.start_time
    seed_time = seed_time.replace(tzinfo=None)
    model.seed_elements(
        lon=np.asarray(lon, dtype=float),
        lat=np.asarray(lat, dtype=float),
        number=len(lon),
        time=seed_time,
        wind_drift_factor=0.0,
    )
    seconds = int(round(config.step_minutes * 60.0))
    model.run(
        duration=timedelta(hours=config.duration_hours),
        time_step=-seconds if reverse else seconds,
        time_step_output=-seconds if reverse else seconds,
    )
    # OpenDrift may serialize equal-time backward seeds in reverse trajectory
    # order. Match the result's recorded first position back to the caller's
    # seed order before comparing endpoints.
    return restore_trajectory_order(
        np.asarray(lon, dtype=float),
        np.asarray(lat, dtype=float),
        np.asarray(model.result.lon[:, 0], dtype=float),
        np.asarray(model.result.lat[:, 0], dtype=float),
        np.asarray(model.result.lon[:, -1], dtype=float),
        np.asarray(model.result.lat[:, -1], dtype=float),
    )


def restore_trajectory_order(
    seed_lon: np.ndarray,
    seed_lat: np.ndarray,
    result_start_lon: np.ndarray,
    result_start_lat: np.ndarray,
    result_end_lon: np.ndarray,
    result_end_lat: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Restore caller seed order after a trajectory engine serializes particles."""
    arrays = [
        np.asarray(values, dtype=float)
        for values in (
            seed_lon,
            seed_lat,
            result_start_lon,
            result_start_lat,
            result_end_lon,
            result_end_lat,
        )
    ]
    if not arrays[0].size or any(values.shape != arrays[0].shape for values in arrays[1:]):
        raise ValueError("Trajectory-order arrays must be non-empty and aligned")
    if not np.isfinite(np.concatenate(arrays)).all():
        raise ValueError("Trajectory-order arrays must be finite")
    unmatched = set(range(len(result_start_lon)))
    order: list[int] = []
    for longitude, latitude in zip(arrays[0], arrays[1], strict=True):
        match = min(
            unmatched,
            key=lambda index: haversine_km(
                float(longitude),
                float(latitude),
                float(arrays[2][index]),
                float(arrays[3][index]),
            ),
        )
        match_error_m = 1_000.0 * haversine_km(
            float(longitude),
            float(latitude),
            float(arrays[2][match]),
            float(arrays[3][match]),
        )
        if match_error_m > 20.0:
            raise ValueError(f"Could not match OpenDrift trajectory to seed within 20 m: {match_error_m:.2f} m")
        unmatched.remove(match)
        order.append(match)
    return arrays[4][order], arrays[5][order]


def _separation_m(
    first_lon: np.ndarray,
    first_lat: np.ndarray,
    second_lon: np.ndarray,
    second_lat: np.ndarray,
) -> np.ndarray:
    return np.asarray(
        [
            1_000.0 * haversine_km(float(a), float(b), float(c), float(d))
            for a, b, c, d in zip(first_lon, first_lat, second_lon, second_lat, strict=True)
        ],
        dtype=float,
    )


def summarize_endpoint_parity(
    forward_separation_m: np.ndarray,
    reverse_separation_m: np.ndarray,
    maximum_endpoint_separation_m: float,
) -> dict[str, object]:
    forward = np.asarray(forward_separation_m, dtype=float)
    reverse = np.asarray(reverse_separation_m, dtype=float)
    if forward.size == 0 or forward.shape != reverse.shape:
        raise ValueError("Forward and reverse benchmark arrays must be non-empty and aligned")
    combined = np.concatenate([forward, reverse])
    if not np.isfinite(combined).all() or maximum_endpoint_separation_m <= 0:
        raise ValueError("Benchmark values must be finite and the acceptance limit positive")
    return {
        "trajectories": int(combined.size),
        "forward_median_separation_m": float(np.median(forward)),
        "forward_maximum_separation_m": float(np.max(forward)),
        "reverse_median_separation_m": float(np.median(reverse)),
        "reverse_maximum_separation_m": float(np.max(reverse)),
        "combined_p90_separation_m": float(np.quantile(combined, 0.90)),
        "combined_maximum_separation_m": float(np.max(combined)),
        "acceptance_limit_m": float(maximum_endpoint_separation_m),
        "all_within_acceptance": bool(np.all(combined <= maximum_endpoint_separation_m)),
    }


def _write_chart(path: Path, forward: np.ndarray, reverse: np.ndarray, limit: float) -> None:
    indices = np.arange(1, len(forward) + 1)
    plot_forward = np.maximum(forward, 0.05)
    plot_reverse = np.maximum(reverse, 0.05)
    figure, axis = plt.subplots(figsize=(8.5, 4.6), constrained_layout=True)
    figure.patch.set_facecolor("#06131a")
    axis.set_facecolor("#0a2029")
    axis.plot(indices, plot_forward, "o-", color="#58e5df", label="Forward")
    axis.plot(indices, plot_reverse, "s-", color="#ffbd59", label="Reverse")
    axis.axhline(limit, color="#ff7187", linestyle="--", label=f"Acceptance · {limit:.0f} m")
    axis.set_yscale("log")
    axis.set_ylim(0.1, max(limit * 1.6, float(np.max([plot_forward, plot_reverse])) * 2.0))
    axis.set_xlabel("Copernicus-grid seed point", color="#dffaf7")
    axis.set_ylabel("ESPADA ↔ OpenDrift endpoint separation (m, log scale)", color="#dffaf7")
    axis.set_xticks(indices)
    axis.tick_params(colors="#a8c0c5")
    axis.grid(alpha=0.15, color="#dffaf7")
    for spine in axis.spines.values():
        spine.set_color("#31515d")
    legend = axis.legend(frameon=False)
    for text in legend.get_texts():
        text.set_color("#dffaf7")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_report(path: Path, result: dict[str, object]) -> None:
    metrics = result["metrics"]
    rows = "".join(
        f"<tr><td>{row['direction'].title()}</td><td>{row['seed']}</td>"
        f"<td>{row['start_longitude']:.5f}, {row['start_latitude']:.5f}</td>"
        f"<td>{row['separation_m']:.2f} m</td></tr>"
        for row in result["comparisons"]
    )
    verdict = "PASS" if result["status"] == "PASS" else "FAIL"
    document = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA physics parity benchmark</title><style>
    :root{{--bg:#041015;--panel:#0a2029;--line:#21404b;--ink:#eafff9;--muted:#92abb1;--cyan:#58e5df;--amber:#ffbd59;--green:#67e8a5;--red:#ff7187}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#113b46,transparent 30%),var(--bg);color:var(--ink);font:15px/1.55 Inter,Segoe UI,Arial,sans-serif}}main{{max-width:1050px;margin:auto;padding:42px 22px 70px}}.tag{{color:var(--cyan);font-size:12px;font-weight:900;letter-spacing:.16em}}h1{{font:500 clamp(34px,6vw,68px)/1.02 Georgia,serif;margin:14px 0}}.lead{{max-width:790px;color:var(--muted);font-size:18px}}.verdict{{display:inline-block;margin:14px 0 24px;padding:9px 13px;border:1px solid var(--green);border-radius:999px;color:var(--green);font-weight:900}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:18px}}.card small{{display:block;color:var(--muted);text-transform:uppercase;font-size:10px;letter-spacing:.1em}}.card b{{display:block;font-size:25px;margin-top:6px;color:var(--cyan)}}section{{margin-top:14px}}img{{width:100%;border-radius:10px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}th{{color:var(--muted);font-size:11px;text-transform:uppercase}}.boundary{{border-left:3px solid var(--amber);padding:12px 14px;background:#33270d55;color:#e9d39f}}@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}.table{{overflow:auto}}}}</style></head><body><main><div class='tag'>ESPADA · INDEPENDENT DRIFT-SOLVER CHECK</div><h1>Same ocean currents.<br>Two independent engines.</h1><p class='lead'>ESPADA and OpenDrift 1.14.11 independently advected the same particles through the same real Copernicus Marine current grid. Their endpoints are compared in metres.</p><div class='verdict'>{verdict} · ALL {metrics['trajectories']} TRAJECTORIES WITHIN {metrics['acceptance_limit_m']:.0f} m</div><div class='grid'><div class='card'><small>Forward median</small><b>{metrics['forward_median_separation_m']:.2f} m</b></div><div class='card'><small>Reverse median</small><b>{metrics['reverse_median_separation_m']:.2f} m</b></div><div class='card'><small>P90 separation</small><b>{metrics['combined_p90_separation_m']:.2f} m</b></div><div class='card'><small>Worst separation</small><b>{metrics['combined_maximum_separation_m']:.2f} m</b></div></div><section class='card'><h2>Parity across the current grid</h2><img src='endpoint_parity.png' alt='Endpoint separation for ESPADA and OpenDrift trajectories'></section><section class='card table'><h2>Reproducible comparisons</h2><table><thead><tr><th>Direction</th><th>Seed</th><th>Starting position</th><th>Endpoint separation</th></tr></thead><tbody>{rows}</tbody></table></section><section class='boundary'><b>Claim boundary:</b> {html.escape(str(result['claim_boundary']))}</section></main></body></html>"""
    path.write_text(document, encoding="utf-8")


def run_physics_benchmark(
    current_file: Path,
    output_dir: Path,
    config: PhysicsBenchmarkConfig,
) -> dict[str, object]:
    current_file = Path(current_file).resolve()
    if not current_file.exists():
        raise FileNotFoundError(f"Copernicus current grid not found: {current_file}")
    grid = load_spatial_current_grid(current_file)
    end_time = config.start_time + timedelta(hours=config.duration_hours)
    if not grid.covers(config.start_time, end_time):
        raise ValueError(f"Current grid does not cover {_iso(config.start_time)} to {_iso(end_time)}")
    start_lon, start_lat = _seed_points(grid)
    espada_forward = _espada_trajectory(grid, start_lon, start_lat, config, reverse=False)
    opendrift_forward = _opendrift_trajectory(
        current_file, start_lon, start_lat, config, reverse=False
    )
    forward_separation = _separation_m(*espada_forward, *opendrift_forward)

    # Use one shared set of observed endpoints so the backward engines are
    # compared directly rather than through their separate forward errors.
    observed_lon, observed_lat = opendrift_forward
    espada_reverse = _espada_trajectory(grid, observed_lon, observed_lat, config, reverse=True)
    opendrift_reverse = _opendrift_trajectory(
        current_file, observed_lon, observed_lat, config, reverse=True
    )
    reverse_separation = _separation_m(*espada_reverse, *opendrift_reverse)
    metrics = summarize_endpoint_parity(
        forward_separation,
        reverse_separation,
        config.maximum_endpoint_separation_m,
    )
    comparisons = []
    for direction, lon, lat, separation in (
        ("forward", start_lon, start_lat, forward_separation),
        ("reverse", observed_lon, observed_lat, reverse_separation),
    ):
        comparisons.extend(
            {
                "direction": direction,
                "seed": index + 1,
                "start_longitude": float(lon[index]),
                "start_latitude": float(lat[index]),
                "separation_m": float(separation[index]),
            }
            for index in range(len(lon))
        )
    result: dict[str, object] = {
        "status": "PASS" if metrics["all_within_acceptance"] else "FAIL",
        "benchmark": "ESPADA particle-local current advection versus OpenDrift",
        "engines": {
            "espada": "explicit Euler with bilinear spatial and linear temporal current interpolation",
            "reference": "OpenDrift 1.14.11 OceanDrift with CF-netCDF reader and Euler advection",
        },
        "input": {
            "source": "Copernicus Marine Service",
            "current_file": str(current_file),
            "grid_bounds": list(grid.bounds),
            "grid_time_start_utc": _iso(grid.time_start),
            "grid_time_end_utc": _iso(grid.time_end),
        },
        "config": {**asdict(config), "start_time": _iso(config.start_time)},
        "metrics": metrics,
        "comparisons": comparisons,
        "claim_boundary": (
            "This verifies deterministic current-advection parity on one Copernicus subset. "
            "Windage, diffusion, oil weathering, beaching and forecast skill are intentionally excluded; "
            "the result is an engineering verification, not real-spill attribution accuracy."
        ),
        "artifacts": ["physics_benchmark.json", "endpoint_parity.png", "physics_benchmark.html"],
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_chart(
        output_dir / "endpoint_parity.png",
        forward_separation,
        reverse_separation,
        config.maximum_endpoint_separation_m,
    )
    (output_dir / "physics_benchmark.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    _write_report(output_dir / "physics_benchmark.html", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark ESPADA drift physics against OpenDrift")
    parser.add_argument("--current-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-time", type=datetime.fromisoformat, required=True)
    parser.add_argument("--duration-hours", type=float, default=10.0)
    parser.add_argument("--step-minutes", type=float, default=60.0)
    parser.add_argument("--maximum-separation-m", type=float, default=250.0)
    args = parser.parse_args()
    config = PhysicsBenchmarkConfig(
        start_time=args.start_time,
        duration_hours=args.duration_hours,
        step_minutes=args.step_minutes,
        maximum_endpoint_separation_m=args.maximum_separation_m,
    )
    result = run_physics_benchmark(args.current_file, args.out, config)
    print(json.dumps(result, indent=2, default=str))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
