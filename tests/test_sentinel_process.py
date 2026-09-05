import json
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from espada.sentinel_process import (
    MAX_SYNC_DIMENSION,
    build_sentinel1_process_payload,
    download_sentinel1_subset,
)


def _catalog(path: Path, *, covered: bool = True) -> Path:
    path.write_text(
        json.dumps(
            {
                "status": "PASS" if covered else "PARTIAL",
                "recommended_scene": {
                    "id": "S1_TEST_COG",
                    "acquisition_time_utc": "2026-08-25T01:02:37Z",
                    "target_point_covered": covered,
                    "has_vv": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _tiff() -> bytes:
    output = BytesIO()
    values = np.linspace(0.01, 1.0, 64 * 64, dtype=np.float32).reshape(64, 64)
    Image.fromarray(values, mode="F").save(output, format="TIFF")
    return output.getvalue()


def test_process_payload_is_bounded_calibrated_vv_request() -> None:
    payload = build_sentinel1_process_payload(
        bbox=(71.25, 18.55, 71.65, 18.90),
        acquisition_time_utc="2026-08-25T01:02:37Z",
        width=1536,
        height=1400,
    )
    data = payload["input"]["data"][0]
    assert data["type"] == "sentinel-1-grd"
    assert data["processing"]["backCoeff"] == "SIGMA0_ELLIPSOID"
    assert '"VV"' in payload["evalscript"]
    assert payload["output"]["width"] <= MAX_SYNC_DIMENSION


def test_download_uses_environment_credentials_without_persisting_them(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CDSE_CLIENT_ID", "private-client")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "private-secret")
    seen: dict[str, object] = {}

    def token_fetcher(client_id: str, client_secret: str) -> str:
        seen["credentials"] = (client_id, client_secret)
        return "short-lived-token"

    def processor(payload: dict, token: str) -> bytes:
        seen["token"] = token
        seen["payload"] = payload
        return _tiff()

    output = tmp_path / "out"
    result = download_sentinel1_subset(
        _catalog(tmp_path / "catalog.json"),
        output,
        bbox=(71.25, 18.55, 71.65, 18.90),
        width=64,
        height=64,
        token_fetcher=token_fetcher,
        processor=processor,
    )
    assert result["status"] == "PASS"
    assert seen["credentials"] == ("private-client", "private-secret")
    assert seen["token"] == "short-lived-token"
    assert (output / "sentinel1_vv.tif").exists()
    assert (output / "sentinel1_vv_quicklook.png").exists()
    status = (output / "sentinel1_subset_status.json").read_text()
    assert "private-client" not in status
    assert "private-secret" not in status
    assert "short-lived-token" not in status


def test_download_rejects_partial_scene_before_request(tmp_path: Path) -> None:
    try:
        download_sentinel1_subset(
            _catalog(tmp_path / "catalog.json", covered=False),
            tmp_path / "out",
            bbox=(71.25, 18.55, 71.65, 18.90),
        )
    except ValueError as error:
        assert "does not cover" in str(error)
    else:
        raise AssertionError("partial scene was accepted")
