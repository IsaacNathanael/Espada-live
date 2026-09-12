from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import requests
import shapefile
from shapely.geometry import MultiPolygon, Polygon, shape

from .geo import write_polygon_geojson


WAKASHIO_MMSI = "372711000"
WAKASHIO_IMO = "9337119"
OBSERVATION_TIME = pd.Timestamp("2020-08-06T06:24:49Z")
GROUNDING_LONGITUDE = 57 + 44.6 / 60
GROUNDING_LATITUDE = -(20 + 26.6 / 60)
UNOSAT_PRODUCT_URL = "https://unosat.org/products/2888"
MAURITIUS_REPORT_URL = (
    "https://blueconomy.govmu.org/Documents/Publications/"
    "Report%20Court%20of%20Investigation-MV%20Wakashio.pdf"
)
SENTINEL_TIME_SOURCE_URL = (
    "https://www.sentinelvision.eu/gallery/pdf/031dbec6963f41c4991d1cf06d8668f4"
)
UNOSAT_ARCHIVE_URL = (
    "https://unosat.org/static/unosat_filesystem/2888/OS20200807MUS_SHP.zip"
)


def download_unosat_archive(output_dir: Path) -> dict[str, object]:
    """Download and safely extract the small official UNOSAT vector bundle."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / "OS20200807MUS_SHP.zip"
    if not archive_path.exists():
        response = requests.get(UNOSAT_ARCHIVE_URL, timeout=90)
        response.raise_for_status()
        archive_path.write_bytes(response.content)
    extract_dir = output_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve()
    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError("UNOSAT archive contains an unsafe path")
        archive.extractall(extract_dir)
    slick = extract_dir / "ST2_20200806_OilSpillExtent_ReefPointeEsny.shp"
    if not slick.exists():
        raise FileNotFoundError("Expected UNOSAT 6 August oil extent is missing")
    return {
        "status": "PASS",
        "provider": "UNITAR-UNOSAT",
        "archive": str(archive_path.resolve()),
        "archive_bytes": archive_path.stat().st_size,
        "slick_shapefile": str(slick.resolve()),
        "source": UNOSAT_ARCHIVE_URL,
    }


def _largest_polygon(value: object) -> Polygon:
    geometry = shape(value)
    if isinstance(geometry, Polygon):
        return geometry
    if isinstance(geometry, MultiPolygon):
        return max(geometry.geoms, key=lambda item: item.area)
    polygon = geometry.convex_hull
    if not isinstance(polygon, Polygon) or polygon.is_empty:
        raise ValueError("UNOSAT source did not contain a usable polygon")
    return polygon


def build_unosat_slick(shapefile_path: Path, output_path: Path) -> dict[str, object]:
    """Convert the official UNOSAT 6 August oil extent into ESPADA's slick contract."""
    reader = shapefile.Reader(str(shapefile_path))
    if len(reader) != 1:
        raise ValueError("Expected exactly one UNOSAT oil-extent feature")
    record = reader.record(0).as_dict()
    polygon = _largest_polygon(reader.shape(0).__geo_interface__)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_polygon_geojson(
        output_path,
        polygon,
        {
            "observation_time_utc": OBSERVATION_TIME.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "observation_time_source": SENTINEL_TIME_SOURCE_URL,
            "detection_confidence": 0.85,
            "confidence_basis": "UNOSAT source attribute: High; numeric value is an ESPADA display mapping, not a calibrated probability",
            "source": "UNITAR-UNOSAT Sentinel-2 oil-spill extent, 6 August 2020",
            "source_product": UNOSAT_PRODUCT_URL,
            "source_sensor": str(record.get("SensorID", "Sentinel-2")),
            "source_confidence": str(record.get("Confidence", "High")),
            "source_area_m2": float(record.get("Area_m2", 0.0)),
            "field_validation": str(record.get("Field_Vali", "Not stated")),
            "review_status": "approved_external_analysis_product",
            "licence": "UNITAR-UNOSAT product terms apply; attribution required",
        },
    )
    return {
        "status": "PASS",
        "slick_geojson": str(output_path.resolve()),
        "observation_time_utc": OBSERVATION_TIME.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_confidence": record.get("Confidence"),
        "source_area_km2": float(record.get("Area_m2", 0.0)) / 1_000_000.0,
    }


