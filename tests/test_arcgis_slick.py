from __future__ import annotations

import json

from espada import arcgis_slick
from espada.slick import load_slick


def test_import_arcgis_polygon_layer_merges_cells(monkeypatch, tmp_path):
    def fake_request(url, params):
        if not url.endswith("/query"):
            return {
                "name": "Possible slick",
                "description": "Subject to ground verification",
                "copyrightText": "Test source",
                "objectIdField": "FID",
                "maxRecordCount": 2000,
            }
        if params.get("returnCountOnly") == "true":
            return {"count": 2}
        return {
            "features": [
                {"geometry": {"rings": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
                {"geometry": {"rings": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}},
            ]
        }

    monkeypatch.setattr(arcgis_slick, "_request_json", fake_request)
    output = tmp_path / "slick.geojson"
    result = arcgis_slick.import_arcgis_polygon_layer(
        layer_url="https://example.test/FeatureServer/1",
        output_path=output,
        observation_time_utc="2023-03-06T21:47:35Z",
        source="External expert mapping",
        detection_confidence=0.7,
        review_status="external_expert_mapping",
        simplify_degrees=0.0,
    )

    assert result["status"] == "PASS"
    assert result["source_feature_count"] == 2
    observation = load_slick(output)
    assert observation.polygon.is_valid
    assert observation.polygon.area == 2.0
    assert observation.review_status == "external_expert_mapping"
    properties = json.loads(output.read_text(encoding="utf-8"))["features"][0]["properties"]
    assert properties["source_feature_count"] == 2


def test_import_arcgis_polygon_layer_preserves_disconnected_regions(monkeypatch, tmp_path):
    def fake_request(url, params):
        if not url.endswith("/query"):
            return {"name": "Possible slick", "objectIdField": "FID", "maxRecordCount": 2000}
        if params.get("returnCountOnly") == "true":
            return {"count": 2}
        return {
            "features": [
                {"geometry": {"rings": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
                {"geometry": {"rings": [[[3, 0], [4, 0], [4, 1], [3, 1], [3, 0]]]}},
            ]
        }

    monkeypatch.setattr(arcgis_slick, "_request_json", fake_request)
    output = tmp_path / "disconnected.geojson"
    result = arcgis_slick.import_arcgis_polygon_layer(
        layer_url="https://example.test/FeatureServer/1",
        output_path=output,
        observation_time_utc="2023-03-06T21:47:35Z",
        source="External expert mapping",
        detection_confidence=0.7,
        review_status="external_expert_mapping",
        simplify_degrees=0.0,
    )

    observation = load_slick(output)
    assert observation.polygon.geom_type == "MultiPolygon"
    assert observation.polygon.area == 2.0
    assert result["connected_components"] == 2
    assert result["selected_area_fraction"] == 1.0
