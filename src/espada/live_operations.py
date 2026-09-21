from __future__ import annotations

import asyncio
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
from .attribution import write_attribution_outputs
from .coast_download import clip_land_archive
from .copernicus import FORECAST_DATASET_ID, normalize_currents
from .environment import load_environment, sync_historical_wind
from .historical_ais import GFW_DELAY_HOURS, HistoricalAISRequest, fetch_gfw_presence
from .live_ais import AISBoundingBox, capture_aisstream
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
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.region = region
        self.environment_interval_seconds = max(30.0, float(environment_interval_seconds))
        self.sentinel_interval_seconds = max(60.0, float(sentinel_interval_seconds))
        self.ais_capture_seconds = max(5.0, float(ais_capture_seconds))
        self.output_root = self.project_root / "out" / "live_operations"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.environment_cache = self.project_root / "data" / "cache" / "live_environment.json"
        self.ais_cache = self.project_root / "data" / "cache" / "live_operations_ais.csv"
        self.state_path = self.output_root / "source_state.json"
        self.coast_path = self.output_root / "coast" / "land_mask.geojson"
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
        }
        self._analysis_thread: threading.Thread | None = None
        self._attribution_thread: threading.Thread | None = None
        self._restore_previous_state()
        self._recover_completed_case()
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
        for section in ("review", "attribution"):
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
            }:
                saved = {
                    **saved,
                    "status": "INTERRUPTED",
                    "message": "The previous evidence job stopped before a final result was recorded.",
                }
            self._state[section] = saved

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
            origin_zone = run_root / "attribution" / "origin_zone.geojson"
            tracks_path = run_root / "attribution" / "candidate_tracks.geojson"
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
                physics = json.loads(physics_path.read_text(encoding="utf-8"))
                release = json.loads(release_path.read_text(encoding="utf-8"))
                ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
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
                slick = load_slick(approved_slick)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            top = ranked[0]
            runner_score = float(ranked[1]["total_score"]) if len(ranked) > 1 else 0.0
            margin = float(top["total_score"]) - runner_score
            limited = (
                len(ranked) >= 2
                and float(top["total_score"]) >= 0.40
                and margin >= 0.05
                and float(top.get("data_quality", 0.0)) >= 0.40
                and float(top.get("forward_error_km", 999.0)) <= 8.0
            )
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
                "message": "Recovered the completed reconstruction and ranking from durable evidence artifacts.",
                "observation_time_utc": acquisition,
                "release_time_utc": release.get("release_time_utc"),
                "assumed_age_hours": float(release.get("assumed_age_hours", 19.0)),
                "origin_zone_url": self._public_url(origin_zone),
                "origin_particles": origin_particles,
                "observed_centroid": [float(slick.polygon.centroid.x), float(slick.polygon.centroid.y)],
                "estimated_origin": release.get("estimated_origin"),
                "credible_radius_90_km": float(release.get("credible_radius_90_km", 0.0)),
                "reverse_analysis_url": self._public_url(run_root / "attribution" / "drift" / "slick_reverse_analysis.png"),
                "forcing_source": forcing.get("source", "Recorded date-matched forcing"),
                "ais_source": "Global Fishing Watch delayed AIS vessel presence",
                "decision": "LIMITED_SHORTLIST" if limited else "ABSTAIN_INSUFFICIENT_EVIDENCE",
                "candidate_count": len(ranked),
                "candidates": ranked[:12],
                "top_candidate": top,
                "score_margin": margin,
                "candidate_tracks_url": self._public_url(tracks_path),
                "ranking_chart_url": self._public_url(run_root / "attribution" / "ranking" / "candidate_ranking.png"),
                "attribution_map_url": self._public_url(run_root / "attribution" / "ranking" / "attribution_map.png"),
                "completed_at_utc": _format_utc(datetime.fromtimestamp(ranking_path.stat().st_mtime, tz=UTC)),
            }
            return

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
        if self.coast_path.exists():
            try:
                cached = json.loads(self.coast_path.read_text(encoding="utf-8"))
                cached_bbox = cached.get("properties", {}).get("requested_bbox")
                if (
                    isinstance(cached_bbox, list)
                    and len(cached_bbox) == 4
                    and np.allclose(np.asarray(cached_bbox, dtype=float), self.region.bbox)
                ):
                    return
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        archive = self.project_root / "data" / "cache" / "natural_earth" / "ne_10m_land.zip"
        if not archive.exists():
            return
        try:
            clip_land_archive(archive, self.coast_path, self.region.bbox, padding_degrees=0.15)
        except Exception:
            # Coastline is a contextual layer; source collection must continue without it.
            return

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
            self._state["analysis"] = {
                "status": "QUEUED",
                "message": "Selected Sentinel-1 scene queued for calibrated download and V6 inference.",
                "queued_at_utc": _format_utc(_utc_now()),
                "requested_scene_id": selected.get("id"),
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
                "message": "Analyst rejected the dark feature; attribution is blocked.",
            }
            self._state["attribution"] = {
                "status": "BLOCKED_BY_REVIEW",
                "message": "No reverse drift or vessel ranking is permitted after rejection.",
                "candidates": [],
            }
            self._state["review"] = result
            self._write_state()
            return result

        physics = analysis.get("physics_screen") or {}
        if str(physics.get("status", "")).startswith("REJECT"):
            raise RuntimeError("The physics screen rejected this feature as an implausible oil signature.")
        source_path = run_root / "segmentation" / "slick_candidate.geojson"
        if not source_path.exists():
            raise FileNotFoundError("The georeferenced slick candidate is missing.")
        payload = json.loads(source_path.read_text(encoding="utf-8"))

        def approve_feature(value: object) -> None:
            if not isinstance(value, dict):
                return
            if value.get("type") == "Feature":
                properties = dict(value.get("properties") or {})
                properties["review_status"] = "analyst_approved"
                properties["reviewed_at_utc"] = _format_utc(_utc_now())
                properties["assumed_age_hours"] = float(age_hours)
                value["properties"] = properties
            for feature in value.get("features", []):
                approve_feature(feature)

        approve_feature(payload)
        review_dir = run_root / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        approved_path = review_dir / "approved_slick.geojson"
        approved_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        result = {
            "status": "APPROVED",
            "scene_id": scene_id,
            "reviewed_at_utc": _format_utc(_utc_now()),
            "assumed_age_hours": float(age_hours),
            "approved_slick_url": self._public_url(approved_path),
            "message": "Analyst approved the candidate for physics and AIS correlation.",
        }
        self._state["review"] = result
        self._state["attribution"] = {
            "status": "READY",
            "message": "Ready to build date-matched forcing and vessel evidence.",
            "candidates": [],
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
            self._state["attribution"] = {
                "status": "QUEUED",
                "message": "Date-matched forcing and AIS evidence are queued.",
                "queued_at_utc": _format_utc(_utc_now()),
                "candidates": [],
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
            base_result = {
                "origin_zone_url": self._public_url(origin_zone),
                "origin_particles": origin_particles,
                "observed_centroid": [
                    float(slick_observation.polygon.centroid.x),
                    float(slick_observation.polygon.centroid.y),
                ],
                "estimated_origin": drift["estimated_origin"],
                "credible_radius_90_km": drift["credible_radius_90_km"],
                "reverse_analysis_url": self._public_url(drift_dir / "slick_reverse_analysis.png"),
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
                status="RANKING",
                message="Forward-verifying every temporally matched AIS candidate.",
                ais_source=ais_source,
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
            runner_score = float(candidates[1]["total_score"]) if len(candidates) > 1 else 0.0
            margin = float(top["total_score"]) - runner_score
            limited = (
                len(candidates) >= 2
                and float(top["total_score"]) >= 0.40
                and margin >= 0.05
                and float(top.get("data_quality", 0.0)) >= 0.40
                and float(top.get("forward_error_km", 999.0)) <= 8.0
            )
            decision = "LIMITED_SHORTLIST" if limited else "ABSTAIN_INSUFFICIENT_EVIDENCE"
            tracks_path = evidence_root / "candidate_tracks.geojson"
            self._write_candidate_tracks(tracks_path, ais_path, candidates)
            self._section_update(
                "attribution",
                status="COMPLETE",
                decision=decision,
                message=(
                    "A comparative shortlist is ready for analyst investigation."
                    if limited
                    else "Evidence gates rejected nomination; retain the origin estimate and collect stronger tracks."
                ),
                candidate_count=len(candidates),
                candidates=candidates[:12],
                top_candidate=top,
                score_margin=margin,
                ais_source=ais_source,
                candidate_tracks_url=self._public_url(tracks_path),
                ranking_chart_url=self._public_url(ranking_dir / "candidate_ranking.png"),
                attribution_map_url=self._public_url(ranking_dir / "attribution_map.png"),
                completed_at_utc=_format_utc(_utc_now()),
                **base_result,
            )
        except Exception as error:
            self._section_update(
                "attribution",
                status="ERROR",
                decision="ABSTAIN_INSUFFICIENT_EVIDENCE",
                completed_at_utc=_format_utc(_utc_now()),
                message=f"{type(error).__name__}: {error}",
            )

    def _analysis_worker(self) -> None:
        catalog_path = self.output_root / "sentinel" / "sentinel1_catalog.json"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            with self._lock:
                requested_scene_id = self._state.get("analysis", {}).get("requested_scene_id")
            scene = next(
                (
                    item
                    for item in catalog.get("scenes", [])
                    if str(item.get("id")) == str(requested_scene_id)
                ),
                None,
            ) or catalog.get("recommended_scene") or {}
            scene_id = str(scene.get("id") or "unknown-scene")
            safe_scene_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in scene_id)
            run_root = self.output_root / "analysis" / safe_scene_id
            input_dir = run_root / "input"
            segmentation_dir = run_root / "segmentation"
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
                catalog_path,
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
            physics_screen = None
            if result.get("status") == "REVIEW_REQUIRED":
                physics_screen = self._build_physics_screen(
                    run_root, str(downloaded["acquisition_time_utc"])
                )
            self._analysis_update(
                status=str(result.get("status", "COMPLETE")),
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
                message=(
                    "A candidate requires human review before reverse-drift attribution."
                    if result.get("status") == "REVIEW_REQUIRED"
                    else "No oil candidate exceeded the frozen V6 decision threshold in this crop."
                    if result.get("status") == "NO_DETECTION"
                    else "SAR processing completed."
                ),
            )
        except Exception as error:
            self._analysis_update(
                status="ERROR",
                completed_at_utc=_format_utc(_utc_now()),
                message=f"{type(error).__name__}: {error}",
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
            self._update_source(
                "ais", status="CONNECTING", last_attempt_utc=attempted, message="Opening AIS stream."
            )
            try:
                status = asyncio.run(
                    capture_aisstream(
                        bounding_box,
                        self.output_root / "ais",
                        self.ais_cache,
                        duration_seconds=self.ais_capture_seconds,
                        window_hours=24.0,
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
        empty = {"positions": [], "tracks": {}, "position_count": 0, "vessel_count": 0}
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
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=6)
        frame = frame.loc[frame["timestamp_utc"] >= cutoff].sort_values("timestamp_utc")
        if frame.empty:
            return empty
        latest = frame.groupby("mmsi", as_index=False).tail(1).tail(500)
        positions = []
        for _, row in latest.iterrows():
            positions.append(
                {
                    "timestamp_utc": row["timestamp_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "mmsi": str(row["mmsi"]),
                    "vessel_name": str(row.get("vessel_name") or "UNKNOWN").strip(),
                    "longitude": float(row["longitude"]),
                    "latitude": float(row["latitude"]),
                    "sog": None if pd.isna(row.get("sog")) else float(row.get("sog")),
                    "cog": None if pd.isna(row.get("cog")) else float(row.get("cog")),
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
            "pipeline": pipeline,
            "analysis": state.get("analysis", {"status": "NOT_RUN"}),
            "review": state.get("review", {"status": "NOT_REVIEWED"}),
            "attribution": state.get("attribution", {"status": "NOT_RUN", "candidates": []}),
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
        attribution_status = str(state.get("attribution", {}).get("status", "NOT_RUN"))
        attribution_running = attribution_status in {
            "QUEUED",
            "PREPARING_FORCING",
            "REVERSING_DRIFT",
            "FETCHING_AIS",
            "RANKING",
        }
        if attribution_running:
            stage = f"EVIDENCE_{attribution_status}"
        elif attribution_status == "COMPLETE":
            stage = "EVIDENCE_SHORTLIST_READY"
        elif attribution_status.startswith("ABSTAIN"):
            stage = "EVIDENCE_SAFE_ABSTENTION"
        elif review_status == "REJECTED":
            stage = "ANALYST_REJECTED_SLICK"
        elif review_status == "APPROVED":
            stage = "SLICK_APPROVED_FOR_ATTRIBUTION"
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
            "attribution_ready": attribution_status in {"COMPLETE", "ABSTAIN_NO_MATCHED_AIS"},
            "message": (
                "No oil candidate is displayed until a calibrated scene is processed and approved."
            ),
        }
