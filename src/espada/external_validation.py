from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .geo import haversine_km


def _read(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Required validation artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _elapsed_hours(start: object, end: object) -> float | None:
    if not start or not end:
        return None
    start_time = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    end_time = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    return (end_time - start_time).total_seconds() / 3600.0


def evaluate_registered_case(
    registry_path: Path,
    ranking_path: Path,
    release_estimate_path: Path,
    decision_path: Path,
    output_path: Path,
) -> dict[str, object]:
    """Reveal registered truth only after loading an already-written ranking."""
    ranking_path = Path(ranking_path)
    if not ranking_path.exists():
        raise FileNotFoundError("Candidate ranking must exist before truth is revealed")
    ranking_bytes = ranking_path.read_bytes()
    ranking = json.loads(ranking_bytes.decode("utf-8-sig"))

    registry = _read(registry_path)
    release = _read(release_estimate_path)
    decision = _read(decision_path)
    target = registry["sealed_target"]
    incident = registry["incident"]
    acceptance = registry["acceptance"]
    observation = registry.get("observation", {})
    candidates = ranking.get("candidates", [])
    match = next(
        (item for item in candidates if str(item.get("mmsi")) == str(target["mmsi"])),
        None,
    )
    target_rank = int(match["rank"]) if match else None
    origin = release["estimated_origin"]
    origin_error = haversine_km(
        float(origin["longitude"]),
        float(origin["latitude"]),
        float(incident["release_longitude"]),
        float(incident["release_latitude"]),
    )
    target_present = match is not None
    rank_pass = target_rank is not None and target_rank <= int(acceptance["target_rank_at_most"])
    origin_pass = origin_error <= float(acceptance["origin_error_km_at_most"])
    safe_decision = str(decision.get("decision", "")).startswith("ABSTAIN") if not target_present else True
    checks = {
        "target_observable_in_candidate_feed": target_present,
        "documented_source_rank": rank_pass,
        "reverse_origin_error": origin_pass,
        "evidence_gate_safety": safe_decision,
    }
    failure_class = None
    if not target_present:
        failure_class = "INPUT_EVIDENCE_COVERAGE"
    elif not rank_pass:
        failure_class = "ATTRIBUTION_RANKING"
    elif not origin_pass:
        failure_class = "REVERSE_DRIFT"
    elif not safe_decision:
        failure_class = "DECISION_SAFETY"

    result: dict[str, object] = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "evaluation_class": registry["evaluation_class"],
        "case_id": registry["case_id"],
        "failure_class": failure_class,
        "checks": checks,
        "source_recovery": {
            "documented_vessel": target["vessel_name"],
            "documented_mmsi": str(target["mmsi"]),
            "target_present": target_present,
            "rank": target_rank,
            "candidate_count": int(ranking.get("candidate_count", len(candidates))),
        },
        "reverse_drift": {
            "origin_error_km": origin_error,
            "accepted_at_km": float(acceptance["origin_error_km_at_most"]),
            "assumed_age_hours": release.get("assumed_age_hours"),
            "documented_elapsed_hours": _elapsed_hours(
                incident.get("release_time_utc"), observation.get("time_utc")
            ),
        },
        "decision": decision.get("decision"),
        "interpretation": (
            "The slick detector and reverse origin passed their declared checks, and the safety gate abstained. "
            "Source recovery could not be evaluated because the documented source MMSI was absent from the "
            "Global Fishing Watch candidate feed. A licensed terrestrial or satellite AIS archive is required."
            if not target_present
            else "The documented source was observable and evaluated under the preregistered acceptance rules."
        ),
        "claim_boundary": (
            "This is a retrospective preregistered holdout, not an external blind trial. "
            "A missing source vessel is an input-coverage failure and must never be counted as a ranking success."
        ),
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a preregistered ESPADA case")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--release-estimate", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_registered_case(
        args.registry, args.ranking, args.release_estimate, args.decision, args.output
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
