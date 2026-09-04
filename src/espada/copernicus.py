from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd
import xarray as xr


FORECAST_DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"
DEFAULT_VARIABLES = ("uo", "vo")
SURFACE_DEPTH_M = 0.49402499198913574


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class CopernicusRequest:
    start_datetime: datetime
    end_datetime: datetime
    dataset_id: str = FORECAST_DATASET_ID
    minimum_longitude: float = 70.5
    maximum_longitude: float = 72.5
    minimum_latitude: float = 17.8
    maximum_latitude: float = 19.8
    minimum_depth: float = SURFACE_DEPTH_M
    maximum_depth: float = SURFACE_DEPTH_M
    variables: tuple[str, ...] = DEFAULT_VARIABLES

    def __post_init__(self) -> None:
        start = parse_utc(self.start_datetime)
        end = parse_utc(self.end_datetime)
        object.__setattr__(self, "start_datetime", start)
        object.__setattr__(self, "end_datetime", end)
        if start >= end:
            raise ValueError("Copernicus start_datetime must be before end_datetime")
        if not -180 <= self.minimum_longitude < self.maximum_longitude <= 180:
            raise ValueError("Copernicus longitude bounds are invalid")
        if not -90 <= self.minimum_latitude < self.maximum_latitude <= 90:
            raise ValueError("Copernicus latitude bounds are invalid")
        if self.minimum_depth < 0 or self.minimum_depth > self.maximum_depth:
            raise ValueError("Copernicus depth bounds are invalid")
        if not self.dataset_id.strip():
            raise ValueError("Copernicus dataset_id is required")
        if not self.variables:
            raise ValueError("At least one Copernicus variable is required")

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "variables": list(self.variables),
            "minimum_longitude": self.minimum_longitude,
            "maximum_longitude": self.maximum_longitude,
            "minimum_latitude": self.minimum_latitude,
            "maximum_latitude": self.maximum_latitude,
            "minimum_depth": self.minimum_depth,
            "maximum_depth": self.maximum_depth,
            "start_datetime": self.start_datetime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_datetime": self.end_datetime.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def build_subset_kwargs(request: CopernicusRequest, output_path: Path) -> dict[str, object]:
    """Build only documented Copernicus Marine subset arguments.

    Credentials are deliberately absent. The official toolbox reads its own
    secure login configuration or supported environment variables.
    """
    output_path = Path(output_path).resolve()
    return {
        **request.to_dict(),
        "output_directory": str(output_path.parent),
        "output_filename": output_path.name,
        "overwrite": True,
    }


def download_subset(
    request: CopernicusRequest,
    output_path: Path,
    *,
    subsetter: Callable[..., object] | None = None,
) -> dict[str, object]:
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if subsetter is None:
        try:
            import copernicusmarine
        except ImportError as error:
            raise RuntimeError(
                "Copernicus Marine Toolbox is not installed. Run scripts/setup_copernicus.ps1."
            ) from error
        subsetter = copernicusmarine.subset
    kwargs = build_subset_kwargs(request, output_path)
    subsetter(**kwargs)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Copernicus download did not create {output_path}")
    return {
        "status": "PASS",
        "source": "Copernicus Marine Service",
        "raw_file": str(output_path),
        "bytes": output_path.stat().st_size,
        "request": request.to_dict(),
        "credential_handling": "official Copernicus Marine Toolbox login; no credentials stored by Espada",
    }


def _find_name(dataset: xr.Dataset, candidates: Sequence[str], label: str) -> str:
    available = set(dataset.coords) | set(dataset.dims)
    for candidate in candidates:
        if candidate in available:
            return candidate
    raise ValueError(f"Copernicus file is missing a {label} coordinate")


def _velocity_scale(units: str | None) -> float:
    normalized = (units or "m/s").lower().replace(" ", "").replace("**", "")
    if normalized in {"m/s", "ms-1", "m.s-1", "ms^-1"}:
        return 1.0
    if normalized in {"cm/s", "cms-1", "cm.s-1", "cms^-1"}:
        return 0.01
    raise ValueError(f"Unsupported Copernicus current unit: {units}")


