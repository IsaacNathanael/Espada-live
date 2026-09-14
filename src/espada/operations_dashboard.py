from __future__ import annotations

import argparse
import base64
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw
from shapely.geometry import MultiPoint, Polygon, mapping, shape

from .coast import load_coast_mask
from .environment import load_cache
from .geo import haversine_km, local_xy_m
from .physics import advect_diffuse_spatial_timeseries
from .spatial_current import load_spatial_current_grid


VESSEL_PROFILES = (
    {"name": "MT Auriga Maris", "type": "Oil / chemical tanker", "flag": "Malta", "length": 183, "beam": 32, "draught": 10.2},
    {"name": "MV Ligurian Crest", "type": "General cargo", "flag": "Italy", "length": 146, "beam": 23, "draught": 8.1},
    {"name": "MT Pelagos Dawn", "type": "Product tanker", "flag": "Greece", "length": 176, "beam": 30, "draught": 9.7},
    {"name": "MV Cobalt Meridian", "type": "Bulk carrier", "flag": "Marshall Islands", "length": 199, "beam": 32, "draught": 11.3},
    {"name": "MV Cap Corse Trader", "type": "Ro-ro cargo", "flag": "France", "length": 164, "beam": 26, "draught": 7.4},
    {"name": "FV Stella Tirrena", "type": "Fishing vessel", "flag": "Italy", "length": 38, "beam": 9, "draught": 4.1},
    {"name": "MV Etruria Wind", "type": "Container feeder", "flag": "Cyprus", "length": 154, "beam": 25, "draught": 8.6},
    {"name": "MT Marevia", "type": "Bunker tanker", "flag": "Italy", "length": 92, "beam": 16, "draught": 5.8},
    {"name": "MV Tyrrhenian Vale", "type": "Bulk carrier", "flag": "Panama", "length": 189, "beam": 31, "draught": 10.8},
    {"name": "Tug Bastia Guardian", "type": "Harbour tug", "flag": "France", "length": 31, "beam": 11, "draught": 4.5},
    {"name": "MV Aegean Cedar", "type": "General cargo", "flag": "Greece", "length": 132, "beam": 21, "draught": 7.2},
    {"name": "FV Calvi Horizon", "type": "Fishing vessel", "flag": "France", "length": 34, "beam": 8, "draught": 3.8},
)


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required operations artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _display_name(mmsi: str, original: str, rank: int | None) -> str:
    if str(original).lower().startswith("blinded synthetic"):
        return "Synthetic source α"
    if str(original).lower().startswith("blinded"):
        return f"Vessel {str(mmsi)[-4:]}"
    if original and original.upper() != "UNKNOWN":
        return original
    return f"Vessel {str(mmsi)[-4:]}" if rank else f"Traffic {str(mmsi)[-4:]}"


def _track_speed_knots(frame: pd.DataFrame) -> float | None:
    ordered = frame.sort_values("timestamp_utc")
    times = pd.to_datetime(ordered["timestamp_utc"], utc=True)
    longitude = ordered["longitude"].to_numpy(dtype=float)
    latitude = ordered["latitude"].to_numpy(dtype=float)
    speeds: list[float] = []
    for index in range(1, len(ordered)):
        hours = (times.iloc[index] - times.iloc[index - 1]).total_seconds() / 3600.0
        if 0 < hours <= 3:
            speed = haversine_km(
                longitude[index - 1],
                latitude[index - 1],
                longitude[index],
                latitude[index],
            ) / hours / 1.852
            if math.isfinite(speed) and speed <= 45:
                speeds.append(float(speed))
    return float(np.median(speeds)) if speeds else None


def _track_span_km(frame: pd.DataFrame) -> float:
    longitude = frame["longitude"].to_numpy(dtype=float)
    latitude = frame["latitude"].to_numpy(dtype=float)
    if len(frame) < 2:
        return 0.0
    return float(
        haversine_km(
            float(longitude.min()),
            float(latitude.min()),
            float(longitude.max()),
            float(latitude.max()),
        )
    )


def _profile_map(mmsis: list[str], known_source_id: str) -> dict[str, dict[str, object]]:
    ordered = sorted(str(value) for value in mmsis)
    if known_source_id in ordered:
        ordered.remove(known_source_id)
        ordered.insert(0, known_source_id)
    profiles: dict[str, dict[str, object]] = {}
    for index, mmsi in enumerate(ordered):
        template = dict(VESSEL_PROFILES[index % len(VESSEL_PROFILES)])
        template["registry"] = f"ESP-COR-{index + 1:02d}"
        template["identity_status"] = "Fictional scenario alias; motion is pseudonymized historical AIS"
        profiles[mmsi] = template
    return profiles


def _polygon_from_particles(longitude: np.ndarray, latitude: np.ndarray) -> Polygon | None:
    longitude = np.asarray(longitude, dtype=float)
    latitude = np.asarray(latitude, dtype=float)
    if longitude.size < 10 or longitude.shape != latitude.shape:
        return None
    center_lon = float(np.median(longitude))
    center_lat = float(np.median(latitude))
    x, y = local_xy_m(longitude, latitude, center_lon, center_lat)
    radius = np.hypot(x, y)
    keep = radius <= np.quantile(radius, 0.90)
    polygon = MultiPoint(np.column_stack((longitude[keep], latitude[keep]))).convex_hull
    return polygon if isinstance(polygon, Polygon) and polygon.is_valid else None