def _candidate_id(mmsi: str) -> str:
    digest = hashlib.sha256(f"espada-wakashio-blind-v1:{mmsi}".encode()).hexdigest()
    return f"CAND-{digest[:6].upper()}"


def build_blinded_candidates(
    gfw_ais_path: Path,
    output_path: Path,
    truth_registry_path: Path,
) -> dict[str, object]:
    """Pseudonymize GFW traffic and add the documented grounded source position.

    The added source track is explicitly case-file evidence, not fabricated AIS.  The
    identity mapping is written separately so the ranker cannot read it.
    """
    frame = pd.read_csv(gfw_ais_path, dtype={"mmsi": str})
    required = {
        "timestamp_utc",
        "mmsi",
        "longitude",
        "latitude",
        "is_interpolated",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"GFW file is missing columns: {sorted(missing)}")
    frame["candidate_id"] = frame["mmsi"].map(_candidate_id)
    mapping = dict(zip(frame["mmsi"], frame["candidate_id"]))
    frame["mmsi"] = frame["candidate_id"]
    frame["vessel_name"] = frame["candidate_id"].map(lambda value: f"Blinded {value}")
    frame["evidence_type"] = "GFW hourly AIS vessel-presence grid"
    frame = frame.drop(columns=["candidate_id"])

    target_id = _candidate_id(WAKASHIO_MMSI)
    target_times = pd.date_range(
        OBSERVATION_TIME - timedelta(hours=4),
        OBSERVATION_TIME,
        freq="1h",
    )
    target_rows = pd.DataFrame(
        {
            "timestamp_utc": target_times.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mmsi": target_id,
            "vessel_name": f"Blinded {target_id}",
            "longitude": GROUNDING_LONGITUDE,
            "latitude": GROUNDING_LATITUDE,
            "is_interpolated": True,
            "source": "Official casualty-file position reconstruction; NOT GFW AIS",
            "sampling_interval_minutes": 60.0,
            "timestamp_source": "documented grounded position",
            "gfw_vessel_id": None,
            "presence_hours": None,
            "evidence_type": "fixed wreck position from official Mauritius investigation",
        }
    )
    for column in target_rows.columns:
        if column not in frame:
            frame[column] = None
    for column in frame.columns:
        if column not in target_rows:
            target_rows[column] = None
    blinded = pd.concat([frame, target_rows[frame.columns]], ignore_index=True)
    blinded = blinded.sort_values(["timestamp_utc", "mmsi"]).reset_index(drop=True)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blinded.to_csv(output_path, index=False)
    ranking_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

    target_in_gfw = WAKASHIO_MMSI in mapping
    target_gfw_positions = int((frame["mmsi"] == target_id).sum())
    disclosure = (
        f"The GFW extract contains {target_gfw_positions} hourly positions for the target "
        "through 5 August 2020. It then has a target-specific gap while peer vessels remain "
        "visible. The documented fixed wreck position fills the incident-time location and "
        "is explicitly marked as case-file evidence, not AIS."
        if target_in_gfw
        else (
            "The public GFW extract did not contain MMSI 372711000. The target's fixed "
            "wreck position was reconstructed from the official casualty record and is "
            "not represented as AIS."
        )
    )
    registry = {
        "schema": "espada.historical-truth.v1",
        "sealed_until_after_ranking": True,
        "target_candidate_id": target_id,
        "identity": {
            "vessel_name": "MV Wakashio",
            "mmsi": WAKASHIO_MMSI,
            "imo": WAKASHIO_IMO,
        },
        "documented_grounding_position": {
            "longitude": GROUNDING_LONGITUDE,
            "latitude": GROUNDING_LATITUDE,
            "source": MAURITIUS_REPORT_URL,
        },
        "input_disclosure": disclosure,
        "target_gfw_positions": target_gfw_positions,
        "ranking_input_sha256": ranking_sha256,
        "gfw_identity_map": mapping,
    }
    truth_registry_path = Path(truth_registry_path)
    truth_registry_path.parent.mkdir(parents=True, exist_ok=True)
    truth_registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return {
        "status": "PASS",
        "blinded_candidates": str(output_path.resolve()),
        "truth_registry": str(truth_registry_path.resolve()),
        "ranking_input_sha256": ranking_sha256,
        "gfw_positions": len(frame),
        "gfw_candidates": int(frame["mmsi"].nunique()),
        "total_candidates": int(blinded["mmsi"].nunique()),
        "target_absent_from_gfw": not target_in_gfw,
        "target_gfw_positions": target_gfw_positions,
    }


