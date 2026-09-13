from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


def _coordinate(dataset: xr.Dataset, candidates: tuple[str, ...], label: str) -> str:
    for name in candidates:
        if name in dataset.coords or name in dataset.dims:
            return name
    raise ValueError(f"Current grid is missing its {label} coordinate")


def _scale(units: str | None) -> float:
    normalized = (units or "m/s").lower().replace(" ", "").replace("**", "")
    if normalized in {"m/s", "ms-1", "m.s-1", "ms^-1"}:
        return 1.0
    if normalized in {"cm/s", "cms-1", "cm.s-1", "cms^-1"}:
        return 0.01
    raise ValueError(f"Unsupported current unit: {units}")


@dataclass(frozen=True)
class SpatialCurrentGrid:
    """Surface-current cube with linear time and bilinear space sampling."""

    times_ns: np.ndarray
    latitudes: np.ndarray
    longitudes: np.ndarray
    east_ms: np.ndarray
    north_ms: np.ndarray
    source: str
    path: Path

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            float(self.longitudes[0]),
            float(self.latitudes[0]),
            float(self.longitudes[-1]),
            float(self.latitudes[-1]),
        )

    @property
    def time_start(self) -> pd.Timestamp:
        return pd.Timestamp(int(self.times_ns[0]), unit="ns", tz="UTC")

    @property
    def time_end(self) -> pd.Timestamp:
        return pd.Timestamp(int(self.times_ns[-1]), unit="ns", tz="UTC")

    def covers(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        start = pd.Timestamp(start)
        end = pd.Timestamp(end)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Spatial-current coverage checks require timezone-aware timestamps")
        return self.time_start <= start.tz_convert("UTC") and self.time_end >= end.tz_convert("UTC")

    def covers_bounds(self, bounds: tuple[float, float, float, float]) -> bool:
        min_lon, min_lat, max_lon, max_lat = bounds
        grid_min_lon, grid_min_lat, grid_max_lon, grid_max_lat = self.bounds
        return (
            grid_min_lon <= min_lon <= max_lon <= grid_max_lon
            and grid_min_lat <= min_lat <= max_lat <= grid_max_lat
        )

    def sample(
        self,
        longitude: np.ndarray | float,
        latitude: np.ndarray | float,
        time: pd.Timestamp | str,
    ) -> tuple[np.ndarray, np.ndarray]:
        lon = np.atleast_1d(np.asarray(longitude, dtype=float))
        lat = np.atleast_1d(np.asarray(latitude, dtype=float))
        if lon.shape != lat.shape or lon.size == 0 or not np.isfinite(np.concatenate([lon, lat])).all():
            raise ValueError("Particle coordinates must be finite, non-empty and aligned")
        timestamp = pd.Timestamp(time)
        if timestamp.tzinfo is None:
            raise ValueError("Current sampling time must include a timezone")
        target_ns = int(timestamp.tz_convert("UTC").value)
        upper = int(np.searchsorted(self.times_ns, target_ns, side="left"))
        upper = min(max(upper, 0), len(self.times_ns) - 1)
        lower = max(0, upper - 1)
        if upper == lower:
            fraction = 0.0
        else:
            fraction = (target_ns - self.times_ns[lower]) / (
                self.times_ns[upper] - self.times_ns[lower]
            )
            fraction = float(np.clip(fraction, 0.0, 1.0))
        east_lower = self._sample_plane(self.east_ms[lower], lon, lat)
        north_lower = self._sample_plane(self.north_ms[lower], lon, lat)
        if upper == lower:
            return east_lower, north_lower
        east_upper = self._sample_plane(self.east_ms[upper], lon, lat)
        north_upper = self._sample_plane(self.north_ms[upper], lon, lat)
        return (
            east_lower + fraction * (east_upper - east_lower),
            north_lower + fraction * (north_upper - north_lower),
        )

    def _sample_plane(
        self, plane: np.ndarray, longitude: np.ndarray, latitude: np.ndarray
    ) -> np.ndarray:
        lon = np.clip(longitude, self.longitudes[0], self.longitudes[-1])
        lat = np.clip(latitude, self.latitudes[0], self.latitudes[-1])
        if len(self.longitudes) == 1:
            x0 = x1 = np.zeros(lon.shape, dtype=int)
            fx = np.zeros(lon.shape)
        else:
            x1 = np.clip(np.searchsorted(self.longitudes, lon), 1, len(self.longitudes) - 1)
            x0 = x1 - 1
            fx = (lon - self.longitudes[x0]) / (self.longitudes[x1] - self.longitudes[x0])
        if len(self.latitudes) == 1:
            y0 = y1 = np.zeros(lat.shape, dtype=int)
            fy = np.zeros(lat.shape)
        else:
            y1 = np.clip(np.searchsorted(self.latitudes, lat), 1, len(self.latitudes) - 1)
            y0 = y1 - 1
            fy = (lat - self.latitudes[y0]) / (self.latitudes[y1] - self.latitudes[y0])
        values = np.stack(
            [plane[y0, x0], plane[y0, x1], plane[y1, x0], plane[y1, x1]], axis=0
        )
        weights = np.stack(
            [(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy], axis=0
        )
        finite = np.isfinite(values)
        weight_sum = np.sum(np.where(finite, weights, 0.0), axis=0)
        weighted = np.sum(np.where(finite, values * weights, 0.0), axis=0)
        fallback = float(np.nanmedian(plane))
        if not np.isfinite(fallback):
            raise ValueError("Current grid contains no finite water velocity")
        return np.divide(weighted, weight_sum, out=np.full(lon.shape, fallback), where=weight_sum > 0)


def load_spatial_current_grid(path: Path) -> SpatialCurrentGrid:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Copernicus current grid not found: {path}")
    with xr.open_dataset(path) as dataset:
        time_name = _coordinate(dataset, ("time", "valid_time"), "time")
        lat_name = _coordinate(dataset, ("latitude", "lat"), "latitude")
        lon_name = _coordinate(dataset, ("longitude", "lon"), "longitude")
        depth_name = next(
            (name for name in ("depth", "deptht", "depthu", "depthv") if name in dataset.dims),
            None,
        )
        components: list[np.ndarray] = []
        for variable in ("uo", "vo"):
            if variable not in dataset:
                raise ValueError(f"Current grid is missing variable {variable}")
            data = dataset[variable]
            if depth_name and depth_name in data.dims:
                data = data.isel({depth_name: 0})
            extra = [name for name in data.dims if name not in {time_name, lat_name, lon_name}]
            for name in extra:
                if data.sizes[name] != 1:
                    raise ValueError(f"Unexpected non-singleton current dimension: {name}")
                data = data.isel({name: 0})
            data = data.transpose(time_name, lat_name, lon_name)
            components.append(np.asarray(data.values, dtype=float) * _scale(data.attrs.get("units")))
        times = pd.to_datetime(dataset[time_name].values, utc=True)
        latitudes = np.asarray(dataset[lat_name].values, dtype=float)
        longitudes = np.asarray(dataset[lon_name].values, dtype=float)
        source = str(dataset.attrs.get("title") or dataset.attrs.get("source") or "Copernicus Marine")
    if len(times) < 2 or latitudes.size == 0 or longitudes.size == 0:
        raise ValueError("Current grid requires at least two time steps and a non-empty spatial grid")
    time_order = np.argsort(times.asi8)
    lat_order = np.argsort(latitudes)
    lon_order = np.argsort(longitudes)
    east = components[0][time_order][:, lat_order][:, :, lon_order]
    north = components[1][time_order][:, lat_order][:, :, lon_order]
    if not np.isfinite(east).any() or not np.isfinite(north).any():
        raise ValueError("Current grid has no finite surface velocities")
    return SpatialCurrentGrid(
        times_ns=np.asarray(times.asi8[time_order], dtype=np.int64),
        latitudes=latitudes[lat_order],
        longitudes=longitudes[lon_order],
        east_ms=east,
        north_ms=north,
        source=source,
        path=path,
    )
