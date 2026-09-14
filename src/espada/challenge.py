from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .ais import normalize_ais_csv
from .attribution import write_attribution_outputs
from .case_alignment import validate_case_alignment
from .coast import load_coast_mask
from .decision import evaluate_case_decision
from .digital_twin import _blind_ais, _interpolated_position, _simulate_slick
from .dossier import generate_evidence_dossier
from .environment import load_cache
from .geo import haversine_km
from .slick import analyze_slick, write_slick_from_particles
from .spatial_current import load_spatial_current_grid
from .time_window import search_release_window


DEFAULT_CONFIG: dict[str, object] = {
    "schema_version": "1.0",
    "name": "ESPADA judge-controlled digital twin",
    "seed": 26143,
    "candidate_vessels": 12,
    "source_vessel_index": 0,
    "satellite_delay_hours": 12.0,
    "release_duration_minutes": 90.0,
    "truth": {
        "current_multiplier": 1.0,
        "wind_multiplier": 1.0,
        "windage": 0.02,
        "diffusivity_m2s": 12.0,
    },
    "evidence": {
        "random_ais_dropout_fraction": 0.05,
        "source_blackout_hours": 0.0,
        "position_noise_m": 75.0,
        "spoofed_decoy_tracks": 0,
    },
    "inference": {
        "minimum_age_hours": 8.0,
        "maximum_age_hours": 16.0,
        "age_step_hours": 2.0,
        "current_multipliers": [0.85, 1.0, 1.15],
        "windages": [0.015, 0.02, 0.025],
        "slick_particles": 300,
        "ensemble_members": 4,
    },
    "simulation_particles": 600,
}


