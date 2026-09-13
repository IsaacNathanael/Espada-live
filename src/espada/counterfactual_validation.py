from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _synthetic_candidate_id(seed: int) -> str:
    digest = hashlib.sha256(f"espada-corsica-counterfactual:{seed}".encode()).hexdigest()
    return f"99{int(digest[:12], 16) % 10_000_000:07d}"


def prepare_counterfactual_ais(
    background_ais_path: Path,
    release_estimate_path: Path,
    output_path: Path,
    sealed_registry_path: Path,
    *,
    seed: int = 26143,
) -> dict[str, object]:
    """Add a clearly labelled synthetic source track to real background traffic.

    This isolates the ranking question: if a source-consistent AIS track had been
    observable, could the evidence engine recover it? It does not validate AIS
    authenticity or reverse-drift accuracy.
    """
    background = pd.read_csv(background_ais_path, dtype={"mmsi": str})
    required = {"timestamp_utc", "mmsi", "longitude", "latitude"}
    missing = required - set(background.columns)
    if missing:
        raise ValueError(f"Background AIS is missing columns: {sorted(missing)}")
    estimate = _read_json(release_estimate_path)
    release_time = pd.Timestamp(estimate["release_time_utc"])
    observation_time = pd.Timestamp(estimate["observation_time_utc"])
    origin = estimate["estimated_origin"]
    candidate_id = _synthetic_candidate_id(seed)
    if candidate_id in set(background["mmsi"].astype(str)):
        raise ValueError("Synthetic candidate ID collides with the background AIS")

    timestamps = pd.date_range(
        release_time - pd.Timedelta(hours=6), observation_time, freq="1h"
    )
    offsets = (timestamps - release_time).total_seconds().to_numpy() / 3600.0
    rng = np.random.default_rng(seed)
    # A slow, plausible synthetic track crossing the inferred origin at release time.
    longitude = float(origin["longitude"]) + 0.0025 * offsets
    latitude = float(origin["latitude"]) + 0.0012 * offsets
    longitude += rng.normal(0.0, 0.00015, len(timestamps))
    latitude += rng.normal(0.0, 0.00015, len(timestamps))
    at_release = int(np.argmin(np.abs(offsets)))
    longitude[at_release] = float(origin["longitude"])
    latitude[at_release] = float(origin["latitude"])

    injected = pd.DataFrame(
        {
            "timestamp_utc": timestamps.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mmsi": candidate_id,
            "vessel_name": f"Blinded synthetic {candidate_id}",
            "longitude": longitude,
            "latitude": latitude,
            "is_interpolated": False,
            "gap_before_minutes": [0.0] + [60.0] * (len(timestamps) - 1),
            "source": "synthetic counterfactual AIS injection; NOT observed AIS",
            "sampling_interval_minutes": 60.0,
        }
    )
    for column in injected.columns:
        if column not in background:
            background[column] = None
    for column in background.columns:
        if column not in injected:
            injected[column] = None
    combined = pd.concat([background, injected[background.columns]], ignore_index=True)
    combined = combined.sort_values(["timestamp_utc", "mmsi"]).reset_index(drop=True)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)

    registry = {
        "schema": "espada.counterfactual-source.v1",
        "sealed_until_after_ranking": True,
        "case_id": "corsica_2018_counterfactual_ais",
        "candidate_id": candidate_id,
        "identity": "CONTROLLED SYNTHETIC SOURCE",
        "construction": (
            "Synthetic track crosses the independently computed reverse-drift origin "
            "at the selected release time and is mixed with real GFW background traffic."
        ),
        "claim_boundary": (
            "This tests candidate ranking under restored observability. It is not real AIS, "
            "not an independent source-recovery case, and not evidence against CSL Virginia."
        ),
        "ranking_input_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
    }
    sealed_registry_path = Path(sealed_registry_path)
    sealed_registry_path.parent.mkdir(parents=True, exist_ok=True)
    sealed_registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return {
        "status": "PASS",
        "experiment": "real-background counterfactual AIS observability test",
        "real_background_positions": len(background),
        "real_background_vessels": int(background["mmsi"].nunique()),
        "synthetic_positions": len(injected),
        "combined_vessels": int(combined["mmsi"].nunique()),
        "output": str(output_path.resolve()),
        "disclosure": "One clearly labelled synthetic source track was added to real background AIS.",
    }


def reveal_counterfactual_result(
    ranking_path: Path, sealed_registry_path: Path, output_path: Path
) -> dict[str, object]:
    ranking_path = Path(ranking_path)
    if not ranking_path.exists():
        raise FileNotFoundError("Ranking must exist before the sealed target is revealed")
    ranking = _read_json(ranking_path)
    registry = _read_json(sealed_registry_path)
    candidates = ranking.get("candidates", [])
    match = next(
        (item for item in candidates if str(item.get("mmsi")) == registry["candidate_id"]),
        None,
    )
    rank = int(match["rank"]) if match else None
    result = {
        "status": "PASS" if rank is not None and rank <= 3 else "FAIL",
        "experiment": "controlled counterfactual AIS observability test",
        "target_present": match is not None,
        "target_rank": rank,
        "top_1": rank == 1,
        "top_3": rank is not None and rank <= 3,
        "candidate_count": int(ranking.get("candidate_count", len(candidates))),
        "comparative_score": match.get("total_score") if match else None,
        "forward_error_km": match.get("forward_error_km") if match else None,
        "integrity": {
            "ranking_existed_before_truth_reveal": True,
            "ranker_received_no_real_vessel_identity": True,
        },
        "claim_boundary": registry["claim_boundary"],
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the counterfactual AIS validation adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--background-ais", type=Path, required=True)
    prepare.add_argument("--release-estimate", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--registry", type=Path, required=True)
    prepare.add_argument("--seed", type=int, default=26143)
    reveal = subparsers.add_parser("reveal")
    reveal.add_argument("--ranking", type=Path, required=True)
    reveal.add_argument("--registry", type=Path, required=True)
    reveal.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_counterfactual_ais(
            args.background_ais,
            args.release_estimate,
            args.output,
            args.registry,
            seed=args.seed,
        )
    else:
        result = reveal_counterfactual_result(args.ranking, args.registry, args.output)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
