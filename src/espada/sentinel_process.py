from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from .models import format_utc
from .sar import preprocess_sar


TOKEN_ENDPOINT = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
PROCESS_ENDPOINT = "https://sh.dataspace.copernicus.eu/process/v1"
MAX_SYNC_DIMENSION = 2500


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Sentinel-1 acquisition time must include a timezone")
    return parsed.astimezone(UTC)


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise ValueError("Sentinel-1 processing bbox is invalid")


def build_sentinel1_process_payload(
    *,
    bbox: tuple[float, float, float, float],
    acquisition_time_utc: str,
    width: int = 1536,
    height: int = 1400,
) -> dict:
    _validate_bbox(bbox)
    if not 32 <= width <= MAX_SYNC_DIMENSION or not 32 <= height <= MAX_SYNC_DIMENSION:
        raise ValueError("Sentinel Hub synchronous output dimensions must be 32 to 2500 pixels")
    acquired = _utc(acquisition_time_utc)
    start = acquired - timedelta(minutes=2)
    end = acquired + timedelta(minutes=2)
    evalscript = """//VERSION=3
function setup() {
  return {
    input: [{ bands: [\"VV\", \"dataMask\"], units: \"LINEAR_POWER\" }],
    output: { id: \"default\", bands: 1, sampleType: \"FLOAT32\" }
  };
}
function evaluatePixel(sample) {
  return [sample.dataMask ? sample.VV : 0.0];
}
"""
    return {
        "input": {
            "bounds": {
                "bbox": list(bbox),
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {
                            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                        "mosaickingOrder": "mostRecent",
                    },
                    "processing": {
                        "orthorectify": True,
                        "backCoeff": "SIGMA0_ELLIPSOID",
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": evalscript,
    }


def request_cdse_token(
    client_id: str,
    client_secret: str,
    *,
    opener: Callable[..., object] = urlopen,
) -> str:
    if not client_id.strip() or not client_secret.strip():
        raise ValueError("CDSE OAuth client ID and secret are required")
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Espada-SIH26143/0.3",
        },
        method="POST",
    )
    with opener(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise ValueError("CDSE token response did not contain an access token")
    return str(token)


def _post_process(payload: dict, token: str, timeout_seconds: int = 180) -> bytes:
    request = Request(
        PROCESS_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "image/tiff",
            "User-Agent": "Espada-SIH26143/0.3",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except HTTPError as error:
        detail = error.read(800).decode("utf-8", errors="replace")
        raise RuntimeError(f"Sentinel Hub processing failed with HTTP {error.code}: {detail}") from error


def _quicklook(tiff_content: bytes, output_path: Path) -> tuple[int, int]:
    with Image.open(BytesIO(tiff_content)) as source:
        image = np.asarray(source, dtype=float)
    if image.ndim == 3:
        image = image[..., 0]
    display, _ = preprocess_sar(image)
    Image.fromarray(np.uint8(display * 255), mode="L").save(output_path)
    return int(image.shape[1]), int(image.shape[0])


def download_sentinel1_subset(
    catalog_path: Path,
    output_dir: Path,
    *,
    bbox: tuple[float, float, float, float],
    width: int = 1536,
    height: int = 1400,
    client_id_env: str = "CDSE_CLIENT_ID",
    client_secret_env: str = "CDSE_CLIENT_SECRET",
    token_fetcher: Callable[[str, str], str] = request_cdse_token,
    processor: Callable[[dict, str], bytes] = _post_process,
) -> dict[str, object]:
    catalog_path = Path(catalog_path)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    scene = catalog.get("recommended_scene")
    if not scene:
        raise ValueError("Sentinel-1 catalogue has no recommended scene")
    if scene.get("target_point_covered") is False:
        raise ValueError("Recommended Sentinel-1 scene does not cover the target point")
    if not scene.get("has_vv"):
        raise ValueError("Recommended Sentinel-1 scene has no VV polarization")
    client_id = os.environ.get(client_id_env, "")
    client_secret = os.environ.get(client_secret_env, "")
    if not client_id or not client_secret:
        raise RuntimeError(
            f"Set {client_id_env} and {client_secret_env} in .env using a Copernicus Data Space OAuth client"
        )

    payload = build_sentinel1_process_payload(
        bbox=bbox,
        acquisition_time_utc=scene["acquisition_time_utc"],
        width=width,
        height=height,
    )
    token = token_fetcher(client_id, client_secret)
    content = processor(payload, token)
    if not content.startswith((b"II*\x00", b"MM\x00*")):
        raise ValueError("Sentinel Hub response is not a TIFF image")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "sentinel1_vv.tif"
    temporary = image_path.with_suffix(".tif.tmp")
    temporary.write_bytes(content)
    temporary.replace(image_path)
    quicklook_path = output_dir / "sentinel1_vv_quicklook.png"
    actual_width, actual_height = _quicklook(content, quicklook_path)
    result = {
        "status": "PASS",
        "provider": "Copernicus Data Space Sentinel Hub Process API",
        "scene_id": scene["id"],
        "acquisition_time_utc": scene["acquisition_time_utc"],
        "polarization": "VV",
        "measurement": "linear-power backscatter",
        "orthorectified": True,
        "backscatter_coefficient": "SIGMA0_ELLIPSOID",
        "bbox": list(bbox),
        "width": actual_width,
        "height": actual_height,
        "bytes": len(content),
        "downloaded_at_utc": format_utc(datetime.now(UTC)),
        "credential_handling": (
            "OAuth client credentials were read from environment variables; neither credentials nor access token were saved"
        ),
        "limitations": [
            "This is a calibrated image input, not proof that an oil slick is present.",
            "Candidate dark regions require analyst review and lookalike rejection.",
            "The synchronous API crop is resolution-limited to keep processing cost and download size controlled.",
        ],
        "artifacts": [
            "sentinel1_vv.tif",
            "sentinel1_vv_quicklook.png",
            "sentinel1_subset_status.json",
        ],
    }
    (output_dir / "sentinel1_subset_status.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result
