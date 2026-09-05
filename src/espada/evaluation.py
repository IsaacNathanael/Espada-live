from __future__ import annotations

import base64
import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .attribution import rank_candidates
from .geo import haversine_km, local_xy_m
from .models import Forcing, format_utc
from .physics import advect_diffuse_constant
from .synthetic_ais import generate_synthetic_ais
from .verification import infer_origins_constant


CONDITIONS = (
    "nominal",
    "current bias",
    "wind bias",
    "AIS dropout",
    "AIS position noise",
    "combined stress",
)
POLLUTER_MMSI = "419000123"


@dataclass(frozen=True)
class EvaluationConfig:
    seed: int = 26143
    cases: int = 24
    particles: int = 400
    ensemble_members: int = 8


def _case_forcing(rng: np.random.Generator, condition: str) -> tuple[Forcing, Forcing]:
    truth = Forcing(
        current_east_ms=float(rng.uniform(0.08, 0.38)),
        current_north_ms=float(rng.uniform(-0.16, 0.16)),
        wind_east_ms=float(rng.uniform(2.0, 9.0)),
        wind_north_ms=float(rng.uniform(-4.0, 4.0)),
        diffusivity_m2s=float(rng.uniform(8.0, 18.0)),
    )
    current_sigma = 0.015
    wind_sigma = 0.30
    if condition in {"current bias", "combined stress"}:
        current_sigma = 0.075
    if condition in {"wind bias", "combined stress"}:
        wind_sigma = 1.65
    believed = Forcing(
        current_east_ms=float(truth.current_east_ms + rng.normal(0.0, current_sigma)),
        current_north_ms=float(truth.current_north_ms + rng.normal(0.0, current_sigma)),
        wind_east_ms=float(truth.wind_east_ms + rng.normal(0.0, wind_sigma)),
        wind_north_ms=float(truth.wind_north_ms + rng.normal(0.0, wind_sigma)),
        diffusivity_m2s=truth.diffusivity_m2s,
    )
    return truth, believed