def _merge_config(raw: dict[str, object]) -> dict[str, object]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def _bounded(value: object, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return number


def validate_config(raw: dict[str, object]) -> dict[str, object]:
    config = _merge_config(raw)
    if str(config.get("schema_version")) != "1.0":
        raise ValueError("Only challenge schema_version 1.0 is supported")
    _bounded(config["seed"], "seed", 0, 2**32 - 1)
    _bounded(config["candidate_vessels"], "candidate_vessels", 2, 76)
    _bounded(config["source_vessel_index"], "source_vessel_index", 0, 75)
    delay = _bounded(config["satellite_delay_hours"], "satellite_delay_hours", 4, 30)
    duration = _bounded(config["release_duration_minutes"], "release_duration_minutes", 0, 180)
    if duration / 60.0 >= delay:
        raise ValueError("release_duration_minutes must be shorter than satellite_delay_hours")
    _bounded(config["simulation_particles"], "simulation_particles", 300, 10_000)

    truth = config["truth"]
    if not isinstance(truth, dict):
        raise ValueError("truth must be an object")
    _bounded(truth["current_multiplier"], "truth.current_multiplier", 0.5, 1.5)
    _bounded(truth["wind_multiplier"], "truth.wind_multiplier", 0.5, 1.5)
    _bounded(truth["windage"], "truth.windage", 0.0, 0.05)
    _bounded(truth["diffusivity_m2s"], "truth.diffusivity_m2s", 0.0, 50.0)

    evidence = config["evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    _bounded(
        evidence["random_ais_dropout_fraction"],
        "evidence.random_ais_dropout_fraction",
        0.0,
        0.9,
    )
    _bounded(evidence["source_blackout_hours"], "evidence.source_blackout_hours", 0.0, 8.0)
    _bounded(evidence["position_noise_m"], "evidence.position_noise_m", 0.0, 2_000.0)
    _bounded(evidence["spoofed_decoy_tracks"], "evidence.spoofed_decoy_tracks", 0, 5)

    inference = config["inference"]
    if not isinstance(inference, dict):
        raise ValueError("inference must be an object")
    minimum_age = _bounded(
        inference["minimum_age_hours"], "inference.minimum_age_hours", 1.0, 30.0
    )
    maximum_age = _bounded(
        inference["maximum_age_hours"], "inference.maximum_age_hours", 1.0, 30.0
    )
    step = _bounded(inference["age_step_hours"], "inference.age_step_hours", 0.5, 12.0)
    if minimum_age > maximum_age:
        raise ValueError("minimum_age_hours cannot exceed maximum_age_hours")
    if int((maximum_age - minimum_age) / step) + 1 > 16:
        raise ValueError("The release-time grid is limited to 16 ages per run")
    current_multipliers = inference.get("current_multipliers")
    windages = inference.get("windages")
    if not isinstance(current_multipliers, list) or not current_multipliers:
        raise ValueError("inference.current_multipliers must be a non-empty list")
    if not isinstance(windages, list) or not windages:
        raise ValueError("inference.windages must be a non-empty list")
    if len(current_multipliers) > 5 or len(windages) > 5:
        raise ValueError("Use at most five current multipliers and five windages")
    for index, value in enumerate(current_multipliers):
        _bounded(value, f"inference.current_multipliers[{index}]", 0.5, 1.5)
    for index, value in enumerate(windages):
        _bounded(value, f"inference.windages[{index}]", 0.0, 0.05)
    _bounded(inference["slick_particles"], "inference.slick_particles", 100, 2_000)
    _bounded(inference["ensemble_members"], "inference.ensemble_members", 2, 20)
    return config


def _age_grid(inference: dict[str, object]) -> tuple[float, ...]:
    minimum = float(inference["minimum_age_hours"])
    maximum = float(inference["maximum_age_hours"])
    step = float(inference["age_step_hours"])
    values = np.arange(minimum, maximum + step * 0.25, step, dtype=float)
    return tuple(float(round(value, 6)) for value in values if value <= maximum + 1e-9)


def _eligible_sources(
    ais: pd.DataFrame,
    release_time: pd.Timestamp,
    release_duration_minutes: float,
) -> list[str]:
    release_end = release_time + pd.Timedelta(minutes=release_duration_minutes)
    eligible: list[tuple[str, int]] = []
    for mmsi, track in ais.groupby("mmsi"):
        if (
            len(track) >= 3
            and track["timestamp_utc"].min() <= release_time
            and track["timestamp_utc"].max() >= release_end
        ):
            eligible.append((str(mmsi), len(track)))
    eligible.sort(key=lambda item: (-item[1], item[0]))
    return [item[0] for item in eligible]


def _select_traffic(
    ais: pd.DataFrame,
    source_mmsi: str,
    candidate_vessels: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    counts = ais.groupby("mmsi").size().sort_values(ascending=False)
    others = [str(value) for value in counts.index if str(value) != source_mmsi]
    jitter = {value: float(rng.random()) for value in others}
    others.sort(key=lambda value: (-int(counts.loc[value]), jitter[value]))
    selected = {source_mmsi, *others[: candidate_vessels - 1]}
    return ais.loc[ais["mmsi"].astype(str).isin(selected)].copy()


def _apply_evidence_stress(
    frame: pd.DataFrame,
    source_id: str,
    release_time: pd.Timestamp,
    source_position: tuple[float, float],
    evidence: dict[str, object],
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, list[str]]:
    original = frame.copy()
    result = frame.copy()
    notes: list[str] = []
    dropout = float(evidence["random_ais_dropout_fraction"])
    if dropout:
        result = result.loc[rng.random(len(result)) >= dropout].copy()
        notes.append(f"{100 * dropout:.1f}% random AIS reception dropout applied")
    blackout = float(evidence["source_blackout_hours"])
    if blackout:
        separation = (result["timestamp_utc"] - release_time).abs()
        remove = result["mmsi"].eq(source_id) & (
            separation <= pd.Timedelta(hours=blackout / 2.0)
        )
        result = result.loc[~remove].copy()
        notes.append(f"{blackout:g}-hour source-vessel AIS blackout applied")

    # Preserve at least two received positions per candidate so the challenge
    # tests uncertainty handling rather than malformed input handling.
    retained = set(result["mmsi"].astype(str))
    for mmsi, track in original.groupby("mmsi"):
        if str(mmsi) not in retained or int((result["mmsi"] == mmsi).sum()) < 2:
            needed = track.sort_values("timestamp_utc").iloc[[0, -1]]
            result = pd.concat([result, needed], ignore_index=True)

    noise_m = float(evidence["position_noise_m"])
    if noise_m:
        latitude_radians = np.deg2rad(result["latitude"].to_numpy(dtype=float))
        result["latitude"] = result["latitude"].to_numpy(dtype=float) + rng.normal(
            0.0, noise_m / 111_320.0, len(result)
        )
        longitude_scale = 111_320.0 * np.maximum(np.cos(latitude_radians), 0.1)
        result["longitude"] = result["longitude"].to_numpy(dtype=float) + rng.normal(
            0.0, noise_m / longitude_scale, len(result)
        )
        notes.append(f"{noise_m:g} m one-sigma AIS position noise applied")

    decoys = int(evidence["spoofed_decoy_tracks"])
    other_ids = [
        str(value)
        for value in result.groupby("mmsi").size().sort_values(ascending=False).index
        if str(value) != source_id
    ]
    injected_decoys = min(decoys, len(other_ids))
    for decoy_id in other_ids[:injected_decoys]:
        mask = result["mmsi"].eq(decoy_id)
        track = result.loc[mask]
        decoy_lon, decoy_lat = _interpolated_position(track, release_time)
        offset_lon = source_position[0] - decoy_lon + float(rng.normal(0.0, 0.006))
        offset_lat = source_position[1] - decoy_lat + float(rng.normal(0.0, 0.006))
        result.loc[mask, "longitude"] = result.loc[mask, "longitude"].astype(float) + offset_lon
        result.loc[mask, "latitude"] = result.loc[mask, "latitude"].astype(float) + offset_lat
        result.loc[mask, "source"] = (
            "CONTROLLED DIGITAL TWIN; deliberately injected spoofing decoy"
        )
    if injected_decoys:
        notes.append(
            f"{injected_decoys} source-proximate spoofing decoy track(s) injected"
        )
    if decoys > injected_decoys:
        notes.append(
            f"Spoofed decoys capped at {injected_decoys}; only that many non-source tracks exist"
        )
    if not notes:
        notes.append("No evidence degradation applied")
    return result.sort_values(["mmsi", "timestamp_utc"]).reset_index(drop=True), notes


def _commit_truth(truth: dict[str, object]) -> tuple[dict[str, object], str]:
    canonical = json.dumps(truth, sort_keys=True, separators=(",", ":"))
    commitment = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "algorithm": "SHA-256",
        "commitment": commitment,
        "status": "LOCKED_BEFORE_INFERENCE",
        "interpretation": (
            "The hidden truth is revealed only after ranking; recomputing this hash "
            "verifies that it was not changed to match the result."
        ),
    }, canonical


def _image_uri(path: Path) -> str:
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _write_report(
    path: Path,
    result: dict[str, object],
    config: dict[str, object],
    case_root: Path,
) -> None:
    outcome = html.escape(str(result["outcome"]).replace("_", " "))
    metrics = result["measured_result"]
    truth = result["truth_reveal"]
    decision = result["operational_decision"]
    images = [
        ("Release-time search", case_root / "time_search" / "release_time_window.png"),
        ("Reverse origin field", case_root / "drift" / "slick_reverse_analysis.png"),
        ("Candidate ranking", case_root / "ranking" / "candidate_ranking.png"),
    ]
    image_cards = "".join(
        f"<article class='card'><h2>{html.escape(title)}</h2><img src='{_image_uri(image)}' alt='{html.escape(title)}'></article>"
        for title, image in images
        if image.exists()
    )
    evidence = config["evidence"]
    controls = [
        ("Candidate vessels", config["candidate_vessels"]),
        ("Satellite delay", f"{config['satellite_delay_hours']} h"),
        ("Release duration", f"{config['release_duration_minutes']} min"),
        ("Random AIS dropout", f"{100 * float(evidence['random_ais_dropout_fraction']):.1f}%"),
        ("Source blackout", f"{evidence['source_blackout_hours']} h"),
        ("Position noise", f"{evidence['position_noise_m']} m"),
        ("Spoofed decoys", evidence["spoofed_decoy_tracks"]),
    ]
    control_rows = "".join(
        f"<tr><td>{html.escape(str(name))}</td><td>{html.escape(str(value))}</td></tr>"
        for name, value in controls
    )
    path.write_text(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA Digital Twin Challenge</title><style>:root{{--bg:#020a0c;--panel:#082128;--line:#17434b;--ink:#eafffb;--muted:#91aaa6;--mint:#61f2d1;--amber:#ffbd4a}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#104047 0,transparent 34%),var(--bg);color:var(--ink);font:14px/1.55 Inter,Segoe UI,sans-serif}}main{{width:min(1160px,calc(100% - 30px));margin:auto;padding:38px 0 70px}}.tag{{color:var(--mint);font-size:10px;font-weight:900;letter-spacing:.16em}}h1{{font:500 clamp(42px,7vw,76px)/.95 Georgia,serif;margin:14px 0}}h1 em{{color:var(--mint);font-style:normal}}p{{color:var(--muted)}}.hero,.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.card{{padding:20px;border:1px solid var(--line);border-radius:17px;background:linear-gradient(145deg,#092a31,#05161a)}}.result{{border-color:var(--mint)}}.result strong{{display:block;color:var(--amber);font:500 30px Georgia,serif}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:15px 0}}.metric{{padding:13px;border:1px solid var(--line);border-radius:10px}}small{{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em}}.metric b{{display:block;margin-top:5px;font-size:21px}}table{{width:100%;border-collapse:collapse}}td{{padding:8px;border-top:1px solid var(--line)}}td:last-child{{text-align:right;color:var(--mint)}}img{{width:100%;border-radius:10px}}.warning{{border-left:3px solid var(--amber);padding:12px 15px;background:rgba(255,189,74,.06);color:#e9d6a9}}@media(max-width:760px){{.hero,.grid,.metrics{{grid-template-columns:1fr}}}}</style></head><body><main><span class='tag'>ESPADA · SEALED-GROUND-TRUTH TEST</span><section class='hero'><div><h1>Change the evidence.<br><em>Test the system.</em></h1><p>A controlled spill is generated with real currents, wind, coastline and pseudonymized vessel traffic. The source is cryptographically locked before inference.</p></div><article class='card result'><small>Measured outcome</small><strong>{outcome}</strong><p>{html.escape(str(result['interpretation']))}</p></article></section><section class='metrics'><div class='metric'><small>Known source rank</small><b>#{metrics['source_rank']} / {metrics['candidate_count']}</b></div><div class='metric'><small>Origin error</small><b>{metrics['origin_error_km']:.2f} km</b></div><div class='metric'><small>Age error</small><b>{metrics['release_age_error_hours']:.1f} h</b></div><div class='metric'><small>Decision</small><b>{html.escape(str(decision).replace('_', ' '))}</b></div></section><section class='grid'><article class='card'><h2>Judge-controlled evidence</h2><table>{control_rows}</table></article><article class='card'><h2>Truth reveal</h2><p>Committed source: <b>{html.escape(str(truth['source_id']))}</b><br>Ranked leader: <b>{html.escape(str(metrics['top_candidate_id']))}</b></p><p>Commitment verified: <b>{str(result['truth_commitment_verified']).upper()}</b><br>Answer key accessed during inference: <b>FALSE</b></p><div class='warning'>A candidate score is not guilt probability. Weak or contradictory evidence must produce an abstention.</div></article></section><section class='grid' style='margin-top:14px'>{image_cards}</section></main></body></html>""",
        encoding="utf-8",
    )


def run_challenge(
    config_path: Path,
    ais_path: Path,
    environment_path: Path,
    spatial_current_grid: Path,
    land_mask: Path,
    output_dir: Path,
) -> dict[str, object]:
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    config = validate_config(raw)
    seed = int(config["seed"])
    rng = np.random.default_rng(seed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scenario_used.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )

    ais = pd.read_csv(ais_path, dtype={"mmsi": str})
    ais["timestamp_utc"] = pd.to_datetime(ais["timestamp_utc"], utc=True)
    environment_bundle = load_cache(environment_path)
    current_grid = load_spatial_current_grid(spatial_current_grid)
    coast = load_coast_mask(land_mask)
    observation_time = min(
        ais["timestamp_utc"].max(), environment_bundle.frame["time_utc"].max()
    ) - pd.Timedelta(hours=2)
    delay = float(config["satellite_delay_hours"])
    release_time = observation_time - pd.Timedelta(hours=delay)
    duration = float(config["release_duration_minutes"])
    if not current_grid.covers(release_time, observation_time):
        raise ValueError("The selected satellite delay is outside the current-grid coverage")
    eligible = _eligible_sources(ais, release_time, duration)
    source_index = int(config["source_vessel_index"])
    if source_index >= len(eligible):
        raise ValueError(
            f"source_vessel_index {source_index} is unavailable; use 0-{len(eligible) - 1} "
            f"for this delay ({len(eligible)} eligible tracks)"
        )
    source_mmsi = eligible[source_index]
    candidate_count = int(config["candidate_vessels"])
    traffic = _select_traffic(ais, source_mmsi, candidate_count, rng)
    source_track = traffic.loc[traffic["mmsi"].astype(str) == source_mmsi]
    blinded, mapping = _blind_ais(traffic)
    source_id = mapping[source_mmsi]

    truth_config = config["truth"]
    observed_lon, observed_lat, release_corridor = _simulate_slick(
        source_track,
        environment_bundle.frame,
        release_time,
        observation_time,
        rng,
        particles=int(config["simulation_particles"]),
        windage=float(truth_config["windage"]),
        diffusivity_m2s=float(truth_config["diffusivity_m2s"]),
        spatial_current_grid=current_grid,
        coast_mask=coast,
        release_duration_minutes=duration,
        current_multiplier=float(truth_config["current_multiplier"]),
        wind_multiplier=float(truth_config["wind_multiplier"]),
    )
    slick_path = write_slick_from_particles(
        output_dir / "synthetic_slick.geojson",
        observed_lon,
        observed_lat,
        observation_time_utc=observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="CONTROLLED DIGITAL TWIN; hidden-source slick generated with real forcing",
    )
    source_position = _interpolated_position(source_track, release_time)
    blinded["source"] = (
        "CONTROLLED DIGITAL TWIN; pseudonymized Global Fishing Watch background traffic"
    )
    stressed, stress_notes = _apply_evidence_stress(
        blinded,
        source_id,
        release_time,
        source_position,
        config["evidence"],
        rng,
    )
    raw_ais_path = output_dir / "ais" / "challenge_traffic_raw.csv"
    raw_ais_path.parent.mkdir(parents=True, exist_ok=True)
    saved = stressed.copy()
    saved["timestamp_utc"] = saved["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    saved.to_csv(raw_ais_path, index=False)
    normalize_ais_csv(raw_ais_path, output_dir / "ais")
    candidate_path = output_dir / "ais" / "ais_normalized.csv"

    nonce = hashlib.sha256(f"espada-challenge:{seed}:truth".encode()).hexdigest()[:24]
    truth = {
        "schema": "espada.challenge.truth.v1",
        "nonce": nonce,
        "source_id": source_id,
        "release_time_utc": release_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observation_time_utc": observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "satellite_delay_hours": delay,
        "release_duration_minutes": duration,
        "release_corridor": [
            {"longitude": float(lon), "latitude": float(lat)}
            for lon, lat in release_corridor
        ],
        "truth_forcing": truth_config,
    }
    commitment, canonical_truth = _commit_truth(truth)
    commitment_path = output_dir / "truth_commitment.json"
    commitment_path.write_text(json.dumps(commitment, indent=2), encoding="utf-8")

    inference = config["inference"]
    time_search = search_release_window(
        slick_path,
        environment_path,
        candidate_path,
        output_dir / "time_search",
        ages_hours=_age_grid(inference),
        current_multipliers=tuple(float(value) for value in inference["current_multipliers"]),
        windages=tuple(float(value) for value in inference["windages"]),
        particles=int(inference["slick_particles"]),
        ensemble_members=int(inference["ensemble_members"]),
        seed=seed + 1,
        spatial_current_grid=spatial_current_grid,
        land_mask=land_mask,
    )
    selected_age = float(time_search["best_supported_age_hours"])
    alignment = validate_case_alignment(
        slick_path,
        environment_path,
        candidate_path,
        output_dir / "case_alignment.json",
        age_hours=selected_age,
    )
    if alignment["status"] != "PASS":
        raise ValueError("Generated challenge inputs failed incident-time alignment")
    analysis = analyze_slick(
        slick_path,
        environment_path,
        output_dir / "drift",
        age_hours=selected_age,
        particles=int(inference["slick_particles"]),
        ensemble_members=int(inference["ensemble_members"]),
        seed=seed + 2,
        spatial_current_grid=spatial_current_grid,
        land_mask=land_mask,
    )
    ranking = write_attribution_outputs(
        output_dir / "ranking",
        candidate_path,
        output_dir / "drift" / "reverse_endpoints.npz",
        output_dir / "drift" / "release_estimate.json",
        output_dir / "drift" / "forward_particles.npz",
    )
    decision = evaluate_case_decision(output_dir, output_dir / "decision")
    generate_evidence_dossier(output_dir, output_dir / "dossier")

    source = next(item for item in ranking["candidates"] if item["mmsi"] == source_id)
    top = ranking["top_candidate"]
    estimate = analysis["estimated_origin"]
    origin_error = min(
        haversine_km(
            point[0],
            point[1],
            float(estimate["longitude"]),
            float(estimate["latitude"]),
        )
        for point in release_corridor
    )
    source_rank = int(source["rank"])
    unsafe_false_priority = (
        str(top["mmsi"]) != source_id
        and decision["decision"] == "PRIORITY_ANALYST_REVIEW"
    )
    if source_rank == 1:
        outcome = "KNOWN_SOURCE_RECOVERED"
    elif source_rank <= 3:
        outcome = "KNOWN_SOURCE_SHORTLISTED"
    elif decision["decision"] == "ABSTAIN_INSUFFICIENT_EVIDENCE":
        outcome = "SAFE_ABSTENTION"
    else:
        outcome = "SOURCE_NOT_RECOVERED"
    reveal_path = output_dir / "truth_reveal.json"
    truth_reveal = {
        **truth,
        "revealed_after_inference": True,
        "commitment": commitment["commitment"],
    }
    reveal_path.write_text(json.dumps(truth_reveal, indent=2), encoding="utf-8")
    commitment_verified = (
        hashlib.sha256(canonical_truth.encode("utf-8")).hexdigest()
        == commitment["commitment"]
    )
    passed = (
        commitment_verified
        and not unsafe_false_priority
        and outcome
        in {"KNOWN_SOURCE_RECOVERED", "KNOWN_SOURCE_SHORTLISTED", "SAFE_ABSTENTION"}
    )
    result = {
        "status": "PASS" if passed else "REVIEW",
        "outcome": outcome,
        "scenario": config["name"],
        "answer_key_accessed_during_inference": False,
        "truth_commitment_verified": commitment_verified,
        "stress_applied": stress_notes,
        "measured_result": {
            "source_rank": source_rank,
            "candidate_count": len(ranking["candidates"]),
            "source_top_1": source_rank == 1,
            "source_top_3": source_rank <= 3,
            "top_candidate_id": str(top["mmsi"]),
            "origin_error_km": float(origin_error),
            "true_release_age_hours": delay,
            "estimated_release_age_hours": selected_age,
            "release_age_error_hours": abs(selected_age - delay),
            "comparative_source_score": float(source["total_score"]),
            "unsafe_false_priority": unsafe_false_priority,
        },
        "operational_decision": decision["decision"],
        "truth_reveal": truth_reveal,
        "interpretation": (
            "The sealed source was recovered without giving its identity to the inference pipeline."
            if source_rank == 1
            else (
                "The sealed source remained in the reviewable Top 3."
                if source_rank <= 3
                else "Evidence was too weak for recovery and the system safely abstained."
            )
        ),
        "claim_boundary": (
            "Controlled digital-twin evidence using real environmental fields and "
            "pseudonymized traffic; not real-incident accuracy or a legal finding."
        ),
        "artifacts": {
            "scenario": str((output_dir / "scenario_used.json").resolve()),
            "commitment": str(commitment_path.resolve()),
            "truth_reveal": str(reveal_path.resolve()),
            "release_time_search": str(
                (output_dir / "time_search" / "release_time_search.html").resolve()
            ),
            "evidence_dossier": str(
                (output_dir / "dossier" / "evidence_dossier.html").resolve()
            ),
            "challenge_report": str((output_dir / "challenge_report.html").resolve()),
        },
    }
    result_path = output_dir / "challenge_result.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_report(output_dir / "challenge_report.html", result, config, output_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a configurable ESPADA digital-twin challenge")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ais", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--spatial-current-grid", type=Path, required=True)
    parser.add_argument("--land-mask", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_challenge(
        args.config,
        args.ais,
        args.environment,
        args.spatial_current_grid,
        args.land_mask,
        args.out,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
