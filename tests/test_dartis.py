from pathlib import Path

import numpy as np

from espada.dartis import (
    DARTIS_SUBSETS,
    DartisRecord,
    _maximum_cardinality_overlap_matching,
    box_iou,
    component_boxes,
    dartis_jpeg_to_db,
    load_voc_boxes,
    match_boxes,
    parse_dartis_metadata,
    select_balanced_records,
)


def _record(subset: str, index: int, group: int) -> DartisRecord:
    return DartisRecord(
        subset=subset,
        jpg_file=f"{subset}-{index:04d}.jpg",
        xml_file=f"{subset}-{index:04d}.xml" if subset.startswith("o") else "",
        patch_name=f"S1_201901{group:02d}_VV_{index}",
        start_time=f"2019-01-{group:02d}T00:00:00",
        sentinel_id=f"S1_GROUP_{group}",
        width=640,
        height=640,
    )


def test_parse_dartis_metadata_collapses_multi_object_rows() -> None:
    header = "\t".join(
        [
            "Image set (subset; oc : oil/coast)",
            "IMAGE (jpg_file)",
            "Binary (xml_file)",
            "ID (patch_name)",
            "Date/Time (start_time)",
            "ID (Sentinel_ID)",
            "Width [pixel] (patch_width)",
            "Height [pixel] (patch_height)",
        ]
    )
    row = "\t".join(
        ["ow", "ow-0001.jpg", "ow-0001.xml", "S1_PATCH", "2019-01-01T00:00:00", "S1_ID", "640", "640"]
    )
    records = parse_dartis_metadata(f"/* description */\n{header}\n{row}\n{row}\n")
    assert len(records) == 1
    assert records[0].label == "oil"
    assert records[0].setting == "water"


def test_balanced_selection_is_deterministic_and_group_diverse() -> None:
    records = [
        _record(subset, index, (index % 3) + 1)
        for subset in DARTIS_SUBSETS
        for index in range(1, 7)
    ]
    first = select_balanced_records(records, per_subset=3, seed=26143)
    second = select_balanced_records(records, per_subset=3, seed=26143)
    assert first == second
    assert {subset: sum(item.subset == subset for item in first) for subset in DARTIS_SUBSETS} == {
        subset: 3 for subset in DARTIS_SUBSETS
    }
    for subset in DARTIS_SUBSETS:
        assert len({item.sentinel_id for item in first if item.subset == subset}) == 3


def test_dartis_sigmoid_inverse_is_centered_and_monotonic() -> None:
    converted = dartis_jpeg_to_db(np.array([[64.0, 127.5, 192.0]], dtype=np.float32))
    assert converted[0, 0] < converted[0, 1] < converted[0, 2]
    assert converted[0, 1] == -22.0


def test_component_boxes_and_one_to_one_matching() -> None:
    mask = np.zeros((50, 60), dtype=bool)
    mask[5:15, 7:17] = True
    mask[25:40, 30:50] = True
    predicted = component_boxes(mask, min_component_pixels=24)
    truth = [(7, 5, 17, 15), (31, 25, 50, 40)]
    matches = match_boxes(predicted, truth, iou_threshold=0.5)
    assert len(matches) == 2
    assert box_iou(predicted[0], truth[0]) == 1.0


def test_matching_maximizes_cardinality_instead_of_greedy_overlap() -> None:
    overlaps = np.array([[0.90, 0.80], [0.85, 0.10]], dtype=np.float64)
    matches = _maximum_cardinality_overlap_matching(overlaps, iou_threshold=0.5)
    assert {(prediction, truth) for prediction, truth, _ in matches} == {(0, 1), (1, 0)}


def test_metadata_rejects_remote_path_components() -> None:
    header = "\t".join(
        [
            "Image set (subset; oc : oil/coast)",
            "IMAGE (jpg_file)",
            "Binary (xml_file)",
            "ID (patch_name)",
            "Date/Time (start_time)",
            "ID (Sentinel_ID)",
            "Width [pixel] (patch_width)",
            "Height [pixel] (patch_height)",
        ]
    )
    row = "\t".join(
        ["ow", "../escape.jpg", "escape.xml", "S1_PATCH", "2019-01-01T00:00:00", "S1_ID", "640", "640"]
    )
    with np.testing.assert_raises(ValueError):
        parse_dartis_metadata(f"/* description */\n{header}\n{row}\n")


def test_pascal_voc_boxes_are_loaded(tmp_path: Path) -> None:
    annotation = tmp_path / "sample.xml"
    annotation.write_text(
        "<annotation><object><bndbox><xmin>7</xmin><ymin>5</ymin>"
        "<xmax>17</xmax><ymax>15</ymax></bndbox></object></annotation>",
        encoding="utf-8",
    )
    assert load_voc_boxes(annotation) == [(7, 5, 17, 15)]
