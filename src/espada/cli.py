from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from .demo import run_demo
from .dashboard import generate_dashboard
from .dossier import generate_evidence_dossier
from .decision import evaluate_case_decision
from .ais import normalize_ais_csv
from .attribution import write_attribution_outputs
from .case_alignment import validate_case_alignment
from .environment import load_environment, sync_historical_wind, write_environment_outputs
from .evaluation import EvaluationConfig, run_synthetic_evaluation
from .historical_ais import HistoricalAISRequest, fetch_gfw_presence, parse_utc_datetime
from .live_ais import AISBoundingBox, capture_aisstream
from .replay import build_forensic_replay
from .ml_dataset import audit_dataset
from .sar import load_sar_image, run_segmentation, run_synthetic_segmentation_demo
from .sentinel_catalog import SentinelSearchRequest, discover_sentinel1
from .sentinel_process import download_sentinel1_subset
from .slick import analyze_slick
from .verification import VerificationConfig, run_verification


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="espada", description="Espada verification tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="run the controlled physics verification")
    verify.add_argument("--out", type=Path, default=Path("out/verification"))
    verify.add_argument("--seed", type=int, default=26143)
    verify.add_argument("--particles", type=int, default=2_000)
    verify.add_argument("--members", type=int, default=20)
    verify.add_argument(
        "--skip-opendrift",
        action="store_true",
        help="skip the slower OpenDrift adapter round-trip check",
    )
    demo = subparsers.add_parser("demo", help="run offline physics and vessel attribution")
    demo.add_argument("--out", type=Path, default=Path("out/demo"))
    demo.add_argument("--seed", type=int, default=26143)
    demo.add_argument("--particles", type=int, default=2_000)
    demo.add_argument("--members", type=int, default=20)
    demo.add_argument(
        "--environment-mode",
        choices=["auto", "live", "cache", "synthetic"],
        default="auto",
    )
    demo.add_argument(
        "--environment-cache",
        type=Path,
        default=Path("data/cache/environment_latest.json"),
    )
    demo.add_argument(
        "--skip-opendrift",
        action="store_true",
        help="skip the slower OpenDrift adapter round-trip check",
    )
    environment = subparsers.add_parser(
        "environment", help="refresh or inspect environmental forcing data"
    )
    environment.add_argument("--mode", choices=["auto", "live", "cache", "synthetic"], default="auto")
    environment.add_argument("--cache", type=Path, default=Path("data/cache/environment_latest.json"))
    environment.add_argument("--out", type=Path, default=Path("out/environment"))
    environment.add_argument("--latitude", type=float, default=18.7167)
    environment.add_argument("--longitude", type=float, default=71.45)
    wind_history = subparsers.add_parser(
        "wind-history", help="download date-matched historical forecast wind"
    )
    wind_history.add_argument("--start", required=True)
    wind_history.add_argument("--end", required=True)
    wind_history.add_argument("--latitude", type=float, default=18.7167)
    wind_history.add_argument("--longitude", type=float, default=71.45)
    wind_history.add_argument("--cache", type=Path, default=Path("data/cache/wind_historical.json"))
    wind_history.add_argument("--out", type=Path, default=Path("out/historical_environment"))
    evaluate = subparsers.add_parser("evaluate", help="run multi-case synthetic evaluation")
    evaluate.add_argument("--out", type=Path, default=Path("out/evaluation"))
    evaluate.add_argument("--seed", type=int, default=26143)
    evaluate.add_argument("--cases", type=int, default=24)
    evaluate.add_argument("--particles", type=int, default=400)
    evaluate.add_argument("--members", type=int, default=8)
    slick = subparsers.add_parser("slick", help="analyze an observed slick GeoJSON")
    slick.add_argument("--input", type=Path, required=True)
    slick.add_argument("--environment-cache", type=Path, required=True)
    slick.add_argument("--out", type=Path, default=Path("out/slick"))
    slick.add_argument("--age-hours", type=float, default=19.0)
    slick.add_argument("--particles", type=int, default=2_000)
    slick.add_argument("--members", type=int, default=20)
    slick.add_argument("--seed", type=int, default=26143)
    dashboard = subparsers.add_parser("dashboard", help="regenerate the offline dashboard")
    dashboard.add_argument("--out", type=Path, default=Path("out/demo"))
    dossier = subparsers.add_parser(
        "dossier", help="build a portable evidence dossier from a completed case"
    )
    dossier.add_argument("--case-root", type=Path, required=True)
    dossier.add_argument("--out", type=Path, required=True)
    decide = subparsers.add_parser(
        "decide", help="apply the declared analyst-escalation policy to a completed case"
    )
    decide.add_argument("--case-root", type=Path, required=True)
    decide.add_argument("--out", type=Path, required=True)
    replay = subparsers.add_parser(
        "replay", help="render an animated forensic replay from a completed case"
    )
    replay.add_argument("--case", type=Path, required=True)
    replay.add_argument("--out", type=Path, default=Path("out/replay"))
    replay.add_argument("--frames", type=int, default=64)
    replay.add_argument("--fps", type=int, default=14)
    sar_demo = subparsers.add_parser("sar-demo", help="run evaluated synthetic SAR segmentation")
    sar_demo.add_argument("--out", type=Path, default=Path("out/sar"))
    sar_demo.add_argument("--seed", type=int, default=26143)
    sar = subparsers.add_parser("sar", help="segment a prepared SAR image")
    sar.add_argument("--input", type=Path, required=True)
    sar.add_argument("--out", type=Path, default=Path("out/sar"))
    sar.add_argument("--observation-time", required=True)
    sar.add_argument("--bbox", type=float, nargs=4, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    sar.add_argument("--analyst-approved", action="store_true")
    sar.add_argument("--model-checkpoint", type=Path)
    sar.add_argument("--calibration", type=Path)
    sar.add_argument("--prediction-bundle", type=Path)
    sar.add_argument("--inference-batch-size", type=int, default=4)
    ml_audit = subparsers.add_parser(
        "ml-audit", help="validate the labelled SAR data and create leakage-safe splits"
    )
    ml_audit.add_argument("--root", type=Path, required=True)
    ml_audit.add_argument("--out", type=Path, default=Path("out/ml_dataset"))
    ml_audit.add_argument("--seed", type=int, default=26143)
    sar_discover = subparsers.add_parser(
        "sar-discover", help="discover date-matched Sentinel-1 GRD scenes"
    )
    sar_discover.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
    )
    sar_discover.add_argument("--start", required=True, help="UTC search start")
    sar_discover.add_argument("--end", required=True, help="UTC search end")
    sar_discover.add_argument("--target", type=float, nargs=2, metavar=("LON", "LAT"))
    sar_discover.add_argument("--limit", type=int, default=100)
    sar_discover.add_argument("--out", type=Path, default=Path("out/sentinel1"))
    sar_discover.add_argument("--skip-preview", action="store_true")
    sar_download = subparsers.add_parser(
        "sar-download", help="download a calibrated Sentinel-1 VV crop"
    )
    sar_download.add_argument("--catalog", type=Path, required=True)
    sar_download.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
    )
    sar_download.add_argument("--width", type=int, default=1536)
    sar_download.add_argument("--height", type=int, default=1400)
    sar_download.add_argument("--out", type=Path, default=Path("out/sentinel1_case"))
    ais = subparsers.add_parser("ais", help="normalize and quality-check an AIS CSV")
    ais.add_argument("--input", type=Path, required=True)
    ais.add_argument("--out", type=Path, default=Path("out/ais_import"))
    live_ais = subparsers.add_parser("ais-live", help="capture live AISStream vessel positions")
    live_ais.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
    )
    live_ais.add_argument("--out", type=Path, default=Path("out/live_ais"))
    live_ais.add_argument("--cache", type=Path, default=Path("data/cache/ais_live.csv"))
    live_ais.add_argument("--duration-seconds", type=float, default=300.0)
    live_ais.add_argument("--window-hours", type=float, default=72.0)
    live_ais.add_argument("--max-messages", type=int)
    live_ais.add_argument("--mmsi", nargs="*")
    live_ais.add_argument("--api-key-env", default="AISSTREAM_API_KEY")
    live_ais.add_argument("--save-raw", action="store_true")
    live_ais.add_argument("--case", type=Path)
    live_ais.add_argument("--rank-out", type=Path, default=Path("out/live_ais_ranking"))
    historical_ais = subparsers.add_parser(
        "ais-history", help="download delayed Global Fishing Watch AIS vessel presence"
    )
    historical_ais.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
    )
    historical_ais.add_argument("--start", required=True, help="UTC start date/time")
    historical_ais.add_argument("--end", required=True, help="UTC end date/time")
    historical_ais.add_argument("--out", type=Path, default=Path("out/historical_ais"))
    historical_ais.add_argument("--token-env", default="GFW_API_ACCESS_TOKEN")
    historical_ais.add_argument("--case", type=Path)
    historical_ais.add_argument("--rank-out", type=Path, default=Path("out/historical_ais_ranking"))
    case_check = subparsers.add_parser(
        "case-check", help="verify that slick, forcing and AIS cover one incident window"
    )
    case_check.add_argument("--slick", type=Path, required=True)
    case_check.add_argument("--environment-cache", type=Path, required=True)
    case_check.add_argument("--ais", type=Path, required=True)
    case_check.add_argument("--out", type=Path, default=Path("out/real_case/case_alignment.json"))
    case_check.add_argument("--age-hours", type=float, required=True)
    case_check.add_argument("--max-ais-offset-hours", type=float, default=2.0)
    rank_ais = subparsers.add_parser("rank-ais", help="rank normalized AIS against a drift case")
    rank_ais.add_argument("--ais", type=Path, required=True)
    rank_ais.add_argument("--case", type=Path, default=Path("out/demo"))
    rank_ais.add_argument("--out", type=Path, default=Path("out/ais_ranking"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify":
        config = VerificationConfig(
            seed=args.seed,
            particles=args.particles,
            ensemble_members=args.members,
        )
        result = run_verification(
            args.out,
            config,
            check_opendrift=not args.skip_opendrift,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "demo":
        result = run_demo(
            args.out,
            seed=args.seed,
            particles=args.particles,
            ensemble_members=args.members,
            check_opendrift=not args.skip_opendrift,
            environment_mode=args.environment_mode,
            environment_cache=args.environment_cache,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "environment":
        bundle = load_environment(
            args.mode,
            args.cache,
            latitude=args.latitude,
            longitude=args.longitude,
        )
        result = write_environment_outputs(bundle, args.out)
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.command == "wind-history":
        result = sync_historical_wind(
            args.cache,
            start=args.start,
            end=args.end,
            latitude=args.latitude,
            longitude=args.longitude,
        )
        args.out.mkdir(parents=True, exist_ok=True)
        status_path = args.out / "historical_wind_status.json"
        result["status_file"] = str(status_path.resolve())
        status_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.command == "evaluate":
        result = run_synthetic_evaluation(
            args.out,
            EvaluationConfig(
                seed=args.seed,
                cases=args.cases,
                particles=args.particles,
                ensemble_members=args.members,
            ),
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "slick":
        result = analyze_slick(
            args.input,
            args.environment_cache,
            args.out,
            age_hours=args.age_hours,
            particles=args.particles,
            ensemble_members=args.members,
            seed=args.seed,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "dashboard":
        path = generate_dashboard(args.out)
        print(json.dumps({"status": "PASS", "dashboard": str(path)}, indent=2))
        return 0
    if args.command == "dossier":
        result = generate_evidence_dossier(args.case_root, args.out)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "decide":
        result = evaluate_case_decision(args.case_root, args.out)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "replay":
        result = build_forensic_replay(
            args.case, args.out, frames=args.frames, fps=args.fps
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "sar-demo":
        result = run_synthetic_segmentation_demo(args.out, seed=args.seed)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "sar":
        result = run_segmentation(
            load_sar_image(args.input),
            args.out,
            source=str(args.input),
            observation_time_utc=args.observation_time,
            bbox=tuple(args.bbox) if args.bbox else None,
            analyst_approved=args.analyst_approved,
            model_checkpoint=args.model_checkpoint,
            calibration_path=args.calibration,
            prediction_bundle=args.prediction_bundle,
            inference_batch_size=args.inference_batch_size,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"PASS", "REVIEW_REQUIRED", "NO_DETECTION"} else 1
    if args.command == "ml-audit":
        result = audit_dataset(args.root, args.out, seed=args.seed)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "sar-discover":
        result = discover_sentinel1(
            SentinelSearchRequest(
                tuple(args.bbox),
                datetime.fromisoformat(args.start.replace("Z", "+00:00")),
                datetime.fromisoformat(args.end.replace("Z", "+00:00")),
                tuple(args.target) if args.target else None,
                args.limit,
            ),
            args.out,
            download_preview=not args.skip_preview,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"PASS", "PARTIAL"} else 1
    if args.command == "sar-download":
        result = download_sentinel1_subset(
            args.catalog,
            args.out,
            bbox=tuple(args.bbox),
            width=args.width,
            height=args.height,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "ais":
        result = normalize_ais_csv(args.input, args.out)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "ais-live":
        result = asyncio.run(
            capture_aisstream(
                AISBoundingBox(*args.bbox),
                args.out,
                args.cache,
                duration_seconds=args.duration_seconds,
                window_hours=args.window_hours,
                max_messages=args.max_messages,
                mmsi=args.mmsi,
                api_key_env=args.api_key_env,
                save_raw=args.save_raw,
            )
        )
        if result["cached_positions"]:
            quality = normalize_ais_csv(
                args.cache,
                args.out,
                normalized_path=args.out / "ais_normalized.csv",
            )
            result["quality_report"] = str((args.out / "ais_quality.json").resolve())
            result["normalized_file"] = quality["normalized_file"]
            if args.case:
                physics = args.case if (args.case / "release_estimate.json").exists() else args.case / "physics"
                ranking = write_attribution_outputs(
                    args.rank_out,
                    args.out / "ais_normalized.csv",
                    physics / "reverse_endpoints.npz",
                    physics / "release_estimate.json",
                    physics / "forward_particles.npz",
                )
                result["ranking"] = {
                    "output": str(args.rank_out.resolve()),
                    "top_candidate": ranking["top_candidate"],
                }
        (args.out / "live_run_result.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "ais-history":
        result = fetch_gfw_presence(
            HistoricalAISRequest(
                AISBoundingBox(*args.bbox),
                parse_utc_datetime(args.start),
                parse_utc_datetime(args.end),
            ),
            args.out,
            token_env=args.token_env,
        )
        if result["status"] == "PASS" and args.case:
            physics = args.case if (args.case / "release_estimate.json").exists() else args.case / "physics"
            ranking = write_attribution_outputs(
                args.rank_out,
                args.out / "ais_normalized.csv",
                physics / "reverse_endpoints.npz",
                physics / "release_estimate.json",
                physics / "forward_particles.npz",
            )
            result["ranking"] = {
                "output": str(args.rank_out.resolve()),
                "top_candidate": ranking["top_candidate"],
            }
            (args.out / "historical_ais_run_result.json").write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8"
            )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "case-check":
        result = validate_case_alignment(
            args.slick,
            args.environment_cache,
            args.ais,
            args.out,
            age_hours=args.age_hours,
            max_ais_offset_hours=args.max_ais_offset_hours,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "rank-ais":
        physics = args.case if (args.case / "release_estimate.json").exists() else args.case / "physics"
        result = write_attribution_outputs(
            args.out,
            args.ais,
            physics / "reverse_endpoints.npz",
            physics / "release_estimate.json",
            physics / "forward_particles.npz",
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
