from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image
from shapely.geometry import Point, box, mapping, shape

from .models import format_utc


STAC_ITEMS_ENDPOINT = (
    "https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items"
)


def _utc(value: str | datetime, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _format(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SentinelSearchRequest:
    bbox: tuple[float, float, float, float]
    start: datetime
    end: datetime
    target: tuple[float, float] | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        min_lon, min_lat, max_lon, max_lat = self.bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("Sentinel-1 search bbox is invalid")
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Sentinel-1 search times must include a timezone")
        if self.start >= self.end:
            raise ValueError("Sentinel-1 search start must be before end")
        if self.target is not None:
            lon, lat = self.target
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError("Sentinel-1 target point is invalid")
        if not 1 <= self.limit <= 100:
            raise ValueError("Sentinel-1 search limit must be between 1 and 100")


def build_sentinel1_search_url(request: SentinelSearchRequest) -> str:
    parameters = {
        "bbox": ",".join(str(value) for value in request.bbox),
        "datetime": f"{_format(request.start)}/{_format(request.end)}",
        "limit": request.limit,
    }
    return f"{STAC_ITEMS_ENDPOINT}?{urlencode(parameters)}"


def _read_json(url: str, timeout_seconds: int = 30) -> dict:
    request = Request(url, headers={"User-Agent": "Espada-SIH26143/0.3"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_bytes(url: str, timeout_seconds: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": "Espada-SIH26143/0.3"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _https_asset(asset: dict | None) -> str | None:
    if not asset:
        return None
    alternate = asset.get("alternate", {}).get("https", {})
    return alternate.get("href") or asset.get("href")


def _parse_scene(feature: dict, request: SentinelSearchRequest) -> tuple[dict, dict] | None:
    geometry_data = feature.get("geometry")
    if not geometry_data:
        return None
    try:
        footprint = shape(geometry_data)
    except (TypeError, ValueError):
        return None
    if footprint.is_empty:
        return None
    if not footprint.is_valid:
        footprint = footprint.buffer(0)
    area = box(*request.bbox)
    overlap = footprint.intersection(area).area / area.area
    if overlap <= 0:
        return None

    properties = feature.get("properties", {})
    assets = feature.get("assets", {})
    polarizations = [str(value).upper() for value in properties.get("sar:polarizations", [])]
    target_covered = None
    if request.target is not None:
        target_covered = bool(footprint.covers(Point(*request.target)))
    acquired = _utc(properties.get("datetime"), "Sentinel-1 acquisition time")
    midpoint = request.start + (request.end - request.start) / 2
    distance_hours = abs((acquired - midpoint).total_seconds()) / 3600
    private = properties.get("_private", {})
    instrument_mode = properties.get("sar:instrument_mode")
    has_vv = "VV" in polarizations and "vv" in assets
    score = (
        overlap * 70
        + (20 if target_covered else 0)
        + (5 if has_vv else 0)
        + (3 if instrument_mode == "IW" else 0)
        + (2 if len(polarizations) >= 2 else 0)
    )
    scene = {
        "id": feature.get("id"),
        "acquisition_time_utc": _format(acquired),
        "platform": properties.get("platform"),
        "product_type": properties.get("product:type"),
        "instrument_mode": instrument_mode,
        "orbit_state": properties.get("sat:orbit_state"),
        "relative_orbit": properties.get("sat:relative_orbit"),
        "polarizations": polarizations,
        "has_vv": has_vv,
        "aoi_overlap_fraction": round(float(overlap), 6),
        "target_point_covered": target_covered,
        "distance_to_window_midpoint_hours": round(distance_hours, 3),
        "ranking_score": round(float(score), 3),
        "product_size_bytes": private.get("product_size"),
        "thumbnail_url": assets.get("thumbnail", {}).get("href"),
        "vv_download_url": _https_asset(assets.get("vv")),
        "product_download_url": assets.get("Product", {}).get("href"),
        "download_requires_cdse_authentication": True,
    }
    footprint_feature = {
        "type": "Feature",
        "id": feature.get("id"),
        "properties": {
            "acquisition_time_utc": scene["acquisition_time_utc"],
            "aoi_overlap_fraction": scene["aoi_overlap_fraction"],
            "target_point_covered": target_covered,
            "polarizations": polarizations,
            "orbit_state": scene["orbit_state"],
        },
        "geometry": mapping(footprint),
    }
    return scene, footprint_feature


def discover_sentinel1(
    request: SentinelSearchRequest,
    output_dir: Path,
    *,
    fetcher: Callable[[str], dict] = _read_json,
    preview_fetcher: Callable[[str], bytes] = _read_bytes,
    download_preview: bool = True,
) -> dict[str, object]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    response = fetcher(build_sentinel1_search_url(request))
    features = response.get("features")
    if not isinstance(features, list):
        raise ValueError("Copernicus STAC response does not contain a feature list")

    parsed = [item for feature in features if (item := _parse_scene(feature, request))]
    parsed.sort(
        key=lambda item: (
            bool(item[0]["target_point_covered"]),
            item[0]["ranking_score"],
            -item[0]["distance_to_window_midpoint_hours"],
        ),
        reverse=True,
    )
    scenes = [item[0] for item in parsed]
    footprints = [item[1] for item in parsed]
    if not scenes:
        status = "NO_DATA"
        recommendation = "Expand the date window or choose a different area."
    elif request.target is not None and not any(scene["target_point_covered"] for scene in scenes):
        status = "PARTIAL"
        recommendation = (
            "Do not run attribution for the target point with this scene; search nearby dates "
            "for a scene that covers it, then re-download matching AIS and environment data."
        )
    else:
        status = "PASS"
        recommendation = (
            "Use the top scene acquisition time as the slick observation time, then download "
            "a small calibrated VV subset for analyst review."
        )

    warnings: list[str] = []
    preview: dict[str, object] | None = None
    if download_preview and scenes and scenes[0]["thumbnail_url"]:
        try:
            content = preview_fetcher(str(scenes[0]["thumbnail_url"]))
            image = Image.open(BytesIO(content)).convert("RGB")
            preview_path = output_dir / "sentinel1_preview.png"
            image.save(preview_path)
            preview = {
                "file": str(preview_path.resolve()),
                "width": image.width,
                "height": image.height,
                "analysis_ready": False,
            }
        except Exception as error:
            warnings.append(f"Preview download failed ({type(error).__name__}); catalogue result is still valid.")

    result = {
        "status": status,
        "provider": "Copernicus Data Space Ecosystem STAC",
        "collection": "sentinel-1-grd",
        "searched_at_utc": format_utc(datetime.now(UTC)),
        "request": {
            "bbox": list(request.bbox),
            "start_utc": _format(request.start),
            "end_utc": _format(request.end),
            "target": list(request.target) if request.target else None,
        },
        "scenes_returned": len(scenes),
        "recommended_scene": scenes[0] if scenes else None,
        "scenes": scenes,
        "preview": preview,
        "recommendation": recommendation,
        "warnings": warnings,
        "limitations": [
            "Catalogue intersection does not prove that an oil slick is visible in the scene.",
            "The thumbnail is for visual triage only and is not radiometrically calibrated analysis data.",
            "Dark SAR signatures can also be caused by low wind, rain cells, biogenic films or land effects.",
            "Full-resolution VV data requires a Copernicus Data Space authenticated download.",
        ],
        "artifacts": ["sentinel1_catalog.json", "sentinel1_footprints.geojson"]
        + (["sentinel1_preview.png"] if preview else []),
    }
    footprints_payload = {
        "type": "FeatureCollection",
        "features": footprints,
    }
    (output_dir / "sentinel1_footprints.geojson").write_text(
        json.dumps(footprints_payload, indent=2), encoding="utf-8"
    )
    (output_dir / "sentinel1_catalog.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result
