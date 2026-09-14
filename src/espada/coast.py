from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from shapely import contains_xy, intersects, linestrings
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

    def blocks_step(
        self,
        start_longitude: np.ndarray | float,
        start_latitude: np.ndarray | float,
        end_longitude: np.ndarray | float,
        end_latitude: np.ndarray | float,
    ) -> np.ndarray:
        """Return particles whose proposed path touches or enters land.

        Checking the entire segment prevents an hourly particle step from
        teleporting across a narrow island when both endpoints are in water.
        """
        arrays = [
            np.atleast_1d(np.asarray(values, dtype=float))
            for values in (
                start_longitude,
                start_latitude,
                end_longitude,
                end_latitude,
            )
        ]
        if not arrays[0].size or any(values.shape != arrays[0].shape for values in arrays[1:]):
            raise ValueError("Coast-step coordinates must be non-empty and aligned")
        if not np.isfinite(np.concatenate(arrays)).all():
            raise ValueError("Coast-step coordinates must be finite")
        coordinates = np.stack(
            [
                np.column_stack([arrays[0], arrays[1]]),
                np.column_stack([arrays[2], arrays[3]]),
            ],
            axis=1,
        )
        paths = linestrings(coordinates)
        return np.asarray(intersects(self.geometry, paths), dtype=bool)


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