def evaluate_blind_ranking(
    ranking_path: Path,
    truth_registry_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    """Reveal the sealed identity only after the candidate ranking exists."""
    ranking_path = Path(ranking_path)
    if not ranking_path.exists():
        raise FileNotFoundError("Ranking must exist before the truth registry is opened")
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    registry = json.loads(Path(truth_registry_path).read_text(encoding="utf-8"))
    candidates = ranking.get("candidates", [])
    target_id = registry["target_candidate_id"]
    match = next((item for item in candidates if item.get("mmsi") == target_id), None)
    if match is None:
        raise ValueError("Sealed target candidate is absent from the ranking")
    rank = int(match["rank"])
    passed_top_1 = rank == 1
    passed_top_3 = rank <= 3
    result = {
        "status": "PASS" if passed_top_3 else "FAIL",
        "evaluation_type": "historical known-source reconstruction; not an external blind test",
        "question": "Can ESPADA rank the documented release vessel above nearby candidates?",
        "answer": (
            f"Yes — MV Wakashio ranked #{rank} of {len(candidates)}."
            if passed_top_3
            else f"No — MV Wakashio ranked #{rank} of {len(candidates)}."
        ),
        "target": registry["identity"],
        "target_candidate_id": target_id,
        "rank": rank,
        "candidate_count": len(candidates),
        "top_1_pass": passed_top_1,
        "top_3_pass": passed_top_3,
        "comparative_score": match.get("total_score"),
        "forward_error_km": match.get("forward_error_km"),
        "integrity": {
            "ranker_received_pseudonymized_ids": True,
            "truth_opened_after_ranking_file_existed": True,
            "ranking_input_sha256": registry["ranking_input_sha256"],
        },
        "disclosure": registry["input_disclosure"],
        "limitations": [
            "This is a transparent historical reconstruction, not an untouched external blind test.",
            "The source-vessel position is reconstructed from the official fixed wreck position, not continuous GFW AIS.",
            "The first visible spill is close to the wreck; this case validates evidence fusion more than long-range drift skill.",
            "UNOSAT marked the source polygon as not yet field validated, despite high analyst confidence.",
            "Candidate ranking is investigative support and never a finding of guilt.",
        ],
        "sources": {
            "official_casualty_report": MAURITIUS_REPORT_URL,
            "unosat_product": UNOSAT_PRODUCT_URL,
            "sentinel_acquisition_time": SENTINEL_TIME_SOURCE_URL,
            "gfw": "Global Fishing Watch public-global-presence:latest",
        },
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "historical_evaluation.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    score = 100.0 * float(match.get("total_score", 0.0))
    verdict = "TOP-1 PASS" if passed_top_1 else ("TOP-3 PASS" if passed_top_3 else "NOT PASSED")
    def distance_text(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "N/A"
        return f"{number:.2f} km" if math.isfinite(number) else "N/A"

    rows = "".join(
        f"<tr class='{'target' if item.get('mmsi') == target_id else ''}'><td>#{item.get('rank')}</td>"
        f"<td>{html.escape(str(item.get('vessel_name')))}</td>"
        f"<td>{100*float(item.get('total_score',0)):.1f}%</td>"
        f"<td>{distance_text(item.get('forward_error_km'))}</td></tr>"
        for item in candidates[:8]
    )
    disclosure_html = html.escape(registry["input_disclosure"])
    report = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA · Wakashio historical replay</title><style>
    :root{{--ink:#eafffb;--muted:#91aaa6;--mint:#61f2d1;--amber:#ffbd4a;--panel:#082329;--line:#17434b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 18% 0,#124149 0,transparent 34%),#020a0c;color:var(--ink);font-family:Inter,Segoe UI,sans-serif}}main{{width:min(1100px,calc(100% - 28px));margin:auto;padding:34px 0 60px}}.tag{{display:inline-block;color:var(--mint);font-size:11px;font-weight:900;letter-spacing:.16em}}h1{{font:500 clamp(38px,6vw,72px)/.98 Georgia,serif;margin:16px 0}}h1 em{{color:var(--mint);font-style:normal}}p{{color:var(--muted);line-height:1.6}}.hero,.grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}}.card{{background:linear-gradient(145deg,#0a2a31,#05171b);border:1px solid var(--line);border-radius:18px;padding:22px}}.verdict{{display:flex;flex-direction:column;justify-content:center}}.verdict b{{font:500 46px Georgia,serif;color:var(--amber)}}.verdict strong{{font-size:18px;margin-top:9px}}.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0}}.kpi{{padding:16px;border-radius:13px;background:#071c21;border:1px solid var(--line)}}.kpi small{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase}}.kpi b{{display:block;margin-top:6px;font:500 25px Georgia,serif}}.flow{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}}.step{{padding:14px;border:1px solid var(--line);border-radius:12px;font-size:12px}}.step i{{display:block;color:var(--mint);font-style:normal;font-weight:900;margin-bottom:6px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:11px;border-top:1px solid var(--line);font-size:12px;text-align:left}}th{{color:var(--muted)}}tr.target{{background:rgba(97,242,209,.11);color:var(--mint);font-weight:800}}.warn{{border-left:3px solid var(--amber);padding:14px 17px;background:rgba(255,189,74,.06);color:#e8d5a9;font-size:12px;line-height:1.55}}ul{{color:#b9ccc8;font-size:12px;line-height:1.6}}a{{color:var(--mint)}}@media(max-width:760px){{.hero,.grid,.flow{{grid-template-columns:1fr}}.kpis{{grid-template-columns:1fr 1fr}}}}
    </style></head><body><main><span class='tag'>ESPADA · HISTORICAL KNOWN-SOURCE REPLAY</span><section class='hero'><div><h1>Could we recover the <em>Wakashio</em>?</h1><p>The answer key stayed separate while ESPADA ranked pseudonymized candidates against an official satellite-derived slick extent.</p></div><div class='card verdict'><b>{verdict}</b><strong>MV Wakashio ranked #{rank} of {len(candidates)}</strong><p>{score:.1f}% comparative case score · {float(match.get('forward_error_km',0)):.2f} km forward error</p></div></section><div class='kpis'><div class='kpi'><small>Satellite</small><b>Sentinel-2</b></div><div class='kpi'><small>Observed</small><b>06:24 UTC</b></div><div class='kpi'><small>Nearby candidates</small><b>{len(candidates)}</b></div></div><section class='flow'><div class='step'><i>01</i>UNOSAT oil extent</div><div class='step'><i>02</i>Reverse drift ensemble</div><div class='step'><i>03</i>Blinded candidate ranking</div><div class='step'><i>04</i>Identity reveal</div></section><section class='grid'><div class='card'><h2>Candidate leaderboard</h2><table><thead><tr><th>Rank</th><th>Blinded ID</th><th>Score</th><th>Replay error</th></tr></thead><tbody>{rows}</tbody></table></div><div class='card'><h2>What makes this honest</h2><p>{disclosure_html}</p><div class='warn'>This proves the evidence-fusion and ranking workflow on a known-source incident. It is not a blind, cross-ocean accuracy claim and it is not evidence of guilt.</div></div></section><section class='card' style='margin-top:14px'><h2>Mandatory limitations</h2><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in result['limitations'])}</ul><p>Sources: <a href='{MAURITIUS_REPORT_URL}'>Mauritius Court of Investigation</a> · <a href='{UNOSAT_PRODUCT_URL}'>UNITAR-UNOSAT product 2888</a> · Global Fishing Watch public vessel-presence data</p></section></main></body></html>"""
    report_path = output_dir / "historical_evaluation_report.html"
    report_path.write_text(report, encoding="utf-8")
    result["report"] = str(report_path.resolve())
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and reveal ESPADA historical cases")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--unosat-shp", type=Path, required=True)
    prepare.add_argument("--gfw-ais", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    download = sub.add_parser("download-unosat")
    download.add_argument("--out", type=Path, required=True)
    reveal = sub.add_parser("reveal")
    reveal.add_argument("--ranking", type=Path, required=True)
    reveal.add_argument("--truth", type=Path, required=True)
    reveal.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "download-unosat":
        result = download_unosat_archive(args.out)
    elif args.command == "prepare":
        result = {
            "slick": build_unosat_slick(args.unosat_shp, args.out / "slick.geojson"),
            "candidates": build_blinded_candidates(
                args.gfw_ais,
                args.out / "blinded_candidates.csv",
                args.out / "sealed_truth.json",
            ),
        }
    else:
        result = evaluate_blind_ranking(args.ranking, args.truth, args.out)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