def _point_series(
    dataset: xr.Dataset,
    variable: str,
    *,
    time_name: str,
    latitude_name: str,
    longitude_name: str,
    depth_name: str | None,
    latitude: float,
    longitude: float,
) -> xr.DataArray:
    if variable not in dataset:
        raise ValueError(f"Copernicus file is missing variable {variable}")
    data = dataset[variable]
    selections: dict[str, float] = {}
    if latitude_name in data.dims:
        selections[latitude_name] = latitude
    if longitude_name in data.dims:
        selections[longitude_name] = longitude
    if depth_name and depth_name in data.dims:
        selections[depth_name] = 0.0
    if selections:
        data = data.sel(selections, method="nearest")
    data = data.squeeze(drop=True)
    extra_dims = [name for name in data.dims if name != time_name]
    if extra_dims:
        raise ValueError(f"Unexpected dimensions remain in {variable}: {extra_dims}")
    if time_name not in data.dims:
        raise ValueError(f"Variable {variable} has no time dimension")
    return data.transpose(time_name)


def _interpolate_frame(
    frame: pd.DataFrame,
    target_times: pd.DatetimeIndex,
    columns: Sequence[str],
) -> pd.DataFrame:
    indexed = frame.set_index("time_utc")[list(columns)].sort_index()
    combined = indexed.index.union(target_times).sort_values()
    expanded = indexed.reindex(combined).interpolate(method="time").ffill().bfill()
    return expanded.reindex(target_times)


def _read_wind_cache(cache_path: Path, target_times: pd.DatetimeIndex) -> tuple[pd.DataFrame, str, list[str]]:
    payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    samples = pd.DataFrame(payload.get("samples", []))
    required = {"time_utc", "wind_east_ms", "wind_north_ms"}
    missing = required - set(samples.columns)
    if missing or samples.empty:
        raise ValueError(f"Wind cache is missing columns: {sorted(missing)}")
    samples["time_utc"] = pd.to_datetime(samples["time_utc"], utc=True)
    for column in ("wind_east_ms", "wind_north_ms"):
        samples[column] = pd.to_numeric(samples[column], errors="coerce")
    if samples[["wind_east_ms", "wind_north_ms"]].isna().any().any():
        raise ValueError("Wind cache contains invalid values")
    warnings: list[str] = []
    if target_times.min() < samples["time_utc"].min() or target_times.max() > samples["time_utc"].max():
        warnings.append("Wind endpoints were held constant outside the cached forecast window.")
    interpolated = _interpolate_frame(
        samples,
        target_times,
        ("wind_east_ms", "wind_north_ms"),
    )
    combined_source = str(payload.get("source", "cached wind source"))
    wind_source = str(payload.get("wind_source", ""))
    if not wind_source:
        wind_source = "Open-Meteo forecast wind" if "Open-Meteo" in combined_source else combined_source
    return interpolated, wind_source, warnings


