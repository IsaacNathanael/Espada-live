from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import requests
import shapefile
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union


NATURAL_EARTH_LAND_URL = (
    "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"
)
ALLOWED_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


def _download(url: str, destination: Path) -> None:
    response = requests.get(url, timeout=(15, 180))
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.write_bytes(response.content)
    partial.replace(destination)


def clip_land_archive(
    archive_path: Path,
    output_path: Path,
    bbox: tuple[float, float, float, float],
    *,
    padding_degrees: float = 0.25,
) -> dict[str, object]:
    min_lon, min_lat, max_lon, max_lat = (float(value) for value in bbox)
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise ValueError("Invalid coastline bounding box")
    if padding_degrees < 0 or padding_degrees > 5:
        raise ValueError("Coastline padding must be between zero and five degrees")
    archive_path = Path(archive_path).resolve()
    if not archive_path.exists():
        raise FileNotFoundError(f"Natural Earth archive not found: {archive_path}")
    region = box(
        max(-180.0, min_lon - padding_degrees),
        max(-90.0, min_lat - padding_degrees),
        min(180.0, max_lon + padding_degrees),
        min(90.0, max_lat + padding_degrees),
    )
    with tempfile.TemporaryDirectory(prefix="espada-coast-") as temporary:
        temporary_path = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            selected = {
                Path(name).suffix.lower(): name
                for name in archive.namelist()
                if Path(name).stem == "ne_10m_land"
                and Path(name).suffix.lower() in ALLOWED_PARTS
            }
            if not {".shp", ".shx", ".dbf"}.issubset(selected):
                raise ValueError("Natural Earth archive is missing required shapefile parts")
            for suffix, member in selected.items():
                (temporary_path / f"ne_10m_land{suffix}").write_bytes(archive.read(member))
        geometries = []
        reader = shapefile.Reader(str(temporary_path / "ne_10m_land.shp"))
        try:
            for item in reader.iterShapes():
                geometry = shape(item.__geo_interface__)
                if geometry.intersects(region):
                    clipped = geometry.intersection(region)
                    if not clipped.is_empty:
                        geometries.append(clipped)
        finally:
            reader.close()
    if not geometries:
        raise ValueError("No Natural Earth land intersects the requested region")
    land = unary_union(geometries)
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "type": "Feature",
        "properties": {
            "dataset": "Natural Earth 1:10m land",
            "source_url": NATURAL_EARTH_LAND_URL,
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "requested_bbox": [min_lon, min_lat, max_lon, max_lat],
            "padding_degrees": padding_degrees,
            "license": "Natural Earth public domain",
        },
        "geometry": mapping(land),
    }
    output_path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    return {
        "status": "PASS",
        "dataset": "Natural Earth 1:10m land",
        "source_url": NATURAL_EARTH_LAND_URL,
        "archive_sha256": document["properties"]["archive_sha256"],
        "requested_bbox": [min_lon, min_lat, max_lon, max_lat],
        "padding_degrees": padding_degrees,
        "land_mask": str(output_path),
        "limitations": [
            "Natural Earth 1:10m is cartographic land geometry, not a navigation-grade shoreline.",
            "The particle boundary rejects any step segment that touches land; beaching and resuspension are not modelled.",
        ],
    }


def sync_land_mask(
    output_path: Path,
    cache_path: Path,
    bbox: tuple[float, float, float, float],
    *,
    padding_degrees: float = 0.25,
) -> dict[str, object]:
    cache_path = Path(cache_path).resolve()
    if not cache_path.exists():
        _download(NATURAL_EARTH_LAND_URL, cache_path)
    result = clip_land_archive(
        cache_path, output_path, bbox, padding_degrees=padding_degrees
    )
    status_path = Path(output_path).with_name("coastline_status.json")
    result["status_file"] = str(status_path.resolve())
    status_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a Natural Earth coastline mask")
    parser.add_argument("--bbox", type=float, nargs=4, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--padding-degrees", type=float, default=0.25)
    args = parser.parse_args()
    result = sync_land_mask(
        args.output,
        args.cache,
        tuple(args.bbox),
        padding_degrees=args.padding_degrees,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
