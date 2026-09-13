from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .attribution import _observed_cloud_xy_km, _score_track
from .coast import load_coast_mask
from .environment import load_cache
from .geo import local_xy_m, sample_polygon
from .models import Forcing
from .spatial_current import load_spatial_current_grid
from .silence import analyze_coverage_aware_silence
from .slick import load_slick
from .verification import infer_origins_spatial_timeseries, infer_origins_timeseries


def _image_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def search_release_window(
    slick_path: Path,
    environment_cache: Path,
    candidates_path: Path,
    output_dir: Path,
    *,
    ages_hours: tuple[float, ...] = (1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0),
    current_multipliers: tuple[float, ...] = (0.75, 1.0, 1.25),
    windages: tuple[float, ...] = (0.01, 0.02, 0.03),
    particles: int = 400,
    ensemble_members: int = 6,
    seed: int = 26143,
    spatial_current_grid: Path | None = None,
    land_mask: Path | None = None,
) -> dict[str, object]:
    """Search vessel and release-age hypotheses without receiving an answer key."""
    observation = load_slick(slick_path)
    environment = load_cache(environment_cache)
    candidates = pd.read_csv(candidates_path, dtype={"mmsi": str})
    if candidates["mmsi"].nunique() < 2:
        raise ValueError("Release-window search requires at least two candidate vessels")
    rng = np.random.default_rng(seed)
    observed_lon, observed_lat = sample_polygon(observation.polygon, particles, rng)
    observed_centroid = (float(np.mean(observed_lon)), float(np.mean(observed_lat)))
    observed_xy_km = _observed_cloud_xy_km(observed_lon, observed_lat, observed_centroid)
    expected_rows = int(candidates.groupby("mmsi").size().max())
    silence = analyze_coverage_aware_silence(candidates)
    silence_by_id = {item["mmsi"]: item for item in silence["vessels"]}
    observation_naive = observation.observation_time.to_pydatetime().replace(tzinfo=None)
    grid = load_spatial_current_grid(spatial_current_grid) if spatial_current_grid else None
    coast = load_coast_mask(land_mask) if land_mask else None
    if coast and not grid:
        raise ValueError("A land mask currently requires a spatial current grid")
    if grid and not grid.covers_bounds(tuple(float(value) for value in observation.polygon.bounds)):
        raise ValueError("Spatial current grid does not cover the observed slick bounds")
    rows: list[dict[str, object]] = []

    for age in ages_hours:
        release = observation.observation_time - pd.Timedelta(hours=age)
        history = environment.frame[(environment.frame["time_utc"] >= release) & (environment.frame["time_utc"] < observation.observation_time)].copy()
        if len(history) < 2:
            continue
        if grid and not grid.covers(release, observation.observation_time):
            continue
        for current_multiplier in current_multipliers:
            varied = history.copy()
            varied["current_east_ms"] *= current_multiplier
            varied["current_north_ms"] *= current_multiplier
            for windage in windages:
                if grid:
                    origin_lon, origin_lat = infer_origins_spatial_timeseries(
                        observed_lon,
                        observed_lat,
                        varied,
                        grid,
                        step_seconds=age * 3600.0 / len(varied),
                        seed=seed + 1,
                        ensemble_members=ensemble_members,
                        windage=windage,
                        current_multiplier=current_multiplier,
                        coast_mask=coast,
                    )
                else:
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
                        observed_xy_km,
                        forcing,
                        expected_rows,
                        silence_by_id.get(str(track["mmsi"].iloc[0])),
                        grid,
                        varied if grid else None,
                        current_multiplier,
                        coast,
                    )
                    for _, track in candidates.groupby("mmsi", sort=False)
                ]
                scored.sort(key=lambda item: item["total_score"], reverse=True)
                for rank, item in enumerate(scored, start=1):
                    rows.append(
                        {
                            "age_hours": age,
                            "estimated_release_time_utc": release.isoformat().replace("+00:00", "Z"),
                            "current_multiplier": current_multiplier,
                            "windage": windage,
                            "candidate_id": item["mmsi"],
                            "rank": rank,
                            "score": item["total_score"],
                            "forward_error_km": item["forward_error_km"],
                            "data_quality": item["data_quality"],
                        }
                    )

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("No release-age hypothesis had sufficient forcing coverage")
    summary = (
        frame.groupby("candidate_id")
        .agg(
            top_1_rate=("rank", lambda values: float((values == 1).mean())),
            top_3_rate=("rank", lambda values: float((values <= 3).mean())),
            median_score=("score", "median"),
            lower_quartile_score=("score", lambda values: float(values.quantile(0.25))),
            median_forward_error_km=("forward_error_km", "median"),
            scenarios=("rank", "size"),
        )
        .reset_index()
        .sort_values(["top_1_rate", "median_score"], ascending=[False, False])
    )
    summary["rank"] = range(1, len(summary) + 1)
    leader_id = str(summary.iloc[0]["candidate_id"])
    leader_rows = frame[frame["candidate_id"] == leader_id]
    age_summary = (
        leader_rows.groupby("age_hours")
        .agg(
            top_1_rate=("rank", lambda values: float((values == 1).mean())),
            top_3_rate=("rank", lambda values: float((values <= 3).mean())),
            median_score=("score", "median"),
            median_forward_error_km=("forward_error_km", "median"),
        )
        .reset_index()
    )
    best = age_summary.sort_values(["top_1_rate", "median_score"], ascending=[False, False]).iloc[0]
    peak_score = float(age_summary["median_score"].max())
    supported = age_summary[(age_summary["top_1_rate"] >= 0.5) & (age_summary["median_score"] >= 0.75 * peak_score)]
    if supported.empty:
        supported = age_summary.nlargest(1, "median_score")
    minimum_age = float(supported["age_hours"].min())
    maximum_age = float(supported["age_hours"].max())
    release_start = observation.observation_time - pd.Timedelta(hours=maximum_age)
    release_end = observation.observation_time - pd.Timedelta(hours=minimum_age)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "release_hypotheses.csv", index=False)
    summary.to_csv(output_dir / "candidate_age_robustness.csv", index=False)
    age_summary.to_csv(output_dir / "leader_age_support.csv", index=False)

    fig, axis = plt.subplots(figsize=(9.4, 4.9), constrained_layout=True, facecolor="#020a0c")
    axis.set_facecolor("#071c21")
    axis.plot(age_summary["age_hours"], age_summary["top_1_rate"] * 100, marker="o", color="#61f2d1", linewidth=2.5, label="Rank 1 support")
    axis.plot(age_summary["age_hours"], age_summary["top_3_rate"] * 100, marker="s", color="#ffbd4a", linewidth=2, label="Top 3 support")
    axis.axvspan(minimum_age, maximum_age, color="#61f2d1", alpha=0.12, label="Supported release-age window")
    axis.set_ylim(-3, 103)
    axis.set_xlabel("Hours before satellite observation", color="#91aaa6")
    axis.set_ylabel("Scenario support (%)", color="#91aaa6")
    axis.set_title(f"Release-time support for {leader_id}", color="#eafffb", fontsize=15)
    axis.tick_params(colors="#91aaa6")
    axis.grid(alpha=0.15)
    axis.legend(facecolor="#082128", edgecolor="#17434b", labelcolor="#dff8f4")
    for spine in axis.spines.values():
        spine.set_color("#17434b")
    chart_path = output_dir / "release_time_window.png"
    fig.savefig(chart_path, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)

    result = {
        "status": "PASS",
        "method": (
            "joint vessel and release-age hypothesis search with particle-local Copernicus currents"
            if grid
            else "joint vessel and release-age hypothesis search with current and windage perturbations"
        ),
        "spatial_current_grid": str(grid.path) if grid else None,
        "land_mask": str(coast.path) if coast else None,
        "answer_key_accessed": False,
        "scenarios_per_candidate": int(summary.iloc[0]["scenarios"]),
        "ages_tested_hours": sorted(float(value) for value in frame["age_hours"].unique()),
        "top_candidate_id": leader_id,
        "top_candidate_rank_1_rate": float(summary.iloc[0]["top_1_rate"]),
        "top_candidate_top_3_rate": float(summary.iloc[0]["top_3_rate"]),
        "best_supported_age_hours": float(best["age_hours"]),
        "supported_age_window_hours": [minimum_age, maximum_age],
        "estimated_release_window_utc": [
            release_start.isoformat().replace("+00:00", "Z"),
            release_end.isoformat().replace("+00:00", "Z"),
        ],
        "candidate_summary": summary.to_dict(orient="records"),
        "limitations": [
            "The time window is a declared hypothesis-support interval, not a calibrated posterior confidence interval.",
            "Age resolution is limited to the tested age grid and environmental-cache coverage.",
            *(
                ["Particles outside the downloaded current subset use its nearest boundary cell."]
                if grid
                else ["Currents are sampled at one representative analysis location."]
            ),
            "A vessel can rank well across multiple ages when its track is stationary or sparsely sampled.",
            "Operational conclusions still require human review and independent evidence.",
        ],
    }
    json_path = output_dir / "release_time_search.json"
    chart_uri = _image_uri(chart_path)
    candidate_rows = "".join(
        f"<tr><td>#{int(row['rank'])}</td><td>{html.escape(str(row['candidate_id']))}</td><td>{100*row['top_1_rate']:.1f}%</td><td>{100*row['top_3_rate']:.1f}%</td><td>{100*row['median_score']:.1f}%</td></tr>"
        for row in summary.to_dict(orient="records")
    )
    html_path = output_dir / "release_time_search.html"
    html_path.write_text(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA Release Time Search</title><style>:root{{--bg:#020a0c;--panel:#082128;--line:#17434b;--ink:#eafffb;--muted:#91aaa6;--mint:#61f2d1;--amber:#ffbd4a}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 15% 0,#104047 0,transparent 34%),var(--bg);color:var(--ink);font:14px/1.55 Inter,Segoe UI,sans-serif}}main{{width:min(1050px,calc(100% - 28px));margin:auto;padding:36px 0 60px}}.tag{{color:var(--mint);font-size:10px;font-weight:900;letter-spacing:.17em}}h1{{font:500 clamp(38px,6vw,68px)/1 Georgia,serif;margin:14px 0}}h1 em{{color:var(--mint);font-style:normal}}p,li{{color:var(--muted)}}.hero,.grid{{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}}.card{{padding:20px;border:1px solid var(--line);border-radius:17px;background:linear-gradient(145deg,#092a31,#05161a)}}.answer strong{{display:block;color:var(--amber);font:500 31px Georgia,serif}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}}.metrics div{{padding:11px;border:1px solid var(--line);border-radius:9px}}small{{display:block;color:var(--muted);font-size:9px;text-transform:uppercase}}.metrics b{{display:block;margin-top:5px;font-size:18px}}img{{display:block;width:100%;margin-top:10px;border-radius:10px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-top:1px solid var(--line);text-align:left;font-size:11px}}th{{color:var(--muted)}}.warn{{border-left:3px solid var(--amber);padding:11px 14px;background:rgba(255,189,74,.06)}}@media(max-width:720px){{.hero,.grid{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}}}</style></head><body><main><span class='tag'>ESPADA · UNKNOWN RELEASE TIME</span><section class='hero'><div><h1>Search the window.<br><em>Do not guess it.</em></h1><p>The answer key is absent. ESPADA tests vessel-age hypotheses while perturbing current strength and windage.</p></div><div class='card answer'><small>Most robust candidate</small><strong>{html.escape(leader_id)}</strong><div class='metrics'><div><small>Best age</small><b>{float(best['age_hours']):g} h</b></div><div><small>Supported window</small><b>{minimum_age:g}-{maximum_age:g} h</b></div><div><small>Top 1 support</small><b>{100*float(summary.iloc[0]['top_1_rate']):.1f}%</b></div></div></div></section><section class='grid' style='margin-top:14px'><article class='card'><h2>Release-time support</h2><img src='{chart_uri}' alt='Release-time hypothesis support'></article><article class='card'><h2>Candidate robustness</h2><table><thead><tr><th>Rank</th><th>Blinded ID</th><th>Top 1</th><th>Top 3</th><th>Median score</th></tr></thead><tbody>{candidate_rows}</tbody></table><p class='warn'>This is a hypothesis-support window, not a calibrated probability interval.</p></article></section></main></body></html>""",
        encoding="utf-8",
    )
    result["artifacts"] = {"json": str(json_path.resolve()), "html": str(html_path.resolve()), "chart": str(chart_path.resolve())}
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search ESPADA vessel and release-age hypotheses")
    parser.add_argument("--slick", type=Path, required=True)
    parser.add_argument("--environment-cache", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--spatial-current-grid", type=Path)
    parser.add_argument("--land-mask", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(search_release_window(args.slick, args.environment_cache, args.candidates, args.out, spatial_current_grid=args.spatial_current_grid, land_mask=args.land_mask), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
