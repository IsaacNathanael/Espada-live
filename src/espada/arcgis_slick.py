from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests
from shapely import coverage_union_all, make_valid, union_all
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping


def _request_json(url: str, params: dict[str, object]) -> dict:
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    document = response.json()
    if "error" in document:
        raise RuntimeError(json.dumps(document["error"], indent=2))
    return document


def import_arcgis_polygon_layer(
    layer_url: str,
    output_path: Path,
    observation_time_utc: str,
    source: str,
    detection_confidence: float,
    review_status: str,
    simplify_degrees: float = 0.00002,
) -> dict:
    layer_url = layer_url.rstrip("/")
    metadata = _request_json(layer_url, {"f": "json"})
    object_id_field = metadata.get("objectIdField", "OBJECTID")
    page_size = min(int(metadata.get("maxRecordCount", 2000)), 2000)
    count_document = _request_json(
        f"{layer_url}/query",
        {"where": "1=1", "returnCountOnly": "true", "f": "json"},
    )
    expected_count = int(count_document["count"])

    polygons: list[Polygon] = []
    offset = 0
    while offset < expected_count:
        page = _request_json(
            f"{layer_url}/query",
            {
                "where": "1=1",
                "outFields": object_id_field,
                "returnGeometry": "true",
                "outSR": 4326,
                "orderByFields": object_id_field,
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "json",
            },
        )
        features = page.get("features", [])
        if not features:
            break
        for feature in features:
            rings = feature.get("geometry", {}).get("rings", [])
            for ring in rings:
                polygon = Polygon(ring)
                if not polygon.is_valid:
                    polygon = polygon.buffer(0)
                if isinstance(polygon, Polygon) and not polygon.is_empty:
                    polygons.append(polygon)
                elif isinstance(polygon, MultiPolygon):
                    polygons.extend(part for part in polygon.geoms if not part.is_empty)
        offset += len(features)

    if not polygons:
        raise RuntimeError("The ArcGIS layer returned no usable polygon geometry")
    try:
        merged = coverage_union_all(polygons)
    except Exception:
        merged = union_all(polygons)
    components = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]
    components = [part for part in components if isinstance(part, Polygon) and not part.is_empty]
    total_area = sum(part.area for part in components)
    retained = merged.simplify(simplify_degrees, preserve_topology=True)
    retained = make_valid(retained)
    if isinstance(retained, GeometryCollection):
        retained = union_all(
            [part for part in retained.geoms if isinstance(part, (Polygon, MultiPolygon))]
        )
    if not isinstance(retained, (Polygon, MultiPolygon)) or retained.is_empty or not retained.is_valid:
        raise RuntimeError("The ArcGIS geometry could not be repaired into valid polygonal geometry")

    retained_area_fraction = min(max(retained.area / total_area, 0.0), 1.0) if total_area else 0.0
    properties = {
        "observation_time_utc": observation_time_utc,
        "detection_confidence": float(detection_confidence),
        "source": source,
        "review_status": review_status,
        "source_layer_url": layer_url,
        "source_layer_name": metadata.get("name"),
        "source_description": metadata.get("description", ""),
        "source_copyright": metadata.get("copyrightText", ""),
        "selection": "all connected polygons from the published layer",
        "source_feature_count": expected_count,
        "connected_components": len(components),
        "selected_area_fraction": retained_area_fraction,
        "retained_area_fraction": retained_area_fraction,
        "caveat": "Published as a possible oil slick and subject to ground verification.",
    }
    document = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": properties, "geometry": mapping(retained)}
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return {
        "status": "PASS",
        "output": str(output_path.resolve()),
        "source_feature_count": expected_count,
        "downloaded_polygon_count": len(polygons),
        "connected_components": len(components),
        "selected_area_fraction": properties["selected_area_fraction"],
        "bounds": list(retained.bounds),
        "review_status": review_status,
        "limitations": [
            "The source describes the layer as a possible oil slick subject to ground verification.",
            "ESPADA retains every connected region and samples them in proportion to mapped area.",
            "This external expert mapping is not an ESPADA V6 model detection.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a public ArcGIS polygon layer as a reviewed slick input")
    parser.add_argument("--layer-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observation-time", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--confidence", type=float, default=0.70)
    parser.add_argument("--review-status", default="external_expert_mapping")
    parser.add_argument("--simplify-degrees", type=float, default=0.00002)
    args = parser.parse_args()
    if not 0.0 <= args.confidence <= 1.0:
        parser.error("--confidence must be between 0 and 1")
    result = import_arcgis_polygon_layer(
        layer_url=args.layer_url,
        output_path=args.output,
        observation_time_utc=args.observation_time,
        source=args.source,
        detection_confidence=args.confidence,
        review_status=args.review_status,
        simplify_degrees=args.simplify_degrees,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
