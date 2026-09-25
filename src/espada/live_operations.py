from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint, mapping

from .ais import normalize_ais_csv
from .ais_filter import filter_ais_candidates
from .attribution import assess_nomination, write_attribution_outputs
from .coast_download import clip_land_archive, sync_land_mask
from .copernicus import FORECAST_DATASET_ID, normalize_currents
from .environment import load_environment, sync_historical_wind
from .historical_ais import GFW_DELAY_HOURS, HistoricalAISRequest, fetch_gfw_presence
from .live_ais import AISBoundingBox, capture_aisstream
from .live_case_register import build_case_register, verify_case_integrity
from .live_closure import load_case_closure, record_case_disposition as write_case_disposition
from .live_evidence_intake import (
    load_evidence_intake,
    review_evidence_return,
    stage_evidence_return,
)
from .live_reanalysis import load_live_reanalysis, run_live_reanalysis
from .live_retasking import build_live_retasking_plan, load_live_retasking_plan
from .live_response import build_live_response_package
from .sentinel_catalog import SentinelSearchRequest, discover_sentinel1
from .sentinel_process import download_sentinel1_subset
from .sar_physics_gate import evaluate_sar_files
from .slick import analyze_slick, load_slick


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: object) -> str:
    """Return a stable digest for a JSON-compatible evidence record."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _analysis_operator_message(error: Exception) -> str:
    """Return a concise operator message while keeping technical detail on disk."""
    detail = str(error).lower()
    if "oauth" in detail or "cdse_client" in detail or "credential" in detail:
        return "Copernicus Data Space credentials are unavailable or were rejected. Check the local .env and retry."
    if "gpu python" in detail or "pytorch" in detail or "application control" in detail:
        return "The calibrated SAR model runtime is unavailable on this computer. Check the approved ML environment and retry."
    if "download" in detail or "http" in detail or "timed out" in detail or "network" in detail:
        return "The calibrated Sentinel-1 crop could not be retrieved. The selected scene was preserved; retry when the provider is reachable."
    if "identity mismatch" in detail or "provenance" in detail:
        return "Scene identity verification failed. Processing stopped before the result could enter the evidence chain."
    return "Satellite analysis stopped safely. Technical details were preserved in the run record."


def freshness_label(
    observed_at: object,
    *,
    now: datetime | None = None,
    live_seconds: float = 300.0,
    delayed_seconds: float = 3_600.0,
) -> dict[str, object]:
    """Return an honest source-age classification for the operator UI."""
    observed = _parse_utc(observed_at)
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if observed is None:
        return {"label": "NO DATA", "age_seconds": None}
    age = max(0.0, (current.astimezone(UTC) - observed).total_seconds())
    if age <= live_seconds:
        label = "LIVE"
    elif age <= delayed_seconds:
        label = "DELAYED"
    else:
        label = "STALE"
    return {"label": label, "age_seconds": round(age, 1)}


@dataclass(frozen=True)
class LiveRegion:
    name: str
    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float

    def __post_init__(self) -> None:
        AISBoundingBox(*self.bbox)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            self.min_longitude,
            self.min_latitude,
            self.max_longitude,
            self.max_latitude,
        )

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.min_longitude + self.max_longitude) / 2.0,
            (self.min_latitude + self.max_latitude) / 2.0,
        )

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "bbox": list(self.bbox), "center": list(self.center)}


def _region_cache_tag(region: LiveRegion) -> str:
    """Return a stable, filesystem-safe identity for a live watch boundary."""
    return "_".join(
        f"{value:.3f}".replace("-", "m").replace(".", "p") for value in region.bbox
    )


def _map_context_bbox(region: LiveRegion) -> tuple[float, float, float, float]:
    """Return a wider orientation view without widening the operational filter."""
    longitude, latitude = region.center
    if 103.4 <= longitude <= 104.5 and 0.8 <= latitude <= 1.7:
        return (103.55, 0.98, 104.32, 1.52)
    width = region.max_longitude - region.min_longitude
    height = region.max_latitude - region.min_latitude
    return (
        max(-180.0, region.min_longitude - width),
        max(-90.0, region.min_latitude - height),
        min(180.0, region.max_longitude + width),
        min(90.0, region.max_latitude + height),
    )


class LiveOperationsEngine:
    """Collect real provider data and expose a single auditable live snapshot.

    There is intentionally no synthetic fallback. A failed source becomes NO_DATA
    or ERROR in the snapshot rather than producing a plausible-looking animation.
    """

    def __init__(
        self,
        project_root: Path,
        region: LiveRegion,
        *,
        environment_interval_seconds: float = 600.0,
        sentinel_interval_seconds: float = 900.0,
        ais_capture_seconds: float = 55.0,
        ais_snapshot_minutes: float = 10.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.region = region
        self.environment_interval_seconds = max(30.0, float(environment_interval_seconds))
        self.sentinel_interval_seconds = max(60.0, float(sentinel_interval_seconds))
        self.ais_capture_seconds = max(5.0, float(ais_capture_seconds))
        self.ais_snapshot_minutes = max(2.0, float(ais_snapshot_minutes))
        self.output_root = self.project_root / "out" / "live_operations"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.map_context_bbox = _map_context_bbox(region)
        region_cache_tag = _region_cache_tag(region)
        self.environment_cache = (
            self.project_root / "data" / "cache" / f"live_environment_{region_cache_tag}.json"
        )
        self.ais_cache = (
            self.project_root / "data" / "cache" / f"live_operations_ais_{region_cache_tag}.csv"
        )
        self.state_path = self.output_root / "source_state.json"
        self.coast_path = self.output_root / "coast" / "land_mask.geojson"
        self.context_coast_path = self.output_root / "coast" / "context_land_mask.geojson"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state: dict[str, Any] = {
            "status": "STARTING",
            "started_at_utc": None,
            "updated_at_utc": None,
            "region": region.to_dict(),
            "sources": {
                "environment": self._empty_source(
                    "Open-Meteo marine current + forecast wind", "MODEL FORECAST"
                ),
                "ais": self._empty_source("AISStream WebSocket", "LIVE STREAM"),
                "sentinel": self._empty_source(
                    "Copernicus Data Space Sentinel-1 GRD", "SATELLITE CATALOGUE"
                ),
            },
            "analysis": {
                "status": "NOT_RUN",
                "message": "No Sentinel-1 scene has been processed in this live session.",
            },
            "review": {
                "status": "NOT_REVIEWED",
                "message": "A model candidate must be accepted or rejected by an analyst.",
            },
            "attribution": {
                "status": "NOT_RUN",
                "message": "Reverse drift starts only after analyst approval.",
                "candidates": [],
            },
            "response": {
                "status": "NOT_BUILT",
                "message": "The evidence package is created after attribution completes.",
            },
            "retasking": {
                "status": "NOT_BUILT",
                "message": "The follow-up evidence plan is created after a response package exists.",
            },
            "evidence_intake": {
                "status": "NOT_READY",
                "message": "Build a follow-up evidence plan before recording returns.",
                "receipts": [],
            },
            "reanalysis": {
                "status": "NOT_READY",
                "message": "Admit follow-up evidence before starting a versioned reanalysis.",
                "original_attribution_unchanged": True,
            },
            "closure": {
                "status": "NOT_READY",
                "message": "Complete attribution before recording a case disposition.",
                "versions": [],
            },
        }
        self._analysis_thread: threading.Thread | None = None
        self._attribution_thread: threading.Thread | None = None
        self._reanalysis_thread: threading.Thread | None = None
        self._restore_previous_state()
        self._recover_completed_case()
        self._recover_response_package()
        self._recover_retasking_plan()
        self._recover_evidence_intake()
        self._recover_reanalysis()
        self._recover_closure()
        self._prepare_coastline()
        self._write_state()

    def _restore_previous_state(self) -> None:
        """Recover durable outputs while ensuring interrupted jobs are not reported complete."""
        if not self.state_path.exists():
            return
        try:
            previous = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if previous.get("region", {}).get("bbox") != list(self.region.bbox):
            return
        previous_sources = previous.get("sources")
        if isinstance(previous_sources, dict):
            for name in self._state["sources"]:
                if isinstance(previous_sources.get(name), dict):
                    self._state["sources"][name].update(previous_sources[name])
        analysis = previous.get("analysis")
        if isinstance(analysis, dict):
            status = str(analysis.get("status", "NOT_RUN"))
            if status in {"QUEUED", "DOWNLOADING", "INFERENCE", "INTERPRETING"}:
                analysis = {
                    **analysis,
                    "status": "INTERRUPTED",
                    "message": "The previous SAR job was interrupted before a final result was recorded.",
                }
            self._state["analysis"] = analysis
        for section in (
            "review", "attribution", "response", "retasking", "evidence_intake", "reanalysis",
            "closure",
        ):
            saved = previous.get(section)
            if not isinstance(saved, dict):
                continue
            status = str(saved.get("status", "NOT_RUN"))
            if status in {
                "QUEUED",
                "PREPARING_FORCING",
                "REVERSING_DRIFT",
                "FETCHING_AIS",
                "RANKING",
                "BUILDING",
                "VERIFYING_INPUTS",
                "MERGING_EVIDENCE",
                "RERANKING",
            }:
                saved = {
                    **saved,
                    "status": "INTERRUPTED",
                    "message": "The previous evidence job stopped before a final result was recorded.",
                }
            self._state[section] = saved

        restored_analysis = self._state.get("analysis", {})
        restored_review = self._state.get("review", {})
        if (
            restored_analysis.get("status") == "REVIEW_REQUIRED"
            and restored_review.get("status") == "NOT_REVIEWED"
        ):
            physics = restored_analysis.get("physics_screen") or {}
            physics_passed = (
                physics.get("status") == "PLAUSIBLE_DARK_SIGNATURE"
                and physics.get("contrast_gate_passed") is True
                and physics.get("wind_gate_passed") is True
            )
            restored_review["message"] = (
                "Model and physics gates passed. Inspect the pixels, set the release-age assumption, then approve or reject the candidate."
                if physics_passed
                else "The candidate did not pass every physics gate. Approval is locked; reject it or preserve it for further evidence."
            )
        elif restored_analysis.get("status") == "NO_DETECTION":
            self._state["review"] = {
                "status": "NOT_REQUIRED",
                "message": "No candidate exceeded the frozen threshold; no analyst decision is required.",
            }

    def _recover_completed_case(self) -> None:
        """Recover the latest completed on-disk case when session state was reset.

        Generated evidence is durable and may outlive a changed watch-window session. Recovery
        is limited to a case whose analysed SAR crop overlaps the active region and whose full
        review, drift and ranking artifacts are present.
        """
        if str(self._state.get("attribution", {}).get("status")) == "COMPLETE":
            return
        analysis_root = self.output_root / "analysis"
        if not analysis_root.exists():
            return
        candidates = sorted(
            analysis_root.glob("*/attribution/ranking/candidates.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for ranking_path in candidates:
            run_root = ranking_path.parents[2]
            input_status_path = run_root / "input" / "sentinel1_subset_status.json"
            result_path = run_root / "segmentation" / "sar_result.json"
            physics_path = run_root / "segmentation" / "physics_screen.json"
            approved_slick = run_root / "review" / "approved_slick.geojson"
            release_path = run_root / "attribution" / "drift" / "release_estimate.json"
            endpoints_path = run_root / "attribution" / "drift" / "reverse_endpoints.npz"
            forward_replay_path = run_root / "attribution" / "drift" / "forward_replay_particles.npz"
            origin_zone = run_root / "attribution" / "origin_zone.geojson"
            tracks_path = run_root / "attribution" / "candidate_tracks.geojson"
            filter_report_path = run_root / "attribution" / "ais" / "ais_filter_report.json"
            required = (
                input_status_path,
                result_path,
                physics_path,
                approved_slick,
                release_path,
                endpoints_path,
                origin_zone,
                tracks_path,
            )
            if not all(path.exists() for path in required):
                continue
            try:
                input_status = json.loads(input_status_path.read_text(encoding="utf-8"))
                crop = [float(value) for value in input_status.get("bbox", [])]
                region = list(self.region.bbox)
                if len(crop) != 4 or crop[2] < region[0] or crop[0] > region[2] or crop[3] < region[1] or crop[1] > region[3]:
                    continue
                result = json.loads(result_path.read_text(encoding="utf-8"))
                input_acquisition = str(input_status.get("acquisition_time_utc") or "")
                result_acquisition = str(result.get("observation_time_utc") or "")
                if not input_acquisition or input_acquisition != result_acquisition:
                    # Never recover a legacy run whose folder/input/result identities disagree.
                    continue
                physics = json.loads(physics_path.read_text(encoding="utf-8"))
                release = json.loads(release_path.read_text(encoding="utf-8"))
                ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
                filter_report = (
                    json.loads(filter_report_path.read_text(encoding="utf-8"))
                    if filter_report_path.exists()
                    else None
                )
                ranked = list(ranking.get("candidates", []))
                if not ranked:
                    continue
                with np.load(endpoints_path) as endpoints:
                    longitude = np.asarray(endpoints["lon"], dtype=float)
                    latitude = np.asarray(endpoints["lat"], dtype=float)
                if longitude.size == 0 or longitude.shape != latitude.shape:
                    continue
                sample_indices = np.linspace(0, longitude.size - 1, min(320, longitude.size), dtype=int)
                origin_particles = [
                    [float(longitude[index]), float(latitude[index])] for index in sample_indices
                ]
                forward_particles: list[list[float]] = []
                if forward_replay_path.exists():
                    with np.load(forward_replay_path) as replay:
                        replay_lon = np.asarray(replay["lon"], dtype=float)
                        replay_lat = np.asarray(replay["lat"], dtype=float)
                    if replay_lon.size and replay_lon.shape == replay_lat.shape:
                        replay_indices = np.linspace(
                            0, replay_lon.size - 1, min(320, replay_lon.size), dtype=int
                        )
                        forward_particles = [
                            [float(replay_lon[index]), float(replay_lat[index])]
                            for index in replay_indices
                        ]
                slick = load_slick(approved_slick)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            top = ranked[0]
            nomination = ranking.get("nomination_assessment") or assess_nomination(ranked)
            margin = float(nomination.get("score_margin", 0.0))
            decision = str(nomination.get("decision", "ABSTAIN_INSUFFICIENT_EVIDENCE"))
            scene_id = str(input_status.get("scene_id") or run_root.name)
            acquisition = str(input_status.get("acquisition_time_utc") or release.get("observation_time_utc"))
            self._state["analysis"] = {
                "status": str(result.get("status", "REVIEW_REQUIRED")),
                "scene_id": scene_id,
                "acquisition_time_utc": acquisition,
                "bbox": crop,
                "detected_pixel_fraction": float(result.get("detected_pixel_fraction", 0.0)),
                "detected_components": int(result.get("segmentation", {}).get("detected_components", 0)),
                "probability_summary": result.get("segmentation", {}).get("probability_summary", {}),
                "review_status": "approved",
                "input_url": self._public_url(run_root / "input" / "sentinel1_vv_quicklook.png"),
                "overview_url": self._public_url(run_root / "segmentation" / "sar_segmentation_overview.png"),
                "mask_url": self._public_url(run_root / "segmentation" / "slick_mask.png"),
                "result_url": self._public_url(result_path),
                "slick_geojson_url": self._public_url(run_root / "segmentation" / "slick_candidate.geojson"),
                "physics_screen": physics,
                "input_provenance": {
                    "provider": input_status.get("provider"),
                    "polarization": input_status.get("polarization"),
                    "measurement": input_status.get("measurement"),
                    "orthorectified": input_status.get("orthorectified"),
                    "backscatter_coefficient": input_status.get("backscatter_coefficient"),
                    "downloaded_at_utc": input_status.get("downloaded_at_utc"),
                },
                "model_provenance": result.get("segmentation", {}),
                "provenance_verified": True,
                "message": "Recovered a completed, analyst-reviewed SAR case from durable evidence artifacts.",
            }
            self._state["review"] = {
                "status": "APPROVED",
                "scene_id": scene_id,
                "reviewed_at_utc": _format_utc(datetime.fromtimestamp(approved_slick.stat().st_mtime, tz=UTC)),
                "assumed_age_hours": float(release.get("assumed_age_hours", 19.0)),
                "approved_slick_url": self._public_url(approved_slick),
                "message": "Recovered the recorded analyst approval from durable evidence artifacts.",
            }
            forcing = release.get("forcing_provenance", {})
            self._state["attribution"] = {
                "status": "COMPLETE",
                "drift_status": "COMPLETE",
                "message": "Recovered the completed reconstruction and ranking from durable evidence artifacts.",
                "observation_time_utc": acquisition,
                "release_time_utc": release.get("release_time_utc"),
                "assumed_age_hours": float(release.get("assumed_age_hours", 19.0)),
                "origin_zone_url": self._public_url(origin_zone),
                "origin_particles": origin_particles,
                "forward_replay_particles": forward_particles,
                "observed_centroid": [float(slick.polygon.centroid.x), float(slick.polygon.centroid.y)],
                "estimated_origin": release.get("estimated_origin"),
                "credible_radius_90_km": float(release.get("credible_radius_90_km", 0.0)),
                "forward_closure": release.get("forward_closure", {}),
                "reverse_analysis_url": self._public_url(run_root / "attribution" / "drift" / "slick_reverse_analysis.png"),
                "drift_validation_url": (
                    self._public_url(run_root / "attribution" / "drift" / "drift_validation.json")
                    if (run_root / "attribution" / "drift" / "drift_validation.json").exists()
                    else None
                ),
                "forcing_source": forcing.get("source", "Recorded date-matched forcing"),
                "ais_source": "Global Fishing Watch delayed AIS vessel presence",
                "ais_filter": (
                    {
                        **{
                            key: filter_report.get(key)
                            for key in (
                                "status",
                                "method",
                                "release_time_utc",
                                "observation_time_utc",
                                "search_radius_km",
                                "release_window_hours",
                                "raw_vessels",
                                "raw_positions",
                                "release_window_vessels",
                                "origin_zone_vessels",
                                "retained_vessels",
                                "retained_positions",
                                "excluded_vessels",
                                "hard_gates",
                                "context_only",
                                "claim_boundary",
                            )
                        },
                        "retained": list(filter_report.get("retained", []))[:50],
                        "excluded": sorted(
                            list(filter_report.get("excluded", [])),
                            key=lambda item: float(item.get("closest_release_distance_km", 1e9)),
                        )[:80],
                        "report_url": self._public_url(filter_report_path),
                    }
                    if filter_report
                    else None
                ),
                "decision": decision,
                "candidate_count": len(ranked),
                "candidates": ranked[:12],
                "top_candidate": top,
                "score_margin": margin,
                "nomination_assessment": nomination,
                "candidate_tracks_url": self._public_url(tracks_path),
                "ranking_chart_url": self._public_url(run_root / "attribution" / "ranking" / "candidate_ranking.png"),
                "attribution_map_url": self._public_url(run_root / "attribution" / "ranking" / "attribution_map.png"),
                "completed_at_utc": _format_utc(datetime.fromtimestamp(ranking_path.stat().st_mtime, tz=UTC)),
            }
            return

    def _recover_response_package(self) -> None:
        scene_id = str(self._state.get("analysis", {}).get("scene_id") or "").strip()
        if not scene_id:
            return
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        response_dir = self.output_root / "analysis" / safe_scene_id / "response"
        summary_path = response_dir / "response_summary.json"
        manifest_path = response_dir / "evidence_manifest.json"
        bundle_path = response_dir / "evidence_bundle.json"
        dossier_path = response_dir / "evidence_dossier.html"
        if not all(path.exists() for path in (summary_path, manifest_path, bundle_path, dossier_path)):
            return
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._state["response"] = {
            "status": "READY",
            "message": "Recovered the completed evidence response package.",
            "response_status": summary.get("response_status"),
            "operational_decision": summary.get("operational_decision"),
            "permitted_action": summary.get("permitted_action"),
            "rationale": summary.get("rationale"),
            "generated_at_utc": summary.get("generated_at_utc"),
            "verified_files": int(manifest.get("verified_files", 0)),
            "required_files": int(manifest.get("required_files", 0)),
            "missing_required_files": int(manifest.get("missing_required_files", 0)),
            "chain_digest_sha256": manifest.get("chain_digest_sha256"),
            "recommended_actions": list(summary.get("recommended_actions", [])),
            "blocked_actions": list(summary.get("blocked_actions", [])),
            "dossier_url": self._public_url(dossier_path),
            "bundle_url": self._public_url(bundle_path),
            "manifest_url": self._public_url(manifest_path),
            "summary_url": self._public_url(summary_path),
        }

    def _recover_retasking_plan(self) -> None:
        scene_id = str(self._state.get("analysis", {}).get("scene_id") or "").strip()
        if not scene_id:
            return
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        plan = load_live_retasking_plan(run_root)
        if not plan:
            return
        self._state["retasking"] = {
            **plan,
            "status": "READY",
            "message": "Recovered the recorded follow-up evidence request package.",
            "plan_url": self._public_url(run_root / "follow_up/evidence_acquisition_plan.json"),
            "requests_csv_url": self._public_url(run_root / "follow_up/evidence_requests.csv"),
        }

    def _recover_evidence_intake(self) -> None:
        scene_id = str(self._state.get("analysis", {}).get("scene_id") or "").strip()
        if not scene_id:
            return
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        intake = load_evidence_intake(run_root)
        if intake.get("status") == "NOT_READY":
            return
        register_path = run_root / "follow_up/intake/evidence_intake_register.json"
        recovered_intake = {**intake}
        if register_path.is_file():
            recovered_intake["register_url"] = self._public_url(register_path)
        self._state["evidence_intake"] = recovered_intake

    def _reanalysis_payload(self, run_root: Path, result: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in result.items()
            if key not in {
                "result_path",
                "ranking_chart_path",
                "attribution_map_path",
                "merged_ais_path",
            }
        }
        version = str(payload.get("version") or "").strip()
        if payload.get("status") == "COMPLETE" and version:
            version_root = run_root / "reanalysis" / version
            payload.update(
                {
                    "result_url": self._public_url(version_root / "reanalysis_result.json"),
                    "ranking_chart_url": self._public_url(
                        version_root / "ranking/candidate_ranking.png"
                    ),
                    "attribution_map_url": self._public_url(
                        version_root / "ranking/attribution_map.png"
                    ),
                    "merged_ais_url": self._public_url(
                        version_root / "ais/ais_normalized.csv"
                    ),
                    "merge_audit_url": self._public_url(version_root / "ais/merge_audit.json"),
                }
            )
        return payload

    def _recover_reanalysis(self) -> None:
        scene_id = str(self._state.get("analysis", {}).get("scene_id") or "").strip()
        if not scene_id:
            return
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        result = load_live_reanalysis(run_root)
        self._state["reanalysis"] = self._reanalysis_payload(run_root, result)

    def _closure_payload(self, run_root: Path, result: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in result.items()
            if key not in {"event_path", "current_path"}
        }
        current_path = run_root / "closure/current.json"
        if current_path.is_file():
            payload["current_record_url"] = self._public_url(current_path)
        event_path = result.get("event_path")
        if isinstance(event_path, Path) and event_path.is_file():
            payload["recorded_event_url"] = self._public_url(event_path)
        latest_version = str(payload.get("latest_version") or "")
        if latest_version == "BASELINE":
            version_path = run_root / "attribution/ranking/candidates.json"
        elif latest_version:
            version_path = run_root / "reanalysis" / latest_version / "reanalysis_result.json"
        else:
            version_path = Path()
        if latest_version and version_path.is_file():
            payload["latest_version_url"] = self._public_url(version_path)
        return payload

    def _recover_closure(self) -> None:
        scene_id = str(self._state.get("analysis", {}).get("scene_id") or "").strip()
        if not scene_id:
            return
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        self._state["closure"] = self._closure_payload(
            run_root, load_case_closure(run_root)
        )

    @staticmethod
    def _empty_source(provider: str, kind: str) -> dict[str, object]:
        return {
            "status": "WAITING",
            "provider": provider,
            "kind": kind,
            "last_attempt_utc": None,
            "last_success_utc": None,
            "latest_observation_utc": None,
            "message": "Waiting for the first provider response.",
        }

    @property
    def running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def _prepare_coastline(self) -> None:
        def matches(path: Path, bbox: tuple[float, float, float, float]) -> bool:
            if not path.exists():
                return False
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached_bbox = cached.get("properties", {}).get("requested_bbox")
                return bool(
                    isinstance(cached_bbox, list)
                    and len(cached_bbox) == 4
                    and np.allclose(np.asarray(cached_bbox, dtype=float), bbox)
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return False

        targets = (
            (self.coast_path, self.region.bbox),
            (self.context_coast_path, self.map_context_bbox),
        )
        if all(matches(path, bbox) for path, bbox in targets):
            return
        archive = self.project_root / "data" / "cache" / "natural_earth" / "ne_10m_land.zip"
        if not archive.exists() and os.environ.get("ESPADA_AUTO_COASTLINE") == "1":
            try:
                sync_land_mask(
                    self.coast_path,
                    archive,
                    self.region.bbox,
                    padding_degrees=0.15,
                )
            except Exception:
                return
        if not archive.exists():
            return
        for path, bbox in targets:
            if matches(path, bbox):
                continue
            try:
                clip_land_archive(archive, path, bbox, padding_degrees=0.15)
            except Exception:
                # Coastline is context only; provider collection must still start.
                continue

    def _write_state(self) -> None:
        with self._lock:
            self._state["updated_at_utc"] = _format_utc(_utc_now())
            payload = json.dumps(self._state, indent=2, default=str)
            temporary = self.state_path.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.state_path)

    def _update_source(self, name: str, **updates: object) -> None:
        with self._lock:
            source = dict(self._state["sources"][name])
            source.update(updates)
            self._state["sources"][name] = source
        self._write_state()

    def _record_source_failure(self, name: str, error: Exception, *, attempted: str) -> None:
        """Keep the last verified payload when a provider refresh fails transiently."""
        with self._lock:
            source = dict(self._state["sources"][name])
        has_verified_payload = bool(source.get("last_success_utc"))
        detail = f"{type(error).__name__}: {error}"
        self._update_source(
            name,
            status="STALE" if has_verified_payload else "ERROR",
            message=(
                f"Refresh failed; displaying the last verified provider response. {detail}"
                if has_verified_payload
                else detail
            ),
            last_attempt_utc=attempted,
            last_error=detail,
        )

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._state["status"] = "RUNNING"
        self._state["started_at_utc"] = _format_utc(_utc_now())
        self._threads = [
            threading.Thread(target=self._scheduled_worker, name="espada-live-sources", daemon=True),
            threading.Thread(target=self._ais_worker, name="espada-live-ais", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        analysis = self._state.get("analysis", {})
        if analysis.get("status") == "REVIEW_REQUIRED" and not analysis.get("physics_screen"):
            threading.Thread(
                target=self._resume_physics_screen,
                name="espada-live-physics-screen",
                daemon=True,
            ).start()
        self._write_state()

    def _resume_physics_screen(self) -> None:
        try:
            scene_id = str(self._state.get("analysis", {}).get("scene_id") or "")
            if not scene_id:
                return
            safe_scene_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in scene_id)
            run_root = self.output_root / "analysis" / safe_scene_id
            acquired = str(self._state["analysis"].get("acquisition_time_utc"))
            screen = self._build_physics_screen(run_root, acquired)
            self._analysis_update(physics_screen=screen)
        except Exception as error:
            self._analysis_update(
                physics_screen={
                    "status": "ERROR",
                    "message": f"Physics screen failed: {type(error).__name__}: {error}",
                }
            )

    def stop(self) -> None:
        self._stop.set()
        self._refresh.set()
        with self._lock:
            self._state["status"] = "STOPPING"
        self._write_state()

    def request_refresh(self) -> None:
        self._refresh.set()

    def start_latest_sar_analysis(self, scene_id: str | None = None) -> dict[str, object]:
        with self._lock:
            if self._analysis_thread and self._analysis_thread.is_alive():
                return dict(self._state["analysis"])
            catalog = self.output_root / "sentinel" / "sentinel1_catalog.json"
            if not catalog.exists():
                raise RuntimeError("No live Sentinel-1 catalogue result exists yet. Refresh providers first.")
            catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
            available = list(catalog_payload.get("scenes", []))
            requested_scene = None
            if scene_id:
                requested_scene = next(
                    (item for item in available if str(item.get("id")) == str(scene_id)),
                    None,
                )
                if requested_scene is None:
                    raise RuntimeError("The selected Sentinel-1 scene is not in the current catalogue.")
            selected = requested_scene or catalog_payload.get("recommended_scene") or {}
            if not selected or not selected.get("id"):
                raise RuntimeError("The current Sentinel-1 catalogue contains no usable scene.")
            selected_scene = {
                key: selected.get(key)
                for key in (
                    "id",
                    "acquisition_time_utc",
                    "platform",
                    "product_type",
                    "instrument_mode",
                    "orbit_state",
                    "relative_orbit",
                    "polarizations",
                    "has_vv",
                    "aoi_overlap_fraction",
                    "target_point_covered",
                )
            }
            self._state["analysis"] = {
                "status": "QUEUED",
                "message": "Selected Sentinel-1 scene queued for calibrated download and V6 inference.",
                "queued_at_utc": _format_utc(_utc_now()),
                "requested_scene_id": selected.get("id"),
                "scene_id": selected.get("id"),
                "acquisition_time_utc": selected.get("acquisition_time_utc"),
                "selected_scene": selected_scene,
                "catalog_sha256": _sha256_file(catalog),
            }
            self._state["review"] = {
                "status": "NOT_REVIEWED",
                "message": "Waiting for the SAR model result.",
            }
            self._state["attribution"] = {
                "status": "NOT_RUN",
                "message": "Reverse drift starts only after analyst approval.",
                "candidates": [],
            }
            self._state["response"] = {
                "status": "NOT_BUILT",
                "message": "The evidence package is created after attribution completes.",
            }
            self._state["retasking"] = {
                "status": "NOT_BUILT",
                "message": "The follow-up evidence plan is created after a response package exists.",
            }
            self._state["evidence_intake"] = {
                "status": "NOT_READY",
                "message": "A new evidence plan is required before recording returns.",
                "receipts": [],
            }
            self._state["reanalysis"] = {
                "status": "NOT_READY",
                "message": "A new admitted evidence set is required before reanalysis.",
                "original_attribution_unchanged": True,
            }
            self._state["closure"] = {
                "status": "NOT_READY",
                "message": "Complete attribution before recording a case disposition.",
                "versions": [],
            }
            self._analysis_thread = threading.Thread(
                target=self._analysis_worker, name="espada-live-sar", daemon=True
            )
            self._analysis_thread.start()
            result = dict(self._state["analysis"])
        self._write_state()
        return result

    def _analysis_update(self, **updates: object) -> None:
        with self._lock:
            analysis = dict(self._state["analysis"])
            analysis.update(updates)
            self._state["analysis"] = analysis
        self._write_state()

    def _section_update(self, section: str, **updates: object) -> None:
        with self._lock:
            payload = dict(self._state.get(section, {}))
            payload.update(updates)
            self._state[section] = payload
        self._write_state()

    @staticmethod
    def _handoff_artifact_paths(run_root: Path) -> dict[str, Path]:
        return {
            "scene_manifest": run_root / "input" / "selected_scene_manifest.json",
            "satellite_input_record": run_root / "input" / "sentinel1_subset_status.json",
            "model_result": run_root / "segmentation" / "sar_result.json",
            "physics_screen": run_root / "segmentation" / "physics_screen.json",
            "approved_slick": run_root / "review" / "approved_slick.geojson",
        }

    def _seal_incident_handoff(
        self,
        *,
        run_root: Path,
        scene_id: str,
        acquisition_time_utc: str,
        reviewed_at_utc: str,
        assumed_age_hours: float,
    ) -> dict[str, object]:
        observation = _parse_utc(acquisition_time_utc)
        if observation is None:
            raise RuntimeError("The verified satellite acquisition time is missing or invalid.")
        release_time = observation - timedelta(hours=float(assumed_age_hours))
        artifacts: dict[str, dict[str, str]] = {}
        for name, path in self._handoff_artifact_paths(run_root).items():
            if not path.exists():
                raise FileNotFoundError(
                    f"Incident handoff cannot be sealed because {name.replace('_', ' ')} is missing."
                )
            artifacts[name] = {
                "relative_path": path.resolve().relative_to(self.project_root).as_posix(),
                "sha256": _sha256_file(path),
            }
        record: dict[str, object] = {
            "status": "SEALED",
            "schema": "espada.incident-handoff.v1",
            "case_id": run_root.name,
            "scene_id": scene_id,
            "watch_region": self.region.to_dict(),
            "observation_time_utc": _format_utc(observation),
            "assumed_age_hours": float(assumed_age_hours),
            "estimated_release_time_utc": _format_utc(release_time),
            "analyst_reviewed_at_utc": reviewed_at_utc,
            "sealed_at_utc": _format_utc(_utc_now()),
            "artifacts": artifacts,
            "claim_boundary": (
                "The analyst accepted an oil-like SAR candidate for reconstruction. "
                "This is not proof of pollutant identity, vessel responsibility, or guilt."
            ),
        }
        record["record_digest_sha256"] = _sha256_json(record)
        handoff_path = run_root / "review" / "incident_handoff.json"
        temporary = handoff_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary.replace(handoff_path)
        return record

    def _verify_incident_handoff(self, run_root: Path) -> dict[str, object]:
        handoff_path = run_root / "review" / "incident_handoff.json"
        if not handoff_path.exists():
            raise RuntimeError(
                "The incident input contract is missing. Review and seal the slick before reconstruction."
            )
        try:
            record = json.loads(handoff_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("The incident input contract cannot be verified.") from error
        recorded_digest = str(record.get("record_digest_sha256") or "")
        digest_payload = dict(record)
        digest_payload.pop("record_digest_sha256", None)
        if not recorded_digest or _sha256_json(digest_payload) != recorded_digest:
            raise RuntimeError("Incident handoff integrity failed: the sealed record changed.")
        if str(record.get("status")) != "SEALED":
            raise RuntimeError("The incident input contract is not sealed.")
        recorded_artifacts = record.get("artifacts")
        if not isinstance(recorded_artifacts, dict):
            raise RuntimeError("Incident handoff integrity failed: artifact manifest is missing.")
        for name, expected_path in self._handoff_artifact_paths(run_root).items():
            entry = recorded_artifacts.get(name)
            expected_relative = expected_path.resolve().relative_to(self.project_root).as_posix()
            if not isinstance(entry, dict) or entry.get("relative_path") != expected_relative:
                raise RuntimeError(
                    f"Incident handoff integrity failed: {name.replace('_', ' ')} path changed."
                )
            if not expected_path.exists() or entry.get("sha256") != _sha256_file(expected_path):
                raise RuntimeError(
                    f"Incident handoff integrity failed: {name.replace('_', ' ')} changed."
                )
        return record

    def review_candidate(self, decision: str, *, age_hours: float = 19.0) -> dict[str, object]:
        decision = decision.strip().upper()
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("review decision must be APPROVE or REJECT")
        if not 1.0 <= float(age_hours) <= 72.0:
            raise ValueError("assumed slick age must be between 1 and 72 hours")
        with self._lock:
            analysis = dict(self._state.get("analysis", {}))
        if analysis.get("status") != "REVIEW_REQUIRED":
            raise RuntimeError("There is no reviewable SAR candidate in the current run.")
        scene_id = str(analysis.get("scene_id") or "unknown-scene")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        if decision == "REJECT":
            result = {
                "status": "REJECTED",
                "scene_id": scene_id,
                "reviewed_at_utc": _format_utc(_utc_now()),
                "handoff_status": "NOT_CREATED",
                "handoff_verified": False,
                "message": "Analyst rejected the dark feature; attribution is blocked.",
            }
            self._state["attribution"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "No reverse drift or vessel ranking is permitted after rejection.",
                "candidates": [],
            }
            self._state["response"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "No attribution response package is created after rejection.",
            }
            self._state["retasking"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "No follow-up attribution plan is created after slick rejection.",
            }
            self._state["evidence_intake"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "Evidence intake is closed after slick rejection.",
                "receipts": [],
            }
            self._state["reanalysis"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "Reanalysis is blocked after slick rejection.",
                "original_attribution_unchanged": True,
            }
            self._state["closure"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "No attribution result exists to close after slick rejection.",
                "versions": [],
            }
            self._state["review"] = result
            self._write_state()
            return result

        physics = analysis.get("physics_screen") or {}
        physics_passed = (
            str(physics.get("status", "")) == "PLAUSIBLE_DARK_SIGNATURE"
            and physics.get("contrast_gate_passed") is True
            and physics.get("wind_gate_passed") is True
        )
        if not physics_passed:
            raise RuntimeError(
                "Approval is locked because the physics screen did not return a complete plausible result."
            )
        if analysis.get("provenance_verified") is not True:
            raise RuntimeError(
                "Approval is locked because the satellite input and model provenance were not verified."
            )
        acquisition_time = str(analysis.get("acquisition_time_utc") or "")
        if _parse_utc(acquisition_time) is None:
            raise RuntimeError(
                "Approval is locked because the verified satellite acquisition time is unavailable."
            )
        source_path = run_root / "segmentation" / "slick_candidate.geojson"
        if not source_path.exists():
            raise FileNotFoundError("The georeferenced slick candidate is missing.")
        payload = json.loads(source_path.read_text(encoding="utf-8"))

        reviewed_at = _format_utc(_utc_now())

        def approve_feature(value: object) -> None:
            if not isinstance(value, dict):
                return
            if value.get("type") == "Feature":
                properties = dict(value.get("properties") or {})
                properties["review_status"] = "analyst_approved"
                properties["reviewed_at_utc"] = reviewed_at
                properties["assumed_age_hours"] = float(age_hours)
                value["properties"] = properties
            for feature in value.get("features", []):
                approve_feature(feature)

        approve_feature(payload)
        review_dir = run_root / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        approved_path = review_dir / "approved_slick.geojson"
        approved_temporary = approved_path.with_suffix(".geojson.tmp")
        approved_temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        approved_temporary.replace(approved_path)
        handoff = self._seal_incident_handoff(
            run_root=run_root,
            scene_id=scene_id,
            acquisition_time_utc=acquisition_time,
            reviewed_at_utc=reviewed_at,
            assumed_age_hours=float(age_hours),
        )
        handoff_path = review_dir / "incident_handoff.json"
        slick_digest = str(handoff["artifacts"]["approved_slick"]["sha256"])
        result = {
            "status": "APPROVED",
            "scene_id": scene_id,
            "reviewed_at_utc": reviewed_at,
            "assumed_age_hours": float(age_hours),
            "estimated_release_time_utc": handoff["estimated_release_time_utc"],
            "approved_slick_url": self._public_url(approved_path),
            "slick_geometry_sha256": slick_digest,
            "handoff_status": "SEALED",
            "handoff_verified": True,
            "handoff_digest_sha256": handoff["record_digest_sha256"],
            "handoff_url": self._public_url(handoff_path),
            "message": "Analyst approved and sealed the incident input for reconstruction.",
        }
        self._state["review"] = result
        self._state["attribution"] = {
            "status": "READY",
            "message": "Sealed input verified; ready to build date-matched forcing and vessel evidence.",
            "candidates": [],
        }
        self._state["response"] = {
            "status": "NOT_BUILT",
            "message": "The evidence package is created after attribution completes.",
        }
        self._state["retasking"] = {
            "status": "NOT_BUILT",
            "message": "The follow-up evidence plan is created after a response package exists.",
        }
        self._state["evidence_intake"] = {
            "status": "NOT_READY",
            "message": "A new evidence plan is required before recording returns.",
            "receipts": [],
        }
        self._state["reanalysis"] = {
            "status": "NOT_READY",
            "message": "A new admitted evidence set is required before reanalysis.",
            "original_attribution_unchanged": True,
        }
        self._state["closure"] = {
            "status": "NOT_READY",
            "message": "Complete attribution before recording a case disposition.",
            "versions": [],
        }
        self._write_state()
        return result

    def start_attribution(self) -> dict[str, object]:
        with self._lock:
            if self._attribution_thread and self._attribution_thread.is_alive():
                return dict(self._state["attribution"])
            review = dict(self._state.get("review", {}))
            if review.get("status") != "APPROVED":
                raise RuntimeError("Approve the SAR candidate before starting reverse drift.")
            scene_id = str(review.get("scene_id") or "").strip()
            if not scene_id:
                raise RuntimeError("The approved incident has no scene identifier.")
            safe_scene_id = "".join(
                char if char.isalnum() or char in "-_" else "_" for char in scene_id
            )
            run_root = self.output_root / "analysis" / safe_scene_id
            handoff = self._verify_incident_handoff(run_root)
            if handoff.get("scene_id") != scene_id:
                raise RuntimeError("Incident handoff integrity failed: scene identity changed.")
            if handoff.get("record_digest_sha256") != review.get("handoff_digest_sha256"):
                raise RuntimeError("Incident handoff integrity failed: review and seal do not match.")
            self._state["attribution"] = {
                "status": "QUEUED",
                "message": "Sealed incident verified; date-matched forcing and AIS evidence are queued.",
                "queued_at_utc": _format_utc(_utc_now()),
                "handoff_digest_sha256": handoff["record_digest_sha256"],
                "candidates": [],
            }
            self._state["response"] = {
                "status": "NOT_BUILT",
                "message": "The evidence package is created after attribution completes.",
            }
            self._state["retasking"] = {
                "status": "NOT_BUILT",
                "message": "The follow-up evidence plan is created after a response package exists.",
            }
            self._state["evidence_intake"] = {
                "status": "NOT_READY",
                "message": "A new evidence plan is required before recording returns.",
                "receipts": [],
            }
            self._state["reanalysis"] = {
                "status": "NOT_READY",
                "message": "A new admitted evidence set is required before reanalysis.",
                "original_attribution_unchanged": True,
            }
            self._state["closure"] = {
                "status": "NOT_READY",
                "message": "Attribution is running; no case disposition can be recorded yet.",
                "versions": [],
            }
            self._attribution_thread = threading.Thread(
                target=self._attribution_worker,
                name="espada-live-attribution",
                daemon=True,
            )
            self._attribution_thread.start()
            result = dict(self._state["attribution"])
        self._write_state()
        return result

    def build_response_package(self) -> dict[str, object]:
        with self._lock:
            analysis = dict(self._state.get("analysis", {}))
            review = dict(self._state.get("review", {}))
            attribution = dict(self._state.get("attribution", {}))
            sources = json.loads(json.dumps(self._state.get("sources", {}), default=str))
        if attribution.get("status") != "COMPLETE":
            raise RuntimeError("Complete attribution before building the evidence response package.")
        scene_id = str(analysis.get("scene_id") or "").strip()
        if not scene_id:
            raise RuntimeError("The completed case has no scene identifier.")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        if not run_root.exists():
            raise FileNotFoundError("The completed case directory is missing.")
        self._section_update(
            "response",
            status="BUILDING",
            message="Hashing case artifacts and assembling the evidence dossier.",
        )
        self._section_update(
            "retasking",
            status="NOT_BUILT",
            message="Build the evidence response package before planning follow-up acquisition.",
        )
        self._section_update(
            "evidence_intake",
            status="NOT_READY",
            message="Build a follow-up evidence plan before recording returns.",
            receipts=[],
        )
        self._section_update(
            "reanalysis",
            status="NOT_READY",
            message="Admit follow-up evidence before starting a versioned reanalysis.",
            original_attribution_unchanged=True,
        )
        try:
            package = build_live_response_package(
                run_root,
                analysis=analysis,
                review=review,
                attribution=attribution,
                sources=sources,
            )
            result = {
                key: value
                for key, value in package.items()
                if key not in {"dossier_path", "bundle_path", "manifest_path", "summary_path"}
            }
            result.update(
                {
                    "status": "READY",
                    "message": "Evidence package generated from recorded case artifacts.",
                    "dossier_url": self._public_url(package["dossier_path"]),
                    "bundle_url": self._public_url(package["bundle_path"]),
                    "manifest_url": self._public_url(package["manifest_path"]),
                    "summary_url": self._public_url(package["summary_path"]),
                }
            )
            self._state["response"] = result
            self._write_state()
            return dict(result)
        except Exception as error:
            self._section_update(
                "response",
                status="ERROR",
                message=f"{type(error).__name__}: {error}",
            )
            raise

    def build_evidence_plan(self) -> dict[str, object]:
        with self._lock:
            analysis = dict(self._state.get("analysis", {}))
            response = dict(self._state.get("response", {}))
        if response.get("status") != "READY":
            raise RuntimeError("Build the evidence response package before planning follow-up acquisition.")
        scene_id = str(analysis.get("scene_id") or "").strip()
        if not scene_id:
            raise RuntimeError("The completed case has no scene identifier.")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        if not run_root.is_dir():
            raise FileNotFoundError("The completed case directory is missing.")
        self._section_update(
            "retasking",
            status="BUILDING",
            message="Reading failed evidence gates and drafting acquisition scopes.",
        )
        try:
            plan = build_live_retasking_plan(run_root)
            result = {
                key: value
                for key, value in plan.items()
                if key not in {"plan_path", "requests_csv_path"}
            }
            result.update(
                {
                    "status": "READY",
                    "message": "Follow-up evidence request package generated without dispatching it.",
                    "plan_url": self._public_url(plan["plan_path"]),
                    "requests_csv_url": self._public_url(plan["requests_csv_path"]),
                }
            )
            with self._lock:
                self._state["retasking"] = result
                self._state["evidence_intake"] = load_evidence_intake(run_root)
                self._state["reanalysis"] = self._reanalysis_payload(
                    run_root, load_live_reanalysis(run_root)
                )
            self._write_state()
            return dict(result)
        except Exception as error:
            self._section_update(
                "retasking",
                status="ERROR",
                message=f"{type(error).__name__}: {error}",
            )
            raise

    def stage_follow_up_evidence(self, submission: dict[str, Any]) -> dict[str, object]:
        with self._lock:
            analysis = dict(self._state.get("analysis", {}))
            retasking = dict(self._state.get("retasking", {}))
        if retasking.get("status") != "READY":
            raise RuntimeError("Build a follow-up evidence plan before recording a return.")
        scene_id = str(analysis.get("scene_id") or "").strip()
        if not scene_id:
            raise RuntimeError("The completed case has no scene identifier.")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        intake = stage_evidence_return(run_root, submission)
        result = {
            key: value for key, value in intake.items() if key != "register_path"
        }
        result["register_url"] = self._public_url(intake["register_path"])
        with self._lock:
            self._state["evidence_intake"] = result
            self._state["reanalysis"] = self._reanalysis_payload(
                run_root, load_live_reanalysis(run_root)
            )
        self._write_state()
        return dict(result)

    def review_follow_up_evidence(
        self,
        receipt_id: str,
        decision: str,
        analyst_note: str,
    ) -> dict[str, object]:
        with self._lock:
            analysis = dict(self._state.get("analysis", {}))
        scene_id = str(analysis.get("scene_id") or "").strip()
        if not scene_id:
            raise RuntimeError("The completed case has no scene identifier.")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        intake = review_evidence_return(
            run_root,
            receipt_id,
            decision,
            analyst_note,
        )
        result = {
            key: value for key, value in intake.items() if key != "register_path"
        }
        result["register_url"] = self._public_url(intake["register_path"])
        with self._lock:
            self._state["evidence_intake"] = result
            self._state["reanalysis"] = self._reanalysis_payload(
                run_root, load_live_reanalysis(run_root)
            )
        self._write_state()
        return dict(result)

    def start_reanalysis(self) -> dict[str, object]:
        with self._lock:
            if self._reanalysis_thread and self._reanalysis_thread.is_alive():
                return dict(self._state["reanalysis"])
            analysis = dict(self._state.get("analysis", {}))
        scene_id = str(analysis.get("scene_id") or "").strip()
        if not scene_id:
            raise RuntimeError("The completed case has no scene identifier.")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        readiness = load_live_reanalysis(run_root)
        if readiness.get("status") != "READY":
            raise RuntimeError(str(readiness.get("message") or "Reanalysis is not ready."))
        with self._lock:
            self._state["reanalysis"] = {
                **self._reanalysis_payload(run_root, readiness),
                "status": "QUEUED",
                "message": "Verifying admitted payloads before the versioned rerun.",
                "queued_at_utc": _format_utc(_utc_now()),
                "original_attribution_unchanged": True,
            }
            self._reanalysis_thread = threading.Thread(
                target=self._reanalysis_worker,
                args=(run_root,),
                name="espada-live-reanalysis",
                daemon=True,
            )
            self._reanalysis_thread.start()
            result = dict(self._state["reanalysis"])
        self._write_state()
        return result

    def _reanalysis_worker(self, run_root: Path) -> None:
        try:
            self._section_update(
                "reanalysis",
                status="RERANKING",
                message=(
                    "Hash-checking admitted evidence, replacing only its declared AIS coverage "
                    "and rerunning the frozen candidate ranker."
                ),
                original_attribution_unchanged=True,
            )
            result = run_live_reanalysis(run_root)
            payload = self._reanalysis_payload(run_root, result)
            with self._lock:
                self._state["reanalysis"] = {
                    **payload,
                    "status": "COMPLETE",
                    "message": (
                        "Versioned reanalysis completed. The original attribution remains unchanged."
                    ),
                }
                self._state["closure"] = self._closure_payload(
                    run_root, load_case_closure(run_root)
                )
            self._write_state()
        except Exception as error:
            self._section_update(
                "reanalysis",
                status="ERROR",
                decision="ABSTAIN_INSUFFICIENT_EVIDENCE",
                completed_at_utc=_format_utc(_utc_now()),
                message=f"{type(error).__name__}: {error}",
                original_attribution_unchanged=True,
            )

    def record_case_disposition(self, submission: dict[str, Any]) -> dict[str, object]:
        with self._lock:
            analysis = dict(self._state.get("analysis", {}))
        scene_id = str(analysis.get("scene_id") or "").strip()
        if not scene_id:
            raise RuntimeError("The completed case has no scene identifier.")
        safe_scene_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in scene_id
        )
        run_root = self.output_root / "analysis" / safe_scene_id
        result = write_case_disposition(run_root, submission)
        payload = self._closure_payload(run_root, result)
        with self._lock:
            self._state["closure"] = payload
        self._write_state()
        return dict(payload)

    def case_register(self) -> dict[str, object]:
        register = build_case_register(self.output_root / "analysis")
        for case in register.get("cases", []):
            if not isinstance(case, dict):
                continue
            scene_id = str(case.get("scene_id") or "")
            case_root = self.output_root / "analysis" / scene_id
            paths = case.pop("paths", {})
            case["urls"] = {
                name: self._public_url(case_root / relative) if relative else None
                for name, relative in paths.items()
            }
            verification_path = case_root / "response/integrity_verification.json"
            case["urls"]["verification"] = (
                self._public_url(verification_path) if verification_path.is_file() else None
            )
        return register

    def verify_case(self, scene_id: str) -> dict[str, object]:
        result = verify_case_integrity(self.output_root / "analysis", scene_id)
        verification_path = (
            self.output_root
            / "analysis"
            / str(result["scene_id"])
            / "response"
            / "integrity_verification.json"
        )
        result["verification_url"] = self._public_url(verification_path)
        return result

    def _write_origin_zone(
        self, output_path: Path, longitude: np.ndarray, latitude: np.ndarray, properties: dict[str, object]
    ) -> None:
        points = np.column_stack((longitude, latitude))
        polygon = MultiPoint(points).convex_hull
        feature = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": properties, "geometry": mapping(polygon)}
            ],
        }
        output_path.write_text(json.dumps(feature, indent=2), encoding="utf-8")

    def _write_candidate_tracks(
        self, output_path: Path, ais_path: Path, candidates: list[dict[str, object]]
    ) -> None:
        frame = pd.read_csv(ais_path, dtype={"mmsi": str})
        by_mmsi = {str(item.get("mmsi")): item for item in candidates}
        features = []
        for mmsi, track in frame.groupby("mmsi", sort=False):
            ordered = track.sort_values("timestamp_utc")
            coordinates = ordered[["longitude", "latitude"]].astype(float).values.tolist()
            if not coordinates:
                continue
            candidate = by_mmsi.get(str(mmsi), {})
            geometry = (
                {"type": "LineString", "coordinates": coordinates}
                if len(coordinates) > 1
                else {"type": "Point", "coordinates": coordinates[0]}
            )
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "mmsi": str(mmsi),
                        "vessel_name": str(ordered["vessel_name"].iloc[0]),
                        "rank": candidate.get("rank"),
                        "total_score": candidate.get("total_score"),
                    },
                    "geometry": geometry,
                }
            )
        output_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
            encoding="utf-8",
        )

    def _attribution_worker(self) -> None:
        try:
            with self._lock:
                analysis = dict(self._state["analysis"])
                review = dict(self._state["review"])
            scene_id = str(analysis.get("scene_id") or "unknown-scene")
            safe_scene_id = "".join(
                char if char.isalnum() or char in "-_" else "_" for char in scene_id
            )
            run_root = self.output_root / "analysis" / safe_scene_id
            handoff = self._verify_incident_handoff(run_root)
            if handoff.get("record_digest_sha256") != review.get("handoff_digest_sha256"):
                raise RuntimeError("Incident handoff integrity failed before drift execution.")
            evidence_root = run_root / "attribution"
            environment_dir = evidence_root / "environment"
            drift_dir = evidence_root / "drift"
            ais_dir = evidence_root / "ais"
            ranking_dir = evidence_root / "ranking"
            for directory in (environment_dir, drift_dir, ais_dir, ranking_dir):
                directory.mkdir(parents=True, exist_ok=True)
            observation_time = _parse_utc(analysis.get("acquisition_time_utc"))
            if observation_time is None:
                raise ValueError("The SAR acquisition timestamp is invalid.")
            age_hours = float(review.get("assumed_age_hours", 19.0))
            release_time = observation_time - timedelta(hours=age_hours)
            forcing_start = release_time - timedelta(hours=2)
            forcing_end = observation_time + timedelta(hours=2)
            longitude, latitude = self.region.center
            wind_cache = environment_dir / "historical_wind.json"
            current_file = environment_dir / "copernicus_currents.nc"
            environment_cache = environment_dir / "environment.json"
            self._section_update(
                "attribution",
                status="PREPARING_FORCING",
                message="Downloading date-matched Copernicus currents and historical wind.",
                observation_time_utc=_format_utc(observation_time),
                release_time_utc=_format_utc(release_time),
                assumed_age_hours=age_hours,
            )
            sync_historical_wind(
                wind_cache,
                start=forcing_start,
                end=forcing_end,
                latitude=latitude,
                longitude=longitude,
            )
            workspace_root = self.project_root.parent.parent
            cmems_python = workspace_root / "work" / "envs" / "cmems-py" / "Scripts" / "python.exe"
            if not cmems_python.exists():
                raise FileNotFoundError("Copernicus Marine environment is not installed.")
            child_environment = os.environ.copy()
            child_environment["PYTHONPATH"] = str(self.project_root / "src")
            download = subprocess.run(
                [
                    str(cmems_python),
                    "-m",
                    "espada.copernicus",
                    "download",
                    "--start",
                    _format_utc(forcing_start),
                    "--end",
                    _format_utc(forcing_end),
                    "--dataset-id",
                    FORECAST_DATASET_ID,
                    "--min-lon",
                    str(self.region.min_longitude),
                    "--max-lon",
                    str(self.region.max_longitude),
                    "--min-lat",
                    str(self.region.min_latitude),
                    "--max-lat",
                    str(self.region.max_latitude),
                    "--out",
                    str(current_file),
                ],
                cwd=self.project_root,
                env=child_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25 * 60,
                check=False,
            )
            if download.returncode != 0:
                detail = (download.stderr or download.stdout)[-2_000:]
                raise RuntimeError(f"Copernicus current download failed: {detail.strip()}")
            normalize_currents(
                current_file,
                environment_cache,
                wind_cache_path=wind_cache,
                latitude=latitude,
                longitude=longitude,
                dataset_id=FORECAST_DATASET_ID,
            )
            self._section_update(
                "attribution",
                status="REVERSING_DRIFT",
                message="Reversing the approved slick through the date-matched forcing ensemble.",
            )
            approved_slick = run_root / "review" / "approved_slick.geojson"
            drift = analyze_slick(
                approved_slick,
                environment_cache,
                drift_dir,
                age_hours=age_hours,
                particles=1_200,
                ensemble_members=14,
                spatial_current_grid=current_file,
                land_mask=self.coast_path if self.coast_path.exists() else None,
            )
            with np.load(drift_dir / "reverse_endpoints.npz") as endpoints:
                origin_lon = np.asarray(endpoints["lon"], dtype=float)
                origin_lat = np.asarray(endpoints["lat"], dtype=float)
            with np.load(drift_dir / "forward_replay_particles.npz") as replay:
                replay_lon = np.asarray(replay["lon"], dtype=float)
                replay_lat = np.asarray(replay["lat"], dtype=float)
            origin_zone = evidence_root / "origin_zone.geojson"
            self._write_origin_zone(
                origin_zone,
                origin_lon,
                origin_lat,
                {
                    "source": "ESPADA reverse particle ensemble",
                    "release_time_utc": drift["estimated_release_time_utc"],
                    "credible_radius_90_km": drift["credible_radius_90_km"],
                },
            )
            slick_observation = load_slick(approved_slick)
            sample_indices = np.linspace(
                0, len(origin_lon) - 1, min(320, len(origin_lon)), dtype=int
            )
            origin_particles = [
                [float(origin_lon[index]), float(origin_lat[index])]
                for index in sample_indices
            ]
            replay_indices = np.linspace(
                0, len(replay_lon) - 1, min(320, len(replay_lon)), dtype=int
            )
            forward_particles = [
                [float(replay_lon[index]), float(replay_lat[index])]
                for index in replay_indices
            ]
            base_result = {
                "drift_status": "COMPLETE",
                "origin_zone_url": self._public_url(origin_zone),
                "origin_particles": origin_particles,
                "forward_replay_particles": forward_particles,
                "observed_centroid": [
                    float(slick_observation.polygon.centroid.x),
                    float(slick_observation.polygon.centroid.y),
                ],
                "estimated_origin": drift["estimated_origin"],
                "credible_radius_90_km": drift["credible_radius_90_km"],
                "forward_closure": drift["forward_closure"],
                "reverse_analysis_url": self._public_url(drift_dir / "slick_reverse_analysis.png"),
                "drift_validation_url": self._public_url(drift_dir / "drift_validation.json"),
                "forcing_source": drift["environment"]["source"],
            }
            self._section_update(
                "attribution",
                status="FETCHING_AIS",
                message="Collecting AIS tracks that overlap the inferred release window.",
                **base_result,
            )
            ais_path: Path | None = None
            ais_source = ""
            latest_gfw = _utc_now() - timedelta(hours=GFW_DELAY_HOURS)
            if observation_time <= latest_gfw:
                estimate = drift["estimated_origin"]
                radius = float(drift["credible_radius_90_km"])
                padding = max(0.18, min(0.48, radius / 85.0))
                center_lon = float(estimate["longitude"])
                center_lat = float(estimate["latitude"])
                request_box = AISBoundingBox(
                    max(-179.999, center_lon - padding),
                    max(-89.999, center_lat - padding),
                    min(179.999, center_lon + padding),
                    min(89.999, center_lat + padding),
                )
                history = fetch_gfw_presence(
                    HistoricalAISRequest(
                        request_box,
                        release_time - timedelta(hours=2),
                        min(observation_time + timedelta(hours=1), latest_gfw),
                    ),
                    ais_dir,
                )
                if history.get("status") == "PASS":
                    ais_path = Path(str(history["normalized_file"]))
                    ais_source = "Global Fishing Watch delayed AIS vessel presence"
            elif self.ais_cache.exists():
                live = pd.read_csv(self.ais_cache, dtype={"mmsi": str})
                live["timestamp_utc"] = pd.to_datetime(live["timestamp_utc"], utc=True, errors="coerce")
                matched = live[
                    (live["timestamp_utc"] >= release_time - timedelta(hours=2))
                    & (live["timestamp_utc"] <= observation_time + timedelta(hours=2))
                ].copy()
                if matched["mmsi"].nunique() >= 2:
                    matched_raw = ais_dir / "temporally_matched_live_ais.csv"
                    matched.to_csv(matched_raw, index=False)
                    normalized = normalize_ais_csv(
                        matched_raw,
                        ais_dir,
                        normalized_path=ais_dir / "ais_normalized.csv",
                    )
                    ais_path = Path(str(normalized["normalized_file"]))
                    ais_source = "AISStream positions overlapping the release window"
            if ais_path is None:
                self._section_update(
                    "attribution",
                    status="ABSTAIN_NO_MATCHED_AIS",
                    decision="ABSTAIN_INSUFFICIENT_EVIDENCE",
                    message=(
                        "Reverse drift completed, but no AIS tracks overlap the release window. "
                        "The system refuses to rank present-day vessels against an older image."
                    ),
                    required_ais_window={
                        "start_utc": _format_utc(release_time - timedelta(hours=2)),
                        "end_utc": _format_utc(observation_time + timedelta(hours=2)),
                    },
                    candidates=[],
                    completed_at_utc=_format_utc(_utc_now()),
                    **base_result,
                )
                return
            self._section_update(
                "attribution",
                status="FILTERING_AIS",
                message="Removing traffic that cannot overlap the release time and origin search area.",
                ais_source=ais_source,
                **base_result,
            )
            filter_report = filter_ais_candidates(
                ais_path,
                drift_dir / "release_estimate.json",
                ais_dir,
            )
            filter_state = {
                **{
                    key: filter_report.get(key)
                    for key in (
                        "status",
                        "method",
                        "release_time_utc",
                        "observation_time_utc",
                        "search_radius_km",
                        "release_window_hours",
                        "raw_vessels",
                        "raw_positions",
                        "release_window_vessels",
                        "origin_zone_vessels",
                        "retained_vessels",
                        "retained_positions",
                        "excluded_vessels",
                        "hard_gates",
                        "context_only",
                        "claim_boundary",
                    )
                },
                "retained": list(filter_report.get("retained", []))[:50],
                "excluded": sorted(
                    list(filter_report.get("excluded", [])),
                    key=lambda item: float(item.get("closest_release_distance_km", 1e9)),
                )[:80],
                "report_url": self._public_url(ais_dir / "ais_filter_report.json"),
            }
            retained_count = int(filter_report.get("retained_vessels", 0))
            if retained_count == 0:
                self._section_update(
                    "attribution",
                    status="ABSTAIN_NO_RELEVANT_AIS",
                    decision="ABSTAIN_INSUFFICIENT_EVIDENCE",
                    message=(
                        "AIS was available, but no track passed both the incident-time and "
                        "origin-area relevance gates. The system refuses to force a suspect list."
                    ),
                    ais_source=ais_source,
                    ais_filter=filter_state,
                    candidate_count=0,
                    candidates=[],
                    completed_at_utc=_format_utc(_utc_now()),
                    **base_result,
                )
                return
            ais_path = Path(str(filter_report["filtered_file"]))
            self._section_update(
                "attribution",
                status="RANKING",
                message=f"Forward-verifying {retained_count} incident-relevant AIS candidate(s).",
                ais_source=ais_source,
                ais_filter=filter_state,
                **base_result,
            )
            ranking = write_attribution_outputs(
                ranking_dir,
                ais_path,
                drift_dir / "reverse_endpoints.npz",
                drift_dir / "release_estimate.json",
                drift_dir / "forward_particles.npz",
            )
            candidates = list(ranking.get("candidates", []))
            top = candidates[0]
            nomination = ranking.get("nomination_assessment") or assess_nomination(candidates)
            margin = float(nomination.get("score_margin", 0.0))
            decision = str(nomination.get("decision", "ABSTAIN_INSUFFICIENT_EVIDENCE"))
            tracks_path = evidence_root / "candidate_tracks.geojson"
            self._write_candidate_tracks(tracks_path, ais_path, candidates)
            self._section_update(
                "attribution",
                status="COMPLETE",
                decision=decision,
                message=(
                    "A comparative shortlist is ready for analyst investigation."
                    if decision == "LIMITED_SHORTLIST"
                    else "Evidence gates rejected nomination; retain the origin estimate and collect stronger tracks."
                ),
                candidate_count=len(candidates),
                candidates=candidates[:12],
                top_candidate=top,
                score_margin=margin,
                nomination_assessment=nomination,
                ais_source=ais_source,
                ais_filter=filter_state,
                candidate_tracks_url=self._public_url(tracks_path),
                ranking_chart_url=self._public_url(ranking_dir / "candidate_ranking.png"),
                attribution_map_url=self._public_url(ranking_dir / "attribution_map.png"),
                completed_at_utc=_format_utc(_utc_now()),
                **base_result,
            )
            with self._lock:
                self._state["closure"] = self._closure_payload(
                    run_root, load_case_closure(run_root)
                )
            self._write_state()
        except Exception as error:
            self._section_update(
                "attribution",
                status="ERROR",
                decision="ABSTAIN_INSUFFICIENT_EVIDENCE",
                completed_at_utc=_format_utc(_utc_now()),
                message=f"{type(error).__name__}: {error}",
            )

    def _analysis_worker(self) -> None:
        run_root: Path | None = None
        try:
            with self._lock:
                queued_analysis = dict(self._state.get("analysis", {}))
            requested_scene_id = queued_analysis.get("requested_scene_id")
            scene = dict(queued_analysis.get("selected_scene") or {})
            if not scene or str(scene.get("id")) != str(requested_scene_id):
                raise RuntimeError("Queued Sentinel-1 scene provenance is incomplete or inconsistent.")
            scene_id = str(scene.get("id") or "unknown-scene")
            safe_scene_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in scene_id)
            run_root = self.output_root / "analysis" / safe_scene_id
            input_dir = run_root / "input"
            segmentation_dir = run_root / "segmentation"
            input_dir.mkdir(parents=True, exist_ok=True)
            frozen_catalog_path = input_dir / "selected_scene_manifest.json"
            frozen_catalog_path.write_text(
                json.dumps(
                    {
                        "status": "FROZEN_FOR_ANALYSIS",
                        "provider": "Copernicus Data Space Ecosystem STAC",
                        "queued_at_utc": queued_analysis.get("queued_at_utc"),
                        "source_catalog_sha256": queued_analysis.get("catalog_sha256"),
                        "recommended_scene": scene,
                        "scenes": [scene],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            longitude, latitude = self.region.center
            crop_bbox = (
                max(self.region.min_longitude, longitude - 0.22),
                max(self.region.min_latitude, latitude - 0.19),
                min(self.region.max_longitude, longitude + 0.22),
                min(self.region.max_latitude, latitude + 0.19),
            )
            self._analysis_update(
                status="DOWNLOADING",
                scene_id=scene_id,
                acquisition_time_utc=scene.get("acquisition_time_utc"),
                bbox=list(crop_bbox),
                message="Downloading calibrated Sentinel-1 VV pixels for the newest usable watch-point scene.",
            )
            downloaded = download_sentinel1_subset(
                frozen_catalog_path,
                input_dir,
                bbox=crop_bbox,
                scene_id=scene_id,
                width=1024,
                height=896,
            )
            if str(downloaded.get("scene_id")) != scene_id:
                raise RuntimeError(
                    "Sentinel-1 download identity mismatch; attribution was stopped before inference."
                )
            if str(downloaded.get("acquisition_time_utc")) != str(scene.get("acquisition_time_utc")):
                raise RuntimeError(
                    "Sentinel-1 acquisition-time provenance mismatch; processing was stopped."
                )
            checkpoint = self.project_root / "out" / "ml_training_v6" / "sar_segmentation_best.pt"
            calibration = self.project_root / "out" / "ml_calibration_v6" / "threshold_calibration.json"
            for required in (checkpoint, calibration):
                if not required.exists():
                    raise FileNotFoundError(f"Required V6 artifact is missing: {required}")
            gpu_python = os.environ.get("ESPADA_GPU_PYTHON", "").strip()
            if not gpu_python:
                gpu_python = str(Path.home() / "ml" / "Scripts" / "python.exe")
            if not Path(gpu_python).exists():
                raise FileNotFoundError(
                    "GPU Python was not found. Set ESPADA_GPU_PYTHON to the environment containing PyTorch."
                )
            prediction = segmentation_dir / "v6_prediction.npz"
            prediction.parent.mkdir(parents=True, exist_ok=True)
            child_environment = os.environ.copy()
            child_environment["PYTHONPATH"] = str(self.project_root / "src")
            self._analysis_update(
                status="INFERENCE",
                input_url=self._public_url(input_dir / "sentinel1_vv_quicklook.png"),
                bytes=int(downloaded.get("bytes", 0)),
                message="Running calibrated V6 segmentation on the downloaded pixels.",
            )
            inference = subprocess.run(
                [
                    gpu_python,
                    "-m",
                    "espada.ml_predict",
                    "--input",
                    str(input_dir / "sentinel1_vv.tif"),
                    "--checkpoint",
                    str(checkpoint),
                    "--calibration",
                    str(calibration),
                    "--output",
                    str(prediction),
                    "--batch-size",
                    "4",
                ],
                cwd=self.project_root,
                env=child_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45 * 60,
                check=False,
            )
            if inference.returncode != 0:
                detail = (inference.stderr or inference.stdout)[-1_500:]
                raise RuntimeError(f"V6 inference failed: {detail.strip()}")
            self._analysis_update(
                status="INTERPRETING",
                message="Converting model probabilities into a georeferenced review candidate.",
            )
            report = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "espada.cli",
                    "sar",
                    "--input",
                    str(input_dir / "sentinel1_vv.tif"),
                    "--out",
                    str(segmentation_dir),
                    "--observation-time",
                    str(downloaded["acquisition_time_utc"]),
                    "--bbox",
                    *(str(value) for value in crop_bbox),
                    "--prediction-bundle",
                    str(prediction),
                    *(
                        ["--land-mask", str(self.coast_path)]
                        if self.coast_path.exists()
                        else []
                    ),
                ],
                cwd=self.project_root,
                env=child_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20 * 60,
                check=False,
            )
            if report.returncode != 0:
                detail = (report.stderr or report.stdout)[-1_500:]
                raise RuntimeError(f"SAR interpretation failed: {detail.strip()}")
            result_path = segmentation_dir / "sar_result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if str(result.get("observation_time_utc")) != str(downloaded.get("acquisition_time_utc")):
                raise RuntimeError(
                    "SAR result provenance does not match the downloaded acquisition time."
                )
            physics_screen = None
            if result.get("status") == "REVIEW_REQUIRED":
                physics_screen = self._build_physics_screen(
                    run_root, str(downloaded["acquisition_time_utc"])
                )
            final_status = str(result.get("status", "COMPLETE"))
            self._analysis_update(
                status=final_status,
                completed_at_utc=_format_utc(_utc_now()),
                scene_id=scene_id,
                acquisition_time_utc=downloaded["acquisition_time_utc"],
                bbox=list(crop_bbox),
                detected_pixel_fraction=float(result.get("detected_pixel_fraction", 0.0)),
                detected_components=int(result.get("segmentation", {}).get("detected_components", 0)),
                probability_summary=result.get("segmentation", {}).get("probability_summary", {}),
                review_status=result.get("review_status"),
                input_url=self._public_url(input_dir / "sentinel1_vv_quicklook.png"),
                overview_url=self._public_url(segmentation_dir / "sar_segmentation_overview.png"),
                mask_url=self._public_url(segmentation_dir / "slick_mask.png"),
                result_url=self._public_url(result_path),
                slick_geojson_url=(
                    self._public_url(segmentation_dir / "slick_candidate.geojson")
                    if (segmentation_dir / "slick_candidate.geojson").exists()
                    else None
                ),
                physics_screen=physics_screen,
                input_provenance={
                    "provider": downloaded.get("provider"),
                    "polarization": downloaded.get("polarization"),
                    "measurement": downloaded.get("measurement"),
                    "orthorectified": downloaded.get("orthorectified"),
                    "backscatter_coefficient": downloaded.get("backscatter_coefficient"),
                    "downloaded_at_utc": downloaded.get("downloaded_at_utc"),
                    "width": downloaded.get("width"),
                    "height": downloaded.get("height"),
                },
                model_provenance={
                    "model_generation": result.get("segmentation", {}).get("model_generation"),
                    "architecture": result.get("segmentation", {}).get("architecture"),
                    "checkpoint_epoch": result.get("segmentation", {}).get("checkpoint_epoch"),
                    "checkpoint_sha256": result.get("segmentation", {}).get("checkpoint_sha256"),
                    "threshold": result.get("segmentation", {}).get("threshold"),
                    "threshold_source": result.get("segmentation", {}).get("threshold_source"),
                    "test_time_augmentation": result.get("segmentation", {}).get("test_time_augmentation"),
                    "device": result.get("segmentation", {}).get("device"),
                    "gpu": result.get("segmentation", {}).get("gpu"),
                },
                provenance_verified=True,
                message=(
                    "A candidate requires human review before reverse-drift attribution."
                    if result.get("status") == "REVIEW_REQUIRED"
                    else "No oil candidate exceeded the frozen V6 decision threshold in this crop."
                    if result.get("status") == "NO_DETECTION"
                    else "SAR processing completed."
                ),
            )
            if final_status == "REVIEW_REQUIRED":
                physics_passed = bool(
                    physics_screen
                    and physics_screen.get("status") == "PLAUSIBLE_DARK_SIGNATURE"
                    and physics_screen.get("contrast_gate_passed") is True
                    and physics_screen.get("wind_gate_passed") is True
                )
                self._section_update(
                    "review",
                    status="NOT_REVIEWED",
                    message=(
                        "Model and physics gates passed. Inspect the pixels, set the release-age assumption, then approve or reject the candidate."
                        if physics_passed
                        else "The candidate did not pass every physics gate. Approval is locked; reject it or preserve it for further evidence."
                    ),
                )
            elif final_status == "NO_DETECTION":
                self._section_update(
                    "review",
                    status="NOT_REQUIRED",
                    message="No candidate exceeded the frozen threshold; no analyst decision is required.",
                )
        except Exception as error:
            error_url = None
            if run_root is not None:
                error_path = run_root / "analysis_error.json"
                error_path.parent.mkdir(parents=True, exist_ok=True)
                error_path.write_text(
                    json.dumps(
                        {
                            "status": "ERROR",
                            "recorded_at_utc": _format_utc(_utc_now()),
                            "error_type": type(error).__name__,
                            "technical_detail": str(error),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                error_url = self._public_url(error_path)
            self._analysis_update(
                status="ERROR",
                completed_at_utc=_format_utc(_utc_now()),
                error_code=type(error).__name__,
                error_record_url=error_url,
                technical_error_recorded=bool(error_url),
                message=_analysis_operator_message(error),
            )
            self._section_update(
                "review",
                status="BLOCKED_BY_ANALYSIS",
                message="Analyst review is unavailable because satellite analysis did not complete.",
            )

    def _public_url(self, path: Path) -> str:
        resolved = Path(path).resolve()
        return "/" + resolved.relative_to(self.project_root).as_posix()

    def _build_physics_screen(self, run_root: Path, acquisition_time_utc: str) -> dict[str, object]:
        acquired = _parse_utc(acquisition_time_utc)
        if acquired is None:
            raise ValueError("Acquisition time is missing or invalid")
        longitude, latitude = self.region.center
        wind_path = run_root / "environment" / "historical_wind.json"
        sync_historical_wind(
            wind_path,
            start=acquired - timedelta(hours=6),
            end=acquired + timedelta(hours=6),
            latitude=latitude,
            longitude=longitude,
        )
        wind_payload = json.loads(wind_path.read_text(encoding="utf-8"))
        rows = list(wind_payload.get("samples", []))
        if not rows:
            raise ValueError("Historical wind response contained no samples")
        nearest = min(
            rows,
            key=lambda row: abs(
                ((_parse_utc(row.get("time_utc")) or acquired) - acquired).total_seconds()
            ),
        )
        wind_speed = float(
            np.hypot(float(nearest["wind_east_ms"]), float(nearest["wind_north_ms"]))
        )
        screen = evaluate_sar_files(
            run_root / "input" / "sentinel1_vv.tif",
            run_root / "segmentation" / "slick_mask.png",
            run_root / "segmentation" / "physics_screen.json",
            wind_speed_ms=wind_speed,
        )
        screen["wind_time_utc"] = nearest.get("time_utc")
        screen["wind_source"] = wind_payload.get("source")
        screen["report_url"] = self._public_url(
            run_root / "segmentation" / "physics_screen.json"
        )
        return screen

    def _scheduled_worker(self) -> None:
        next_environment = 0.0
        next_sentinel = 0.0
        while not self._stop.is_set():
            monotonic = time.monotonic()
            forced = self._refresh.is_set()
            if forced:
                self._refresh.clear()
            if forced or monotonic >= next_environment:
                self.refresh_environment()
                next_environment = time.monotonic() + self.environment_interval_seconds
            if forced or monotonic >= next_sentinel:
                self.refresh_sentinel()
                next_sentinel = time.monotonic() + self.sentinel_interval_seconds
            self._stop.wait(1.0)

    def _ais_worker(self) -> None:
        if not os.environ.get("AISSTREAM_API_KEY", "").strip():
            self._update_source(
                "ais",
                status="NOT_CONFIGURED",
                last_attempt_utc=_format_utc(_utc_now()),
                message="AISSTREAM_API_KEY is not loaded. No vessel positions will be invented.",
            )
            return
        bounding_box = AISBoundingBox(*self.region.bbox)
        while not self._stop.is_set():
            attempted = _format_utc(_utc_now())
            with self._lock:
                has_verified_capture = bool(
                    self._state.get("sources", {}).get("ais", {}).get("last_success_utc")
                )
            self._update_source(
                "ais",
                status="PASS" if has_verified_capture else "CONNECTING",
                last_attempt_utc=attempted,
                message=(
                    "Live stream active; collecting the next provider window."
                    if has_verified_capture
                    else "Opening AIS stream."
                ),
            )
            try:
                status = asyncio.run(
                    capture_aisstream(
                        bounding_box,
                        self.output_root / "ais",
                        self.ais_cache,
                        duration_seconds=self.ais_capture_seconds,
                        window_hours=self.ais_snapshot_minutes / 60.0,
                    )
                )
                succeeded = _format_utc(_utc_now())
                accepted = int(status.get("positions_accepted", 0))
                if accepted:
                    latest = self._latest_ais_time()
                    self._update_source(
                        "ais",
                        status="PASS",
                        last_success_utc=succeeded,
                        latest_observation_utc=latest,
                        positions_accepted=accepted,
                        cached_positions=int(status.get("cached_positions", 0)),
                        cached_vessels=int(status.get("cached_vessels", 0)),
                        message=f"Accepted {accepted} live positions in the latest capture window.",
                        warnings=status.get("warnings", []),
                    )
                elif status.get("status") == "ERROR":
                    error_kind = str(status.get("error_kind") or "CONNECTION_FAILED")
                    warnings = list(status.get("warnings", []))
                    rate_limited = error_kind == "RATE_LIMITED"
                    self._update_source(
                        "ais",
                        status="STALE" if has_verified_capture else "ERROR",
                        last_attempt_utc=attempted,
                        latest_observation_utc=self._latest_ais_time(),
                        positions_accepted=0,
                        cached_positions=int(status.get("cached_positions", 0)),
                        cached_vessels=int(status.get("cached_vessels", 0)),
                        error_kind=error_kind,
                        message=(
                            "AISStream rate-limited this connection. Use one running ESPADA service per API key; "
                            "the system will retry with a safe backoff."
                            if rate_limited
                            else "AISStream could not establish a verified provider connection."
                        ),
                        warnings=warnings,
                    )
                    self._stop.wait(180.0 if rate_limited else 30.0)
                else:
                    self._update_source(
                        "ais",
                        status="NO_DATA",
                        last_success_utc=succeeded,
                        latest_observation_utc=self._latest_ais_time(),
                        positions_accepted=0,
                        cached_positions=int(status.get("cached_positions", 0)),
                        cached_vessels=int(status.get("cached_vessels", 0)),
                        message="Provider connection succeeded but supplied no positions for this region.",
                        warnings=status.get("warnings", []),
                    )
            except Exception as error:
                self._update_source(
                    "ais",
                    status="ERROR",
                    message=f"{type(error).__name__}: {error}",
                    last_attempt_utc=attempted,
                )
                self._stop.wait(15.0)

    def _latest_ais_time(self) -> str | None:
        if not self.ais_cache.exists() or self.ais_cache.stat().st_size < 10:
            return None
        try:
            frame = pd.read_csv(self.ais_cache, usecols=["timestamp_utc"])
            times = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce").dropna()
            return times.max().strftime("%Y-%m-%dT%H:%M:%SZ") if not times.empty else None
        except (OSError, ValueError, pd.errors.ParserError):
            return None

    def refresh_environment(self) -> None:
        attempted = _format_utc(_utc_now())
        self._update_source(
            "environment",
            status="REFRESHING",
            last_attempt_utc=attempted,
            message="Requesting current environmental model fields.",
        )
        longitude, latitude = self.region.center
        try:
            bundle = load_environment(
                "live", self.environment_cache, latitude=latitude, longitude=longitude
            )
            frame = bundle.frame.copy()
            frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
            now = pd.Timestamp.now(tz="UTC")
            current_index = (frame["time_utc"] - now).abs().idxmin()
            current = frame.loc[current_index]
            samples = []
            for _, row in frame.iloc[: min(48, len(frame))].iterrows():
                samples.append(
                    {
                        "time_utc": row["time_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "current_east_ms": round(float(row["current_east_ms"]), 5),
                        "current_north_ms": round(float(row["current_north_ms"]), 5),
                        "wind_east_ms": round(float(row["wind_east_ms"]), 5),
                        "wind_north_ms": round(float(row["wind_north_ms"]), 5),
                    }
                )
            current_vector = {
                "time_utc": current["time_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "current_east_ms": round(float(current["current_east_ms"]), 5),
                "current_north_ms": round(float(current["current_north_ms"]), 5),
                "current_speed_ms": round(
                    float(np.hypot(current["current_east_ms"], current["current_north_ms"])), 5
                ),
                "wind_east_ms": round(float(current["wind_east_ms"]), 5),
                "wind_north_ms": round(float(current["wind_north_ms"]), 5),
                "wind_speed_ms": round(
                    float(np.hypot(current["wind_east_ms"], current["wind_north_ms"])), 5
                ),
            }
            succeeded = _format_utc(_utc_now())
            self._update_source(
                "environment",
                status="PASS",
                last_success_utc=succeeded,
                latest_observation_utc=current_vector["time_utc"],
                source=bundle.source,
                temporal_resolution=bundle.temporal_resolution,
                location={"longitude": longitude, "latitude": latitude},
                current=current_vector,
                samples=samples,
                message="Fresh provider response received; vectors are model fields, not observations.",
            )
        except Exception as error:
            self._record_source_failure("environment", error, attempted=attempted)

    def refresh_sentinel(self) -> None:
        attempted_at = _utc_now()
        attempted = _format_utc(attempted_at)
        self._update_source(
            "sentinel",
            status="SEARCHING",
            last_attempt_utc=attempted,
            message="Searching the official Sentinel-1 GRD catalogue.",
        )
        longitude, latitude = self.region.center
        request = SentinelSearchRequest(
            bbox=self.region.bbox,
            start=attempted_at - timedelta(days=14),
            end=attempted_at,
            target=(longitude, latitude),
            limit=100,
        )
        try:
            result = discover_sentinel1(
                request,
                self.output_root / "sentinel",
                download_preview=False,
            )
            scenes = list(result.get("scenes", []))
            latest = max(scenes, key=lambda item: str(item.get("acquisition_time_utc", ""))) if scenes else None
            succeeded = _format_utc(_utc_now())
            self._update_source(
                "sentinel",
                status=result.get("status", "NO_DATA"),
                last_success_utc=succeeded,
                latest_observation_utc=(latest or {}).get("acquisition_time_utc"),
                scenes_returned=len(scenes),
                latest_scene=latest,
                scenes=scenes[:20],
                footprints_url="/out/live_operations/sentinel/sentinel1_footprints.geojson",
                message=(
                    "Catalogue search returned Sentinel-1 acquisitions."
                    if scenes
                    else "No Sentinel-1 acquisition intersected this region in the search window."
                ),
                warnings=result.get("warnings", []),
            )
        except Exception as error:
            self._record_source_failure("sentinel", error, attempted=attempted)

    def _ais_payload(self) -> dict[str, object]:
        empty = {
            "positions": [],
            "tracks": {},
            "position_count": 0,
            "vessel_count": 0,
            "underway_count": 0,
            "stationary_count": 0,
            "unknown_motion_count": 0,
            "window_minutes": self.ais_snapshot_minutes,
        }
        if not self.ais_cache.exists() or self.ais_cache.stat().st_size < 10:
            return empty
        try:
            frame = pd.read_csv(self.ais_cache, dtype={"mmsi": str})
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            return empty
        required = {"timestamp_utc", "mmsi", "longitude", "latitude"}
        if frame.empty or not required.issubset(frame.columns):
            return empty
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
        for column in ("longitude", "latitude", "sog", "cog"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["timestamp_utc", "mmsi", "longitude", "latitude"])
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=self.ais_snapshot_minutes)
        frame = frame.loc[frame["timestamp_utc"] >= cutoff].sort_values("timestamp_utc")
        min_longitude, min_latitude, max_longitude, max_latitude = self.region.bbox
        frame = frame.loc[
            frame["longitude"].between(min_longitude, max_longitude, inclusive="both")
            & frame["latitude"].between(min_latitude, max_latitude, inclusive="both")
        ]
        if frame.empty:
            return empty
        latest = frame.groupby("mmsi", as_index=False).tail(1).tail(500)
        positions = []
        for _, row in latest.iterrows():
            speed = None if pd.isna(row.get("sog")) else float(row.get("sog"))
            motion_state = "unknown" if speed is None else "underway" if speed > 1.0 else "stationary"
            positions.append(
                {
                    "timestamp_utc": row["timestamp_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "mmsi": str(row["mmsi"]),
                    "vessel_name": str(row.get("vessel_name") or "UNKNOWN").strip(),
                    "longitude": float(row["longitude"]),
                    "latitude": float(row["latitude"]),
                    "sog": speed,
                    "cog": None if pd.isna(row.get("cog")) else float(row.get("cog")),
                    "motion_state": motion_state,
                    "source": str(row.get("source") or "AISStream live"),
                }
            )
        tracks: dict[str, list[list[object]]] = {}
        for mmsi, group in frame.groupby("mmsi"):
            thinned = group.iloc[:: max(1, len(group) // 80)]
            tracks[str(mmsi)] = [
                [
                    float(row["longitude"]),
                    float(row["latitude"]),
                    row["timestamp_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                ]
                for _, row in thinned.iterrows()
            ]
        return {
            "positions": positions,
            "tracks": tracks,
            "position_count": len(frame),
            "vessel_count": len(positions),
            "underway_count": sum(item["motion_state"] == "underway" for item in positions),
            "stationary_count": sum(item["motion_state"] == "stationary" for item in positions),
            "unknown_motion_count": sum(item["motion_state"] == "unknown" for item in positions),
            "window_minutes": self.ais_snapshot_minutes,
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            state = json.loads(json.dumps(self._state, default=str))
        now = _utc_now()
        for key, source in state["sources"].items():
            if key == "sentinel":
                source["freshness"] = freshness_label(
                    source.get("latest_observation_utc"),
                    now=now,
                    live_seconds=36 * 3_600,
                    delayed_seconds=10 * 86_400,
                )
            elif key == "environment":
                source["freshness"] = freshness_label(
                    source.get("last_success_utc"),
                    now=now,
                    live_seconds=1_800,
                    delayed_seconds=7_200,
                )
            else:
                source["freshness"] = freshness_label(
                    source.get("latest_observation_utc"), now=now
                )
        ais = self._ais_payload()
        pipeline = self._pipeline_status(state, ais)
        return {
            "status": state["status"],
            "generated_at_utc": _format_utc(now),
            "region": self.region.to_dict(),
            "sources": state["sources"],
            "ais": ais,
            "coastline_url": (
                "/out/live_operations/coast/land_mask.geojson" if self.coast_path.exists() else None
            ),
            "map_context": {
                "name": "Singapore Strait overview"
                if "singapore" in self.region.name.lower()
                else f"{self.region.name} context",
                "bbox": list(self.map_context_bbox),
                "coastline_url": (
                    "/out/live_operations/coast/context_land_mask.geojson"
                    if self.context_coast_path.exists()
                    else None
                ),
            },
            "pipeline": pipeline,
            "analysis": state.get("analysis", {"status": "NOT_RUN"}),
            "review": state.get("review", {"status": "NOT_REVIEWED"}),
            "attribution": state.get("attribution", {"status": "NOT_RUN", "candidates": []}),
            "response": state.get("response", {"status": "NOT_BUILT"}),
            "retasking": state.get("retasking", {"status": "NOT_BUILT", "tasks": []}),
            "evidence_intake": state.get(
                "evidence_intake", {"status": "NOT_READY", "receipts": []}
            ),
            "reanalysis": state.get(
                "reanalysis",
                {
                    "status": "NOT_READY",
                    "original_attribution_unchanged": True,
                },
            ),
            "closure": state.get(
                "closure",
                {"status": "NOT_READY", "versions": []},
            ),
            "case_register": self.case_register(),
            "truth_policy": {
                "synthetic_fallback": False,
                "empty_feed_behavior": "show NO DATA and render no objects",
                "candidate_scores": "investigative ranking, never guilt probability",
            },
        }

    @staticmethod
    def _pipeline_status(state: dict[str, Any], ais: dict[str, object]) -> dict[str, object]:
        environment = state["sources"]["environment"]
        sentinel = state["sources"]["sentinel"]
        environment_ready = (
            environment.get("status") in {"PASS", "STALE"}
            and bool(environment.get("last_success_utc"))
            and bool(environment.get("current"))
        )
        sentinel_ready = (
            sentinel.get("status") in {"PASS", "PARTIAL", "STALE"}
            and bool(sentinel.get("last_success_utc"))
            and bool(sentinel.get("scenes"))
        )
        vessels_ready = int(ais.get("vessel_count", 0)) > 0
        analysis_status = str(state.get("analysis", {}).get("status", "NOT_RUN"))
        analysis_running = analysis_status in {
            "QUEUED",
            "DOWNLOADING",
            "INFERENCE",
            "INTERPRETING",
        }
        analysis_complete = analysis_status in {"PASS", "REVIEW_REQUIRED", "NO_DETECTION"}
        review_status = str(state.get("review", {}).get("status", "NOT_REVIEWED"))
        handoff_ready = (
            state.get("review", {}).get("handoff_status") == "SEALED"
            and state.get("review", {}).get("handoff_verified") is True
        )
        attribution_status = str(state.get("attribution", {}).get("status", "NOT_RUN"))
        response_status = str(state.get("response", {}).get("status", "NOT_BUILT"))
        retasking_status = str(state.get("retasking", {}).get("status", "NOT_BUILT"))
        intake_status = str(state.get("evidence_intake", {}).get("status", "NOT_READY"))
        reanalysis_status = str(state.get("reanalysis", {}).get("status", "NOT_READY"))
        closure_status = str(state.get("closure", {}).get("status", "NOT_READY"))
        case_state = str(state.get("closure", {}).get("case_state", ""))
        attribution_running = attribution_status in {
            "QUEUED",
            "PREPARING_FORCING",
            "REVERSING_DRIFT",
            "FETCHING_AIS",
            "RANKING",
        }
        if closure_status == "RECORDED" and case_state == "CLOSED_INCONCLUSIVE":
            stage = "CASE_CLOSED_INCONCLUSIVE"
        elif closure_status == "RECORDED" and case_state == "REFERRED_FOR_REVIEW":
            stage = "CASE_REFERRED_FOR_REVIEW"
        elif closure_status == "RECORDED" and case_state == "OPEN":
            stage = "CASE_OPEN_EVIDENCE_COLLECTION"
        elif closure_status == "SUPERSEDED":
            stage = "CASE_DISPOSITION_REVIEW_REQUIRED"
        elif reanalysis_status == "COMPLETE":
            stage = "VERSIONED_REANALYSIS_COMPLETE"
        elif reanalysis_status in {"QUEUED", "VERIFYING_INPUTS", "MERGING_EVIDENCE", "RERANKING"}:
            stage = "VERSIONED_REANALYSIS_RUNNING"
        elif intake_status == "REANALYSIS_READY":
            stage = "FOLLOW_UP_EVIDENCE_ADMITTED"
        elif intake_status == "RETURNS_RECORDED":
            stage = "FOLLOW_UP_EVIDENCE_REVIEW"
        elif retasking_status == "READY":
            stage = "FOLLOW_UP_EVIDENCE_PLAN_READY"
        elif retasking_status == "BUILDING":
            stage = "FOLLOW_UP_EVIDENCE_PLAN_BUILDING"
        elif response_status == "READY":
            stage = "EVIDENCE_PACKAGE_READY"
        elif response_status == "BUILDING":
            stage = "EVIDENCE_PACKAGE_BUILDING"
        elif attribution_running:
            stage = f"EVIDENCE_{attribution_status}"
        elif attribution_status == "COMPLETE":
            stage = "EVIDENCE_SHORTLIST_READY"
        elif attribution_status.startswith("ABSTAIN"):
            stage = "EVIDENCE_SAFE_ABSTENTION"
        elif review_status == "REJECTED":
            stage = "ANALYST_REJECTED_SLICK"
        elif review_status == "APPROVED" and handoff_ready:
            stage = "INCIDENT_INPUT_SEALED"
        elif review_status == "APPROVED":
            stage = "INCIDENT_HANDOFF_REQUIRED"
        elif analysis_running:
            stage = f"SAR_{analysis_status}"
        elif analysis_status == "REVIEW_REQUIRED":
            stage = "SAR_REVIEW_REQUIRED"
        elif analysis_status == "NO_DETECTION":
            stage = "MONITORING_NO_DETECTION"
        elif not sentinel_ready:
            stage = "WAITING_FOR_SATELLITE_ACQUISITION"
        elif not environment_ready:
            stage = "WAITING_FOR_ENVIRONMENT"
        elif not vessels_ready:
            stage = "MONITORING_NO_LIVE_AIS"
        else:
            stage = "MONITORING_READY"
        return {
            "stage": stage,
            "environment_ready": environment_ready,
            "sentinel_catalog_ready": sentinel_ready,
            "live_ais_ready": vessels_ready,
            "slick_detection_ready": analysis_complete,
            "analyst_review_ready": review_status == "APPROVED",
            "incident_handoff_ready": handoff_ready,
            "attribution_ready": attribution_status in {"COMPLETE", "ABSTAIN_NO_MATCHED_AIS"},
            "response_ready": response_status == "READY",
            "retasking_ready": retasking_status == "READY",
            "evidence_intake_ready": intake_status in {
                "RETURNS_RECORDED",
                "REANALYSIS_READY",
            },
            "reanalysis_eligible": intake_status == "REANALYSIS_READY",
            "reanalysis_ready": reanalysis_status in {"READY", "COMPLETE"},
            "reanalysis_complete": reanalysis_status == "COMPLETE",
            "closure_ready": closure_status in {"READY", "SUPERSEDED", "RECORDED"},
            "closure_recorded": closure_status == "RECORDED",
            "message": (
                "No oil candidate is displayed until a calibrated scene is processed and approved."
            ),
        }
