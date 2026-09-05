import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from espada.sentinel_catalog import (
    SentinelSearchRequest,
    build_sentinel1_search_url,
    discover_sentinel1,
)


def _request(target: tuple[float, float] | None = (71.45, 18.7167)) -> SentinelSearchRequest:
    return SentinelSearchRequest(
        (70.8, 17.8, 73.0, 20.0),
        datetime(2026, 8, 24, tzinfo=UTC),
        datetime(2026, 8, 26, tzinfo=UTC),
        target,
    )


def _feature(*, min_lat: float = 17.1, max_lat: float = 19.5) -> dict:
    return {
        "type": "Feature",
        "id": "S1_TEST_COG",
        "bbox": [70.9, min_lat, 73.7, max_lat],
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[70.9, min_lat], [73.7, min_lat], [73.7, max_lat], [70.9, max_lat], [70.9, min_lat]]
            ],
        },
        "properties": {
            "datetime": "2026-08-25T01:02:37Z",
            "platform": "sentinel-1d",
            "product:type": "IW_GRDH_1S",
            "sar:instrument_mode": "IW",
            "sat:orbit_state": "descending",
            "sat:relative_orbit": 107,
            "sar:polarizations": ["VV", "VH"],
            "_private": {"product_size": 1_000_000},
        },
        "assets": {
            "vv": {
                "href": "s3://example/vv.tiff",
                "alternate": {"https": {"href": "https://example.test/vv.tiff"}},
            },
            "Product": {"href": "https://example.test/product"},
            "thumbnail": {"href": "https://example.test/preview.png"},
        },
    }


def _png() -> bytes:
    output = BytesIO()
    Image.new("L", (24, 16), 128).save(output, format="PNG")
    return output.getvalue()


def test_search_url_uses_current_cdse_stac_and_exact_window() -> None:
    url = build_sentinel1_search_url(_request())
    assert url.startswith("https://stac.dataspace.copernicus.eu/v1/")
    assert "bbox=70.8%2C17.8%2C73.0%2C20.0" in url
    assert "2026-08-24T00%3A00%3A00Z%2F2026-08-26T00%3A00%3A00Z" in url


def test_discovery_ranks_target_covering_vv_scene_and_writes_audit(tmp_path: Path) -> None:
    result = discover_sentinel1(
        _request(),
        tmp_path,
        fetcher=lambda _: {"type": "FeatureCollection", "features": [_feature()]},
        preview_fetcher=lambda _: _png(),
    )
    assert result["status"] == "PASS"
    assert result["recommended_scene"]["target_point_covered"] is True
    assert result["recommended_scene"]["has_vv"] is True
    assert result["preview"]["analysis_ready"] is False
    assert (tmp_path / "sentinel1_catalog.json").exists()
    assert (tmp_path / "sentinel1_footprints.geojson").exists()
    assert (tmp_path / "sentinel1_preview.png").exists()
    audit = json.loads((tmp_path / "sentinel1_catalog.json").read_text())
    assert audit["collection"] == "sentinel-1-grd"


def test_discovery_marks_edge_only_scene_partial(tmp_path: Path) -> None:
    result = discover_sentinel1(
        _request(),
        tmp_path,
        fetcher=lambda _: {"features": [_feature(min_lat=19.6, max_lat=21.5)]},
        download_preview=False,
    )
    assert result["status"] == "PARTIAL"
    assert result["recommended_scene"]["target_point_covered"] is False


def test_discovery_reports_no_data_without_fabricating_scene(tmp_path: Path) -> None:
    result = discover_sentinel1(
        _request(), tmp_path, fetcher=lambda _: {"features": []}, download_preview=False
    )
    assert result["status"] == "NO_DATA"
    assert result["recommended_scene"] is None
