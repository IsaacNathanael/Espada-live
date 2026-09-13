from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry import shape
from shapely.ops import unary_union


@dataclass(frozen=True)
class CoastMask:
    """Polygonal land mask used as a zero-flux particle boundary."""

    geometry: object
    path: Path

    def contains(
        self, longitude: np.ndarray | float, latitude: np.ndarray | float
    ) -> np.ndarray:
        lon = np.atleast_1d(np.asarray(longitude, dtype=float))
        lat = np.atleast_1d(np.asarray(latitude, dtype=float))
        if lon.shape != lat.shape:
            raise ValueError("Coast-mask coordinates must be aligned")
        return np.asarray(contains_xy(self.geometry, lon, lat), dtype=bool)


def load_coast_mask(path: Path) -> CoastMask:
    """Load Polygon/MultiPolygon land geometry from GeoJSON."""

    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Coastline GeoJSON not found: {path}")
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if document.get("type") == "FeatureCollection":
        geometries = [shape(item["geometry"]) for item in document.get("features", [])]
    elif document.get("type") == "Feature":
        geometries = [shape(document["geometry"])]
    else:
        geometries = [shape(document)]
    polygons = [
        geometry
        for geometry in geometries
        if geometry.geom_type in {"Polygon", "MultiPolygon"}
    ]
    if not polygons:
        raise ValueError("Coastline GeoJSON contains no Polygon or MultiPolygon land geometry")
    geometry = unary_union(polygons)
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    if geometry.is_empty:
        raise ValueError("Coastline land geometry is empty")
    return CoastMask(geometry=geometry, path=path)