def _slick_timeline(
    case_root: Path,
    slick: dict,
    challenge_result: dict,
    scenario: dict,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    final_geometry = slick["features"][0]["geometry"]
    truth = challenge_result.get("truth_reveal", {})
    fallback_time = str(
        truth.get(
            "observation_time_utc",
            slick["features"][0].get("properties", {}).get("observation_time_utc", ""),
        )
    )
    fallback = [{"time": fallback_time, "geometry": final_geometry, "particleCount": None}]
    if not truth or not scenario:
        return fallback, {
            "physicsReplay": "UNAVAILABLE",
            "finalGeometryIoU": None,
            "timelineFrames": 1,
        }

    try:
        release_time = pd.Timestamp(truth["release_time_utc"])
        observation_time = pd.Timestamp(truth["observation_time_utc"])
        environment = load_cache(case_root / "environment/environment.json").frame
        current_grid = load_spatial_current_grid(case_root / "environment/currents.nc")
        coast = load_coast_mask(case_root / "environment/land_mask.geojson")
        forcing = scenario["truth"]
        particles = int(scenario.get("simulation_particles", 600))
        release_duration_minutes = float(scenario.get("release_duration_minutes", 90.0))
        release_corridor = truth["release_corridor"]
        delays = np.linspace(0.0, release_duration_minutes / 60.0, 3)
        cohort_sizes = np.full(3, particles // 3, dtype=int)
        cohort_sizes[: particles % 3] += 1
        rng = np.random.default_rng(int(scenario.get("seed", 26143)))
        # run_challenge selects background traffic with the same generator before
        # slick diffusion. Reproduce those draws so this time-lapse is bit-for-bit
        # aligned with the saved controlled observation.
        source_traffic = pd.read_csv(
            case_root / "ais/ais_normalized.csv", dtype={"mmsi": str}
        )
        rng.random(max(0, source_traffic["mmsi"].nunique() - 1))
        cohorts: list[dict[str, object]] = []

        for delay_hours, cohort_size, release_point in zip(
            delays, cohort_sizes, release_corridor
        ):
            cohort_time = release_time + pd.Timedelta(hours=float(delay_hours))
            history = environment.loc[
                (environment["time_utc"] >= cohort_time)
                & (environment["time_utc"] < observation_time)
            ]
            duration_seconds = (observation_time - cohort_time).total_seconds()
            step_seconds = duration_seconds / len(history)
            longitude = np.full(int(cohort_size), float(release_point["longitude"]))
            latitude = np.full(int(cohort_size), float(release_point["latitude"]))
            states: list[tuple[pd.Timestamp, np.ndarray, np.ndarray]] = [
                (cohort_time, longitude.copy(), latitude.copy())
            ]
            for step_index, row in enumerate(history.itertuples()):
                longitude, latitude = advect_diffuse_spatial_timeseries(
                    longitude,
                    latitude,
                    [row.time_utc],
                    np.asarray([row.wind_east_ms * float(forcing["wind_multiplier"])]),
                    np.asarray([row.wind_north_ms * float(forcing["wind_multiplier"])]),
                    step_seconds,
                    current_grid,
                    rng,
                    windage=float(forcing["windage"]),
                    diffusivity_m2s=float(forcing["diffusivity_m2s"]),
                    current_multiplier=float(forcing["current_multiplier"]),
                    coast_mask=coast,
                )
                states.append(
                    (
                        cohort_time
                        + pd.Timedelta(seconds=(step_index + 1) * step_seconds),
                        longitude.copy(),
                        latitude.copy(),
                    )
                )
            cohorts.append({"release": cohort_time, "states": states})

        frame_times = list(pd.date_range(release_time, observation_time, periods=13))
        frames: list[dict[str, object]] = []
        for frame_time in frame_times:
            frame_longitude: list[np.ndarray] = []
            frame_latitude: list[np.ndarray] = []
            for cohort in cohorts:
                if frame_time < cohort["release"]:
                    continue
                states = cohort["states"]
                eligible = [state for state in states if state[0] <= frame_time]
                state = eligible[-1] if eligible else states[0]
                frame_longitude.append(state[1])
                frame_latitude.append(state[2])
            polygon = (
                _polygon_from_particles(
                    np.concatenate(frame_longitude), np.concatenate(frame_latitude)
                )
                if frame_longitude
                else None
            )
            frames.append(
                {
                    "time": frame_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "geometry": mapping(polygon) if polygon else None,
                    "particleCount": int(sum(len(value) for value in frame_longitude)),
                }
            )

        reconstructed_lon = np.concatenate(
            [cohort["states"][-1][1] for cohort in cohorts]
        )
        reconstructed_lat = np.concatenate(
            [cohort["states"][-1][2] for cohort in cohorts]
        )
        reconstructed = _polygon_from_particles(reconstructed_lon, reconstructed_lat)
        final_polygon = shape(final_geometry)
        geometry_iou = (
            float(reconstructed.intersection(final_polygon).area / reconstructed.union(final_polygon).area)
            if reconstructed and not reconstructed.union(final_polygon).is_empty
            else None
        )
        frames[-1]["geometry"] = final_geometry
        return frames, {
            "physicsReplay": "PASS",
            "finalGeometryIoU": geometry_iou,
            "timelineFrames": len(frames),
            "particleModel": "three-cohort advection-diffusion with particle-local currents and coastline blocking",
        }
    except (FileNotFoundError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return fallback, {
            "physicsReplay": "FALLBACK_FINAL_GEOMETRY_ONLY",
            "finalGeometryIoU": None,
            "timelineFrames": 1,
        }


def _sar_alignment(
    image_path: Path,
    slick: dict,
    bbox: list[float],
    observation_time: str,
    acquisition_time: str,
) -> dict[str, object]:
    geometry = shape(slick["features"][0]["geometry"])
    bounds = geometry.bounds
    within_bbox = (
        bounds[0] >= bbox[0]
        and bounds[1] >= bbox[1]
        and bounds[2] <= bbox[2]
        and bounds[3] <= bbox[3]
    )
    contrast_ratio: float | None = None
    try:
        image = np.asarray(Image.open(image_path).convert("L"), dtype=float)
        height, width = image.shape
        mask_image = Image.new("1", (width, height), 0)
        drawer = ImageDraw.Draw(mask_image)
        parts = geometry.geoms if geometry.geom_type == "MultiPolygon" else (geometry,)
        for part in parts:
            pixels = [
                (
                    (longitude - bbox[0]) / (bbox[2] - bbox[0]) * (width - 1),
                    (bbox[3] - latitude) / (bbox[3] - bbox[1]) * (height - 1),
                )
                for longitude, latitude in part.exterior.coords
            ]
            drawer.polygon(pixels, fill=1)
        mask = np.asarray(mask_image, dtype=bool)
        if mask.any():
            ys, xs = np.where(mask)
            pad = 40
            local = np.zeros_like(mask)
            local[
                max(0, int(ys.min()) - pad) : min(height, int(ys.max()) + pad + 1),
                max(0, int(xs.min()) - pad) : min(width, int(xs.max()) + pad + 1),
            ] = True
            background = local & ~mask
            if background.any() and float(image[background].mean()) > 0:
                contrast_ratio = float(image[mask].mean() / image[background].mean())
    except (FileNotFoundError, OSError, ValueError):
        contrast_ratio = None
    time_offset = abs(
        (pd.Timestamp(acquisition_time) - pd.Timestamp(observation_time)).total_seconds()
    ) / 60.0
    return {
        "geometryWithinSarBbox": bool(within_bbox),
        "sarAcquisitionOffsetMinutes": float(time_offset),
        "rawSarLocalContrastRatio": contrast_ratio,
        "rawSarOilClaimSupported": False,
        "displayMode": "Real Sentinel-1 context plus a labelled physics-generated oil-return composite",
        "interpretation": (
            "The controlled slick is georegistered inside the SAR crop, but the raw pixels are not "
            "claimed as a verified oil detection. The composite makes the simulated sensor return and "
            "its exact detection boundary visible without falsifying the raw image."
        ),
    }


def build_operations_dashboard(
    project_root: Path,
    output_path: Path,
    dossier_href: str | None = None,
) -> dict[str, object]:
    root = Path(project_root)
    case = root / "out/external_validation/corsica_2018"
    challenge = root / "out/challenge"
    use_challenge = all(
        path.exists()
        for path in (
            challenge / "challenge_result.json",
            challenge / "synthetic_slick.geojson",
            challenge / "ranking/candidates.json",
            challenge / "ais/ais_normalized.csv",
        )
    )
    challenge_result = _read_json(challenge / "challenge_result.json") if use_challenge else {}
    scenario = _read_json(challenge / "scenario_used.json") if use_challenge else {}
    run = challenge if use_challenge else case / "counterfactual_ais/run"
    ranking = _read_json(run / "ranking/candidates.json")
    decision = _read_json(run / "decision/decision_gate.json")
    estimate = _read_json(run / "drift/release_estimate.json")
    # The counterfactual replay deliberately swaps only AIS. Its reviewed slick
    # remains in the parent Corsica run, so accept either layout.
    slick_path = run / "synthetic_slick.geojson" if use_challenge else run / "sar/slick_observation.geojson"
    if not slick_path.exists():
        slick_path = case / "run/sar/slick_observation.geojson"
    slick = _read_json(slick_path)
    environment = _read_json(case / "environment/environment.json")
    satellite = _read_json(case / "sar_input/sentinel1_subset_status.json")
    twin = _read_json(root / "out/digital_twin/digital_twin_summary.json")
    validation_path = root / "out/system_validation/system_scorecard.json"
    validation = _read_json(validation_path) if validation_path.exists() else {}
    slick_analysis_path = run / "drift/slick_analysis.json"
    slick_analysis = (
        _read_json(slick_analysis_path) if slick_analysis_path.exists() else {"input": {}}
    )
    time_search_path = run / "time_search/release_time_search.json"
    time_search = _read_json(time_search_path) if time_search_path.exists() else {}
    ais = pd.read_csv(run / "ais/ais_normalized.csv", dtype={"mmsi": str})
    candidate_lookup = {str(item["mmsi"]): item for item in ranking["candidates"]}
    known_source_id = str(challenge_result.get("truth_reveal", {}).get("source_id", ""))
    profiles = _profile_map(ais["mmsi"].astype(str).unique().tolist(), known_source_id)
    tracks = []
    for mmsi, frame in ais.groupby("mmsi", sort=False):
        frame = frame.sort_values("timestamp_utc")
        candidate = candidate_lookup.get(str(mmsi), {})
        rank = int(candidate["rank"]) if candidate.get("rank") else None
        original_name = str(frame["vessel_name"].iloc[0])
        profile = profiles.get(str(mmsi), {}) if use_challenge else {}
        median_speed = _track_speed_knots(frame)
        route_span_km = _track_span_km(frame)
        motion_status = (
            "Coastal transit / manoeuvring"
            if route_span_km >= 5.0
            else (
                "At anchor / slow drift"
                if median_speed is not None and median_speed < 1.0
                else (
                    "Low-speed manoeuvre"
                    if median_speed is not None and median_speed < 4.0
                    else "Under way"
                )
            )
        )
        tracks.append(
            {
                "mmsi": str(mmsi),
                "registry": profile.get("registry", f"AIS {str(mmsi)[-6:]}"),
                "name": profile.get("name", _display_name(str(mmsi), original_name, rank)),
                "vesselType": profile.get("type", "Unknown vessel type"),
                "flag": profile.get("flag", "Not available"),
                "lengthM": profile.get("length"),
                "beamM": profile.get("beam"),
                "draughtM": profile.get("draught"),
                "identityStatus": profile.get(
                    "identity_status", "AIS-reported identity when available"
                ),
                "medianSpeedKnots": median_speed,
                "routeSpanKm": route_span_km,
                "motionStatus": motion_status,
                "reportCount": int(len(frame)),
                "coverageStart": str(frame["timestamp_utc"].iloc[0]),
                "coverageEnd": str(frame["timestamp_utc"].iloc[-1]),
                "rank": rank,
                "score": candidate.get("total_score"),
                "presence": candidate.get("presence_score"),
                "forward": candidate.get("forward_consistency"),
                "forwardError": candidate.get("forward_error_km"),
                "shapeError": candidate.get("forward_shape_error_km"),
                "quality": candidate.get("data_quality"),
                "bestTime": candidate.get("best_match_time_utc"),
                "silence": candidate.get("silence_classification"),
                "interpolated": candidate.get("release_position_interpolated", False),
                "synthetic": original_name.lower().startswith("blinded synthetic"),
                "knownSource": bool(known_source_id and str(mmsi) == known_source_id),
                "points": [
                    {
                        "t": row.timestamp_utc,
                        "lon": float(row.longitude),
                        "lat": float(row.latitude),
                    }
                    for row in frame.itertuples()
                ],
            }
        )
    with np.load(run / "drift/reverse_endpoints.npz") as bundle:
        lon = np.asarray(bundle["lon"], dtype=float)
        lat = np.asarray(bundle["lat"], dtype=float)
    sample = np.linspace(0, len(lon) - 1, min(420, len(lon)), dtype=int)
    reverse_points = [[float(lon[index]), float(lat[index])] for index in sample]
    image = base64.b64encode((case / "sar_input/sentinel1_vv_quicklook.png").read_bytes()).decode("ascii")
    top_candidates = []
    for item in ranking["candidates"][:10]:
        track = next(value for value in tracks if value["mmsi"] == str(item["mmsi"]))
        top_candidates.append(track)
    track_lookup = {track["mmsi"]: track for track in tracks}
    scenario_lab = []
    hypotheses_path = run / "time_search/release_hypotheses.csv"
    if hypotheses_path.exists() and known_source_id:
        hypotheses = pd.read_csv(hypotheses_path, dtype={"candidate_id": str})
        for keys, group in hypotheses.groupby(
            ["age_hours", "current_multiplier", "windage"], sort=True
        ):
            ranked = group.sort_values("rank")
            leader = ranked.iloc[0]
            source_rows = ranked.loc[ranked["candidate_id"] == known_source_id]
            source_row = source_rows.iloc[0] if not source_rows.empty else None
            leader_track = track_lookup.get(str(leader["candidate_id"]), {})
            scenario_lab.append(
                {
                    "ageHours": float(keys[0]),
                    "currentMultiplier": float(keys[1]),
                    "windage": float(keys[2]),
                    "leaderId": str(leader["candidate_id"]),
                    "leaderName": leader_track.get("name", "Pseudonymized vessel"),
                    "leaderScore": float(leader["score"]),
                    "sourceRank": int(source_row["rank"]) if source_row is not None else None,
                    "sourceScore": float(source_row["score"]) if source_row is not None else None,
                    "sourceForwardError": (
                        float(source_row["forward_error_km"])
                        if source_row is not None
                        else None
                    ),
                }
            )
    bbox = satellite["bbox"]
    slick_timeline, physics_validation = _slick_timeline(
        case, slick, challenge_result, scenario
    )
    truth_reveal = challenge_result.get("truth_reveal", {})
    truth_release_time = str(
        truth_reveal.get("release_time_utc", estimate["release_time_utc"])
    )
    observation_time = str(
        truth_reveal.get("observation_time_utc", estimate["observation_time_utc"])
    )
    playback_start = (
        pd.Timestamp(truth_release_time) - pd.Timedelta(hours=4)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    alignment = {
        **physics_validation,
        **_sar_alignment(
            case / "sar_input/sentinel1_vv_quicklook.png",
            slick,
            bbox,
            observation_time,
            satellite["acquisition_time_utc"],
        ),
    }
    release_corridor = truth_reveal.get("release_corridor", [])
    if known_source_id and release_corridor:
        source_track = ais.loc[ais["mmsi"].astype(str) == known_source_id].copy()
        source_track["timestamp_utc"] = pd.to_datetime(
            source_track["timestamp_utc"], utc=True
        )
        source_track = source_track.sort_values("timestamp_utc")
        seconds = source_track["timestamp_utc"].map(
            lambda value: value.timestamp()
        ).to_numpy(dtype=float)
        target = pd.Timestamp(truth_release_time).timestamp()
        source_longitude = float(
            np.interp(target, seconds, source_track["longitude"].to_numpy(dtype=float))
        )
        source_latitude = float(
            np.interp(target, seconds, source_track["latitude"].to_numpy(dtype=float))
        )
        alignment["sourceTrackReleaseErrorKm"] = haversine_km(
            source_longitude,
            source_latitude,
            float(release_corridor[0]["longitude"]),
            float(release_corridor[0]["latitude"]),
        )
        alignment["sourceTrackReleaseMatch"] = (
            "PASS" if alignment["sourceTrackReleaseErrorKm"] <= 1.0 else "REVIEW"
        )
    else:
        alignment["sourceTrackReleaseErrorKm"] = None
        alignment["sourceTrackReleaseMatch"] = "UNAVAILABLE"
    payload = {
        "case": {
            "id": "corsica-2018-sealed-challenge" if use_challenge else "corsica-2018-hybrid",
            "name": "Corsica · Sealed Challenge" if use_challenge else "Corsica · 2018",
            "mode": "CONTROLLED DIGITAL TWIN" if use_challenge else "HYBRID SIMULATION",
            "observationTime": observation_time,
            "releaseTime": estimate["release_time_utc"],
            "truthReleaseTime": truth_release_time,
            "releaseDurationMinutes": float(scenario.get("release_duration_minutes", 0.0)),
            "playbackStart": playback_start,
            "playbackEnd": observation_time,
            "decision": decision["decision"],
            "candidateCount": ranking["candidate_count"],
        },
        "bbox": bbox,
        "satelliteImage": f"data:image/png;base64,{image}",
        "satellite": satellite,
        "slick": slick,
        "slickTimeline": slick_timeline,
        "release": {
            **estimate["estimated_origin"],
            "radius50": estimate.get("credible_radius_50_km"),
            "radius90": estimate["credible_radius_90_km"],
        },
        "truthRelease": (
            truth_reveal.get("release_corridor", [{}])[0]
            if truth_reveal.get("release_corridor")
            else estimate["estimated_origin"]
        ),
        "reverse": reverse_points,
        "tracks": tracks,
        "candidates": top_candidates,
        "environment": environment["samples"],
        "environmentSource": environment["source"],
        "decision": decision,
        "digitalTwin": twin,
        "validation": validation,
        "validationScorecard": (
            "validation_scorecard.html"
            if dossier_href
            else "../system_validation/system_scorecard.html"
        ),
        "realWorldValidation": (
            "real_world_validation.html"
            if dossier_href
            else "../validation_showcase/real_world_validation.html"
        ),
        "challenge": challenge_result,
        "scenario": scenario,
        "scenarioLab": scenario_lab,
        "timeSearch": time_search,
        "slickAnalysis": slick_analysis,
        "alignment": alignment,
        "dossier": dossier_href
        or (
            "../challenge/dossier/evidence_dossier.html"
            if use_challenge
            else "../external_validation/corsica_2018/counterfactual_ais/run/dossier/evidence_dossier.html"
        ),
        "trafficSummary": (
            f"{len(tracks)} pseudonymized historical GFW tracks with fictional scenario aliases"
            if use_challenge
            else "76 real historical tracks + 1 disclosed synthetic source track"
        ),
        "slickSource": (
            "Controlled hidden-source slick generated with real environmental forcing"
            if use_challenge
            else "Analyst-reviewed counterfactual slick observation"
        ),
        "disclosure": (
            "Real Sentinel-1 context, real Copernicus/Open-Meteo forcing and pseudonymized real GFW traffic; "
            "the slick is a sealed-ground-truth controlled simulation."
            if use_challenge
            else "Real Sentinel-1 image, real Copernicus/Open-Meteo forcing and 76 real GFW background tracks; "
            "one disclosed synthetic source track tests observability."
        ),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    document = _document(data)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    (output_path.parent / "alignment_report.json").write_text(
        json.dumps(alignment, indent=2), encoding="utf-8"
    )
    return {
        "status": "PASS",
        "output": str(output_path.resolve()),
        "ships": len(tracks),
        "candidate_cards": len(top_candidates),
        "mode": payload["case"]["mode"],
        "source": "sealed challenge" if use_challenge else "counterfactual replay",
        "physics_replay": alignment["physicsReplay"],
        "final_geometry_iou": alignment["finalGeometryIoU"],
    }


def _document(data: str) -> str:
    return r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESPADA Operations</title><style>
:root{--bg:#030b10;--panel:#081820;--panel2:#0c222c;--line:#173541;--ink:#ecfffb;--muted:#87a2aa;--cyan:#52e2dc;--amber:#ffbd59;--red:#ff7187;--green:#67e8a5}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;overflow-x:hidden;background:var(--bg);color:var(--ink);font:13px/1.45 Inter,Segoe UI,Arial,sans-serif}button{font:inherit;color:inherit}button:focus-visible,input:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}.app{height:100vh;display:grid;grid-template-rows:62px minmax(0,1fr) 88px;background:radial-gradient(circle at 10% 0,#103640 0,transparent 28%),var(--bg)}
header{display:flex;align-items:center;gap:18px;padding:0 18px;border-bottom:1px solid var(--line);background:#041118e8;backdrop-filter:blur(12px);z-index:20}.brand{display:flex;align-items:center;gap:10px;font-weight:900;letter-spacing:.15em}.mark{width:32px;height:32px;display:grid;place-items:center;border:1px solid #3a7777;border-radius:9px;color:var(--cyan)}.case{border-left:1px solid var(--line);padding-left:18px}.case b{display:block;font-size:14px}.case span,.source{color:var(--muted);font-size:10px}.badge{padding:6px 9px;border:1px solid #8d6d2c;border-radius:999px;color:var(--amber);font-size:9px;font-weight:900;letter-spacing:.1em}.spacer{flex:1}.server{font-size:9px;color:var(--muted)}.server.on{color:var(--green)}.primary,.soft{border-radius:9px;padding:9px 13px;cursor:pointer;font-weight:800}.primary{border:1px solid var(--cyan);background:var(--cyan);color:#031013}.soft{border:1px solid var(--line);background:#0a2028}.primary:disabled{opacity:.55;cursor:wait}
.workspace{min-height:0;display:grid;grid-template-columns:minmax(0,1.28fr) minmax(340px,.72fr);gap:10px;padding:10px}.map-card,.side{min-height:0;border:1px solid var(--line);border-radius:16px;background:var(--panel);overflow:hidden}.map-card{position:relative}.map{position:absolute;inset:0;background:#06212a}.sar{width:100%;height:100%;opacity:.76;filter:contrast(1.18) saturate(.4);transition:.3s}.map-shade{position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,29,37,.26),rgba(0,22,30,.01)),radial-gradient(circle at 55% 45%,transparent,#00101888);pointer-events:none}.overlay{position:absolute;inset:0;width:100%;height:100%}.gridline{stroke:#75d9d41c;stroke-width:1}.oil-halo{fill:#000;stroke:none;opacity:.32;filter:url(#oilFeather)}.oil-return{fill:url(#oilSurface);stroke:#75878b;stroke-width:1;filter:url(#oilTexture);opacity:.76;transition:opacity .35s}.detection{fill:none;stroke:var(--amber);stroke-width:2.2;stroke-dasharray:8 4;filter:drop-shadow(0 0 4px #ffbd59aa);opacity:0;transition:.3s}.detection.show{opacity:1}.release{fill:#52e2dc18;stroke:var(--cyan);stroke-width:2;opacity:0;transform-origin:center;filter:drop-shadow(0 0 12px #52e2dc)}.release.show{opacity:1;animation:pulse 2.2s infinite}.particle{fill:#8affee;opacity:0}.particle.show{opacity:.48}.traffic-trail{fill:none;stroke:#8ad8d3;stroke-width:1.05;opacity:.25}.traffic-trail.top{stroke:var(--amber);stroke-width:1.8;opacity:.7}.track{fill:none;stroke:#eafffb;stroke-width:1.8;opacity:.82;stroke-dasharray:7 4}.track.top{stroke:var(--amber);stroke-width:2.4}.report-dot{fill:#c9ffff;opacity:.65}.leak-pulse{fill:#ffbd5966;stroke:var(--amber);stroke-width:2;animation:pulse 1.2s infinite}.ship{cursor:pointer;opacity:1;transition:opacity .35s ease,filter .25s}.ship .hull{fill:#79eee8;stroke:#01262c;stroke-width:1.1;filter:drop-shadow(0 2px 3px #001)}.ship .deck{fill:#d8fffb;stroke:#15464c;stroke-width:.65}.ship .bridge{fill:#123942;stroke:#d8fffb;stroke-width:.5}.ship .keel{stroke:#15545a;stroke-width:.7}.ship.top .hull{fill:var(--amber)}.ship.top .deck{fill:#fff1cf}.ship.dim{opacity:.16}.ship.hidden{opacity:0;pointer-events:none}.ship.acquired .hull{animation:acquired .8s ease}.ship.selected .hull{stroke:white;stroke-width:2.2;filter:drop-shadow(0 0 6px white)}.ship-cluster{cursor:pointer}.ship-cluster circle{fill:#0a2b34;stroke:#79eee8;stroke-width:1.5;filter:drop-shadow(0 2px 4px #001)}.ship-cluster text{fill:#ecfffb;font:bold 10px Inter,Segoe UI,sans-serif;text-anchor:middle;dominant-baseline:central;pointer-events:none}
@keyframes pulse{50%{stroke-width:5;opacity:.55}}@keyframes acquired{0%{transform:scale(.35);opacity:0}55%{transform:scale(1.4);opacity:1}100%{transform:scale(1)}}.map-tools{position:absolute;top:14px;left:14px;display:flex;gap:7px;z-index:5}.tool{border:1px solid #31515d;background:#04151dde;padding:8px 10px;border-radius:8px;cursor:pointer;font-weight:800}.tool[aria-pressed=true]{border-color:var(--cyan);color:var(--cyan)}.legend{position:absolute;left:14px;bottom:14px;display:flex;gap:12px;padding:8px 10px;border:1px solid #31515d;border-radius:9px;background:#04151dde;color:#bcd0d4;font-size:9px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}.map-status{position:absolute;right:14px;top:14px;padding:9px 11px;border-radius:9px;background:#04151dde;border:1px solid #31515d;text-align:right}.map-status b{display:block;color:var(--cyan)}.map-status span{font-size:9px;color:var(--muted)}.coverage-live{position:absolute;right:14px;top:68px;padding:6px 9px;border-radius:8px;background:#04151dcc;border:1px solid #31515d;color:var(--muted);font-size:9px;z-index:5}.coverage-live b{color:var(--ink)}
.tooltip{position:absolute;display:none;pointer-events:none;z-index:10;width:190px;padding:10px;border:1px solid #3a6570;border-radius:10px;background:#04151df2;box-shadow:0 12px 30px #0008}.tooltip.show{display:block}.tooltip b{display:block;font-size:13px}.tooltip span{display:block;color:var(--muted);font-size:10px;margin-top:3px}.tooltip em{color:var(--amber);font-style:normal}
.side{display:grid;grid-template-rows:auto auto minmax(0,1fr);overflow:hidden}.side-head{padding:15px 16px 12px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:end}.eyebrow{font-size:9px;letter-spacing:.15em;color:var(--cyan);font-weight:900}.side-head h2{margin:3px 0 0;font:500 23px Georgia,serif}.count{color:var(--muted);font-size:10px}.decision-summary{margin:10px 11px 0;padding:11px 12px;border:1px solid var(--line);border-radius:11px;background:#06171d}.decision-summary small{display:block;color:var(--muted);font-size:8px;letter-spacing:.12em}.decision-summary b{display:block;margin:3px 0;color:var(--cyan);font-size:14px}.decision-summary span{color:var(--muted);font-size:9px}.decision-summary.abstain{border-color:#806532}.decision-summary.abstain b{color:var(--amber)}.side-scroll{overflow:auto;padding:11px}.candidates{display:grid;gap:7px}.candidate{display:grid;grid-template-columns:31px 1fr auto;gap:9px;align-items:center;width:100%;padding:10px;border:1px solid var(--line);border-radius:11px;background:#071a22;text-align:left;cursor:pointer}.candidate:hover,.candidate.active{border-color:#4e858c;background:#0b252e}.rank{width:28px;height:28px;display:grid;place-items:center;border-radius:8px;background:#102f38;color:var(--cyan);font-weight:900}.candidate:first-child .rank{background:#4b3b1c;color:var(--amber)}.candidate b{display:block}.candidate small{color:var(--muted)}.score{color:var(--amber);font-weight:900}.more{width:100%;border:0;background:none;color:var(--muted);padding:9px;cursor:pointer}.detail{margin-top:11px;padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}.detail-placeholder{text-align:center;color:var(--muted);padding:20px}.detail-top{display:flex;justify-content:space-between;gap:12px}.detail h3{margin:2px 0;font:500 20px Georgia,serif}.detail-id{color:var(--muted);font:10px monospace}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:11px 0}.metric{padding:9px;border-radius:9px;background:#06171d}.metric span{display:block;color:var(--muted);font-size:9px}.metric b{display:block;margin-top:3px}.bar{height:4px;background:#14313a;border-radius:5px;margin-top:5px;overflow:hidden}.bar i{display:block;height:100%;background:var(--cyan)}.detail-actions{display:flex;gap:6px}.detail-actions button,.detail-actions a{flex:1;padding:8px;border:1px solid var(--line);border-radius:8px;background:#09232b;color:var(--ink);text-align:center;text-decoration:none;cursor:pointer;font-size:10px;font-weight:800}.notice{margin-top:9px;color:#c9b77f;font-size:9px;border-left:2px solid var(--amber);padding-left:8px}
.bottom{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:10px;padding:0 10px 10px}.timeline,.weather{border:1px solid var(--line);border-radius:14px;background:var(--panel);min-width:0}.timeline{display:grid;grid-template-columns:auto 1fr 150px;align-items:center;gap:12px;padding:12px 14px}.play{width:38px;height:38px;border-radius:50%;border:1px solid #39717a;background:#0a2931;cursor:pointer}.time-readout{text-align:right}.time-readout b{display:block;color:var(--amber);font-size:9px;letter-spacing:.08em}.timebox{display:block;font:10px monospace;color:var(--cyan)}.range-wrap{position:relative;height:24px;padding-top:4px}input[type=range]{position:relative;z-index:2;width:100%;accent-color:var(--cyan)}.event-mark{position:absolute;top:0;height:22px;border-left:1px solid var(--amber);z-index:1}.event-mark.obs{border-color:var(--cyan)}.event-mark span{position:absolute;top:-10px;left:4px;white-space:nowrap;color:var(--amber);font-size:7px}.event-mark.obs span{color:var(--cyan);transform:translateX(-100%);left:-4px}.range-labels{display:flex;justify-content:space-between;color:var(--muted);font-size:8px}.weather{display:grid;grid-template-columns:1fr 1fr 1fr}.weather button{border:0;border-right:1px solid var(--line);background:transparent;text-align:left;padding:12px;cursor:pointer}.weather button:last-child{border:0}.weather span{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}.weather b{display:block;font-size:15px;margin-top:4px}.arrow{display:inline-block;color:var(--cyan);margin-right:4px}.profile-line{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.profile-line span{padding:4px 6px;border:1px solid var(--line);border-radius:6px;color:#bfd2d6;font-size:9px}
.popover{position:absolute;left:14px;top:54px;display:none;padding:9px;border:1px solid #31515d;border-radius:10px;background:#04151df5;z-index:8}.popover.show{display:grid;gap:7px}.popover label{display:flex;gap:8px;align-items:center;font-size:11px}.phase{position:absolute;inset:auto 20px 22px 20px;display:none;grid-template-columns:repeat(4,1fr);gap:6px;z-index:7}.phase.show{display:grid}.phase div{padding:8px;border:1px solid #31515d;background:#04151ded;border-radius:8px;color:var(--muted);font-size:9px}.phase div.on{border-color:var(--cyan);color:var(--cyan)}.phase div.done{color:var(--green)}
.investigation{min-height:100vh;padding:64px clamp(18px,5vw,76px) 76px;background:#eef1f3;color:#132536;border-top:5px solid var(--amber)}.investigation-head{display:flex;justify-content:space-between;align-items:end;gap:24px;max-width:1320px;margin:0 auto 24px}.investigation-head .eyebrow{color:#9a6511}.investigation-head h2{margin:4px 0 5px;font:700 clamp(28px,4vw,44px)/1.05 Bahnschrift,Segoe UI,sans-serif;color:#071f35}.investigation-head p{margin:0;color:#5f6d78;max-width:680px}.back-top{color:#173954;text-decoration:none;font-weight:700;border:1px solid #b9c3ca;border-radius:8px;padding:9px 12px;background:white;white-space:nowrap}.investigation-grid{max-width:1320px;margin:auto;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.work-card{background:white;border:1px solid #cbd3d9;border-radius:12px;padding:18px;box-shadow:0 6px 18px #1a334512}.work-card.wide{grid-column:span 2}.work-card h3{margin:4px 0 5px;font:700 19px Bahnschrift,Segoe UI,sans-serif;color:#0a2943}.work-card p{margin:0 0 13px;color:#687680;font-size:11px}.work-kicker{font-size:9px;letter-spacing:.13em;font-weight:800;color:#a56b10}.uncertainty-visual{height:135px;display:flex;align-items:center;justify-content:center;position:relative}.uncertainty-ring{position:absolute;border-radius:50%;border:2px solid #d29a38;background:#e5b75c18}.uncertainty-ring.r90{width:128px;height:128px}.uncertainty-ring.r50{width:82px;height:82px;background:#e5b75c2b}.origin-pin{position:relative;width:12px;height:12px;border-radius:50%;background:#173954;border:3px solid white;box-shadow:0 0 0 2px #173954}.uncertainty-values,.strength-list,.source-list{display:grid;gap:8px}.work-row{display:flex;justify-content:space-between;gap:14px;padding-top:8px;border-top:1px solid #e1e5e8;color:#566672;font-size:11px}.work-row b{color:#122d43;text-align:right}.strength{display:grid;grid-template-columns:110px 1fr 46px;gap:8px;align-items:center;font-size:10px}.strength-track{height:7px;background:#e1e6ea;border-radius:10px;overflow:hidden}.strength-track i{display:block;height:100%;background:#244e6c;border-radius:10px}.strength.warn .strength-track i{background:#d29127}.decision-strip{margin-top:12px;padding:10px;border-left:4px solid #d29127;background:#fff5df;color:#704c13;font-weight:700}.comparison{display:grid;grid-template-columns:1fr 32px 1fr;gap:8px;align-items:stretch}.compare-vessel{border:1px solid #d9dfe3;border-radius:9px;padding:12px;background:#fafbfc}.compare-vessel strong{display:block;color:#0c2a42}.compare-vessel small{color:#71808b}.compare-vessel dl{display:grid;grid-template-columns:1fr auto;margin:10px 0 0;gap:5px;font-size:10px}.compare-vessel dt{color:#71808b}.compare-vessel dd{margin:0;font-weight:700}.versus{display:grid;place-items:center;color:#9a6511;font-weight:900}.reason{margin-top:10px;padding:9px;border-radius:7px;background:#eff3f5;color:#3f5668;font-size:10px}.lab-controls{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.lab-control{padding:11px;border:1px solid #d9dfe3;border-radius:9px;background:#fafbfc}.lab-control label{display:flex;justify-content:space-between;font-size:10px;font-weight:700}.lab-control input{width:100%;accent-color:#c2821f}.lab-result{margin-top:12px;display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.lab-result div{padding:10px;background:#102c43;color:white;border-radius:8px}.lab-result span{display:block;color:#adbbc5;font-size:8px;text-transform:uppercase}.lab-result b{display:block;margin-top:4px}.work-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.work-action{display:grid;place-items:center;border:1px solid #bdc7ce;background:#f8fafb;color:#16344c;border-radius:8px;padding:11px;cursor:pointer;font-weight:700;text-align:center;text-decoration:none}.work-action.primary-action{background:#173954;color:white;border-color:#173954}.work-action.amber-action{background:#d18b22;color:#111d26;border-color:#d18b22}.action-status{margin-top:10px;min-height:20px;color:#526675;font-size:10px}.source-item{display:grid;grid-template-columns:10px 1fr auto;gap:8px;align-items:center;padding:7px 0;border-top:1px solid #e3e7ea;font-size:10px}.source-state{width:8px;height:8px;border-radius:50%;background:#287d5a}.source-item span{color:#667681}.source-item b{color:#18364d}.source-time{color:#7a8790;font:9px Consolas,monospace}.section-disclosure{max-width:1320px;margin:18px auto 0;padding:11px 13px;background:#dfe5e9;border-left:4px solid #173954;color:#52636f;font-size:10px}
dialog{width:min(680px,calc(100% - 24px));padding:0;border:1px solid #37616c;border-radius:16px;background:#071820;color:var(--ink);box-shadow:0 30px 90px #000c}dialog::backdrop{background:#010609cc;backdrop-filter:blur(4px)}.modal{padding:19px}.modal-head{display:flex;justify-content:space-between}.modal h2{margin:4px 0 12px;font:500 28px Georgia,serif}.close{border:0;background:none;color:var(--muted);font-size:24px;cursor:pointer}.fact{display:grid;grid-template-columns:160px 1fr;gap:10px;padding:9px 0;border-top:1px solid var(--line)}.fact span{color:var(--muted)}.disclosure{padding:11px;border-left:3px solid var(--amber);background:#2e250f55;color:#dec991;margin-top:12px}.case-grid{display:grid;gap:8px}.case-card{display:grid;grid-template-columns:1fr auto;gap:8px 16px;padding:12px;border:1px solid var(--line);border-radius:11px;background:#06171d}.case-card small{display:block;color:var(--cyan);font-size:8px;font-weight:900;letter-spacing:.12em}.case-card strong{display:block;margin:3px 0}.case-card p{grid-column:1;margin:0;color:var(--muted);font-size:10px}.case-card .case-metric{text-align:right;color:var(--amber);font-weight:900}.case-card a,.case-card button,.scorecard-link{align-self:end;border:1px solid #31515d;border-radius:7px;background:#09232b;color:var(--ink);padding:7px 9px;text-decoration:none;cursor:pointer;font-size:9px;font-weight:800}.scorecard-link{display:block;margin-top:10px;text-align:center}.spark{width:100%;height:150px;background:#05141a;border-radius:10px}.hidden{display:none!important}
@media(max-width:900px){.app{height:auto;min-height:100vh;grid-template-rows:auto auto auto}header{padding:10px;flex-wrap:wrap}.workspace{grid-template-columns:1fr}.map-card{height:58vh}.side{max-height:none}.bottom{grid-template-columns:1fr}.weather{min-height:70px}.server{display:none}.investigation{padding:42px 14px}.investigation-head{align-items:start}.investigation-grid{grid-template-columns:1fr}.work-card.wide{grid-column:auto}.lab-controls,.lab-result,.work-actions{grid-template-columns:1fr}.comparison{grid-template-columns:1fr}.versus{height:20px}}
</style></head><body><div class="app" id="operations"><header><div class="brand"><div class="mark">E</div>ESPADA</div><div class="case"><b id="caseName"></b><span id="caseSub">Sealed evidence replay</span></div><div class="badge" id="modeBadge"></div><div class="spacer"></div><span class="server" id="server">SAVED EVIDENCE MODE</span><button class="soft" id="caseLibrary">Validation · 3 cases</button><button class="soft" id="workspaceJump">Analysis workspace ↓</button><button class="soft" id="details">Case details</button><button class="primary" id="run">▶ Run attribution</button></header>
<main class="workspace"><section class="map-card" id="mapCard"><img class="sar" id="sar" alt="Real Sentinel-1 context"><div class="map-shade"></div><svg class="overlay" id="overlay" viewBox="0 0 1000 650" preserveAspectRatio="none"><defs><marker id="routeArrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#ffbd59"/></marker><linearGradient id="oilSurface" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#020304"/><stop offset=".52" stop-color="#11191b"/><stop offset="1" stop-color="#000102"/></linearGradient><filter id="oilFeather" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3.2"/></filter><filter id="oilTexture" x="-10%" y="-10%" width="120%" height="120%"><feTurbulence type="fractalNoise" baseFrequency=".035" numOctaves="2" seed="26143" result="noise"/><feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2"/><feGaussianBlur stdDeviation=".35"/></filter></defs><g id="grid"></g><g id="slickLayer"></g><g id="detectionLayer"></g><g id="particleLayer"></g><g id="releaseLayer"></g><g id="trailLayer"></g><g id="trackLayer"></g><g id="leakLayer"></g><g id="shipLayer"></g><g id="clusterLayer"></g></svg><div class="map-tools"><button class="tool" id="layers">☷ Layers</button><button class="tool" id="satellite" aria-pressed="false">◐ Raw SAR</button><button class="tool" id="alignment">✓ Alignment</button><button class="tool" id="reset">↺ Reset</button></div><div class="popover" id="layerMenu"><label><input type="checkbox" data-layer="slick" checked> Simulated oil return</label><label><input type="checkbox" data-layer="detection" checked> Detection boundary</label><label><input type="checkbox" data-layer="reverse" checked> Reverse drift</label><label><input type="checkbox" data-layer="ships" checked> Relevant ships</label><label><input type="checkbox" data-layer="background"> All background traffic</label><label><input type="checkbox" data-layer="trails" checked> Recent AIS trails</label></div><div class="map-status"><b id="mapStatus">PRE-RELEASE · NO SLICK</b><span id="mapSub">Press play to replay the event, or run the analysis</span></div><div class="coverage-live"><b id="reportingCount">0 reporting</b> · <span id="missingCount">0 without a current AIS fix</span></div><div class="legend"><span><i class="dot" style="background:#52e2dc"></i>Reported ship</span><span><i class="dot" style="background:#050709;border:1px solid #ffbd59"></i>Controlled oil return</span><span><i class="dot" style="background:#72f1e6"></i>Probable origin</span></div><div class="tooltip" id="tooltip"></div><div class="phase" id="phase"><div>01 · Observe</div><div>02 · Reverse</div><div>03 · Rank</div><div>04 · Verify</div></div></section>
<aside class="side"><div class="side-head"><div><div class="eyebrow">INVESTIGATION SHORTLIST</div><h2>Candidate vessels</h2></div><div class="count"><span id="candidateCount"></span><br>Top 3 after analysis</div></div><div class="decision-summary" id="decisionSummary"><small>OPERATIONAL DECISION</small><b id="decisionState">AWAITING ANALYSIS</b><span id="decisionReason">No vessel has been nominated.</span></div><div class="side-scroll"><div class="candidates" id="candidateList"></div><button class="more hidden" id="more">Show more candidates</button><div class="detail" id="detail"><div class="detail-placeholder">Run the analysis to create a reviewable shortlist.</div></div></div></aside></main>
<footer class="bottom"><section class="timeline"><button class="play" id="play" aria-label="Play timeline">▶</button><div><div class="range-wrap"><i class="event-mark" id="leakMark"><span>LEAK BEGINS</span></i><i class="event-mark obs" id="obsMark"><span>SAR OBSERVATION</span></i><input id="time" type="range" min="0" max="1000" value="0"></div><div class="range-labels"><span id="startTime"></span><span>Actual AIS timing · gaps remain gaps</span><span id="endTime"></span></div></div><div class="time-readout"><b id="eventState">PRE-RELEASE</b><span class="timebox" id="timeBox"></span></div></section><section class="weather"><button data-env="wind"><span>Wind</span><b><i class="arrow" id="windArrow">→</i><span id="windValue" style="display:inline"></span></b></button><button data-env="current"><span>Surface current</span><b><i class="arrow" id="currentArrow">→</i><span id="currentValue" style="display:inline"></span></b></button><button data-env="coverage"><span>AIS reporting now</span><b id="coverage">0 / 0</b></button></section></footer></div>
<section class="investigation" id="investigation"><div class="investigation-head"><div><div class="eyebrow">ANALYSIS & VALIDATION</div><h2>Investigation workspace</h2><p>The operational view stays simple. Detailed uncertainty, evidence checks and sensitivity testing are kept here for analysts.</p></div><a class="back-top" href="#operations">Return to incident map ↑</a></div><div class="investigation-grid">
<article class="work-card"><div class="work-kicker">ORIGIN UNCERTAINTY</div><h3>Probable release zone</h3><p>An ensemble produces an area and time window—not a single certain release point.</p><div class="uncertainty-visual"><div class="uncertainty-ring r90"></div><div class="uncertainty-ring r50"></div><div class="origin-pin"></div></div><div class="uncertainty-values"><div class="work-row"><span>50% particle radius</span><b id="radius50">—</b></div><div class="work-row"><span>90% particle radius</span><b id="radius90">—</b></div><div class="work-row"><span>Supported time window</span><b id="releaseWindow">—</b></div></div></article>
<article class="work-card"><div class="work-kicker">EVIDENCE STRENGTH</div><h3>Four checks, one safe decision</h3><p>No single percentage is presented as guilt. Independent evidence dimensions remain visible.</p><div class="strength-list"><div class="strength"><span>Origin presence</span><div class="strength-track"><i id="strengthPresence"></i></div><b id="strengthPresenceValue">—</b></div><div class="strength"><span>Forward agreement</span><div class="strength-track"><i id="strengthForward"></i></div><b id="strengthForwardValue">—</b></div><div class="strength"><span>AIS track quality</span><div class="strength-track"><i id="strengthQuality"></i></div><b id="strengthQualityValue">—</b></div><div class="strength warn"><span>Slick input confidence</span><div class="strength-track"><i id="strengthSar"></i></div><b id="strengthSarValue">—</b></div></div><div class="decision-strip" id="workspaceDecision">Run attribution to populate the evidence checks.</div></article>
<article class="work-card"><div class="work-kicker">CANDIDATE COMPARISON</div><h3>Why not the other vessel?</h3><p>The leading explanations are compared directly so the ranking can be challenged.</p><div class="comparison" id="comparisonPanel"><div class="detail-placeholder">Run attribution to compare the leading candidates.</div></div></article>
<article class="work-card wide"><div class="work-kicker">SCENARIO LAB · 45 SAVED PHYSICS RERUNS</div><h3>Test whether the answer survives changed assumptions</h3><p>The recovered 12-hour case is the baseline. Move any control to inspect a previously computed blind stress scenario.</p><div class="lab-controls"><div class="lab-control"><label><span>Slick age</span><b id="ageLabel">—</b></label><input id="labAge" type="range" min="0" max="4" step="1" value="2"></div><div class="lab-control"><label><span>Current multiplier</span><b id="currentLabel">—</b></label><input id="labCurrent" type="range" min="0" max="2" step="1" value="1"></div><div class="lab-control"><label><span>Windage</span><b id="windageLabel">—</b></label><input id="labWindage" type="range" min="0" max="2" step="1" value="1"></div></div><div class="lab-result"><div><span>Leading candidate</span><b id="labLeader">—</b></div><div><span>Known source rank</span><b id="labRank">—</b></div><div><span>Source score</span><b id="labScore">—</b></div><div><span>Forward error</span><b id="labError">—</b></div></div></article>
<article class="work-card"><div class="work-kicker">INVESTIGATION ACTIONS</div><h3>Move from result to review</h3><p>Actions are deliberately human-controlled; ESPADA does not automatically accuse a vessel.</p><div class="work-actions"><button class="work-action primary-action" id="markReview">Mark for review</button><button class="work-action" id="requestEvidence">Evidence needed</button><a class="work-action amber-action" id="openDossier" target="_blank">Open dossier</a></div><div class="action-status" id="actionStatus">No analyst action recorded in this browser session.</div></article>
<article class="work-card wide"><div class="work-kicker">DATA PROVENANCE</div><h3>Inputs used in this replay</h3><p>Every source is labelled as observed, modelled, historical or controlled.</p><div class="source-list"><div class="source-item"><i class="source-state"></i><span>Satellite context</span><b>Sentinel-1 VV · observed</b><time class="source-time" id="sourceSarTime"></time></div><div class="source-item"><i class="source-state"></i><span>Surface currents</span><b>Copernicus Marine · modelled</b><time class="source-time">cached for case</time></div><div class="source-item"><i class="source-state"></i><span>Wind</span><b>Open-Meteo · historical model</b><time class="source-time">cached for case</time></div><div class="source-item"><i class="source-state"></i><span>Vessel motion</span><b>Global Fishing Watch · historical AIS</b><time class="source-time">pseudonymized</time></div><div class="source-item"><i class="source-state" style="background:#d29127"></i><span>Slick observation</span><b>Controlled digital twin</b><time class="source-time">sealed ground truth</time></div></div></article>
</div><div class="section-disclosure">Decision support only. Candidate scores compare explanations inside this case; they are not guilt probabilities, identity findings or legal conclusions.</div></section>
<dialog id="modal"><div class="modal"><div class="modal-head"><div><div class="eyebrow" id="modalEyebrow">CASE DETAILS</div><h2 id="modalTitle"></h2></div><button class="close" id="close">×</button></div><div id="modalBody"></div></div></dialog>
<script>const DATA=__DATA__;
const $=id=>document.getElementById(id),bbox=DATA.bbox,W=1000,H=650;let selected=null,showCount=3,playing=false,ran=false,analysisComplete=false,analysisRunning=false,detectionAvailable=false,rawMode=false,engine=false,runTimer=[],lastSlickFrame=-2;const minT=Date.parse(DATA.case.playbackStart),maxT=Date.parse(DATA.case.playbackEnd),releaseT=Date.parse(DATA.case.releaseTime),truthReleaseT=Date.parse(DATA.case.truthReleaseTime),releaseEndT=truthReleaseT+Number(DATA.case.releaseDurationMinutes||0)*60000,obsT=Date.parse(DATA.case.observationTime);
function pct(v){return v==null?'N/A':(v*100).toFixed(1)+'%'}function num(v,d=1){return v==null?'N/A':Number(v).toFixed(d)}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function point(lon,lat){return{x:(lon-bbox[0])/(bbox[2]-bbox[0])*W,y:(bbox[3]-lat)/(bbox[3]-bbox[1])*H}}function geoPaths(g){const polys=g.type==='Polygon'?[g.coordinates]:g.coordinates;return polys.map(poly=>poly.map(ring=>ring.map((c,i)=>{const p=point(c[0],c[1]);return(i?'L':'M')+p.x.toFixed(1)+' '+p.y.toFixed(1)}).join(' ')+' Z').join(' '))}
function init(){ $('caseName').textContent=DATA.case.name;$('caseSub').textContent=DATA.case.mode==='CONTROLLED DIGITAL TWIN'?'Real forcing · real AIS motion · controlled slick':'Historical evidence replay';$('modeBadge').textContent=DATA.case.mode;$('sar').src=DATA.satelliteImage;$('candidateCount').textContent=DATA.case.candidateCount+' compared';$('startTime').textContent=stamp(minT);$('endTime').textContent=stamp(maxT);$('leakMark').style.left=((truthReleaseT-minT)/(maxT-minT)*100)+'%';$('obsMark').style.left=((obsT-minT)/(maxT-minT)*100)+'%';grid();slick();particles();ships();renderCandidates();setTime(minT);initWorkspace();health()}
function grid(){let s='';for(let i=1;i<6;i++)s+=`<line class="gridline" x1="${i*W/6}" y1="0" x2="${i*W/6}" y2="${H}"/><line class="gridline" x1="0" y1="${i*H/6}" x2="${W}" y2="${i*H/6}"/>`;$('grid').innerHTML=s}
function slick(){const finalPaths=geoPaths(DATA.slick.features[0].geometry);$('detectionLayer').innerHTML=finalPaths.map(d=>`<path class="detection" d="${d}"/>`).join('');setSlickTime(minT,true)}
function layerOn(name){const control=document.querySelector(`[data-layer="${name}"]`);return !control||control.checked}
function setSlickTime(t,force=false){let frameIndex=-1;for(let i=0;i<DATA.slickTimeline.length;i++){if(Date.parse(DATA.slickTimeline[i].time)<=t)frameIndex=i}if(force||frameIndex!==lastSlickFrame){lastSlickFrame=frameIndex;const frame=frameIndex>=0?DATA.slickTimeline[frameIndex]:null;if(!rawMode&&layerOn('slick')&&t>=truthReleaseT){if(frame&&frame.geometry){$('slickLayer').innerHTML=geoPaths(frame.geometry).map(d=>`<path class="oil-halo" d="${d}"/><path class="oil-return" d="${d}"/>`).join('')}else{const p=point(DATA.truthRelease.longitude,DATA.truthRelease.latitude);$('slickLayer').innerHTML=`<circle class="oil-halo" cx="${p.x}" cy="${p.y}" r="5"/><circle class="oil-return" cx="${p.x}" cy="${p.y}" r="3"/>`}}else $('slickLayer').innerHTML=''}const detectionOn=!rawMode&&layerOn('detection')&&detectionAvailable&&t>=obsT;document.querySelectorAll('.detection').forEach(x=>x.classList.toggle('show',detectionOn));$('leakLayer').innerHTML='';if(!rawMode&&analysisComplete&&t>=truthReleaseT&&t<=releaseEndT){const source=DATA.tracks.find(track=>track.knownSource),pos=source?positionAt(source,t):null,p=pos?point(pos.lon,pos.lat):point(DATA.truthRelease.longitude,DATA.truthRelease.latitude);$('leakLayer').innerHTML=`<circle class="leak-pulse" cx="${p.x}" cy="${p.y}" r="8"/>`}updateEvent(t)}
function updateEvent(t){let state='PRE-RELEASE',status='PRE-RELEASE · NO SLICK',sub='AIS traffic only; no oil exists yet';if(t>=truthReleaseT&&t<releaseEndT){state='RELEASE IN PROGRESS';status='CONTROLLED RELEASE ACTIVE';sub='Oil is entering the water from the moving source track'}else if(t>=releaseEndT&&t<obsT){state='SLICK DRIFTING';status='SLICK ADVECTING';sub='Particle plume follows currents, windage and diffusion'}else if(t>=obsT){state='SAR OBSERVATION';status=rawMode?'RAW SAR · NO OIL CLAIM':'SIMULATED SAR OIL RETURN';sub=rawMode?'Unaltered Sentinel-1 context':'Physics-generated oil return, georegistered to the SAR crop'}$('eventState').textContent=state;if(!analysisRunning){$('mapStatus').textContent=status;$('mapSub').textContent=sub}}
function particles(){const c=centroid();$('particleLayer').innerHTML=DATA.reverse.map((v,i)=>{const e=point(v[0],v[1]);return`<circle class="particle" cx="${c.x}" cy="${c.y}" r="${1.5+(i%3)*.35}" data-x="${e.x}" data-y="${e.y}"/>`}).join('');const r=point(DATA.release.longitude,DATA.release.latitude);const px=Math.max(16,DATA.release.radius90/90*W/(bbox[2]-bbox[0]));$('releaseLayer').innerHTML=`<circle class="release" cx="${r.x}" cy="${r.y}" r="${Math.min(95,px)}"/><circle class="release" cx="${r.x}" cy="${r.y}" r="5"/>`}
function centroid(){let x=0,y=0,n=0;const g=DATA.slick.features[0].geometry,polys=g.type==='Polygon'?[g.coordinates]:g.coordinates;polys.forEach(p=>p[0].forEach(c=>{const q=point(c[0],c[1]);x+=q.x;y+=q.y;n++}));return{x:x/n,y:y/n}}
const boat='<path class="hull" d="M0 -13 C3.8 -10.5 5.8 -5 5.8 6.8 L3 11 L-3 11 L-5.8 6.8 L-5.8 -5 C-5.8 -9 -3.2 -11.6 0 -13 Z"/><rect class="deck" x="-3.6" y="-1" width="7.2" height="6.6" rx="1"/><rect class="bridge" x="-2.5" y="2" width="5" height="3" rx=".7"/><line class="keel" x1="0" y1="-9" x2="0" y2="8"/>';
function ships(){ $('shipLayer').innerHTML=DATA.tracks.map(t=>`<g class="ship" data-id="${t.mmsi}" tabindex="0" role="button" aria-label="${esc(t.name)}">${boat}</g>`).join('');document.querySelectorAll('.ship').forEach(el=>{el.addEventListener('mouseenter',e=>tip(e,el.dataset.id));el.addEventListener('mousemove',moveTip);el.addEventListener('mouseleave',()=> $('tooltip').classList.remove('show'));el.addEventListener('click',()=>select(el.dataset.id));el.addEventListener('keydown',e=>{if(e.key==='Enter')select(el.dataset.id)})})}
function positionAt(track,t){const p=track.points,q=p.map(x=>Date.parse(x.t));if(t<q[0]||t>q[q.length-1])return null;let i=q.findIndex(v=>v>=t);if(i<0)return null;if(i===0)return{...p[0],h:0,reported:true};const a=p[i-1],b=p[i],ta=q[i-1],tb=q[i];if(tb-ta>10800000)return null;const f=Math.max(0,Math.min(1,(t-ta)/(tb-ta))),dx=b.lon-a.lon,dy=b.lat-a.lat;return{lon:a.lon+dx*f,lat:a.lat+dy*f,h:Math.atan2(dx,-dy)*180/Math.PI,reported:Math.min(t-ta,tb-t)<=2100000}}
function trackSegments(track,t,windowMs=null){const points=track.points.filter(p=>Date.parse(p.t)<=t&&(!windowMs||Date.parse(p.t)>=t-windowMs)),segments=[];let segment=[];points.forEach(p=>{if(segment.length&&Date.parse(p.t)-Date.parse(segment[segment.length-1].t)>10800000){if(segment.length>1)segments.push(segment);segment=[]}segment.push(p)});if(segment.length>1)segments.push(segment);return segments}
function mapRelevant(track){return layerOn('background')||track.knownSource||(track.rank&&track.rank<=5)}
function drawTrafficTrails(t){if(!layerOn('trails')){$('trailLayer').innerHTML='';return}$('trailLayer').innerHTML=DATA.tracks.filter(mapRelevant).flatMap(track=>trackSegments(track,t,21600000).map(segment=>{const pts=segment.map(p=>point(p.lon,p.lat));return`<polyline class="traffic-trail ${analysisComplete&&track.rank===1?'top':''}" points="${pts.map(p=>p.x+','+p.y).join(' ')}"/>`})).join('')}
function clusterShips(states){const groups=[];states.forEach(state=>{let group=groups.find(g=>g.some(other=>Math.hypot(other.p.x-state.p.x,other.p.y-state.p.y)<15));if(group)group.push(state);else groups.push([state])});$('clusterLayer').innerHTML='';groups.forEach(group=>{if(group.length<2)return;group.forEach(state=>state.el.classList.add('hidden'));const x=group.reduce((s,v)=>s+v.p.x,0)/group.length,y=group.reduce((s,v)=>s+v.p.y,0)/group.length,names=group.map(v=>v.track.name);$('clusterLayer').insertAdjacentHTML('beforeend',`<g class="ship-cluster" transform="translate(${x} ${y})" data-ids="${group.map(v=>v.track.mmsi).join(',')}" tabindex="0" role="button" aria-label="${esc(names.join(', '))}"><circle r="11"/><text y=".5">${group.length}</text><title>${esc(names.join(' · '))}</title></g>`)});document.querySelectorAll('.ship-cluster').forEach(el=>{const open=()=>{const vessels=el.dataset.ids.split(',').map(id=>DATA.tracks.find(t=>t.mmsi===id));modal('Co-located AIS reports',vessels.map(t=>`<div class="fact"><span>${esc(t.vesselType)}</span><b>${esc(t.name)}</b></div>`).join('')+'<div class="disclosure">These vessels occupy the same coarse historical AIS grid cell. ESPADA clusters them instead of inventing false separation.</div>','AIS POSITION CLUSTER')};el.onclick=open;el.onkeydown=e=>{if(e.key==='Enter')open()}})}
function setTime(t){const val=Math.max(minT,Math.min(maxT,t));$('time').value=((val-minT)/(maxT-minT)*1000).toFixed(0);$('timeBox').textContent=new Date(val).toISOString().slice(0,16).replace('T',' ')+' UTC';let reporting=0;const visible=[];DATA.tracks.forEach(track=>{const el=document.querySelector(`.ship[data-id="${track.mmsi}"]`),pos=positionAt(track,val),wasHidden=el.classList.contains('hidden');if(pos)reporting++;if(!pos||!mapRelevant(track)){el.classList.add('hidden');return}el.classList.remove('hidden');if(wasHidden){el.classList.add('acquired');setTimeout(()=>el.classList.remove('acquired'),850)}const p=point(pos.lon,pos.lat);el.setAttribute('transform',`translate(${p.x} ${p.y}) rotate(${pos.h}) scale(${analysisComplete&&track.rank&&track.rank<=3?1.05:.78})`);visible.push({track,el,pos,p})});clusterShips(visible);$('reportingCount').textContent=reporting+' reporting';$('missingCount').textContent=(DATA.tracks.length-reporting)+' in AIS gaps';$('coverage').textContent=reporting+' / '+DATA.tracks.length;drawTrafficTrails(val);environment(val);setSlickTime(val);if(selected)drawTrack(DATA.tracks.find(t=>t.mmsi===selected),val)}
function tip(e,id){const t=DATA.tracks.find(x=>x.mmsi===id),box=$('tooltip'),current=minT+Number($('time').value)/1000*(maxT-minT),live=Boolean(positionAt(t,current));box.innerHTML=analysisComplete?`<b>${esc(t.name)}</b><span>${esc(t.vesselType)} · ${esc(t.flag)}</span><span>${live?'AIS reporting now':'No current AIS fix'} · ${num(t.medianSpeedKnots,1)} kn median</span><span>${t.rank?'Rank #'+t.rank+' · <em>'+pct(t.score)+'</em> comparative evidence':'Background traffic'}</span><span>Click for full evidence</span>`:`<b>${esc(t.name)}</b><span>${esc(t.vesselType)} · ${esc(t.flag)}</span><span>${live?'AIS reporting now':'No current AIS fix'}</span><span>Scenario alias on a real historical motion track</span>`;box.classList.add('show');moveTip(e)}function moveTip(e){const card=$('mapCard').getBoundingClientRect(),box=$('tooltip');box.style.left=Math.min(card.width-205,e.clientX-card.left+12)+'px';box.style.top=Math.min(card.height-125,e.clientY-card.top+12)+'px'}
function stamp(value){return new Date(value).toISOString().slice(5,16).replace('T',' ')+' UTC'}
function profile(t){return`<div class="profile-line"><span>${esc(t.vesselType)}</span><span>Flag ${esc(t.flag)}</span><span>${num(t.lengthM,0)} × ${num(t.beamM,0)} m</span><span>Draught ${num(t.draughtM,1)} m</span><span>${num(t.medianSpeedKnots,1)} kn median</span></div><div class="fact"><span>Motion state</span><b>${esc(t.motionStatus)}</b></div><div class="fact"><span>Observed route span</span><b>${num(t.routeSpanKm,1)} km</b></div><div class="fact"><span>AIS reports retained</span><b>${t.reportCount}</b></div><div class="fact"><span>Observed window</span><b>${stamp(t.coverageStart)} — ${stamp(t.coverageEnd)}</b></div><div class="notice">${esc(t.identityStatus)}. The alias is not a claim about the real vessel's identity.</div>`}
function renderCandidates(){if(!analysisComplete){$('candidateList').innerHTML='<div class="detail-placeholder">No ranking yet. Run the analysis to compare every vessel.</div>';$('more').classList.add('hidden');return}const list=DATA.candidates.slice(0,showCount);$('candidateList').innerHTML=list.map(t=>`<button class="candidate ${selected===t.mmsi?'active':''}" data-id="${t.mmsi}"><span class="rank">${t.rank}</span><span><b>${esc(t.name)}</b><small>${esc(t.vesselType)} · ${num(t.forwardError,2)} km replay</small></span><span class="score">${pct(t.score)}</span></button>`).join('');document.querySelectorAll('.candidate').forEach(b=>b.onclick=()=>select(b.dataset.id));$('more').classList.remove('hidden');$('more').textContent=showCount===3?'Show more candidates':'Show Top 3 only'}
function select(id){selected=id;const t=DATA.tracks.find(x=>x.mmsi===id),current=minT+Number($('time').value)/1000*(maxT-minT);document.querySelectorAll('.ship').forEach(s=>{s.classList.toggle('selected',s.dataset.id===id);s.classList.toggle('dim',s.dataset.id!==id)});drawTrack(t,current);if(!analysisComplete){$('detail').innerHTML=`<div class="detail-top"><div><div class="eyebrow">TRACKED VESSEL · ${esc(t.registry)}</div><h3>${esc(t.name)}</h3></div></div>${profile(t)}<div class="notice">No attribution score exists until the analysis runs.</div>`;return}renderCandidates();$('detail').innerHTML=`<div class="detail-top"><div><div class="eyebrow">${t.knownSource?'SEALED SOURCE · RANK #'+t.rank:'RANK #'+t.rank} · ${esc(t.registry)}</div><h3>${esc(t.name)}</h3></div><div class="score">${pct(t.score)}</div></div>${profile(t)}<div class="metrics"><div class="metric"><span>Origin presence</span><b>${pct(t.presence)}</b><div class="bar"><i style="width:${(t.presence||0)*100}%"></i></div></div><div class="metric"><span>Forward agreement</span><b>${pct(t.forward)}</b><div class="bar"><i style="width:${(t.forward||0)*100}%"></i></div></div><div class="metric"><span>Track quality</span><b>${pct(t.quality)}</b></div><div class="metric"><span>AIS gap</span><b>${esc((t.silence||'not evaluated').replaceAll('_',' '))}</b></div></div><div class="detail-actions"><button id="replayVessel">Replay event</button><button id="why">Why ranked?</button><a href="${DATA.dossier}" target="_blank">Dossier ↗</a></div><div class="notice">Comparative evidence supports analyst review only. It is not a guilt probability.</div>`;$('replayVessel').onclick=()=>{setTime(minT);if(!playing)togglePlay()};$('why').onclick=()=>why(t)}
function drawTrack(t,current=maxT){if(!t){$('trackLayer').innerHTML='';return}const segments=trackSegments(t,current,null),lines=segments.map(segment=>{const pts=segment.map(p=>point(p.lon,p.lat));return`<polyline class="track ${t.rank===1?'top':''}" marker-end="url(#routeArrow)" points="${pts.map(p=>p.x+','+p.y).join(' ')}"/>`}).join(''),dots=t.points.filter(p=>Date.parse(p.t)<=current).map(p=>{const q=point(p.lon,p.lat);return`<circle class="report-dot" cx="${q.x}" cy="${q.y}" r="2"/>`}).join('');$('trackLayer').innerHTML=lines+dots}
function environment(t){const e=DATA.environment.reduce((a,b)=>Math.abs(Date.parse(b.time_utc)-t)<Math.abs(Date.parse(a.time_utc)-t)?b:a);vector('wind',e.wind_east_ms,e.wind_north_ms,'m/s');vector('current',e.current_east_ms,e.current_north_ms,'m/s')}
function vector(name,e,n,unit){const speed=Math.hypot(e,n),deg=Math.atan2(e,-n)*180/Math.PI;$(name+'Arrow').style.transform=`rotate(${deg}deg)`;$(name+'Value').textContent=speed.toFixed(name==='wind'?1:2)+' '+unit}
function setStrength(id,value){const safe=value==null?0:Math.max(0,Math.min(1,Number(value)));$(id).style.width=(safe*100)+'%';$(id+'Value').textContent=value==null?'—':pct(value)}
function renderWorkspace(ready){const first=DATA.candidates[0],second=DATA.candidates[1];if(!ready||!first){['strengthPresence','strengthForward','strengthQuality','strengthSar'].forEach(id=>setStrength(id,null));$('workspaceDecision').textContent='Run attribution to populate the evidence checks.';$('comparisonPanel').innerHTML='<div class="detail-placeholder">Run attribution to compare the leading candidates.</div>';return}setStrength('strengthPresence',first.presence);setStrength('strengthForward',first.forward);setStrength('strengthQuality',first.quality);setStrength('strengthSar',(DATA.slickAnalysis.input||{}).detection_confidence);const abstain=DATA.case.decision.startsWith('ABSTAIN');$('workspaceDecision').textContent=abstain?'ABSTAIN — ranking retained, automatic nomination blocked.':'REVIEW — evidence gates permit human analyst review.';if(!second){$('comparisonPanel').innerHTML='<div class="detail-placeholder">Only one candidate is available.</div>';return}const qualityGap=(first.quality||0)-(second.quality||0),errorGap=(second.forwardError||0)-(first.forwardError||0),reason=qualityGap>0.03?`${esc(first.name)} has ${(qualityGap*100).toFixed(1)} points better AIS track quality.`:errorGap>0.05?`${esc(first.name)} reproduces the slick with ${errorGap.toFixed(2)} km less centroid error.`:'The candidates are too close for automatic nomination.';$('comparisonPanel').innerHTML=`<div class="compare-vessel"><strong>#1 ${esc(first.name)}</strong><small>${esc(first.vesselType)}</small><dl><dt>Comparative score</dt><dd>${pct(first.score)}</dd><dt>Forward error</dt><dd>${num(first.forwardError,2)} km</dd><dt>Track quality</dt><dd>${pct(first.quality)}</dd></dl></div><div class="versus">VS</div><div class="compare-vessel"><strong>#2 ${esc(second.name)}</strong><small>${esc(second.vesselType)}</small><dl><dt>Comparative score</dt><dd>${pct(second.score)}</dd><dt>Forward error</dt><dd>${num(second.forwardError,2)} km</dd><dt>Track quality</dt><dd>${pct(second.quality)}</dd></dl></div><div class="reason" style="grid-column:1/-1">${reason}</div>`}
function initWorkspace(){const search=DATA.timeSearch||{},window=search.estimated_release_window_utc||[];$('radius50').textContent=num(DATA.release.radius50,2)+' km';$('radius90').textContent=num(DATA.release.radius90,2)+' km';$('releaseWindow').textContent=window.length===2?stamp(window[0])+' — '+stamp(window[1]):'Not available';$('sourceSarTime').textContent=stamp(DATA.satellite.acquisition_time_utc);$('openDossier').href=DATA.dossier;$('workspaceJump').onclick=()=> $('investigation').scrollIntoView({behavior:'smooth'});$('markReview').onclick=()=>{$('actionStatus').textContent='Marked for analyst review in this browser session.';$('markReview').textContent='✓ Review marked'};$('requestEvidence').onclick=()=>modal('Additional evidence required',`<div class="fact"><span>Satellite</span><b>Request a second SAR or optical observation after the slick event</b></div><div class="fact"><span>AIS</span><b>Obtain terrestrial or commercial AIS around reported gaps</b></div><div class="fact"><span>Independent records</span><b>Check port calls, registry, logbook and spill reports</b></div><div class="disclosure">These checks strengthen or reject a hypothesis. Missing evidence must never be interpreted as guilt.</div>`,'FOLLOW-UP CHECKLIST');['labAge','labCurrent','labWindage'].forEach(id=>$(id).oninput=updateLab);renderWorkspace(false);updateLab()}
function updateLab(){const rows=DATA.scenarioLab||[];if(!rows.length){$('labLeader').textContent='Not available';return}const ages=[...new Set(rows.map(x=>x.ageHours))].sort((a,b)=>a-b),currents=[...new Set(rows.map(x=>x.currentMultiplier))].sort((a,b)=>a-b),windages=[...new Set(rows.map(x=>x.windage))].sort((a,b)=>a-b);$('labAge').max=ages.length-1;$('labCurrent').max=currents.length-1;$('labWindage').max=windages.length-1;const age=ages[Math.min(Number($('labAge').value),ages.length-1)],current=currents[Math.min(Number($('labCurrent').value),currents.length-1)],windage=windages[Math.min(Number($('labWindage').value),windages.length-1)];$('ageLabel').textContent=age+' h';$('currentLabel').textContent=current.toFixed(2)+'×';$('windageLabel').textContent=(windage*100).toFixed(1)+'%';if(!analysisComplete){$('labLeader').textContent='Locked';$('labRank').textContent='Run attribution';$('labScore').textContent='—';$('labError').textContent='—';return}const row=rows.find(x=>x.ageHours===age&&x.currentMultiplier===current&&x.windage===windage);if(!row)return;$('labLeader').textContent=row.leaderName;$('labRank').textContent='#'+row.sourceRank+' / '+DATA.case.candidateCount;$('labScore').textContent=pct(row.sourceScore);$('labError').textContent=num(row.sourceForwardError,2)+' km'}
function togglePlay(){playing=!playing;$('play').textContent=playing?'❚❚':'▶';if(playing&&Number($('time').value)>=999)setTime(minT);let last=performance.now();function frame(now){if(!playing)return;let t=minT+Number($('time').value)/1000*(maxT-minT);t+=(maxT-minT)*(now-last)/14000;last=now;if(t>=maxT){t=maxT;playing=false;$('play').textContent='▶'}setTime(t);if(playing)requestAnimationFrame(frame)}if(playing)requestAnimationFrame(frame)}
function animateParticles(duration=2400){const start=performance.now(),nodes=[...document.querySelectorAll('.particle')];nodes.forEach(n=>n.classList.add('show'));function f(now){const p=Math.min(1,(now-start)/duration),ease=1-Math.pow(1-p,3);nodes.forEach(n=>{const sx=Number(n.getAttribute('cx')),sy=Number(n.getAttribute('cy')),ex=Number(n.dataset.x),ey=Number(n.dataset.y);n.setAttribute('transform',`translate(${(ex-sx)*ease} ${(ey-sy)*ease})`)});if(p<1)requestAnimationFrame(f)}requestAnimationFrame(f)}
function phase(i,state='on'){const nodes=[...$('phase').children];nodes.forEach((n,j)=>{n.className=j<i?'done':j===i?state:''})}
async function run(){
if(ran)resetRun();ran=true;analysisComplete=false;analysisRunning=true;detectionAvailable=true;selected=null;renderCandidates();$('trackLayer').innerHTML='';$('run').disabled=true;$('phase').classList.add('show');$('decisionSummary').classList.remove('abstain');$('decisionState').textContent='ANALYSIS RUNNING';$('decisionReason').textContent='Comparing drift, timing, shape and AIS quality.';phase(0);setTime(obsT);$('mapStatus').textContent='SAR SLICK OBSERVED';$('mapSub').textContent='Detection boundary locked to the controlled observation';
let result=null;try{if(engine){const r=await fetch('/api/run-operations',{method:'POST'});if(!r.ok)throw new Error('engine response');result=await r.json()}}catch(e){result=null}
runTimer.push(setTimeout(()=>{phase(1);$('mapStatus').textContent='REVERSING DRIFT';$('mapSub').textContent='Particle ensemble searches backwards through real forcing';animateParticles();setTime(obsT)},700));
runTimer.push(setTimeout(()=>{phase(2);document.querySelectorAll('.release').forEach(x=>x.classList.add('show'));$('mapStatus').textContent='MATCHING AIS TRACKS';$('mapSub').textContent='Only vessels present in the probable origin field gain evidence';setTime(releaseT)},3200));
runTimer.push(setTimeout(()=>{phase(3);$('mapStatus').textContent='FORWARD VERIFYING';$('mapSub').textContent='Candidate releases are replayed towards the observed slick';setTime(obsT)},4100));
runTimer.push(setTimeout(()=>{phase(4,'done');analysisRunning=false;analysisComplete=true;const abstain=DATA.case.decision.startsWith('ABSTAIN'),label=abstain?'ABSTAIN · INSUFFICIENT EVIDENCE':DATA.case.decision.replaceAll('_',' ');$('mapStatus').textContent=abstain?'RANKED · NO AUTOMATIC NOMINATION':label;$('mapSub').textContent=(result?'Fresh local ranking':'Saved evidence replay')+' · automatic event replay starting';$('decisionState').textContent=label;$('decisionReason').textContent=abstain?'The shortlist is visible, but evidence gates prevent escalation.':'Evidence gates permit human analyst review.';$('decisionSummary').classList.toggle('abstain',abstain);document.querySelectorAll('.ship').forEach(s=>{const t=DATA.tracks.find(x=>x.mmsi===s.dataset.id);s.classList.toggle('top',t&&t.rank===1);s.classList.toggle('truth',t&&t.knownSource)});renderCandidates();select(DATA.candidates[0].mmsi);renderWorkspace(true);updateLab();$('run').disabled=false;$('run').textContent='Run again';setTime(minT);if(!playing)togglePlay()},5100))}
function resetRun(){runTimer.forEach(clearTimeout);runTimer=[];ran=false;analysisComplete=false;analysisRunning=false;detectionAvailable=false;rawMode=false;playing=false;showCount=3;lastSlickFrame=-2;$('play').textContent='▶';$('run').disabled=false;$('run').textContent='▶ Run attribution';document.querySelectorAll('.particle,.release').forEach(x=>{x.classList.remove('show');x.style.display='';if(x.classList.contains('particle'))x.removeAttribute('transform')});$('shipLayer').style.display='';$('clusterLayer').style.display='';$('trailLayer').style.display='';$('trackLayer').style.display='';$('phase').classList.remove('show');$('layerMenu').classList.remove('show');document.querySelectorAll('[data-layer]').forEach(c=>c.checked=c.dataset.layer!=='background');$('satellite').setAttribute('aria-pressed','false');$('sar').style.opacity='.76';$('decisionSummary').classList.remove('abstain');$('decisionState').textContent='AWAITING ANALYSIS';$('decisionReason').textContent='No vessel has been nominated.';selected=null;document.querySelectorAll('.ship').forEach(s=>s.classList.remove('selected','dim','top','truth'));$('trackLayer').innerHTML='';$('leakLayer').innerHTML='';$('detail').innerHTML='<div class="detail-placeholder">Run the analysis to create a reviewable shortlist.</div>';renderCandidates();renderWorkspace(false);updateLab();setTime(minT)}
function modal(title,body,eyebrow='DETAILS'){$('modalTitle').textContent=title;$('modalEyebrow').textContent=eyebrow;$('modalBody').innerHTML=body;$('modal').showModal()}
function caseLibrary(){const v=DATA.validation||{},s=v.synthetic_robustness||{},w=v.known_source_case||{},a=v.abstention_case||{},m=(DATA.challenge||{}).measured_result||{};modal('Three cases. One honest system.',`<div class="case-grid"><article class="case-card"><div><small>SEALED CONTROLLED RECOVERY</small><strong>Corsica digital twin</strong><p>Known source stayed hidden until ranking was complete.</p></div><div class="case-metric">#${m.source_rank||1} / ${m.candidate_count||DATA.case.candidateCount}<br>${num(m.origin_error_km,2)} km</div><button id="replayCurrentCase">Replay current case</button></article><article class="case-card"><div><small>KNOWN-SOURCE RECONSTRUCTION</small><strong>${esc(w.case||'MV Wakashio')}</strong><p>Documented source recovered from historical evidence.</p></div><div class="case-metric">#${w.documented_source_rank||1} / ${w.candidate_count||5}<br>${num(w.shape_error_km,2)} km shape error</div><a href="${DATA.realWorldValidation}#wakashio" target="_blank">Open proof ↗</a></article><article class="case-card"><div><small>EVIDENCE-LIMITED ABSTENTION</small><strong>${esc(a.case||'MT Princess Empress')}</strong><p>A high comparative score could not override weak evidence.</p></div><div class="case-metric">${a.candidate_count||52} candidates<br>${pct(a.data_quality)} data quality</div><a href="${DATA.realWorldValidation}#mindoro" target="_blank">Open proof ↗</a></article></div><a class="scorecard-link" href="${DATA.validationScorecard}" target="_blank">Full validation scorecard · ${s.cases||24} stress cases ↗</a><div class="disclosure">${esc(v.claim_boundary||'Controlled and historical validation supports analyst review; it is not a legal finding or population accuracy claim.')}</div>`,'VALIDATION CASE LIBRARY');$('replayCurrentCase').onclick=()=>{$('modal').close();resetRun();$('operations').scrollIntoView({behavior:'smooth'})}}
function why(t){modal('Why this vessel?',`<div class="fact"><span>Release-zone presence</span><b>${pct(t.presence)}</b></div><div class="fact"><span>Forward slick agreement</span><b>${pct(t.forward)}</b></div><div class="fact"><span>Centroid error</span><b>${num(t.forwardError,2)} km</b></div><div class="fact"><span>Shape error</span><b>${num(t.shapeError,2)} km</b></div><div class="fact"><span>AIS quality</span><b>${pct(t.quality)}</b></div><div class="fact"><span>Gap interpolation</span><b>${t.interpolated?'Used and penalized':'Not used'}</b></div><div class="disclosure">Scores compare vessels within this case. They are not probabilities of guilt.</div>`,'EVIDENCE BREAKDOWN')}
function details(){const measured=(DATA.challenge||{}).measured_result||{};modal(DATA.case.name,`<div class="fact"><span>Raw satellite context</span><b>Sentinel-1 VV · ${DATA.satellite.acquisition_time_utc}</b></div><div class="fact"><span>Controlled observation</span><b>${DATA.case.observationTime} · ${num(DATA.alignment.sarAcquisitionOffsetMinutes,0)} min from SAR acquisition</b></div><div class="fact"><span>Slick input</span><b>${esc(DATA.slickSource)}</b></div><div class="fact"><span>Environment</span><b>Copernicus currents + Open-Meteo wind</b></div><div class="fact"><span>Traffic</span><b>${esc(DATA.trafficSummary)}</b></div><div class="fact"><span>Identity policy</span><b>Real motion is retained; fictional scenario aliases prevent false identification</b></div><div class="fact"><span>Sealed-source result</span><b>${measured.source_rank?'Rank #'+measured.source_rank+' of '+measured.candidate_count:'Not available'}</b></div><div class="fact"><span>Operational decision</span><b>${esc(DATA.case.decision.replaceAll('_',' '))}</b></div><div class="disclosure">${esc(DATA.disclosure)} This is controlled validation, not a real accusation.</div>`,'PROVENANCE & CLAIM BOUNDARY')}
function alignmentDetails(){const a=DATA.alignment,raw=a.rawSarLocalContrastRatio;modal('SAR and slick alignment',`<div class="fact"><span>Geospatial containment</span><b>${a.geometryWithinSarBbox?'PASS · slick is inside the SAR crop':'REVIEW'}</b></div><div class="fact"><span>Source at release point</span><b>${esc(a.sourceTrackReleaseMatch)} · ${num(a.sourceTrackReleaseErrorKm,2)} km offset</b></div><div class="fact"><span>Physics replay</span><b>${esc(a.physicsReplay)} · ${a.timelineFrames} time frames</b></div><div class="fact"><span>Final geometry match</span><b>${a.finalGeometryIoU==null?'Not evaluated':pct(a.finalGeometryIoU)+' IoU'}</b></div><div class="fact"><span>Raw-pixel local contrast</span><b>${raw==null?'Not available':raw.toFixed(3)+'× local background'}</b></div><div class="fact"><span>SAR time offset</span><b>${num(a.sarAcquisitionOffsetMinutes,1)} minutes</b></div><div class="disclosure">${esc(a.interpretation)}</div>`,'ACCURACY & HONESTY CHECK')}
function envDetails(type){const title=type==='wind'?'Historical wind':'Surface current',vals=DATA.environment.map(e=>type==='wind'?Math.hypot(e.wind_east_ms,e.wind_north_ms):Math.hypot(e.current_east_ms,e.current_north_ms)),max=Math.max(...vals),pts=vals.map((v,i)=>`${i/(vals.length-1)*560},${130-v/max*105}`).join(' ');modal(title,`<svg class="spark" viewBox="0 0 560 150"><polyline points="${pts}" fill="none" stroke="#52e2dc" stroke-width="3"/><line x1="0" y1="130" x2="560" y2="130" stroke="#31515d"/></svg><div class="fact"><span>Source</span><b>${esc(DATA.environmentSource)}</b></div><div class="fact"><span>Meaning</span><b>Modelled forcing used by reverse drift</b></div><div class="disclosure">Environmental values are model estimates, not direct observations.</div>`,'ENVIRONMENT DETAILS')}
async function health(){try{const r=await fetch('/api/health',{cache:'no-store'});if(r.ok){engine=true;$('server').textContent='LOCAL ENGINE CONNECTED';$('server').classList.add('on')}}catch(e){}}
$('run').onclick=run;$('reset').onclick=resetRun;$('play').onclick=togglePlay;$('time').oninput=e=>setTime(minT+Number(e.target.value)/1000*(maxT-minT));$('layers').onclick=()=> $('layerMenu').classList.toggle('show');$('satellite').onclick=e=>{rawMode=!rawMode;e.currentTarget.setAttribute('aria-pressed',String(rawMode));e.currentTarget.textContent=rawMode?'◐ Composite':'◐ Raw SAR';$('sar').style.opacity=rawMode?'.94':'.76';lastSlickFrame=-2;setSlickTime(minT+Number($('time').value)/1000*(maxT-minT),true)};$('alignment').onclick=alignmentDetails;$('more').onclick=()=>{showCount=showCount===3?10:3;renderCandidates()};$('caseLibrary').onclick=caseLibrary;$('details').onclick=details;$('close').onclick=()=> $('modal').close();document.querySelectorAll('[data-env]').forEach(b=>b.onclick=()=>b.dataset.env==='coverage'?modal('AIS coverage',`<div class="fact"><span>Reporting at selected time</span><b>${esc($('coverage').textContent)} tracked vessels</b></div><div class="fact"><span>Evidence source</span><b>${esc(DATA.trafficSummary)}</b></div><div class="fact"><span>Interpolation policy</span><b>Only gaps of 3 hours or less are connected</b></div><div class="fact"><span>Long gaps</span><b>Ship is hidden and counted as unavailable</b></div><div class="disclosure">Ships fade in only when a real AIS report window begins. Missing AIS remains missing evidence and never increases attribution.</div>`,'DATA QUALITY'):envDetails(b.dataset.env));document.querySelectorAll('[data-layer]').forEach(c=>c.onchange=()=>{const name=c.dataset.layer,current=minT+Number($('time').value)/1000*(maxT-minT);if(name==='slick'||name==='detection'){lastSlickFrame=-2;setSlickTime(current,true)}if(name==='reverse'){document.querySelectorAll('.particle,.release').forEach(x=>x.style.display=c.checked?'':'none')}if(name==='ships'){$('shipLayer').style.display=c.checked?'':'none';$('clusterLayer').style.display=c.checked?'':'none'}if(name==='background')setTime(current);if(name==='trails'){if(!c.checked)$('trailLayer').innerHTML='';else drawTrafficTrails(current)}});init();
</script></body></html>'''.replace("__DATA__", data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESPADA's map-first operations dashboard")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dossier-href")
    args = parser.parse_args()
    print(
        json.dumps(
            build_operations_dashboard(
                args.project_root,
                args.output,
                dossier_href=args.dossier_href,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
