from __future__ import annotations

import json
import math
import os
import random
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "espada-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .models import Forcing, format_utc


MARINE_ENDPOINT = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
CUSTOMER_MARINE_ENDPOINT = "https://customer-marine-api.open-meteo.com/v1/marine"
CUSTOMER_WEATHER_ENDPOINT = "https://customer-api.open-meteo.com/v1/forecast"
HISTORICAL_WEATHER_ENDPOINT = "https://historical-forecast-api.open-meteo.com/v1/forecast"
HISTORICAL_REANALYSIS_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_FORECAST_START = pd.Timestamp("2021-03-23T00:00:00Z")
REQUIRED_COLUMNS = {
    "time_utc",
    "latitude",
    "longitude",
    "current_east_ms",
    "current_north_ms",
    "wind_east_ms",
    "wind_north_ms",
    "source",
}


@dataclass(frozen=True)
class EnvironmentBundle:
    frame: pd.DataFrame
    requested_mode: str
    active_mode: str
    source: str
    cache_path: Path
    warnings: tuple[str, ...] = ()
    temporal_resolution: str = "hourly"

    def mean_forcing(self, diffusivity_m2s: float = 12.0) -> Forcing:
        return Forcing(
            current_east_ms=float(self.frame["current_east_ms"].mean()),
            current_north_ms=float(self.frame["current_north_ms"].mean()),
            wind_east_ms=float(self.frame["wind_east_ms"].mean()),
            wind_north_ms=float(self.frame["wind_north_ms"].mean()),
            diffusivity_m2s=diffusivity_m2s,
        )


def _api_url(endpoint: str, parameters: dict[str, object]) -> str:
    return f"{endpoint}?{urlencode(parameters)}"


def _utc_timestamp(value: str | datetime, label: str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.tz_convert("UTC")


def build_open_meteo_urls(
    latitude: float,
    longitude: float,
    forecast_days: int = 2,
    *,
    api_key: str | None = None,
) -> tuple[str, str]:
    api_key = (api_key if api_key is not None else os.environ.get("OPEN_METEO_API_KEY", "")).strip()
    marine_endpoint = CUSTOMER_MARINE_ENDPOINT if api_key else MARINE_ENDPOINT
    weather_endpoint = CUSTOMER_WEATHER_ENDPOINT if api_key else WEATHER_ENDPOINT
    common = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "GMT",
        "forecast_days": forecast_days,
    }
    if api_key:
        common["apikey"] = api_key
    marine = _api_url(
        marine_endpoint,
        {
            **common,
            "hourly": "ocean_current_velocity,ocean_current_direction",
            "cell_selection": "sea",
        },
    )
    weather = _api_url(
        weather_endpoint,
        {
            **common,
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",
        },
    )
    return marine, weather


def build_historical_wind_url(
    latitude: float,
    longitude: float,
    start: str | datetime,
    end: str | datetime,
) -> str:
    start_time = _utc_timestamp(start, "historical wind start")
    end_time = _utc_timestamp(end, "historical wind end")
    if start_time >= end_time:
        raise ValueError("historical wind start must be before end")
    endpoint = (
        HISTORICAL_REANALYSIS_ENDPOINT
        if start_time < HISTORICAL_FORECAST_START
        else HISTORICAL_WEATHER_ENDPOINT
    )
    return _api_url(
        endpoint,
        {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_time.strftime("%Y-%m-%d"),
            "end_date": end_time.strftime("%Y-%m-%d"),
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",
            "timezone": "GMT",
            "cell_selection": "sea",
        },
    )


def _read_json(url: str, timeout_seconds: int = 25, attempts: int = 3) -> dict:
    """Read a provider response with bounded, provider-aware retries.

    Free public APIs can briefly return 429/5xx responses. Retrying those
    responses with jitter is safe; authentication and request errors are not.
    """
    request = Request(url, headers={"User-Agent": "Espada-SIH26143/1.0"})
    for attempt in range(max(1, attempts)):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code < 600
            if not retryable or attempt + 1 >= attempts:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            try:
                delay = float(retry_after) if retry_after else 2.0**attempt
            except ValueError:
                delay = 2.0**attempt
            time.sleep(min(30.0, max(1.0, delay) + random.uniform(0.0, 0.5)))
        except URLError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(10.0, 2.0**attempt + random.uniform(0.0, 0.5)))
    raise RuntimeError("Environmental provider retry loop exited unexpectedly")


