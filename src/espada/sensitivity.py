from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
import numpy as np
import pandas as pd

from .attribution import _score_track
from .environment import load_cache
from .geo import haversine_km, local_xy_m, sample_polygon
from .models import Forcing
from .silence import analyze_coverage_aware_silence
from .slick import load_slick
from .verification import infer_origins_timeseries


def _image_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def run_historical_sensitivity(
    slick_path: Path,
    environment_cache: Path,
    candidates_path: Path,
    truth_registry_path: Path,
    output_dir: Path,
    *,
    ages_hours: tuple[float, ...] = (1.5, 2.0, 3.0, 4.0, 6.0),
    current_multipliers: tuple[float, ...] = (0.75, 1.0, 1.25),
    windages: tuple[float, ...] = (0.01, 0.02, 0.03),
    particles: int = 600,
    ensemble_members: int = 8,
    seed: int = 26143,
) -> dict[str, object]:
    """Stress-test a historical ranking across declared physics assumptions."""
    if particles < 100 or ensemble_members < 2:
        raise ValueError("Sensitivity ensemble is too small")
    observation = load_slick(slick_path)
    environment = load_cache(environment_cache)
    candidates = pd.read_csv(candidates_path, dtype={"mmsi": str})
    registry = json.loads(Path(truth_registry_path).read_text(encoding="utf-8"))
    target_id = str(registry["target_candidate_id"])
    ground_truth = registry["documented_grounding_position"]
    ground_lon = float(ground_truth["longitude"])
    ground_lat = float(ground_truth["latitude"])
    rng = np.random.default_rng(seed)
    observed_lon, observed_lat = sample_polygon(observation.polygon, particles, rng)
    observed_centroid = (float(np.mean(observed_lon)), float(np.mean(observed_lat)))
    expected_rows = int(candidates.groupby("mmsi").size().max())
    silence = analyze_coverage_aware_silence(candidates)
    silence_by_id = {item["mmsi"]: item for item in silence["vessels"]}
    observation_naive = observation.observation_time.to_pydatetime().replace(tzinfo=None)

    scenarios: list[dict[str, object]] = []
    for age in ages_hours:
        release = observation.observation_time - pd.Timedelta(hours=age)
        history = environment.frame[
            (environment.frame["time_utc"] >= release)
            & (environment.frame["time_utc"] < observation.observation_time)
        ].copy()
        if len(history) < 2:
            raise ValueError(f"Environmental cache has fewer than two steps for age {age}")
        for current_multiplier in current_multipliers:
            varied = history.copy()
            varied["current_east_ms"] *= current_multiplier
            varied["current_north_ms"] *= current_multiplier
            for windage in windages:
                origin_lon, origin_lat = infer_origins_timeseries(
                    observed_lon,
                    observed_lat,
                    varied,
                    step_seconds=age * 3600.0 / len(varied),
                    seed=seed + 1,
                    ensemble_members=ensemble_members,
                    windage=windage,
                )
                estimated_lon = float(np.mean(origin_lon))
                estimated_lat = float(np.mean(origin_lat))
                x, y = local_xy_m(origin_lon, origin_lat, estimated_lon, estimated_lat)
                radius_90 = float(np.quantile(np.hypot(x, y) / 1000.0, 0.90))
                forcing = Forcing(
                    current_east_ms=float(varied["current_east_ms"].mean()),
                    current_north_ms=float(varied["current_north_ms"].mean()),
                    wind_east_ms=float(varied["wind_east_ms"].mean()),
                    wind_north_ms=float(varied["wind_north_ms"].mean()),
                    windage=windage,
                    diffusivity_m2s=12.0,
                )
                scored = [
                    _score_track(
                        track,
                        release.to_pydatetime().replace(tzinfo=None),
                        observation_naive,
                        estimated_lon,
                        estimated_lat,
                        radius_90,
                        observed_centroid,
                        forcing,
                        expected_rows,
                        silence_by_id.get(str(track["mmsi"].iloc[0])),
                    )
                    for _, track in candidates.groupby("mmsi", sort=False)
                ]
                scored.sort(key=lambda item: item["total_score"], reverse=True)
                for rank, item in enumerate(scored, start=1):
                    item["rank"] = rank
                target = next(item for item in scored if item["mmsi"] == target_id)
                best_other = next(item for item in scored if item["mmsi"] != target_id)
                origin_error = haversine_km(
                    estimated_lon, estimated_lat, ground_lon, ground_lat
                )
                scenarios.append(
                    {
                        "age_hours": age,
                        "current_multiplier": current_multiplier,
                        "windage": windage,
                        "target_rank": target["rank"],
                        "target_score": target["total_score"],
                        "score_margin_to_best_other": target["total_score"]
                        - best_other["total_score"],
                        "forward_error_km": target["forward_error_km"],
                        "origin_error_km": origin_error,
                        "credible_radius_90_km": radius_90,
                        "known_position_inside_90pct_radius": origin_error <= radius_90,
                        "top_candidate_id": scored[0]["mmsi"],
                    }
                )

    frame = pd.DataFrame(scenarios)
    total = len(frame)
    top1_rate = float((frame["target_rank"] == 1).mean())
    top3_rate = float((frame["target_rank"] <= 3).mean())
    coverage_rate = float(frame["known_position_inside_90pct_radius"].mean())
    if top1_rate >= 0.80 and top3_rate == 1.0:
        verdict = "ROBUST TOP-1"
        interpretation = "The documented source remains first across most declared perturbations."
    elif top3_rate >= 0.80:
        verdict = "ROBUST SHORTLIST"
        interpretation = "The documented source is usually retained in the top three, but first place is assumption-sensitive."
    else:
        verdict = "ASSUMPTION-SENSITIVE"
        interpretation = "The source frequently leaves the top three; stronger local forcing data is required."

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "sensitivity_scenarios.csv"
    frame.to_csv(csv_path, index=False)

    cmap = ListedColormap(["#43e0bd", "#f7c55b", "#ef8f4e", "#e95d72", "#743c68"])
    norm = BoundaryNorm([0.5, 1.5, 2.5, 3.5, 4.5, 5.5], cmap.N)
    fig, axes = plt.subplots(
        1,
        len(current_multipliers),
        figsize=(13.2, 4.7),
        constrained_layout=True,
        facecolor="#020a0c",
    )
    axes = np.atleast_1d(axes)
    for axis, current_multiplier in zip(axes, current_multipliers):
        subset = frame[frame["current_multiplier"] == current_multiplier]
        matrix = subset.pivot(index="age_hours", columns="windage", values="target_rank")
        matrix = matrix.reindex(index=ages_hours, columns=windages)
        image = axis.imshow(matrix.to_numpy(), cmap=cmap, norm=norm, aspect="auto")
        for row in range(matrix.shape[0]):
            for column in range(matrix.shape[1]):
                rank = int(matrix.iloc[row, column])
                axis.text(column, row, f"#{rank}", ha="center", va="center", color="#031312", fontweight="bold")
        axis.set_title(f"Current × {current_multiplier:.2f}", color="#eafffb", fontsize=11)
        axis.set_xticks(range(len(windages)), [f"{100*w:.0f}%" for w in windages])
        axis.set_yticks(range(len(ages_hours)), [f"{age:g} h" for age in ages_hours])
        axis.set_xlabel("Windage", color="#91aaa6")
        axis.set_ylabel("Assumed age", color="#91aaa6")
        axis.tick_params(colors="#91aaa6")
        axis.set_facecolor("#071c21")
    colorbar = fig.colorbar(image, ax=axes.tolist(), ticks=[1, 2, 3, 4, 5], shrink=0.88)
    colorbar.set_label("Known-source rank", color="#91aaa6")
    colorbar.ax.tick_params(colors="#91aaa6")
    fig.suptitle("MV Wakashio rank under 45 physics assumptions", color="#eafffb", fontsize=16)
    chart_path = output_dir / "sensitivity_rank_matrix.png"
    fig.savefig(chart_path, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)

    worst = frame.sort_values(
        ["target_rank", "score_margin_to_best_other"], ascending=[False, True]
    ).head(8)
    worst_rows = "".join(
        "<tr>"
        f"<td>{row.age_hours:g} h</td><td>{row.current_multiplier:.2f}×</td>"
        f"<td>{100*row.windage:.0f}%</td><td>#{int(row.target_rank)}</td>"
        f"<td>{100*row.target_score:.1f}%</td><td>{row.origin_error_km:.2f} km</td>"
        "</tr>"
        for row in worst.itertuples()
    )
    report = {
        "status": "PASS",
        "evaluation_type": "declared-assumption sensitivity analysis",
        "verdict": verdict,
        "interpretation": interpretation,
        "scenarios": total,
        "factor_grid": {
            "age_hours": list(ages_hours),
            "current_multipliers": list(current_multipliers),
            "windage": list(windages),
        },
        "target": registry["identity"],
        "top_1_rate": top1_rate,
        "top_3_rate": top3_rate,
        "worst_rank": int(frame["target_rank"].max()),
        "median_rank": float(frame["target_rank"].median()),
        "known_position_inside_90pct_radius_rate": coverage_rate,
        "median_origin_error_km": float(frame["origin_error_km"].median()),
        "limitations": [
            "Sensitivity rates describe this one reconstruction and are not population accuracy.",
            "The sweep perturbs current magnitude, windage and spill age; it does not cover every model structural error.",
            "Coastal bathymetry, boom deployment and shoreline retention are not represented.",
            "The documented wreck position is case-file evidence, not a continuous incident-time AIS track.",
        ],
        "artifacts": {
            "scenario_csv": str(csv_path.resolve()),
            "rank_matrix": str(chart_path.resolve()),
        },
    }
    json_path = output_dir / "sensitivity_report.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    limitations_html = "".join(f"<li>{html.escape(item)}</li>" for item in report["limitations"])
    chart_uri = _image_uri(chart_path)
    html_path = output_dir / "sensitivity_report.html"
    html_path.write_text(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA · Robustness audit</title><style>
        :root{{--bg:#020a0c;--panel:#072128;--line:#17434b;--ink:#eafffb;--muted:#91aaa6;--mint:#61f2d1;--amber:#ffbd4a}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 15% 0,#104047 0,transparent 34%),var(--bg);color:var(--ink);font-family:Inter,Segoe UI,sans-serif}}main{{width:min(1120px,calc(100% - 28px));margin:auto;padding:36px 0 60px}}.tag{{color:var(--mint);font-size:11px;font-weight:900;letter-spacing:.16em}}h1{{font:500 clamp(38px,6vw,70px)/.98 Georgia,serif;margin:14px 0}}h1 em{{color:var(--mint);font-style:normal}}p,li{{color:var(--muted);line-height:1.6}}.card{{background:linear-gradient(145deg,#092a31,#05161a);border:1px solid var(--line);border-radius:18px;padding:21px}}.hero{{display:grid;grid-template-columns:1.1fr .9fr;gap:14px;align-items:stretch}}.verdict{{display:flex;flex-direction:column;justify-content:center}}.verdict b{{font:500 37px Georgia,serif;color:var(--amber)}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:15px 0}}.metric small{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase}}.metric strong{{display:block;margin-top:7px;font:500 28px Georgia,serif}}img{{width:100%;display:block;border-radius:12px;margin-top:10px}}.grid{{display:grid;grid-template-columns:1.25fr .75fr;gap:14px;margin-top:14px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-top:1px solid var(--line);font-size:12px;text-align:left}}th{{color:var(--muted)}}.warn{{border-left:3px solid var(--amber);padding:13px 16px;background:rgba(255,189,74,.06);color:#e8d5a9;font-size:12px}}@media(max-width:760px){{.hero,.grid{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr 1fr}}}}
        </style></head><body><main><span class='tag'>ESPADA · ASSUMPTION STRESS TEST</span><section class='hero'><div><h1>Does the answer survive <em>uncertainty?</em></h1><p>Forty-five reruns vary spill age, current magnitude and windage. Every run keeps vessel identities blinded.</p></div><div class='card verdict'><b>{verdict}</b><p>{html.escape(interpretation)}</p></div></section><section class='metrics'><div class='card metric'><small>Top-1 stability</small><strong>{100*top1_rate:.1f}%</strong></div><div class='card metric'><small>Top-3 retention</small><strong>{100*top3_rate:.1f}%</strong></div><div class='card metric'><small>Worst rank</small><strong>#{int(frame['target_rank'].max())}</strong></div><div class='card metric'><small>90% zone coverage</small><strong>{100*coverage_rate:.1f}%</strong></div></section><section class='card'><h2>Rank stability matrix</h2><p>Green cells are rank #1. Each panel applies a different current-speed multiplier.</p><img src='{chart_uri}' alt='Wakashio sensitivity rank matrix'></section><section class='grid'><div class='card'><h2>Most difficult assumptions</h2><table><thead><tr><th>Age</th><th>Current</th><th>Windage</th><th>Rank</th><th>Score</th><th>Origin error</th></tr></thead><tbody>{worst_rows}</tbody></table></div><div class='card'><h2>Interpret correctly</h2><div class='warn'>This is robustness for one known-source case—not “model accuracy.” It shows whether a conclusion collapses when reasonable assumptions change.</div><ul>{limitations_html}</ul></div></section></main></body></html>""",
        encoding="utf-8",
    )
    report["artifacts"]["html_report"] = str(html_path.resolve())
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stress-test an ESPADA historical case")
    parser.add_argument("--slick", type=Path, required=True)
    parser.add_argument("--environment-cache", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_historical_sensitivity(
        args.slick,
        args.environment_cache,
        args.candidates,
        args.truth,
        args.out,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
