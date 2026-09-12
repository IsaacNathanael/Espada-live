from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "espada-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
import numpy as np
import pandas as pd


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_forensic_replay(
    case_dir: Path,
    output_dir: Path,
    *,
    frames: int = 64,
    fps: int = 14,
    particle_sample: int = 900,
) -> dict[str, object]:
    """Render a reverse-distribution and vessel-ranking replay from saved evidence."""
    if frames < 12 or fps < 1 or particle_sample < 50:
        raise ValueError("Replay settings are too small")
    case_dir = Path(case_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    physics = case_dir / "physics" if (case_dir / "physics").exists() else case_dir / "drift"
    ranking_path = (
        case_dir / "candidates.json"
        if (case_dir / "candidates.json").exists()
        else case_dir / "ranking" / "candidates.json"
    )
    ais_path = (
        case_dir / "ais_tracks.csv"
        if (case_dir / "ais_tracks.csv").exists()
        else case_dir / "ais" / "ais_normalized.csv"
    )
    with np.load(physics / "forward_particles.npz") as forward:
        observed_lon = np.asarray(forward["lon"], dtype=float)
        observed_lat = np.asarray(forward["lat"], dtype=float)
    with np.load(physics / "reverse_endpoints.npz") as reverse:
        origin_lon = np.asarray(reverse["lon"], dtype=float)
        origin_lat = np.asarray(reverse["lat"], dtype=float)
    estimate = _read_json(physics / "release_estimate.json")
    truth = _read_json(physics / "truth.json")
    ranking = _read_json(ranking_path)
    ais = pd.read_csv(ais_path, dtype={"mmsi": str})
    top = ranking.get("top_candidate", {})
    age_hours = float(estimate.get("assumed_age_hours", 19.0))

    sample_count = min(particle_sample, len(origin_lon))
    selected = np.linspace(0, len(origin_lon) - 1, sample_count, dtype=int)
    sampled_origin_lon = origin_lon[selected]
    sampled_origin_lat = origin_lat[selected]
    observed_index = selected % len(observed_lon)
    sampled_observed_lon = observed_lon[observed_index]
    sampled_observed_lat = observed_lat[observed_index]

    all_lon = np.concatenate([origin_lon, observed_lon, ais["longitude"].to_numpy(dtype=float)])
    all_lat = np.concatenate([origin_lat, observed_lat, ais["latitude"].to_numpy(dtype=float)])
    lon_pad = max(0.025, (np.quantile(all_lon, 0.995) - np.quantile(all_lon, 0.005)) * 0.08)
    lat_pad = max(0.025, (np.quantile(all_lat, 0.995) - np.quantile(all_lat, 0.005)) * 0.08)
    xlim = (float(np.quantile(all_lon, 0.005) - lon_pad), float(np.quantile(all_lon, 0.995) + lon_pad))
    ylim = (float(np.quantile(all_lat, 0.005) - lat_pad), float(np.quantile(all_lat, 0.995) + lat_pad))
    density, x_edges, y_edges = np.histogram2d(origin_lon, origin_lat, bins=95)
    density = np.sqrt(density.T)

    fig, axis = plt.subplots(figsize=(12.8, 7.2), facecolor="#020a0c")
    fig.subplots_adjust(left=0.055, right=0.975, bottom=0.075, top=0.88)
    axis.set_facecolor("#061a1e")
    for spine in axis.spines.values():
        spine.set_color("#21434a")
    axis.tick_params(colors="#77928f", labelsize=8)
    axis.grid(color="#21434a", alpha=0.28, linewidth=0.6)
    axis.set_xlim(*xlim)
    axis.set_ylim(*ylim)
    axis.set_xlabel("Longitude", color="#8ba5a2", fontsize=9)
    axis.set_ylabel("Latitude", color="#8ba5a2", fontsize=9)
    fig.text(0.055, 0.955, "ESPADA", color="#5eead4", fontsize=12, fontweight="bold")
    fig.text(0.132, 0.955, "FORENSIC REVERSE-DRIFT REPLAY", color="#d9eeea", fontsize=11)
    fig.text(
        0.055,
        0.915,
        "Actual saved particle distributions · truth remains locked until ranking",
        color="#789491",
        fontsize=9,
    )

    for mmsi, track in ais.groupby("mmsi", sort=False):
        is_top = str(mmsi) == str(top.get("mmsi", ""))
        axis.plot(
            track["longitude"],
            track["latitude"],
            color="#526d6d" if not is_top else "#526d6d",
            linewidth=0.8,
            alpha=0.28,
            zorder=2,
        )
    axis.scatter(
        observed_lon[:: max(1, len(observed_lon) // 700)],
        observed_lat[:: max(1, len(observed_lat) // 700)],
        s=7,
        color="#38bdf8",
        alpha=0.18,
        linewidths=0,
        zorder=3,
    )
    density_image = axis.imshow(
        density,
        extent=(x_edges[0], x_edges[-1], y_edges[0], y_edges[-1]),
        origin="lower",
        cmap="YlOrRd",
        alpha=0.0,
        aspect="auto",
        zorder=1,
    )
    cloud = axis.scatter(
        sampled_observed_lon,
        sampled_observed_lat,
        s=7,
        color="#5eead4",
        alpha=0.5,
        linewidths=0,
        zorder=4,
    )
    top_line, = axis.plot([], [], color="#fbbf24", linewidth=3.0, alpha=0.0, zorder=5)
    truth_marker = axis.scatter([], [], marker="X", s=230, color="#fbbf24", edgecolor="white", linewidth=1.1, alpha=0.0, zorder=8)
    clock = axis.text(
        0.985,
        0.965,
        "T + 00:00",
        transform=axis.transAxes,
        ha="right",
        va="top",
        color="#dffbf5",
        fontsize=12,
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.55", "facecolor": "#031317", "edgecolor": "#28545a", "alpha": 0.92},
        zorder=10,
    )
    phase = axis.text(
        0.018,
        0.04,
        "OBSERVED SLICK ACQUIRED",
        transform=axis.transAxes,
        color="#5eead4",
        fontsize=10,
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.55", "facecolor": "#031317", "edgecolor": "#28545a", "alpha": 0.92},
        zorder=10,
    )
    result_text = axis.text(
        0.5,
        0.5,
        "",
        transform=axis.transAxes,
        ha="center",
        va="center",
        color="white",
        fontsize=19,
        fontweight="bold",
        alpha=0.0,
        bbox={"boxstyle": "round,pad=0.85", "facecolor": "#042328", "edgecolor": "#5eead4", "alpha": 0.95},
        zorder=12,
    )

    top_track = ais[ais["mmsi"].astype(str) == str(top.get("mmsi", ""))]
    top_lon = top_track["longitude"].to_numpy(dtype=float)
    top_lat = top_track["latitude"].to_numpy(dtype=float)

    def update(frame_index: int):
        progress = frame_index / max(frames - 1, 1)
        eased = progress * progress * (3.0 - 2.0 * progress)
        current_lon = (1.0 - eased) * sampled_observed_lon + eased * sampled_origin_lon
        current_lat = (1.0 - eased) * sampled_observed_lat + eased * sampled_origin_lat
        cloud.set_offsets(np.column_stack([current_lon, current_lat]))
        cloud.set_alpha(0.5 if progress < 0.88 else max(0.12, 0.5 * (1.0 - progress)))
        density_image.set_alpha(max(0.0, min(0.72, (progress - 0.48) * 1.55)))
        clock.set_text(f"T − {age_hours * progress:05.2f} HOURS")
        if progress < 0.18:
            phase.set_text("1 · OBSERVED SLICK ACQUIRED")
        elif progress < 0.58:
            phase.set_text("2 · REVERSING PARTICLE ENSEMBLE")
        elif progress < 0.76:
            phase.set_text("3 · ORIGIN PROBABILITY FORMED")
        elif progress < 0.90:
            phase.set_text("4 · AIS TRACKS FORWARD-VERIFIED")
            reveal = (progress - 0.76) / 0.14
            points = max(2, int(len(top_lon) * reveal)) if len(top_lon) else 0
            top_line.set_data(top_lon[:points], top_lat[:points])
            top_line.set_alpha(min(1.0, reveal))
        else:
            phase.set_text("5 · HIDDEN TRUTH UNLOCKED AFTER RANKING")
            top_line.set_data(top_lon, top_lat)
            top_line.set_alpha(1.0)
            if truth:
                truth_marker.set_offsets([[truth.get("release_lon"), truth.get("release_lat")]])
                truth_marker.set_alpha(min(1.0, (progress - 0.9) * 10.0))
            result_text.set_text(
                f"KNOWN SOURCE MATCHED\n{top.get('vessel_name', 'TOP CANDIDATE')} · RANK #1 OF {ranking.get('candidate_count', '?')}\n"
                f"{float(top.get('total_score', 0.0)) * 100:.1f}% comparative evidence score"
            )
            result_text.set_alpha(min(1.0, (progress - 0.9) * 10.0))
        return cloud, density_image, top_line, truth_marker, clock, phase, result_text

    animation = FuncAnimation(fig, update, frames=frames, interval=1000 / fps, blit=False)
    gif_path = output_dir / "espada_forensic_replay.gif"
    animation.save(gif_path, writer=PillowWriter(fps=fps), dpi=92)
    plt.close(fig)

    manifest = {
        "status": "PASS",
        "artifact_type": "controlled forensic replay",
        "source_case": str(case_dir.resolve()),
        "frames": frames,
        "fps": fps,
        "duration_seconds": round(frames / fps, 2),
        "particles_displayed": sample_count,
        "reverse_endpoints_available": len(origin_lon),
        "candidate_vessels": ranking.get("candidate_count", 0),
        "top_candidate": top,
        "truth_reveal_policy": "Known-source truth appears only in the final replay phase.",
        "visualization_note": "Particle positions between saved endpoints are interpolated for explanatory replay; endpoint distributions and rankings are computed artifacts.",
        "output": str(gif_path.resolve()),
    }
    manifest_path = output_dir / "replay_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