def _speed_to_ms(values: np.ndarray, unit: str) -> np.ndarray:
    normalized = unit.strip().lower().replace(" ", "")
    if normalized in {"m/s", "ms-1", "m/s²"}:
        return values
    if normalized in {"km/h", "kmh", "km/hours"}:
        return values / 3.6
    if normalized in {"kn", "knot", "knots"}:
        return values * 0.514444
    raise ValueError(f"Unsupported velocity unit: {unit}")


def _toward_components(speed_ms: np.ndarray, direction_degrees: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    radians = np.deg2rad(direction_degrees)
    return speed_ms * np.sin(radians), speed_ms * np.cos(radians)


def _from_components(speed_ms: np.ndarray, direction_degrees: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    east, north = _toward_components(speed_ms, direction_degrees)
    return -east, -north


def parse_open_meteo(marine: dict, weather: dict) -> pd.DataFrame:
    if marine.get("error") or weather.get("error"):
        raise ValueError("Environmental API returned an error response")
    marine_hourly = marine.get("hourly", {})
    weather_hourly = weather.get("hourly", {})
    current_speed = np.asarray(marine_hourly.get("ocean_current_velocity", []), dtype=float)
    current_direction = np.asarray(marine_hourly.get("ocean_current_direction", []), dtype=float)
    current_times = marine_hourly.get("time", [])
    if not current_times or len(current_times) != len(current_speed) or len(current_speed) != len(current_direction):
        raise ValueError("Marine response has incomplete current arrays")
    unit = marine.get("hourly_units", {}).get("ocean_current_velocity", "km/h")
    current_speed = _speed_to_ms(current_speed, unit)
    current_east, current_north = _toward_components(current_speed, current_direction)
    current = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(current_times, utc=True),
            "current_east_ms": current_east,
            "current_north_ms": current_north,
        }
    )

    wind_speed = np.asarray(weather_hourly.get("wind_speed_10m", []), dtype=float)
    wind_direction = np.asarray(weather_hourly.get("wind_direction_10m", []), dtype=float)
    wind_times = weather_hourly.get("time", [])
    if not wind_times or len(wind_times) != len(wind_speed) or len(wind_speed) != len(wind_direction):
        raise ValueError("Weather response has incomplete wind arrays")
    wind_unit = weather.get("hourly_units", {}).get("wind_speed_10m", "m/s")
    wind_speed = _speed_to_ms(wind_speed, wind_unit)
    wind_east, wind_north = _from_components(wind_speed, wind_direction)
    wind = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(wind_times, utc=True),
            "wind_east_ms": wind_east,
            "wind_north_ms": wind_north,
        }
    )
    frame = current.merge(wind, on="time_utc", how="inner")
    frame["latitude"] = float(marine["latitude"])
    frame["longitude"] = float(marine["longitude"])
    frame["source"] = "Open-Meteo / MeteoFrance SMOC currents + forecast wind"
    return validate_environment(frame)


def parse_historical_wind(weather: dict) -> pd.DataFrame:
    if weather.get("error"):
        raise ValueError("Historical wind API returned an error response")
    hourly = weather.get("hourly", {})
    times = hourly.get("time", [])
    speed = np.asarray(hourly.get("wind_speed_10m", []), dtype=float)
    direction = np.asarray(hourly.get("wind_direction_10m", []), dtype=float)
    if not times or len(times) != len(speed) or len(speed) != len(direction):
        raise ValueError("Historical wind response has incomplete arrays")
    unit = weather.get("hourly_units", {}).get("wind_speed_10m", "m/s")
    east, north = _from_components(_speed_to_ms(speed, unit), direction)
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(times, utc=True),
            "wind_east_ms": east,
            "wind_north_ms": north,
        }
    )
    if frame[["wind_east_ms", "wind_north_ms"]].isna().any().any():
        raise ValueError("Historical wind response contains missing values")
    return frame.drop_duplicates("time_utc").sort_values("time_utc").reset_index(drop=True)