def _disturb_ais(
    frame: pd.DataFrame,
    condition: str,
    release_time: datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    result = frame.copy()
    if condition in {"AIS dropout", "combined stress"}:
        timestamps = pd.to_datetime(result["timestamp_utc"], utc=True).dt.tz_localize(None)
        window_hours = 0.75 if condition == "AIS dropout" else 1.35
        hours = np.abs((timestamps - release_time).dt.total_seconds() / 3600.0)
        remove = result["mmsi"].astype(str).eq(POLLUTER_MMSI) & (hours <= window_hours)
        result = result.loc[~remove].copy()
    if condition in {"AIS position noise", "combined stress"}:
        sigma = 0.0035 if condition == "AIS position noise" else 0.008
        result["longitude"] += rng.normal(0.0, sigma, len(result))
        result["latitude"] += rng.normal(0.0, sigma, len(result))
    return result.reset_index(drop=True)


def _run_case(
    case_index: int,
    condition: str,
    config: EvaluationConfig,
    work_dir: Path,
) -> dict:
    case_seed = config.seed + case_index * 101
    rng = np.random.default_rng(case_seed)
    release_lon = float(rng.uniform(71.20, 71.75))
    release_lat = float(rng.uniform(18.40, 19.00))
    release_time = datetime(2026, 1, 1) + timedelta(days=case_index, hours=2)
    age_hours = float(rng.uniform(9.0, 28.0))
    true_forcing, believed_forcing = _case_forcing(rng, condition)

    observed_lon, observed_lat = advect_diffuse_constant(
        np.full(config.particles, release_lon),
        np.full(config.particles, release_lat),
        age_hours * 3600.0,
        true_forcing,
        rng,
    )
    origin_lon, origin_lat = infer_origins_constant(
        observed_lon,
        observed_lat,
        age_hours,
        believed_forcing,
        seed=case_seed + 1,
        ensemble_members=config.ensemble_members,
    )
    estimated_lon = float(np.mean(origin_lon))
    estimated_lat = float(np.mean(origin_lat))
    origin_error_km = haversine_km(release_lon, release_lat, estimated_lon, estimated_lat)
    x, y = local_xy_m(origin_lon, origin_lat, estimated_lon, estimated_lat)
    radial_km = np.hypot(x, y) / 1000.0
    radius_50 = float(np.quantile(radial_km, 0.50))
    radius_90 = float(np.quantile(radial_km, 0.90))

    case_dir = work_dir / f"case-{case_index:03d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    observation_time = release_time + timedelta(hours=age_hours)
    truth = {
        "release_lon": release_lon,
        "release_lat": release_lat,
        "release_time_utc": format_utc(release_time),
        "observation_time_utc": format_utc(observation_time),
    }
    (case_dir / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    estimate = {
        "release_time_utc": format_utc(release_time),
        "observation_time_utc": format_utc(observation_time),
        "estimated_origin": {"longitude": estimated_lon, "latitude": estimated_lat},
        "credible_radius_50_km": radius_50,
        "credible_radius_90_km": radius_90,
        "assumed_age_hours": age_hours,
        "believed_forcing": believed_forcing.to_dict(),
    }
    (case_dir / "release_estimate.json").write_text(json.dumps(estimate), encoding="utf-8")
    np.savez_compressed(case_dir / "forward.npz", lon=observed_lon, lat=observed_lat)
    np.savez_compressed(case_dir / "reverse.npz", lon=origin_lon, lat=origin_lat)
    ais = generate_synthetic_ais(
        case_dir / "truth.json",
        case_dir / "ais.csv",
        seed=case_seed,
    )
    ais = _disturb_ais(ais, condition, release_time, rng)
    ais.to_csv(case_dir / "ais.csv", index=False)
    candidates, *_ = rank_candidates(
        case_dir / "ais.csv",
        case_dir / "reverse.npz",
        case_dir / "release_estimate.json",
        case_dir / "forward.npz",
    )
    polluter = next(item for item in candidates if item["mmsi"] == POLLUTER_MMSI)
    score_margin = float(candidates[0]["total_score"] - candidates[1]["total_score"])
    current_bias = float(
        np.hypot(
            true_forcing.current_east_ms - believed_forcing.current_east_ms,
            true_forcing.current_north_ms - believed_forcing.current_north_ms,
        )
    )
    wind_bias = float(
        np.hypot(
            true_forcing.wind_east_ms - believed_forcing.wind_east_ms,
            true_forcing.wind_north_ms - believed_forcing.wind_north_ms,
        )
    )
    return {
        "case": case_index + 1,
        "condition": condition,
        "seed": case_seed,
        "age_hours": age_hours,
        "origin_error_km": origin_error_km,
        "credible_radius_50_km": radius_50,
        "credible_radius_90_km": radius_90,
        "truth_in_50_region": origin_error_km <= radius_50,
        "truth_in_90_region": origin_error_km <= radius_90,
        "polluter_rank": int(polluter["rank"]),
        "polluter_score": float(polluter["total_score"]),
        "top_score_margin": score_margin,
        "top1_hit": int(polluter["rank"]) == 1,
        "top3_hit": int(polluter["rank"]) <= 3,
        "current_bias_ms": current_bias,
        "wind_bias_ms": wind_bias,
        "polluter_ais_rows": int((ais["mmsi"].astype(str) == POLLUTER_MMSI).sum()),
    }


def _condition_metrics(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for condition in CONDITIONS:
        group = frame.loc[frame["condition"] == condition]
        if group.empty:
            continue
        rows.append(
            {
                "condition": condition,
                "cases": len(group),
                "top1_accuracy": float(group["top1_hit"].mean()),
                "top3_accuracy": float(group["top3_hit"].mean()),
                "mean_reciprocal_rank": float((1.0 / group["polluter_rank"]).mean()),
                "median_origin_error_km": float(group["origin_error_km"].median()),
                "p90_origin_error_km": float(group["origin_error_km"].quantile(0.90)),
                "coverage_90": float(group["truth_in_90_region"].mean()),
            }
        )
    return rows


def _plot_overview(path: Path, frame: pd.DataFrame, breakdown: list[dict]) -> None:
    order = [item for item in CONDITIONS if item in set(frame["condition"])]
    lookup = {item["condition"]: item for item in breakdown}
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    colors = ["#087F7B", "#347FA0", "#6C78A8", "#C47B36", "#A86687", "#D05A4A"]

    data = [frame.loc[frame["condition"] == name, "origin_error_km"] for name in order]
    box = axes[0, 0].boxplot(data, tick_labels=order, patch_artist=True, showmeans=True)
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.78)
    axes[0, 0].axhline(5.0, color="#D05A4A", linestyle="--", linewidth=1.5, label="5 km target")
    axes[0, 0].set_ylabel("Origin error (km)")
    axes[0, 0].set_title("A · Origin recovery by stress condition")
    axes[0, 0].tick_params(axis="x", rotation=22)
    axes[0, 0].legend(frameon=False)
    axes[0, 0].grid(axis="y", alpha=0.2)

    x = np.arange(len(order))
    width = 0.36
    top1 = [100 * lookup[name]["top1_accuracy"] for name in order]
    top3 = [100 * lookup[name]["top3_accuracy"] for name in order]
    axes[0, 1].bar(x - width / 2, top1, width, label="Top-1", color="#087F7B")
    axes[0, 1].bar(x + width / 2, top3, width, label="Top-3", color="#F49A24")
    axes[0, 1].set_xticks(x, order, rotation=22, ha="right")
    axes[0, 1].set_ylim(0, 105)
    axes[0, 1].set_ylabel("Attribution accuracy (%)")
    axes[0, 1].set_title("B · Known-source retrieval")
    axes[0, 1].legend(frameon=False)
    axes[0, 1].grid(axis="y", alpha=0.2)

    coverage = [100 * frame["truth_in_50_region"].mean(), 100 * frame["truth_in_90_region"].mean()]
    bars = axes[1, 0].bar(["50% region", "90% region"], coverage, color=["#347FA0", "#6C78A8"])
    axes[1, 0].scatter([0, 1], [50, 90], marker="D", s=70, color="#D05A4A", label="Ideal coverage")
    axes[1, 0].bar_label(bars, fmt="%.1f%%", padding=4)
    axes[1, 0].set_ylim(0, 105)
    axes[1, 0].set_ylabel("Empirical truth coverage (%)")
    axes[1, 0].set_title("C · Uncertainty calibration check")
    axes[1, 0].legend(frameon=False)
    axes[1, 0].grid(axis="y", alpha=0.2)

    for color, condition in zip(colors, order):
        group = frame.loc[frame["condition"] == condition]
        axes[1, 1].scatter(
            group["origin_error_km"],
            100 * group["top_score_margin"],
            label=condition,
            s=45,
            alpha=0.85,
            color=color,
        )
    axes[1, 1].axvline(5.0, color="#D05A4A", linestyle="--", linewidth=1.2)
    axes[1, 1].axhline(0.0, color="#839197", linewidth=1.0)
    axes[1, 1].set_xlabel("Origin error (km)")
    axes[1, 1].set_ylabel("Top score separation (percentage points)")
    axes[1, 1].set_title("D · Confidence separation versus drift error")
    axes[1, 1].legend(frameon=False, fontsize=8, ncol=2)
    axes[1, 1].grid(alpha=0.2)

    fig.suptitle("Espada synthetic validation · physics and attribution robustness", fontsize=17)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _model_card() -> dict:
    return {
        "current_operational_core": {
            "machine_learning": False,
            "method": "Physics-based advection-diffusion ensemble plus transparent weighted evidence scoring",
            "score_is_probability": False,
        },
        "planned_sar_segmentation": {
            "model": "ResNet34-based U-Net",
            "status": "implemented_and_gpu_smoke_verified_not_fully_trained_or_integrated",
            "task": "Sentinel-1 VV oil-slick semantic segmentation",
            "baseline": "Adaptive dark-patch thresholding with morphology and wind/shape checks",
            "training_plan": "Fine-tune a pretrained encoder only after dataset and split verification",
            "evaluation_metrics": ["oil-class IoU", "Dice/F1", "precision", "recall", "false positives per scene"],
            "reference": "https://skytruth.org/cerulean/methods",
            "training_dataset": "https://doi.org/10.5281/zenodo.4672426",
            "split_policy": "acquisition-date grouped; no group may cross train, validation or test",
        },
    }


def _write_html(path: Path, summary: dict, plot_path: Path) -> None:
    image = base64.b64encode(plot_path.read_bytes()).decode("ascii")
    metrics = summary["overall"]
    rows = "".join(
        f"<tr><td>{item['condition']}</td><td>{item['cases']}</td><td>{item['top1_accuracy']*100:.1f}%</td><td>{item['top3_accuracy']*100:.1f}%</td><td>{item['median_origin_error_km']:.2f} km</td><td>{item['coverage_90']*100:.1f}%</td></tr>"
        for item in summary["by_condition"]
    )
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Espada synthetic evaluation</title><style>
    :root{{--ink:#0b1f2a;--muted:#60717b;--line:#d9e3e7;--paper:#f3f6f5;--white:#fff;--teal:#087f7b;--orange:#f49a24}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,Segoe UI,Arial,sans-serif}}main{{max-width:1240px;margin:auto;padding:30px}}h1{{font:600 34px Georgia,serif;margin:5px 0}}.eyebrow{{color:var(--teal);font-size:12px;font-weight:800;letter-spacing:.12em}}.note{{color:var(--muted);font-size:13px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}}.kpi,.panel{{background:var(--white);border:1px solid var(--line);border-radius:12px}}.kpi{{padding:16px}}.kpi span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;margin-bottom:8px}}.kpi strong{{font:600 25px Georgia,serif}}.panel{{padding:17px;margin-bottom:14px}}img{{width:100%;display:block}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-size:10px;text-transform:uppercase}}.warning{{border-left:4px solid var(--orange);background:#fff3e2;padding:12px 14px;margin:16px 0;font-size:12px}}@media(max-width:700px){{main{{padding:14px}}.kpis{{grid-template-columns:1fr 1fr}}.panel{{overflow:auto}}}}
    </style></head><body><main><div class="eyebrow">ESPADA · SIH26143</div><h1>Synthetic evaluation</h1><p class="note">{summary['config']['cases']} independent cases across six stress conditions. Hidden truth is opened only after inference.</p><div class="warning">These are controlled synthetic results—not real-incident accuracy and not evidence of guilt.</div>
    <section class="kpis"><div class="kpi"><span>Top-1 accuracy</span><strong>{metrics['top1_accuracy']*100:.1f}%</strong></div><div class="kpi"><span>Top-3 accuracy</span><strong>{metrics['top3_accuracy']*100:.1f}%</strong></div><div class="kpi"><span>Median origin error</span><strong>{metrics['median_origin_error_km']:.2f} km</strong></div><div class="kpi"><span>90% region coverage</span><strong>{metrics['coverage_90']*100:.1f}%</strong></div></section>
    <section class="panel"><img src="data:image/png;base64,{image}" alt="Four-panel synthetic evaluation overview"></section><section class="panel"><table><thead><tr><th>Condition</th><th>Cases</th><th>Top-1</th><th>Top-3</th><th>Median origin error</th><th>90% coverage</th></tr></thead><tbody>{rows}</tbody></table></section>
    <section class="panel"><strong>Current model:</strong> physics ensemble + transparent evidence score. <strong>Planned SAR ML:</strong> ResNet34 U-Net; not yet trained or integrated.</section></main></body></html>"""
    path.write_text(document, encoding="utf-8")


def run_synthetic_evaluation(output_dir: Path, config: EvaluationConfig | None = None) -> dict:
    config = config or EvaluationConfig()
    if config.cases < 6 or config.particles <= 0 or config.ensemble_members <= 0:
        raise ValueError("Evaluation needs at least six cases and positive particle/member counts")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="espada-eval-", dir=output_dir) as temporary:
        work_dir = Path(temporary)
        cases = [
            _run_case(index, CONDITIONS[index % len(CONDITIONS)], config, work_dir)
            for index in range(config.cases)
        ]
    frame = pd.DataFrame(cases)
    frame.to_csv(output_dir / "evaluation_cases.csv", index=False)
    breakdown = _condition_metrics(frame)
    overall = {
        "top1_accuracy": float(frame["top1_hit"].mean()),
        "top3_accuracy": float(frame["top3_hit"].mean()),
        "mean_reciprocal_rank": float((1.0 / frame["polluter_rank"]).mean()),
        "median_origin_error_km": float(frame["origin_error_km"].median()),
        "p90_origin_error_km": float(frame["origin_error_km"].quantile(0.90)),
        "coverage_50": float(frame["truth_in_50_region"].mean()),
        "coverage_90": float(frame["truth_in_90_region"].mean()),
    }
    acceptance = {
        "top3_accuracy_at_least_80_percent": overall["top3_accuracy"] >= 0.80,
        "median_origin_error_under_5_km": overall["median_origin_error_km"] <= 5.0,
        "p90_origin_error_under_15_km": overall["p90_origin_error_km"] <= 15.0,
        "all_results_finite": bool(np.isfinite(frame.select_dtypes(include=[np.number])).all().all()),
    }
    summary = {
        "status": "PASS" if all(acceptance.values()) else "FAIL",
        "evaluation": "controlled synthetic robustness evaluation",
        "config": asdict(config),
        "overall": overall,
        "by_condition": breakdown,
        "acceptance": acceptance,
        "anti_leakage": "The ranker never receives truth.json; the evaluation wrapper opens truth after ranking.",
        "limitations": [
            "Synthetic releases and AIS tracks may not represent real incident complexity.",
            "Constant forcing fields are used per case.",
            "Release age is supplied rather than inferred.",
            "Candidate score is comparative evidence, not a calibrated probability of guilt.",
        ],
        "artifacts": [
            "evaluation_overview.png",
            "evaluation_cases.csv",
            "evaluation_summary.json",
            "evaluation_report.html",
            "model_card.json",
        ],
    }
    plot_path = output_dir / "evaluation_overview.png"
    _plot_overview(plot_path, frame, breakdown)
    (output_dir / "evaluation_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (output_dir / "model_card.json").write_text(
        json.dumps(_model_card(), indent=2), encoding="utf-8"
    )
    _write_html(output_dir / "evaluation_report.html", summary, plot_path)
    return summary