def normalize_currents(
    netcdf_path: Path,
    cache_path: Path,
    *,
    wind_cache_path: Path,
    latitude: float = 18.7167,
    longitude: float = 71.45,
    dataset_id: str = FORECAST_DATASET_ID,
    hourly: bool = True,
) -> dict[str, object]:
    """Convert a CMEMS current grid into Espada's validated forcing cache contract."""
    netcdf_path = Path(netcdf_path).resolve()
    cache_path = Path(cache_path).resolve()
    if not netcdf_path.exists():
        raise FileNotFoundError(f"Copernicus NetCDF file not found: {netcdf_path}")
    with xr.open_dataset(netcdf_path) as dataset:
        time_name = _find_name(dataset, ("time", "valid_time"), "time")
        latitude_name = _find_name(dataset, ("latitude", "lat"), "latitude")
        longitude_name = _find_name(dataset, ("longitude", "lon"), "longitude")
        try:
            depth_name = _find_name(dataset, ("depth", "deptht", "depthu", "depthv"), "depth")
        except ValueError:
            depth_name = None
        east = _point_series(
            dataset,
            "uo",
            time_name=time_name,
            latitude_name=latitude_name,
            longitude_name=longitude_name,
            depth_name=depth_name,
            latitude=latitude,
            longitude=longitude,
        )
        north = _point_series(
            dataset,
            "vo",
            time_name=time_name,
            latitude_name=latitude_name,
            longitude_name=longitude_name,
            depth_name=depth_name,
            latitude=latitude,
            longitude=longitude,
        )
        east_scale = _velocity_scale(east.attrs.get("units"))
        north_scale = _velocity_scale(north.attrs.get("units"))
        native = pd.DataFrame(
            {
                "time_utc": pd.to_datetime(east[time_name].values, utc=True),
                "current_east_ms": np.asarray(east.values, dtype=float) * east_scale,
                "current_north_ms": np.asarray(north.values, dtype=float) * north_scale,
            }
        ).dropna()
    native = native.drop_duplicates("time_utc").sort_values("time_utc").reset_index(drop=True)
    if len(native) < 2:
        raise ValueError("Copernicus subset must contain at least two valid time steps")
    if hourly:
        target_times = pd.date_range(
            native["time_utc"].iloc[0],
            native["time_utc"].iloc[-1],
            freq="1h",
        )
        currents = _interpolate_frame(
            native,
            target_times,
            ("current_east_ms", "current_north_ms"),
        )
        temporal_resolution = "hourly linear interpolation from native Copernicus timestamps"
    else:
        target_times = pd.DatetimeIndex(native["time_utc"])
        currents = native.set_index("time_utc")[["current_east_ms", "current_north_ms"]]
        temporal_resolution = "native Copernicus timestamps"
    wind, wind_source, warnings = _read_wind_cache(wind_cache_path, target_times)
    source = f"Copernicus Marine {dataset_id} currents + {wind_source}"
    normalized = pd.DataFrame(
        {
            "time_utc": target_times,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "current_east_ms": currents["current_east_ms"].to_numpy(dtype=float),
            "current_north_ms": currents["current_north_ms"].to_numpy(dtype=float),
            "wind_east_ms": wind["wind_east_ms"].to_numpy(dtype=float),
            "wind_north_ms": wind["wind_north_ms"].to_numpy(dtype=float),
            "source": source,
        }
    )
    numeric = normalized[
        ["latitude", "longitude", "current_east_ms", "current_north_ms", "wind_east_ms", "wind_north_ms"]
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("Normalized Copernicus forcing contains non-finite values")
    records = normalized.copy()
    records["time_utc"] = records["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "schema_version": "1.0",
        "source": source,
        "fetched_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attribution": "Copernicus Marine Service currents; wind from the named cached source.",
        "dataset_id": dataset_id,
        "raw_file": str(netcdf_path),
        "selection": {"latitude": latitude, "longitude": longitude, "depth_m": 0.0},
        "native_sample_count": len(native),
        "temporal_resolution": temporal_resolution,
        "warnings": warnings,
        "limitations": [
            "Model output, not an observation or navigational product.",
            "The current grid is sampled at the nearest surface cell for this milestone.",
            "Native current timestamps are linearly interpolated for hourly integration.",
        ],
        "samples": records.to_dict(orient="records"),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary_path.replace(cache_path)
    return {
        "status": "PASS",
        "source": source,
        "dataset_id": dataset_id,
        "raw_file": str(netcdf_path),
        "cache_file": str(cache_path),
        "native_sample_count": len(native),
        "normalized_sample_count": len(normalized),
        "time_start_utc": records["time_utc"].iloc[0],
        "time_end_utc": records["time_utc"].iloc[-1],
        "temporal_resolution": temporal_resolution,
        "warnings": warnings,
    }


def _request_from_args(args: argparse.Namespace) -> CopernicusRequest:
    return CopernicusRequest(
        start_datetime=parse_utc(args.start),
        end_datetime=parse_utc(args.end),
        dataset_id=args.dataset_id,
        minimum_longitude=args.min_lon,
        maximum_longitude=args.max_lon,
        minimum_latitude=args.min_lat,
        maximum_latitude=args.max_lat,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Espada Copernicus Marine adapter")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("request", "download"):
        command = commands.add_parser(name)
        command.add_argument("--start", required=True)
        command.add_argument("--end", required=True)
        command.add_argument("--dataset-id", default=FORECAST_DATASET_ID)
        command.add_argument("--min-lon", type=float, default=70.5)
        command.add_argument("--max-lon", type=float, default=72.5)
        command.add_argument("--min-lat", type=float, default=17.8)
        command.add_argument("--max-lat", type=float, default=19.8)
        command.add_argument("--out", type=Path, required=name == "download")
    normalize = commands.add_parser("normalize")
    normalize.add_argument("--raw", type=Path, required=True)
    normalize.add_argument("--cache", type=Path, required=True)
    normalize.add_argument("--wind-cache", type=Path, required=True)
    normalize.add_argument("--latitude", type=float, default=18.7167)
    normalize.add_argument("--longitude", type=float, default=71.45)
    normalize.add_argument("--dataset-id", default=FORECAST_DATASET_ID)
    normalize.add_argument("--native-times", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"request", "download"}:
        request = _request_from_args(args)
        if args.command == "request":
            result = {
                "status": "PASS",
                "request": request.to_dict(),
                "credential_handling": "no username or password is accepted by Espada",
            }
        else:
            result = download_subset(request, args.out)
    else:
        result = normalize_currents(
            args.raw,
            args.cache,
            wind_cache_path=args.wind_cache,
            latitude=args.latitude,
            longitude=args.longitude,
            dataset_id=args.dataset_id,
            hourly=not args.native_times,
        )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
