from __future__ import annotations

import zipfile
from pathlib import Path

import shapefile

from espada.coast import load_coast_mask
from espada.coast_download import clip_land_archive


def _archive(tmp_path: Path) -> Path:
    base = tmp_path / "ne_10m_land"
    writer = shapefile.Writer(str(base), shapeType=shapefile.POLYGON)
    writer.field("name", "C")
    writer.poly([[[9.0, 43.0], [9.0, 43.5], [9.5, 43.5], [9.5, 43.0], [9.0, 43.0]]])
    writer.record("test land")
    writer.close()
    archive = tmp_path / "land.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for suffix in (".shp", ".shx", ".dbf"):
            output.write(base.with_suffix(suffix), f"folder/ne_10m_land{suffix}")
    return archive


def test_archive_is_clipped_and_emits_a_loadable_mask(tmp_path: Path) -> None:
    output = tmp_path / "land_mask.geojson"
    result = clip_land_archive(
        _archive(tmp_path), output, (9.2, 43.2, 9.3, 43.3), padding_degrees=0.0
    )
    mask = load_coast_mask(output)
    assert result["status"] == "PASS"
    assert bool(mask.contains(9.25, 43.25)[0])
    assert not bool(mask.contains(9.35, 43.25)[0])
