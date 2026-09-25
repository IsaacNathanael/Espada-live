import json
from pathlib import Path

import numpy as np

from espada.environment import (
    build_historical_wind_url,
    build_open_meteo_urls,
    load_environment,
    parse_open_meteo,
    sync_historical_wind,
    write_environment_outputs,
)
from espada.demo import run_demo


def _responses() -> tuple[dict, dict]:
    times = [f"2026-09-03T{hour:02d}:00" for hour in range(24)]
    marine = {
        "latitude": 18.70,
        "longitude": 71.46,
        "hourly_units": {"ocean_current_velocity": "km/h"},
        "hourly": {
            "time": times,
            "ocean_current_velocity": [3.6, 7.2] + [0.72] * 22,
            "ocean_current_direction": [90.0, 0.0] + [90.0] * 22,
        },
    }
    weather = {
        "hourly_units": {"wind_speed_10m": "m/s"},
        "hourly": {
            "time": times,
            "wind_speed_10m": [5.0, 4.0] + [4.0] * 22,
            "wind_direction_10m": [270.0, 180.0] + [270.0] * 22,
        },
    }
    return marine, weather


def test_open_meteo_conversion_respects_direction_conventions() -> None:
    marine, weather = _responses()
    frame = parse_open_meteo(marine, weather)
    assert np.isclose(frame.loc[0, "current_east_ms"], 1.0)
    assert np.isclose(frame.loc[1, "current_north_ms"], 2.0)
    assert np.isclose(frame.loc[0, "wind_east_ms"], 5.0)
    assert np.isclose(frame.loc[1, "wind_north_ms"], 4.0)


def test_live_mode_writes_cache_and_cache_mode_reopens_it(tmp_path: Path) -> None:
    marine, weather = _responses()
    replies = iter([marine, weather])
    cache = tmp_path / "environment.json"
    live = load_environment("live", cache, fetcher=lambda _: next(replies))
    assert live.active_mode == "live"
    assert cache.exists()
    cached = load_environment("cache", cache)
    assert cached.active_mode == "cache"
    assert len(cached.frame) == 24


def test_auto_mode_falls_back_without_mislabeling(tmp_path: Path) -> None:
    def offline(_: str) -> dict:
        raise OSError("offline")

    bundle = load_environment("auto", tmp_path / "missing.json", fetcher=offline)
    assert bundle.active_mode == "synthetic"
    assert "synthetic" in bundle.source
    result = write_environment_outputs(bundle, tmp_path / "out")
    assert result["status"] == "PASS"
    assert result["active_mode"] == "synthetic"
    assert (tmp_path / "out" / "environment_timeseries.png").exists()


def test_urls_request_required_variables() -> None:
    marine, weather = build_open_meteo_urls(18.7167, 71.45)
    assert "ocean_current_velocity" in marine
    assert "ocean_current_direction" in marine
    assert "wind_speed_10m" in weather
    assert "wind_direction_10m" in weather


def test_customer_open_meteo_key_uses_dedicated_endpoints() -> None:
    marine, weather = build_open_meteo_urls(1.255, 104.125, api_key="private-key")
    assert marine.startswith("https://customer-marine-api.open-meteo.com/")
    assert weather.startswith("https://customer-api.open-meteo.com/")
    assert "apikey=private-key" in marine
    assert "apikey=private-key" in weather


def test_historical_wind_uses_archive_host_and_exact_dates(tmp_path: Path) -> None:
    times = [f"2026-08-29T{hour:02d}:00" for hour in range(24)] + [
        f"2026-08-30T{hour:02d}:00" for hour in range(24)
    ]
    response = {
        "hourly_units": {"wind_speed_10m": "m/s"},
        "hourly": {
            "time": times,
            "wind_speed_10m": [5.0] * len(times),
            "wind_direction_10m": [270.0] * len(times),
        },
    }
    captured: list[str] = []
    cache = tmp_path / "wind.json"
    result = sync_historical_wind(
        cache,
        start="2026-08-29T06:00:00Z",
        end="2026-08-30T18:00:00Z",
        fetcher=lambda url: captured.append(url) or response,
    )
    assert result["status"] == "PASS"
    assert result["sample_count"] == 37
    assert "historical-forecast-api.open-meteo.com" in captured[0]
    assert "start_date=2026-08-29" in captured[0]
    payload = json.loads(cache.read_text())
    assert payload["wind_source"] == "Open-Meteo Historical Forecast API wind"
    assert np.isclose(payload["samples"][0]["wind_east_ms"], 5.0)


def test_historical_wind_requires_timezone() -> None:
    with np.testing.assert_raises_regex(ValueError, "timezone"):
        build_historical_wind_url(18.7, 71.4, "2026-08-29", "2026-08-30T00:00:00Z")


def test_historical_wind_uses_reanalysis_before_forecast_archive() -> None:
    url = build_historical_wind_url(
        43.246167,
        9.4795,
        "2018-10-07T03:00:00Z",
        "2018-10-08T08:00:00Z",
    )
    assert "archive-api.open-meteo.com" in url
    assert "start_date=2018-10-07" in url


def test_demo_can_use_a_real_cache_contract(tmp_path: Path) -> None:
    marine, weather = _responses()
    replies = iter([marine, weather])
    cache = tmp_path / "environment.json"
    load_environment("live", cache, fetcher=lambda _: next(replies))
    result = run_demo(
        tmp_path / "demo",
        particles=160,
        ensemble_members=4,
        check_opendrift=False,
        environment_mode="cache",
        environment_cache=cache,
    )
    assert result["status"] == "PASS"
    assert result["environment"]["active_mode"] == "cache"
    assert result["environment"]["source"].startswith("Open-Meteo")
    verification = json.loads(
        (tmp_path / "demo" / "physics" / "verification_result.json").read_text(encoding="utf-8")
    )
    assert "time-varying" in verification["backend"]
    assert (tmp_path / "demo" / "physics" / "forcing_series.csv").exists()
