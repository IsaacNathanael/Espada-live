from pathlib import Path

import numpy as np
from PIL import Image

from espada.ml_dataset import acquisition_group, audit_dataset


def _write_pair(root: Path, original_split: str, name: str, offset: int) -> None:
    image_dir = root / original_split / "images"
    mask_dir = root / original_split / "masks"
    image_dir.mkdir(parents=True, exist_ok=True)
    mask_dir.mkdir(parents=True, exist_ok=True)
    image = np.linspace(-30, 5, 64 * 64, dtype=np.float32).reshape(64, 64)
    mask = np.zeros((64, 64), dtype=np.float32)
    mask[8 + offset : 20 + offset, 10:26] = 1
    Image.fromarray(image, mode="F").save(image_dir / name)
    Image.fromarray(mask, mode="F").save(mask_dir / name)


def test_acquisition_group_handles_both_filename_styles() -> None:
    assert acquisition_group("2018_12_19_f_.tif") == "2018-12-19"
    assert acquisition_group("20200319b.tif") == "2020-03-19"


def test_audit_builds_non_overlapping_group_split(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    names = [
        ("train", "2018_01_01.tif"),
        ("train", "2018_01_02.tif"),
        ("train", "2018_01_03.tif"),
        ("test", "2018_01_04.tif"),
        ("test", "2018_01_05.tif"),
    ]
    for index, (original_split, name) in enumerate(names):
        _write_pair(root, original_split, name, index)

    result = audit_dataset(root, tmp_path / "out")

    assert result["status"] == "PASS"
    assert result["safe_split"]["acquisition_overlap"] == []
    assert {row["split"] for row in result["safe_split"]["metrics"]} == {
        "train",
        "validation",
        "test",
    }
    assert (tmp_path / "out" / "scene_manifest.csv").exists()
    assert (tmp_path / "out" / "dataset_audit.png").exists()
