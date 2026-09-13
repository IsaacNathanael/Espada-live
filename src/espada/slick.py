from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "espada-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint, MultiPolygon, Polygon

from .coast import load_coast_mask
from .environment import load_cache
from .geo import Polygonal, local_xy_m, polygon_from_geojson, sample_polygon, write_polygon_geojson
from .models import Forcing
from .spatial_current import load_spatial_current_grid
from .verification import infer_origins_spatial_timeseries, infer_origins_timeseries


@dataclass(frozen=True)
class SlickObservation:
    polygon: Polygonal
    observation_time: pd.Timestamp
    detection_confidence: float
    source: str
    review_status: str


def load_slick(path: Path) -> SlickObservation:
    path = Path(path)
    polygon, properties = polygon_from_geojson(path)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Slick GeoJSON must contain a valid, non-empty polygon or multipolygon")
    min_lon, min_lat, max_lon, max_lat = polygon.bounds
    if min_lon < -180 or max_lon > 180 or min_lat < -90 or max_lat > 90:
        raise ValueError("Slick coordinates fall outside valid longitude/latitude bounds")
    timestamp_value = properties.get("observation_time_utc")
    if not timestamp_value:
        raise ValueError("Slick properties must include observation_time_utc")
    observation_time = pd.Timestamp(timestamp_value)
    if observation_time.tzinfo is None:
        raise ValueError("Slick observation_time_utc must include a UTC timezone")
    observation_time = observation_time.tz_convert("UTC")
    try:
        confidence = float(properties["detection_confidence"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Slick properties must include numeric detection_confidence") from error
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Slick detection_confidence must be between 0 and 1")
    review_status = str(
        properties.get(
            "review_status",
            "synthetic_truth" if properties.get("evaluation_only") else "analyst_provided",
        )
    )
    if review_status == "pending":
        raise ValueError("Slick candidate requires analyst approval before drift attribution")
    return SlickObservation(
        polygon=polygon,
        observation_time=observation_time,
        detection_confidence=confidence,
        source=str(properties.get("source", "analyst-provided GeoJSON")),
        review_status=review_status,
    )


def _polygon_area_km2(polygon: Polygonal) -> float:
    ref_lon = float(polygon.centroid.x)
    ref_lat = float(polygon.centroid.y)

    def ring_area(coordinates: object) -> float:
        points = np.asarray(coordinates, dtype=float)
        x, y = local_xy_m(points[:, 0], points[:, 1], ref_lon, ref_lat)
        return 0.5 * abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))

    parts = polygon.geoms if isinstance(polygon, MultiPolygon) else (polygon,)
    area_m2 = 0.0
    for part in parts:
        area_m2 += ring_area(part.exterior.coords)
        area_m2 -= sum(ring_area(interior.coords) for interior in part.interiors)
    return max(area_m2, 0.0) / 1_000_000.0


def write_slick_from_particles(
    path: Path,
    longitude: np.ndarray,
    latitude: np.ndarray,
    *,
    observation_time_utc: str,
    detection_confidence: float = 0.90,
    source: str = "synthetic SAR-like validation fixture",
) -> Path:
    longitude = np.asarray(longitude, dtype=float)
    latitude = np.asarray(latitude, dtype=float)
    if longitude.size < 10 or longitude.shape != latitude.shape:
        raise ValueError("At least ten aligned particles are required to build a slick polygon")
    center_lon = float(np.median(longitude))
    center_lat = float(np.median(latitude))
    x, y = local_xy_m(longitude, latitude, center_lon, center_lat)
    keep = np.hypot(x, y) <= np.quantile(np.hypot(x, y), 0.90)
    polygon = MultiPoint(np.column_stack((longitude[keep], latitude[keep]))).convex_hull
    if not isinstance(polygon, Polygon):
        raise ValueError("Particles did not form a valid slick polygon")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_polygon_geojson(
        path,
        polygon,
        {
            "observation_time_utc": observation_time_utc,
            "detection_confidence": float(detection_confidence),
            "source": source,
            "evaluation_only": True,
        },
    )
    return path


