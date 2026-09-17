from __future__ import annotations

import json

import numpy as np

from espada.sar import apply_land_exclusion, filter_operational_components


def test_land_exclusion_removes_only_mapped_land_candidates(tmp_path) -> None:
    land = tmp_path / "land.geojson"
    land.write_text(
        json.dumps(
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0.45, 0], [0.45, 1], [0, 1], [0, 0]]],
                },
            }
        ),
        encoding="utf-8",
    )
    mask = np.ones((20, 20), dtype=bool)
    score = np.ones((20, 20), dtype=np.float32)

    filtered, filtered_score, report = apply_land_exclusion(
        mask, score, (0.0, 0.0, 1.0, 1.0), land
    )

    assert not filtered[:, :8].any()
    assert filtered[:, 13:].all()
    assert not filtered_score[:, :8].any()
    assert report["candidate_pixels_removed"] > 0
    assert report["candidate_pixels_after"] > 0


def test_operational_filter_removes_edge_and_tiny_components() -> None:
    mask = np.zeros((30, 30), dtype=bool)
    mask[0:8, 2:10] = True
    mask[12:14, 12:14] = True
    mask[10:22, 16:28] = True

    filtered, report = filter_operational_components(mask, minimum_pixels=32)

    assert not filtered[0:8, 2:10].any()
    assert not filtered[12:14, 12:14].any()
    assert filtered[10:22, 16:28].all()
    assert report["components_after"] == 1
