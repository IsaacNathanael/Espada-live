"""Build a clearly labelled known-source exercise for the existing recorded UI.

The SAR reference and Wakashio grounding are real. The drawn slick geometry,
release hour, forcing, and alternative vessel tracks are controlled inputs, not
historical observations. The output tests the interface and ranking logic; it
is not a forensic reconstruction or model-validation result.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "operator/live_command/recorded_case"
CASE = BASE / "known_source"
ASSETS = CASE / "assets"
PUBLIC = "/operator/live_command/recorded_case/"
OBSERVED = datetime(2020, 8, 11, 6, 0, tzinfo=timezone.utc)
RELEASE = OBSERVED - timedelta(hours=4)
SCENE = "EXERCISE_S1_WAKASHIO_20200811"
SOURCE = (57.743333, -20.443333)


def utc(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((a[0] - b[0]) * 111.32 * math.cos(lat), (a[1] - b[1]) * 111.32)


def offset(point: tuple[float, float], east_km: float, north_km: float) -> tuple[float, float]:
    return (
        point[0] + east_km / (111.32 * math.cos(math.radians(point[1]))),
        point[1] + north_km / 111.32,
    )


def ring(point: tuple[float, float], east_km: float, north_km: float) -> list[list[float]]:
    points = [
        offset(point, east_km * math.cos(2 * math.pi * i / 32), north_km * math.sin(2 * math.pi * i / 32))
        for i in range(33)
    ]
    return [[round(lon, 7), round(lat, 7)] for lon, lat in reversed(points)]


def feature_collection(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def polygon_feature(name: str, points: list[list[float]]) -> dict:
    return {"type": "Feature", "properties": {"name": name, "exercise_geometry": True},
            "geometry": {"type": "Polygon", "coordinates": [points]}}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def asset(name: str) -> str:
    return PUBLIC + "known_source/assets/" + name


def candidate(name: str, exercise_id: str, position: tuple[float, float],
              rank: int, motion: str) -> dict:
    distance = km(position, SOURCE)
    # The same simple distance response scores every route. With constant
    # forcing, forward displacement preserves the relative route distance.
    presence = 0.88 / (1 + (distance / 13) ** 2)
    forward_error = distance + (0.24 if rank == 1 else 0.0)
    forward = 0.94 / (1 + (forward_error / 13) ** 2)
    exercise_track_completeness = 0.8
    score = round(0.55 * presence + 0.40 * forward + 0.05 * exercise_track_completeness, 4)
    return {
        "mmsi": exercise_id, "vessel_name": name, "rank": rank,
        "total_score": score, "presence_score": round(presence, 4),
        "forward_consistency": round(forward, 4),
        "forward_error_km": round(forward_error, 3),
        "data_quality": exercise_track_completeness, "release_position_interpolated": False,
        "identity_status": "exercise_identity_not_AIS_verified",
        "best_match_time_utc": utc(RELEASE), "motion_state": motion,
        "track_bearing_deg": None if motion == "grounded" else 5,
        "significant_gaps": 0, "silence_classification": "not_assessed_synthetic_tracks",
        "evidence": ["Controlled route overlaps the constructed release zone."],
        "limitations": ["Exercise track, not historical AIS; no accusation can be drawn."],
    }


def build() -> None:
    default_path = BASE / "snapshot.json"
    default = json.loads(default_path.read_text(encoding="utf-8"))
    default_id = default["analysis"]["scene_id"]
    original_record = next(
        record for record in default["case_register"]["cases"] if record["scene_id"] == default_id
    )
    original_record["recorded_case_key"] = "singapore"
    original_record["display_name"] = "Singapore · evidence-limited abstention"

    # The release hour and forcing are explicitly exercise assumptions. A real
    # source cannot be inferred from a single public preview image.
    drift_east_km, drift_north_km = -2.0, 1.0
    slick = offset(SOURCE, drift_east_km - 0.18, drift_north_km + 0.16)
    origin = offset(slick, -drift_east_km, -drift_north_km)
    replayed = offset(slick, 0.18, -0.16)
    closure_error = km(replayed, slick)
    assert km(origin, SOURCE) < 0.3
    assert 0.2 < closure_error < 0.3

    vessels = [
        ("MV Wakashio", "EXERCISE-01", SOURCE, "grounded",
         [SOURCE, SOURCE, SOURCE]),
        ("MV Coral Trader", "EXERCISE-02", (57.885, -20.442), "underway",
         [(57.84, -20.64), (57.885, -20.442), (57.95, -20.20)]),
        ("MV Aster Point", "EXERCISE-03", (58.055, -20.40), "underway",
         [(58.00, -20.13), (58.055, -20.40), (58.20, -20.68)]),
    ]
    # Ocean-only alternative legs run east of the reef rather than cutting
    # through the island. The grounded vessel has an intentionally fixed route.
    waypoint_hours = [-2, 0, 4]
    for name, _, _, motion, waypoints in vessels:
        if motion == "underway":
            assert all(point[0] >= 57.8 for point in waypoints), name
            speeds = [km(waypoints[i], waypoints[i + 1]) / (waypoint_hours[i + 1] - waypoint_hours[i]) / 1.852
                      for i in range(2)]
            assert all(3 <= speed <= 12 for speed in speeds), (name, speeds)
        else:
            assert all(km(waypoints[0], point) == 0 for point in waypoints), name
    tracks = feature_collection(*[
        {"type": "Feature", "properties": {"rank": i + 1 if i < 2 else 11, "mmsi": vessel[1],
         "vessel_name": vessel[0], "source": "controlled exercise"},
         "geometry": {"type": "LineString", "coordinates": [list(p) for p in vessel[4]]}}
        for i, vessel in enumerate(vessels)
    ])
    slick_geo = feature_collection(polygon_feature("Illustrative slick interpretation", ring(slick, 1.35, 0.55)))
    origin_geo = feature_collection(polygon_feature("Illustrative origin uncertainty", ring(origin, 1.7, 1.4)))
    write_json(ASSETS / "slick_candidate.geojson", slick_geo)
    write_json(ASSETS / "origin_zone.geojson", origin_geo)
    write_json(ASSETS / "candidate_tracks.geojson", tracks)
    write_json(ASSETS / "scene_footprint.geojson", feature_collection(polygon_feature(
        "Exercise map extent", [[57.66, -20.51], [57.66, -20.26], [58.16, -20.26],
                                [58.16, -20.51], [57.66, -20.51]])))
    # The source geoBoundaries polygons use the opposite spherical winding
    # convention from D3. Reverse rings for rendering without moving vertices.
    coast = json.loads((BASE / "mauritius_land.geojson").read_text(encoding="utf-8"))
    for feature in coast["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            for ring_points in polygon:
                ring_points.reverse()
    write_json(ASSETS / "mauritius_land_d3.geojson", coast)

    first = candidate(vessels[0][0], vessels[0][1], vessels[0][2], 1, vessels[0][3])
    second = candidate(vessels[1][0], vessels[1][1], vessels[1][2], 2, vessels[1][3])
    assert first["total_score"] > second["total_score"] + 0.30
    margin = round(first["total_score"] - second["total_score"], 3)
    retained = []
    excluded = []
    for name, exercise_id, position, motion, waypoints in vessels:
        distance = km(position, origin)
        record = {
            "mmsi": exercise_id, "vessel_name": name,
            "disposition": "retained" if distance <= 20 else "excluded",
            "reason": "space_time_gate_passed" if distance <= 20 else "outside_origin_search_area",
            "time_gate_passed": True, "origin_gate_passed": distance <= 20,
            "closest_release_distance_km": round(distance, 3),
            "closest_report_time_utc": utc(RELEASE), "closest_time_offset_hours": 0,
            "motion_state": motion, "course_evidence": "exercise_route_only",
            "track_continuity": "controlled_complete", "positions": len(waypoints),
        }
        (retained if distance <= 20 else excluded).append(record)
    assert [v["vessel_name"] for v in retained] == ["MV Wakashio", "MV Coral Trader"]
    assert len(excluded) == 1

    assessment = {
        "decision": "LIMITED_SHORTLIST", "passed": True, "score_margin": margin,
        "near_tied_count": 1, "ambiguity_band": 0.02, "failed_gate_ids": [],
        "gates": [
            {"id": "candidate_population", "label": "Comparative population", "passed": True,
             "observed": 2, "requirement": ">= 2 incident-relevant exercise tracks"},
            {"id": "top_score", "label": "Minimum evidence score", "passed": True,
             "observed": first["total_score"], "requirement": ">= 0.40"},
            {"id": "score_margin", "label": "Candidate separation", "passed": True,
             "observed": margin, "requirement": ">= 0.05"},
            {"id": "track_quality", "label": "Exercise track completeness", "passed": True,
             "observed": first["data_quality"], "requirement": ">= 0.40"},
            {"id": "forward_error", "label": "Constructed forward replay", "passed": True,
             "observed": first["forward_error_km"], "requirement": "<= 8.0 km"},
        ],
        "rationale": "MV Wakashio is the only route in the constructed source zone and ranks #1 of 2 retained candidates. This is a controlled exercise, not independent vessel attribution.",
        "claim_boundary": "The real image and documented grounding do not validate synthetic drift or invented AIS tracks.",
    }

    example = copy.deepcopy(default)
    example["region"] = {"name": "Mauritius · Pointe d'Esny case exercise",
                         "bbox": [57.66, -20.53, 58.16, -20.26], "center": [57.91, -20.395]}
    example["map_context"] = {"name": "Southeast Mauritius context",
                              "bbox": [57.40, -20.55, 58.20, -20.12],
                              "coastline_url": asset("mauritius_land_d3.geojson")}
    example["coastline_url"] = asset("mauritius_land_d3.geojson")
    example["sources"] = {
        "environment": {"status": "RECORDED", "kind": "EXERCISE FORCING", "provider": "Controlled constant-vector current and wind assumptions",
                        "latest_observation_utc": utc(OBSERVED), "message": "Illustrative forcing only; no historical ocean-grid download used.",
                        "current": {"current_east_ms": -0.05, "current_north_ms": 0.03,
                                    "current_speed_ms": 0.058, "wind_east_ms": -5.5,
                                    "wind_north_ms": 3.0, "wind_speed_ms": 6.26}},
        "ais": {"status": "RECORDED", "kind": "SYNTHETIC EXERCISE ROUTES", "provider": "Three explicitly constructed exercise tracks",
                "latest_observation_utc": utc(OBSERVED), "message": "No historical AIS is claimed for the two alternative vessels."},
        "sentinel": {"status": "RECORDED", "kind": "REAL SAR REFERENCE", "provider": "ESA Sentinel-1 via Sentinel Hub / Wikimedia Commons",
                     "latest_observation_utc": utc(OBSERVED), "scenes_returned": 1,
                     "latest_scene": {"id": SCENE, "platform": "Sentinel-1", "polarizations": ["VV", "VH"],
                                      "acquisition_time_utc": utc(OBSERVED)},
                     "scenes": [{"id": SCENE, "platform": "Sentinel-1", "polarizations": ["VV", "VH"],
                                 "acquisition_time_utc": utc(OBSERVED), "processed_evidence": True}],
                     "footprints_url": asset("scene_footprint.geojson"),
                     "message": "The public SAR preview is dated 11 Aug 2020; 06:00 UTC is an exercise clock, not verified image metadata."},
    }
    example["ais"] = {"positions": [
        {"mmsi": vessel[1], "vessel_name": vessel[0], "longitude": vessel[4][-1][0],
         "latitude": vessel[4][-1][1], "timestamp_utc": utc(OBSERVED),
         "motion_state": "stationary" if vessel[3] == "grounded" else "underway",
         "sog": 0 if vessel[3] == "grounded" else 6.2,
         "cog": None if vessel[3] == "grounded" else 4,
         "source": "Controlled exercise route — not provider AIS"}
        for vessel in vessels],
        "tracks": {vessel[1]: [list(point) for point in vessel[4]] for vessel in vessels},
        "position_count": 3, "vessel_count": 3, "underway_count": 2,
        "stationary_count": 1, "window_minutes": 240, "mode": "recorded_case",
        "evidence_time_utc": utc(OBSERVED),
    }
    example["pipeline"]["message"] = "Controlled known-source exercise; real SAR preview and documented grounding, constructed drift and other traffic."
    example["analysis"] = {
        "status": "REVIEW_REQUIRED", "scene_id": SCENE, "acquisition_time_utc": utc(OBSERVED),
        "bbox": example["region"]["bbox"], "detected_pixel_fraction": None,
        "detected_components": 1, "probability_summary": {}, "review_status": "approved",
        "input_url": PUBLIC + "wakashio_sentinel1_20200811.jpg",
        "overview_url": None, "mask_url": None,
        "result_url": asset("exercise_evidence.json"),
        "slick_geojson_url": asset("slick_candidate.geojson"),
        "physics_screen": {"status": "REFERENCE_ONLY", "method": "Published SAR preview + documented grounding; exercise geometry",
                           "components_detected": 1, "components_assessed": 0,
                           "weighted_local_contrast_db": None, "wind_speed_ms": None,
                           "contrast_gate_passed": None, "wind_gate_passed": None,
                           "interpretation": "This real SAR reference shows the Wakashio spill, but no calibrated ESPADA mask or physical-screen result was generated from this JPEG."},
        "input_provenance": {"provider": "ESA Sentinel-1 via Sentinel Hub / Wikimedia Commons",
                             "polarization": "VV + VH preview", "measurement": "public display image, not calibrated pixels",
                             "source_url": "https://commons.wikimedia.org/wiki/File:Sentinel-1_(IW-VVVH)_image_on_2020-08-11_(1).jpg",
                             "acquisition_time_verified": False},
        "model_provenance": {"method": "Reference image, no inference run", "model_type": "none",
                             "model_generation": None, "threshold": None},
        "provenance_verified": False,
        "message": "Real Sentinel-1 preview; slick outline is an exercise interpretation, not an ESPADA model output.",
    }
    example["review"] = {"status": "APPROVED", "scene_id": SCENE,
                         "reviewed_at_utc": utc(OBSERVED), "assumed_age_hours": 4,
                         "approved_slick_url": asset("slick_candidate.geojson"),
                         "message": "Approved for this controlled exercise only; the slick polygon and age are assumed."}
    example["attribution"] = {
        "status": "COMPLETE", "drift_status": "COMPLETE",
        "message": "Controlled source-recovery exercise completed; not a blind historical replay.",
        "observation_time_utc": utc(OBSERVED), "release_time_utc": utc(RELEASE),
        "assumed_age_hours": 4, "origin_zone_url": asset("origin_zone.geojson"),
        "origin_particles": [[round(p[0], 7), round(p[1], 7)] for p in [offset(origin, 0.8 * math.cos(i), 0.6 * math.sin(i)) for i in range(32)]],
        "forward_replay_particles": [[round(p[0], 7), round(p[1], 7)] for p in [offset(replayed, 0.8 * math.cos(i), 0.6 * math.sin(i)) for i in range(32)]],
        "observed_centroid": list(slick), "estimated_origin": {"longitude": origin[0], "latitude": origin[1]},
        "credible_radius_90_km": 1.7,
        "forward_closure": {"status": "COMPUTED", "method": "Constant-vector exercise reversal and replay",
                            "particles_seeded": 32, "particles_retained": 32,
                            "centroid": {"longitude": replayed[0], "latitude": replayed[1]},
                            "centroid_error_km": round(closure_error, 3), "cloud_shape_error_km": round(closure_error, 3),
                            "interpretation": "Internal consistency of a constructed scenario; not measured accuracy."},
        "reverse_analysis_url": asset("exercise_evidence.json"),
        "drift_validation_url": asset("exercise_evidence.json"),
        "forcing_source": "Controlled constant-vector exercise; not Copernicus Marine or Open-Meteo historical forcing",
        "ais_source": "Synthetic routes plus documented grounded position",
        "ais_filter": {"status": "PASS", "method": "exercise time-and-origin relevance filter",
                       "release_time_utc": utc(RELEASE), "observation_time_utc": utc(OBSERVED),
                       "search_radius_km": 20, "release_window_hours": 1,
                       "raw_vessels": 3, "raw_positions": 9, "release_window_vessels": 3,
                       "origin_zone_vessels": 2, "retained_vessels": 2, "retained_positions": 6,
                       "excluded_vessels": 1, "retained": retained, "excluded": excluded,
                       "report_url": asset("exercise_evidence.json"),
                       "claim_boundary": "These are exercise tracks, not provider AIS."},
        "decision": "LIMITED_SHORTLIST", "candidate_count": 2,
        "candidates": [first, second], "top_candidate": first,
        "score_margin": margin, "nomination_assessment": assessment,
        "candidate_tracks_url": asset("candidate_tracks.geojson"),
        "ranking_chart_url": None, "attribution_map_url": None,
        "completed_at_utc": utc(OBSERVED),
    }
    evidence = {
        "scenario": "Known-source reconstruction exercise", "documented_source": "MV Wakashio",
        "known_source_reference": "https://environment.govmu.org/Pages/wakashio.aspx",
        "sar_reference": example["analysis"]["input_provenance"]["source_url"],
        "coastline_reference": "https://www.geoboundaries.org/api/current/gbOpen/MUS/ADM0/",
        "satellite_date": "2020-08-11", "exercise_clock_utc": utc(OBSERVED),
        "exercise_release_time_utc": utc(RELEASE), "release_hour_verified": False,
        "synthetic_inputs": ["slick polygon", "constant drift vector", "MV Coral Trader route",
                             "MV Aster Point route", "exercise IDs", "ranking score"],
        "route_logic": "Grounded Wakashio fixed at the documented reef; alternatives remain offshore and do not cross land.",
        "exercise_routes": [
            {"name": vessel[0], "exercise_id": vessel[1], "status": vessel[3],
             "waypoints": [{"time_utc": utc(RELEASE + timedelta(hours=hour)),
                             "longitude": point[0], "latitude": point[1]}
                            for hour, point in zip(waypoint_hours, vessel[4])],
             "leg_speeds_knots": [round(km(vessel[4][i], vessel[4][i + 1]) /
                                        (waypoint_hours[i + 1] - waypoint_hours[i]) / 1.852, 2)
                                  for i in range(2)]}
            for vessel in vessels
        ],
        "ranking": [{"name": c["vessel_name"], "rank": c["rank"], "score": c["total_score"],
                     "forward_error_km": c["forward_error_km"]} for c in [first, second]],
        "claim_boundary": "Demonstrates source recovery in a constructed case only. It does not validate real-world detection, AIS attribution, or legal responsibility.",
    }
    write_json(ASSETS / "exercise_evidence.json", evidence)
    example["response"] = {
        "status": "READY", "response_status": "EXERCISE_SHORTLIST_READY",
        "operational_decision": "LIMITED_SHORTLIST", "permitted_action": "ANALYST REVIEW ONLY",
        "rationale": assessment["rationale"], "generated_at_utc": utc(OBSERVED),
        "verified_files": 0, "required_files": 0, "missing_required_files": 0,
        "chain_digest_sha256": None,
        "recommended_actions": ["Treat MV Wakashio as the recovered known source in this exercise.",
                                "For a real incident, obtain original SAR pixels, date-matched forcing and independently verified AIS."],
        "blocked_actions": ["Present synthetic routes as historical AIS", "Claim a blind or legal attribution"],
        "message": "Read-only exercise dossier; no original operational evidence package was generated.",
        "dossier_url": asset("evidence_dossier.html"),
        "bundle_url": asset("exercise_evidence.json"), "manifest_url": None,
        "summary_url": asset("exercise_evidence.json"),
    }
    example["retasking"] = {"status": "NOT_BUILT", "message": "Follow-up tasking is not part of this controlled exercise."}
    example["evidence_intake"] = {"status": "NOT_READY", "message": "No evidence returns are accepted in read-only exercise mode.", "receipts": []}
    example["reanalysis"] = {"status": "NOT_READY", "message": "No versioned reanalysis was conducted for this controlled exercise.",
                             "version_count": 0, "eligible_receipts": [], "original_attribution_unchanged": True}
    example["closure"] = {"status": "NOT_READY", "message": "Exercise result only; no operational case disposition or accusation.",
                          "case_state": "EXERCISE", "versions": [], "version_count": 0,
                          "history": [], "event_count": 0, "allowed_dispositions": []}
    example["recorded_case"] = {
        "mode": "RECORDED_READ_ONLY", "case_key": "known_source", "scene_id": SCENE,
        "description": "Known-source exercise: real 11 Aug 2020 SAR reference and documented Wakashio grounding; all other tracks, slick geometry, release hour and forcing are constructed.",
        "public_copy": "Controlled reconstruction, not an external blind test or operational evidence package.",
        "synthetic": True,
    }
    example["generated_at_utc"] = utc(OBSERVED)
    example["updated_at_utc"] = utc(OBSERVED)

    known_record = {
        "scene_id": SCENE, "recorded_scene_id": SCENE,
        "recorded_case_key": "known_source",
        "display_name": "Mauritius · known-source exercise",
        "stage": "EVIDENCE_PACKAGE_READY", "acquisition_time_utc": utc(OBSERVED),
        "updated_at_utc": utc(OBSERVED), "platform": "Sentinel-1 reference",
        "polarization": "VV+VH display", "candidate_pixel_fraction": None,
        "operational_decision": "LIMITED_SHORTLIST_EXERCISE", "response_status": "EXERCISE_SHORTLIST_READY",
        "permitted_action": "ANALYST REVIEW ONLY", "candidate_count": 2,
        "top_score": first["total_score"], "verified_files": 0,
        "required_files": 0, "missing_required_files": 0,
        "chain_digest_sha256": None,
        "integrity_status": "EXERCISE", "artifact_count": 8,
        "provenance_warnings": ["Constructed exercise; alternative tracks and release hour are not historical observations."],
        "milestones": {key: True for key in ("observed", "detected", "reviewed", "reconstructed", "attributed", "packaged")},
        "urls": {"dossier": asset("evidence_dossier.html"), "manifest": None,
                 "summary": asset("exercise_evidence.json"),
                 "sar_diagnostic": PUBLIC + "wakashio_sentinel1_20200811.jpg",
                 "verification": None},
    }
    records = [original_record, known_record]
    for snapshot in (default, example):
        register = snapshot["case_register"]
        register["cases"] = copy.deepcopy(records)
        register["case_count"] = 2
        register["sealed_count"] = 1
        register["review_required_count"] = 0
        register["integrity_verified_count"] = 1
        register["provenance_warning_count"] = 0
    write_json(default_path, default)
    write_json(CASE / "snapshot.json", example)

    dossier = f"""<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>ESPADA · Wakashio known-source exercise</title><style>body{{font:17px/1.55 system-ui,sans-serif;background:#f6f5f1;color:#173143;max-width:900px;margin:40px auto;padding:0 20px}}h1{{font-size:2rem}}.flag{{background:#fff1d0;border-left:5px solid #c18624;padding:16px}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}a{{color:#175d84}}</style><h1>Known-source recovery · controlled exercise</h1><p class=\"flag\"><strong>Not historical AIS attribution.</strong> The 11 August 2020 Sentinel-1 preview and the Wakashio grounding are real; the slick outline, release hour, forcing, alternative vessel routes and comparative scores are constructed exercise inputs.</p><p>The fixed grounded position of <strong>MV Wakashio</strong> falls inside the exercise release zone. The two alternative named vessels travel plausible offshore routes; one is outside the spatial gate and one is a weaker retained match. They are fictional ships, not implicated real vessels.</p><table><tr><th>Exercise route</th><th>Outcome</th><th>Role</th></tr><tr><td>MV Wakashio</td><td>#1 · {first['total_score']:.0%}</td><td>Documented grounded source</td></tr><tr><td>MV Coral Trader</td><td>#2 · {second['total_score']:.0%}</td><td>Fictional offshore alternative</td></tr><tr><td>MV Aster Point</td><td>Excluded</td><td>Fictional route outside origin gate</td></tr></table><p>Result: <strong>limited analyst shortlist</strong>, not guilt probability or legal accusation. The known source was not concealed in a blind trial. This tests interface coherence and ranking under controlled conditions, not field performance.</p><p>Sources: <a href=\"https://commons.wikimedia.org/wiki/File:Sentinel-1_(IW-VVVH)_image_on_2020-08-11_(1).jpg\">Sentinel-1 preview (ESA via Sentinel Hub; CC BY-SA 4.0)</a> · <a href=\"https://environment.govmu.org/Pages/wakashio.aspx\">Mauritius Ministry of Environment incident record</a>.</p><p><a href=\"../../../cases.html?mode=recorded&amp;case=known_source\">← Return to ESPADA case register</a></p></html>"""
    (ASSETS / "evidence_dossier.html").write_text(dossier, encoding="utf-8")
    print("Built known-source exercise in the existing recorded case format")


if __name__ == "__main__":
    build()