def analyze_slick(
    slick_path: Path,
    environment_cache: Path,
    output_dir: Path,
    *,
    age_hours: float = 19.0,
    particles: int = 2_000,
    ensemble_members: int = 20,
    seed: int = 26143,
    spatial_current_grid: Path | None = None,
    land_mask: Path | None = None,
) -> dict[str, object]:
    if age_hours <= 0 or particles <= 0 or ensemble_members <= 0:
        raise ValueError("Age, particle count, and ensemble member count must be positive")
    observation = load_slick(slick_path)
    environment = load_cache(environment_cache)
    release_time = observation.observation_time - timedelta(hours=age_hours)
    frame = environment.frame
    history = frame[
        (frame["time_utc"] >= release_time) & (frame["time_utc"] < observation.observation_time)
    ].copy()
    if len(history) < 2:
        raise ValueError("Environmental cache does not cover the requested slick-age window")
    rng = np.random.default_rng(seed)
    observed_lon, observed_lat = sample_polygon(observation.polygon, particles, rng)
    step_seconds = age_hours * 3600.0 / len(history)
    grid = load_spatial_current_grid(spatial_current_grid) if spatial_current_grid else None
    coast = load_coast_mask(land_mask) if land_mask else None
    if coast and not grid:
        raise ValueError("A land mask currently requires a spatial current grid")
    if grid:
        if not grid.covers(release_time, observation.observation_time):
            raise ValueError(
                "Spatial current grid does not cover the requested slick-age window: "
                f"{grid.time_start} to {grid.time_end}"
            )
        if not grid.covers_bounds(tuple(float(value) for value in observation.polygon.bounds)):
            raise ValueError("Spatial current grid does not cover the observed slick bounds")
        origin_lon, origin_lat = infer_origins_spatial_timeseries(
            observed_lon,
            observed_lat,
            history,
            grid,
            step_seconds=step_seconds,
            seed=seed + 1,
            ensemble_members=ensemble_members,
            coast_mask=coast,
        )
    else:
        origin_lon, origin_lat = infer_origins_timeseries(
            observed_lon,
            observed_lat,
            history,
            step_seconds=step_seconds,
            seed=seed + 1,
            ensemble_members=ensemble_members,
        )
    estimated_lon = float(np.mean(origin_lon))
    estimated_lat = float(np.mean(origin_lat))
    x, y = local_xy_m(origin_lon, origin_lat, estimated_lon, estimated_lat)
    radial_km = np.hypot(x, y) / 1_000.0
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output_dir / "observed_particles.npz", lon=observed_lon, lat=observed_lat)
    np.savez_compressed(output_dir / "forward_particles.npz", lon=observed_lon, lat=observed_lat)
    np.savez_compressed(output_dir / "reverse_endpoints.npz", lon=origin_lon, lat=origin_lat)
    forcing_history = history.copy()
    forcing_history["time_utc"] = forcing_history["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    forcing_history.to_csv(output_dir / "forcing_history.csv", index=False)
    write_polygon_geojson(
        output_dir / "slick_normalized.geojson",
        observation.polygon,
        {
            "observation_time_utc": observation.observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "detection_confidence": observation.detection_confidence,
            "source": observation.source,
        },
    )

    fig, (slick_axis, origin_axis) = plt.subplots(
        1, 2, figsize=(12, 5.4), constrained_layout=True
    )
    polygon_parts = (
        observation.polygon.geoms
        if isinstance(observation.polygon, MultiPolygon)
        else (observation.polygon,)
    )
    for part in polygon_parts:
        exterior = np.asarray(part.exterior.coords)
        slick_axis.fill(exterior[:, 0], exterior[:, 1], color="#087F7B", alpha=0.28)
    slick_axis.scatter(observed_lon, observed_lat, s=3, alpha=0.18, color="#087F7B")
    slick_axis.set_title("Observed slick polygon")
    density = origin_axis.hist2d(origin_lon, origin_lat, bins=70, cmap="YlOrRd")
    fig.colorbar(density[3], ax=origin_axis, label="Backward endpoints")
    origin_axis.scatter(
        [estimated_lon], [estimated_lat], marker="*", s=190, color="white", edgecolor="black"
    )
    origin_axis.set_title("Probable release zone")
    for axis in (slick_axis, origin_axis):
        axis.set_xlabel("Longitude")
        axis.set_ylabel("Latitude")
        axis.grid(alpha=0.16)
    fig.savefig(output_dir / "slick_reverse_analysis.png", dpi=180)
    plt.close(fig)

    believed_forcing = Forcing(
        current_east_ms=float(history["current_east_ms"].mean()),
        current_north_ms=float(history["current_north_ms"].mean()),
        wind_east_ms=float(history["wind_east_ms"].mean()),
        wind_north_ms=float(history["wind_north_ms"].mean()),
        diffusivity_m2s=12.0,
    )
    radius_50 = float(np.quantile(radial_km, 0.50))
    radius_90 = float(np.quantile(radial_km, 0.90))
    release_estimate = {
        "release_time_utc": release_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observation_time_utc": observation.observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estimated_origin": {"longitude": estimated_lon, "latitude": estimated_lat},
        "credible_radius_50_km": radius_50,
        "credible_radius_90_km": radius_90,
        "assumed_age_hours": age_hours,
        "believed_forcing": believed_forcing.to_dict(),
        "forcing_provenance": {
            "source": environment.source,
            "forcing_steps": len(history),
            "temporal_resolution": environment.temporal_resolution,
            "spatial_mode": "particle-local bilinear currents" if grid else "single-location currents",
            "spatial_current_grid": str(grid.path) if grid else None,
            "spatial_grid_bounds": list(grid.bounds) if grid else None,
            "land_mask": str(coast.path) if coast else None,
            "coast_policy": "reject particle steps ending on land" if coast else None,
        },
        "assumption": "Release age is supplied by the analyst and must be sensitivity-tested.",
    }
    (output_dir / "release_estimate.json").write_text(
        json.dumps(release_estimate, indent=2), encoding="utf-8"
    )
    result = {
        "status": "PASS",
        "input": {
            "slick_geojson": str(Path(slick_path).resolve()),
            "observation_time_utc": observation.observation_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "detection_confidence": observation.detection_confidence,
            "area_km2": _polygon_area_km2(observation.polygon),
            "source": observation.source,
        },
        "environment": {
            "source": environment.source,
            "forcing_steps": len(history),
            "temporal_resolution": environment.temporal_resolution,
            "spatial_mode": "particle-local bilinear currents" if grid else "single-location currents",
            "spatial_current_grid": str(grid.path) if grid else None,
            "land_mask": str(coast.path) if coast else None,
        },
        "assumed_age_hours": age_hours,
        "estimated_release_time_utc": release_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estimated_origin": {"longitude": estimated_lon, "latitude": estimated_lat},
        "credible_radius_50_km": radius_50,
        "credible_radius_90_km": radius_90,
        "particles": particles,
        "ensemble_members": ensemble_members,
        "interpretation": "Processing PASS means the input contract and inference completed; it is not an accuracy score.",
        "limitations": [
            "Spill age is supplied by the analyst for this milestone.",
            *(
                [
                    "Currents vary through time and space; wind varies through time at one analysis location.",
                    "Particles outside the downloaded current subset use its nearest boundary cell.",
                    *(["Particle steps ending on supplied land polygons are rejected."] if coast else []),
                ]
                if grid
                else ["Current and wind vary through time but use one analysis location."]
            ),
            "The slick polygon must come from a validated detector or analyst review.",
        ],
        "artifacts": [
            "slick_normalized.geojson",
            "observed_particles.npz",
            "forward_particles.npz",
            "reverse_endpoints.npz",
            "forcing_history.csv",
            "release_estimate.json",
            "slick_reverse_analysis.png",
            "slick_analysis.json",
        ],
    }
    (output_dir / "slick_analysis.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result
