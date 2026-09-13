from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
from shapely import union_all
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape


EARTH_RADIUS_M = 6_371_008.8
Polygonal = Polygon | MultiPolygon


def local_xy_m(
    lon: np.ndarray | float,
    lat: np.ndarray | float,
    ref_lon: float,
    ref_lat: float,
) -> tuple[np.ndarray, np.ndarray]:
    lon_array = np.asarray(lon, dtype=float)
    lat_array = np.asarray(lat, dtype=float)
    x = np.deg2rad(lon_array - ref_lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat))
    y = np.deg2rad(lat_array - ref_lat) * EARTH_RADIUS_M
    return x, y


def lonlat_from_local_m(
    x: np.ndarray | float,
    y: np.ndarray | float,
    ref_lon: float,
    ref_lat: float,
) -> tuple[np.ndarray, np.ndarray]:
    x_array = np.asarray(x, dtype=float)
    y_array = np.asarray(y, dtype=float)
    lon = ref_lon + np.rad2deg(x_array / (EARTH_RADIUS_M * math.cos(math.radians(ref_lat))))
    lat = ref_lat + np.rad2deg(y_array / EARTH_RADIUS_M)
    return lon, lat


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)) / 1000.0


def interpolate_track(
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    fractions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    clipped = np.clip(np.asarray(fractions, dtype=float), 0.0, 1.0)
    return (
        start_lon + clipped * (end_lon - start_lon),
        start_lat + clipped * (end_lat - start_lat),
    )


def _polygonal_geometry(geometry: object) -> Polygonal:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        parts: list[Polygon] = []
        for part in geometry.geoms:
            if isinstance(part, Polygon):
                parts.append(part)
            elif isinstance(part, MultiPolygon):
                parts.extend(part.geoms)
        merged = union_all(parts) if parts else None
        if isinstance(merged, (Polygon, MultiPolygon)):
            return merged
    raise ValueError("GeoJSON geometry must be a Polygon or MultiPolygon")


def polygon_from_geojson(path: Path) -> tuple[Polygonal, dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    feature = document["features"][0]
    polygon = _polygonal_geometry(shape(feature["geometry"]))
    return polygon, feature.get("properties", {})


def write_polygon_geojson(path: Path, polygon: Polygonal, properties: dict) -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": mapping(polygon),
            }
        ],
    }
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def write_line_geojson(path: Path, coordinates: Iterable[tuple[float, float]], properties: dict) -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "LineString", "coordinates": list(coordinates)},
            }
        ],
    }
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def _sample_single_polygon(
    polygon: Polygon, count: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    if count == 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    min_lon, min_lat, max_lon, max_lat = polygon.bounds
    accepted_lon: list[float] = []
    accepted_lat: list[float] = []
    while len(accepted_lon) < count:
        batch = max(256, 2 * (count - len(accepted_lon)))
        lons = rng.uniform(min_lon, max_lon, batch)
        lats = rng.uniform(min_lat, max_lat, batch)
        from shapely import contains_xy

        keep = contains_xy(polygon, lons, lats)
        accepted_lon.extend(lons[keep].tolist())
        accepted_lat.extend(lats[keep].tolist())
    return np.asarray(accepted_lon[:count]), np.asarray(accepted_lat[:count])


def sample_polygon(
    polygon: Polygonal, count: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    if isinstance(polygon, Polygon):
        return _sample_single_polygon(polygon, count, rng)
    parts = [part for part in polygon.geoms if not part.is_empty and part.area > 0]
    if not parts:
        raise ValueError("MultiPolygon contains no sampleable regions")
    areas = np.asarray([part.area for part in parts], dtype=float)
    allocations = rng.multinomial(count, areas / areas.sum())
    longitude: list[np.ndarray] = []
    latitude: list[np.ndarray] = []
    for part, allocation in zip(parts, allocations, strict=True):
        lons, lats = _sample_single_polygon(part, int(allocation), rng)
        longitude.append(lons)
        latitude.append(lats)
    lons = np.concatenate(longitude)
    lats = np.concatenate(latitude)
    order = rng.permutation(count)
    return lons[order], lats[order]
