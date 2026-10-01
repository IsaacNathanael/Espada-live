"""Publish a portable, read-only replay from the saved Wakashio evidence run.

No provider calls or model inference occur here. The generated file is a frozen
copy of already computed artifacts, with their limitations retained.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "out" / "wakashio"
DESTINATION = ROOT / "operator" / "historical_case" / "case-data.json"
SCRIPT_DESTINATION = DESTINATION.with_suffix(".js")


def read_json(relative: str) -> dict:
    return json.loads((CASE / relative).read_text(encoding="utf-8"))


def sha256(relative: str) -> str:
    return hashlib.sha256((CASE / relative).read_bytes()).hexdigest()


def build() -> dict:
    evaluation = read_json("evaluation/historical_evaluation.json")
    quality = read_json("ais/ais_quality.json")
    status = read_json("ais/historical_ais_status.json")
    slick = read_json("prepared/slick.geojson")
    analysis = read_json("drift/slick_analysis.json")
    release = read_json("drift/release_estimate.json")
    ranking = read_json("ranking/candidates.json")
    decision = read_json("decision/decision_gate.json")
    truth = read_json("prepared/sealed_truth.json")
    alignment = read_json("case_alignment.json")

    # Registry lookups are display context, not fields supplied by the historical
    # GFW extract. Do not infer a ship's 2020 name from a current registry entry.
    registry = {
        "215337000": {
            "name": "BOKA EXPEDITION",
            "source": "https://www.tradlinx.com/vessel-tracking/VesselId%3A209326-Vessel%3ABOKA_EXPEDITION-MMSI%3A215337000-CallSign%3A9HA5068-IMO%3A9358943",
            "note": "Later registry identification; 2020 name not verified here.",
        },
        "372711000": {
            "name": "MV Wakashio",
            "source": evaluation["sources"]["official_casualty_report"],
            "note": "Identity corroborated by the official casualty report.",
        },
        "376955000": {
            "name": "STANFORD HAWK",
            "source": "https://www.itu.int/en/ITU-R/terrestrial/mars/Documents/1st_ListV_compilation%202025.pdf",
            "note": "Later registry identification; 2020 name not verified here.",
        },
        "645465000": {
            "name": None,
            "source": None,
            "note": "Name not verified; show the recorded MMSI only.",
        },
        "660003900": {
            "name": "VB CARTIER",
            "source": "https://www.shipspotting.com/photos/3179874?imo=9395848",
            "note": "Registry/photo identification; verify name at incident time before operational use.",
        },
    }

    positions: dict[str, list[dict]] = {mmsi: [] for mmsi in registry}
    with (CASE / "ais/ais_normalized.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            mmsi = str(row["mmsi"])
            if mmsi not in positions:
                raise ValueError(f"Unexpected vessel {mmsi} in saved AIS extract")
            positions[mmsi].append(
                {
                    "time": row["timestamp_utc"],
                    "longitude": round(float(row["longitude"]), 6),
                    "latitude": round(float(row["latitude"]), 6),
                }
            )
    for track in positions.values():
        track.sort(key=lambda sample: sample["time"])
    if sum(map(len, positions.values())) != quality["valid_rows"]:
        raise ValueError("Saved AIS count no longer agrees with quality report")
    if len(positions) != ranking["candidate_count"]:
        raise ValueError("Saved ranking and AIS vessel counts disagree")

    ranked = []
    reverse_identity = {candidate: mmsi for mmsi, candidate in truth["gfw_identity_map"].items()}
    for candidate in ranking["candidates"]:
        mmsi = reverse_identity[candidate["mmsi"]]
        ranked.append(
            {
                "rank": candidate["rank"],
                "mmsi": mmsi,
                "name": registry[mmsi]["name"],
                "score": candidate["total_score"],
                "presence": candidate["presence_score"],
                "forward_error_km": candidate["forward_error_km"],
                "shape_error_km": candidate["forward_shape_error_km"],
                "data_quality": candidate["data_quality"],
                "ais_gap": candidate["silence_classification"],
            }
        )

    return {
        "schema": "espada.historical-case-site.v1",
        "mode": "RECORDED HISTORICAL REPLAY — NOT LIVE",
        "case": "Mauritius oil-spill reconstruction · August 2020",
        "observation_time_utc": release["observation_time_utc"],
        "release_time_utc": release["release_time_utc"],
        "source_vessel": evaluation["target"],
        "documented_wreck": truth["documented_grounding_position"],
        "slick": slick,
        "slick_area_km2": analysis["input"]["area_km2"],
        "slick_source": slick["features"][0]["properties"],
        "release": release,
        "alignment": alignment,
        "ranking": ranked,
        "decision": {
            "value": decision["decision"],
            "recommended_action": decision["recommended_action"],
            "checks": decision["checks"],
            "policy_interpretation": decision["policy_interpretation"],
        },
        "vessels": [
            {"mmsi": mmsi, **registry[mmsi], "positions": positions[mmsi]}
            for mmsi in sorted(positions)
        ],
        "limitations": evaluation["limitations"],
        "sources": {
            **evaluation["sources"],
            "gfw_documentation": "https://globalfishingwatch.org/platform-update/global-ais-vessel-presence-dataset/",
        },
        "provider": {
            "name": status["provider"],
            "product": status["data_product"],
            "sampling": "Hourly presence at 0.01-degree grid-cell centres; not raw AIS tracks",
            "rows": quality["valid_rows"],
            "vessels": quality["vessel_count"],
        },
        "source_sha256": {
            "ais": sha256("ais/ais_normalized.csv"),
            "slick": sha256("prepared/slick.geojson"),
            "ranking": sha256("ranking/candidates.json"),
            "release": sha256("drift/release_estimate.json"),
        },
    }


if __name__ == "__main__":
    result = build()
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(result, indent=2, ensure_ascii=False)
    DESTINATION.write_text(serialized + "\n", encoding="utf-8")
    SCRIPT_DESTINATION.write_text(
        "window.ESPADA_WAKASHIO_CASE = " + serialized + ";\n", encoding="utf-8"
    )
    assets = DESTINATION.parent / "assets"
    assets.mkdir(exist_ok=True)
    shutil.copy2(CASE / "drift/slick_reverse_analysis.png", assets / "slick_reverse_analysis.png")
    print(f"Published {DESTINATION} ({result['provider']['rows']} real vessel-presence rows)")