def sync_historical_wind(
    cache_path: Path,
    *,
    start: str | datetime,
    end: str | datetime,
    latitude: float = 18.7167,
    longitude: float = 71.45,
    fetcher: Callable[[str], dict] = _read_json,
) -> dict[str, object]:
    start_time = _utc_timestamp(start, "historical wind start")
    end_time = _utc_timestamp(end, "historical wind end")
    url = build_historical_wind_url(latitude, longitude, start_time, end_time)
    frame = parse_historical_wind(fetcher(url))
    frame = frame[(frame["time_utc"] >= start_time) & (frame["time_utc"] <= end_time)].copy()
    if len(frame) < 2:
        raise ValueError("Historical wind response does not cover the requested window")
    records = frame.copy()
    records["time_utc"] = records["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    source = (
        "Open-Meteo Historical Weather API reanalysis wind"
        if url.startswith(HISTORICAL_REANALYSIS_ENDPOINT)
        else "Open-Meteo Historical Forecast API wind"
    )
    records["source"] = source
    payload = {
        "schema_version": "1.0",
        "source": source,
        "wind_source": source,
        "fetched_at_utc": format_utc(datetime.now(UTC)),
        "requested_start_utc": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_end_utc": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "selection": {"latitude": latitude, "longitude": longitude},
        "temporal_resolution": "hourly",
        "attribution": "Open-Meteo historical forecast or reanalysis API, selected by date coverage.",
        "limitations": [
            "Archived forecast and reanalysis values are model estimates, not direct wind observations.",
            "Point sampling does not represent every wind variation across the slick area.",
        ],
        "samples": records.to_dict(orient="records"),
    }
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(cache_path)
    return {
        "status": "PASS",
        "source": source,
        "cache_file": str(cache_path.resolve()),
        "sample_count": len(frame),
        "time_start_utc": records["time_utc"].iloc[0],
        "time_end_utc": records["time_utc"].iloc[-1],
        "selection": payload["selection"],
        "limitations": payload["limitations"],
    }


def validate_environment(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Environmental data is missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Environmental data is empty")
    numeric = [
        "latitude",
        "longitude",
        "current_east_ms",
        "current_north_ms",
        "wind_east_ms",
        "wind_north_ms",
    ]
    result = frame.copy()
    result["time_utc"] = pd.to_datetime(result["time_utc"], utc=True)
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if not np.isfinite(result[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Environmental data contains non-finite values")
    if result["time_utc"].duplicated().any():
        raise ValueError("Environmental data contains duplicate timestamps")
    return result.sort_values("time_utc").reset_index(drop=True)


def _cache_payload(frame: pd.DataFrame, source: str, fetched_at: datetime) -> dict:
    records = frame.copy()
    records["time_utc"] = records["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema_version": "1.0",
        "source": source,
        "fetched_at_utc": format_utc(fetched_at),
        "attribution": "Open-Meteo; ocean currents from MeteoFrance SMOC.",
        "wind_source": "Open-Meteo forecast wind",
        "temporal_resolution": "hourly",
        "limitations": [
            "Model output, not an observation or navigational product.",
            "Approximately 0.08 degree resolution; coastal accuracy is limited.",
        ],
        "samples": records.to_dict(orient="records"),
    }


def sync_open_meteo(
    cache_path: Path,
    *,
    latitude: float = 18.7167,
    longitude: float = 71.45,
    forecast_days: int = 2,
    fetcher: Callable[[str], dict] = _read_json,
) -> EnvironmentBundle:
    marine_url, weather_url = build_open_meteo_urls(latitude, longitude, forecast_days)
    frame = parse_open_meteo(fetcher(marine_url), fetcher(weather_url))
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _cache_payload(frame, str(frame["source"].iloc[0]), datetime.now(UTC))
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return EnvironmentBundle(frame, "live", "live", payload["source"], cache_path)


def load_cache(cache_path: Path, requested_mode: str = "cache") -> EnvironmentBundle:
    cache_path = Path(cache_path)
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    frame = validate_environment(pd.DataFrame(payload["samples"]))
    return EnvironmentBundle(
        frame,
        requested_mode,
        "cache",
        payload["source"],
        cache_path,
        tuple(payload.get("warnings", ())),
        str(payload.get("temporal_resolution", "hourly")),
    )


def synthetic_environment(cache_path: Path, requested_mode: str = "synthetic") -> EnvironmentBundle:
    start = datetime(2026, 3, 15, 0, 0)
    hours = np.arange(48, dtype=float)
    frame = pd.DataFrame(
        {
            "time_utc": [start + timedelta(hours=float(hour)) for hour in hours],
            "latitude": 18.7167,
            "longitude": 71.45,
            "current_east_ms": 0.32 + 0.025 * np.sin(hours / 5.0),
            "current_north_ms": 0.08 + 0.018 * np.cos(hours / 7.0),
            "wind_east_ms": 4.5 + 0.7 * np.sin(hours / 4.0),
            "wind_north_ms": -1.8 + 0.5 * np.cos(hours / 6.0),
            "source": "synthetic offline fallback",
        }
    )
    return EnvironmentBundle(
        validate_environment(frame),
        requested_mode,
        "synthetic",
        "synthetic offline fallback",
        Path(cache_path),
        ("No real cache was used; values are clearly labelled synthetic.",),
    )


def load_environment(
    mode: str,
    cache_path: Path,
    *,
    latitude: float = 18.7167,
    longitude: float = 71.45,
    fetcher: Callable[[str], dict] = _read_json,
) -> EnvironmentBundle:
    normalized = mode.lower()
    if normalized not in {"auto", "live", "cache", "synthetic"}:
        raise ValueError("mode must be auto, live, cache, or synthetic")
    if normalized == "synthetic":
        return synthetic_environment(cache_path)
    if normalized == "cache":
        return load_cache(cache_path)
    if normalized == "live":
        return sync_open_meteo(
            cache_path,
            latitude=latitude,
            longitude=longitude,
            fetcher=fetcher,
        )
    try:
        live = sync_open_meteo(
            cache_path,
            latitude=latitude,
            longitude=longitude,
            fetcher=fetcher,
        )
        return EnvironmentBundle(live.frame, "auto", "live", live.source, live.cache_path)
    except Exception as live_error:
        if Path(cache_path).exists():
            cached = load_cache(cache_path, requested_mode="auto")
            return EnvironmentBundle(
                cached.frame,
                "auto",
                "cache",
                cached.source,
                cached.cache_path,
                (f"Live refresh failed; using cache ({type(live_error).__name__}).",),
            )
        fallback = synthetic_environment(cache_path, requested_mode="auto")
        return EnvironmentBundle(
            fallback.frame,
            "auto",
            "synthetic",
            fallback.source,
            fallback.cache_path,
            (f"Live refresh failed and no cache exists ({type(live_error).__name__}).",),
        )


def write_environment_outputs(bundle: EnvironmentBundle, output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = bundle.frame
    forcing = bundle.mean_forcing()
    hours = np.arange(len(frame))
    current_speed = np.hypot(frame["current_east_ms"], frame["current_north_ms"])
    wind_speed = np.hypot(frame["wind_east_ms"], frame["wind_north_ms"])
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, constrained_layout=True)
    top.plot(hours, current_speed, color="#087F7B", linewidth=2.2)
    top.set_ylabel("Current (m/s)")
    top.set_title(f"Environmental forcing · {bundle.active_mode.upper()} mode")
    top.grid(alpha=0.2)
    bottom.plot(hours, wind_speed, color="#F49A24", linewidth=2.2)
    bottom.set_ylabel("Wind (m/s)")
    bottom.set_xlabel("Forcing sample")
    bottom.grid(alpha=0.2)
    fig.savefig(output_dir / "environment_timeseries.png", dpi=180)
    plt.close(fig)
    result = {
        "status": "PASS",
        "requested_mode": bundle.requested_mode,
        "active_mode": bundle.active_mode,
        "source": bundle.source,
        "temporal_resolution": bundle.temporal_resolution,
        "sample_count": len(frame),
        "time_start_utc": frame["time_utc"].iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_end_utc": frame["time_utc"].iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mean_forcing": forcing.to_dict(),
        "warnings": list(bundle.warnings),
        "artifacts": ["environment_status.json", "environment_timeseries.png"],
    }
    (output_dir / "environment_status.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result
