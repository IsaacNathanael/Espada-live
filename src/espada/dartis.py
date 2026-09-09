from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

import numpy as np
from PIL import Image
from scipy import ndimage


DARTIS_DOI = "10.1594/PANGAEA.980773"
DARTIS_METADATA_URL = (
    "https://doi.pangaea.de/10.1594/PANGAEA.980773?format=textfile"
)
DARTIS_FILE_BASE = "https://download.pangaea.de/dataset/980773/files/"
DARTIS_CITATION = (
    "Yang, Yi-Jie; Singha, Suman (2025): Oil slicks, look-alikes and other "
    "remarkable SAR signatures in Sentinel-1 imagery in the Eastern "
    "Mediterranean Sea in 2019. PANGAEA, https://doi.org/10.1594/PANGAEA.980773"
)
DARTIS_SUBSETS = ("ow", "oc", "nw", "nc")
DARTIS_EXTERNAL_AUDIT_SEED = 26143
DARTIS_EXTERNAL_AUDIT_PER_SUBSET = 25
DARTIS_EXTERNAL_AUDIT_SELECTION_SHA256 = (
    "583b7d664e77d1caf13e16e137b67836993aa0e98a86dac921acd74a5a63b57e"
)
DARTIS_EXTERNAL_AUDIT_ROLE = (
    "locked external evaluation; never used for V6 training or calibration"
)

# The public DARTIS JPEGs were produced with I_N=255*sigmoid((I-beta)/(3*sigma)).
# This fixed output scale is the median full-scene standard deviation measured only
# from ESPADA's 14 training scenes; external labels never select this conversion.
ESPADA_TRAIN_MEDIAN_SCENE_STD_DB = 2.0834640860557556


@dataclass(frozen=True)
class DartisRecord:
    subset: str
    jpg_file: str
    xml_file: str
    patch_name: str
    start_time: str
    sentinel_id: str
    width: int
    height: int

    @property
    def label(self) -> str:
        return "oil" if self.subset.startswith("o") else "no_oil"

    @property
    def setting(self) -> str:
        return "water" if self.subset.endswith("w") else "coast"


def _validated_dataset_filename(value: str) -> str:
    """Accept a remote dataset basename, never a path supplied by metadata."""
    value = value.strip()
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or posix.name != value
        or windows.name != value
        or value in {".", ".."}
    ):
        raise ValueError(f"Unsafe DARTIS dataset filename: {value!r}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_body(text: str) -> str:
    if "*/" not in text:
        raise ValueError("DARTIS metadata is missing its PANGAEA description block")
    return text.split("*/", 1)[1].lstrip("\r\n")


def _field(fieldnames: Iterable[str], marker: str) -> str:
    matches = [name for name in fieldnames if marker in name]
    if len(matches) != 1:
        raise ValueError(f"Expected one DARTIS metadata field containing {marker!r}")
    return matches[0]


def parse_dartis_metadata(text: str) -> list[DartisRecord]:
    reader = csv.DictReader(io.StringIO(_metadata_body(text)), delimiter="\t")
    if not reader.fieldnames:
        raise ValueError("DARTIS metadata contains no tabular header")
    fields = {
        "subset": _field(reader.fieldnames, "subset;"),
        "jpg": _field(reader.fieldnames, "jpg_file"),
        "xml": _field(reader.fieldnames, "xml_file"),
        "patch": _field(reader.fieldnames, "patch_name"),
        "start": _field(reader.fieldnames, "start_time"),
        "sentinel": _field(reader.fieldnames, "Sentinel_ID"),
        "width": _field(reader.fieldnames, "patch_width"),
        "height": _field(reader.fieldnames, "patch_height"),
    }
    unique: dict[tuple[str, str], DartisRecord] = {}
    for row in reader:
        subset = row[fields["subset"]].strip()
        jpg_file = row[fields["jpg"]].strip()
        if subset not in DARTIS_SUBSETS or not jpg_file:
            continue
        jpg_file = _validated_dataset_filename(jpg_file)
        xml_value = row[fields["xml"]].strip()
        xml_file = _validated_dataset_filename(xml_value) if xml_value else ""
        record = DartisRecord(
            subset=subset,
            jpg_file=jpg_file,
            xml_file=xml_file,
            patch_name=row[fields["patch"]].strip(),
            start_time=row[fields["start"]].strip(),
            sentinel_id=row[fields["sentinel"]].strip(),
            width=int(row[fields["width"]]),
            height=int(row[fields["height"]]),
        )
        key = (subset, jpg_file)
        if key in unique and unique[key] != record:
            raise ValueError(f"Conflicting metadata for DARTIS image {jpg_file}")
        unique[key] = record
    records = sorted(unique.values(), key=lambda item: (item.subset, item.jpg_file))
    if not records:
        raise ValueError("No DARTIS image records were parsed")
    return records


def _stable_score(seed: int, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def select_balanced_records(
    records: Iterable[DartisRecord], *, per_subset: int = 25, seed: int = 26143
) -> list[DartisRecord]:
    if per_subset < 1:
        raise ValueError("per_subset must be at least one")
    selected: list[DartisRecord] = []
    records = list(records)
    for subset in DARTIS_SUBSETS:
        groups: dict[str, list[DartisRecord]] = defaultdict(list)
        for record in records:
            if record.subset == subset:
                group = record.sentinel_id or record.start_time[:10]
                groups[group].append(record)
        if sum(map(len, groups.values())) < per_subset:
            raise ValueError(f"DARTIS subset {subset} has fewer than {per_subset} images")
        group_names = sorted(groups, key=lambda value: _stable_score(seed, f"{subset}:{value}"))
        queues = {
            name: sorted(
                groups[name],
                key=lambda item: _stable_score(seed, f"{subset}:{item.jpg_file}"),
            )
            for name in group_names
        }
        subset_selection: list[DartisRecord] = []
        while len(subset_selection) < per_subset:
            progressed = False
            for name in group_names:
                if queues[name] and len(subset_selection) < per_subset:
                    subset_selection.append(queues[name].pop(0))
                    progressed = True
            if not progressed:
                break
        selected.extend(subset_selection)
    return sorted(selected, key=lambda item: (DARTIS_SUBSETS.index(item.subset), item.jpg_file))


def selection_sha256(records: Iterable[DartisRecord]) -> str:
    payload = json.dumps(
        [asdict(item) for item in records], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_image(path: Path, width: int, height: int) -> None:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        if image.size != (width, height):
            raise ValueError(
                f"DARTIS image {path.name} has size {image.size}, expected {(width, height)}"
            )


def load_voc_boxes(path: Path) -> list[tuple[int, int, int, int]]:
    root = ET.parse(path).getroot()
    boxes: list[tuple[int, int, int, int]] = []
    for item in root.findall("object"):
        box = item.find("bndbox")
        if box is None:
            continue
        values = tuple(int(float(box.findtext(name, "0"))) for name in ("xmin", "ymin", "xmax", "ymax"))
        xmin, ymin, xmax, ymax = values
        if xmax <= xmin or ymax <= ymin:
            raise ValueError(f"Invalid Pascal-VOC box in {path.name}: {values}")
        boxes.append(values)
    if not boxes:
        raise ValueError(f"Oil annotation {path.name} contains no bounding boxes")
    return boxes


def _download_file(session, url: str, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with session.get(url, stream=True, timeout=(20, 90)) as response:
                response.raise_for_status()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            stream.write(chunk)
            temporary.replace(destination)
            return destination.stat().st_size
        except Exception as exc:  # retries are reported if all attempts fail
            last_error = exc
    raise RuntimeError(f"Failed to download {url}: {last_error}")


def download_dartis_subset(
    output_dir: Path, *, per_subset: int = 25, seed: int = 26143
) -> dict[str, object]:
    import requests

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "ESPADA research client/0.6"
    metadata_path = output_dir / "DARTIS_2019.tab"
    if not metadata_path.is_file():
        response = session.get(DARTIS_METADATA_URL, timeout=(20, 90))
        response.raise_for_status()
        metadata_path.write_bytes(response.content)
    metadata_text = metadata_path.read_text(encoding="utf-8")
    all_records = parse_dartis_metadata(metadata_text)
    selected = select_balanced_records(all_records, per_subset=per_subset, seed=seed)
    selected_digest = selection_sha256(selected)
    is_locked_audit = (
        seed == DARTIS_EXTERNAL_AUDIT_SEED
        and per_subset == DARTIS_EXTERNAL_AUDIT_PER_SUBSET
    )
    if (
        is_locked_audit
        and selected_digest != DARTIS_EXTERNAL_AUDIT_SELECTION_SHA256
    ):
        raise RuntimeError(
            "The upstream DARTIS metadata no longer reproduces ESPADA's locked "
            "external-audit selection"
        )

    downloaded_bytes = 0
    reused_files = 0
    manifest_rows: list[dict[str, object]] = []
    for index, record in enumerate(selected, start=1):
        print(
            f"Preparing DARTIS image {index}/{len(selected)}: {record.jpg_file}",
            flush=True,
        )
        image_path = output_dir / "images" / record.subset / record.jpg_file
        try:
            _validate_image(image_path, record.width, record.height)
            reused_files += 1
        except (FileNotFoundError, OSError, ValueError):
            downloaded_bytes += _download_file(
                session, DARTIS_FILE_BASE + record.jpg_file, image_path
            )
            _validate_image(image_path, record.width, record.height)

        annotation_path: Path | None = None
        if record.xml_file:
            annotation_path = output_dir / "annotations" / record.subset / record.xml_file
            try:
                load_voc_boxes(annotation_path)
                reused_files += 1
            except (FileNotFoundError, OSError, ValueError, ET.ParseError):
                downloaded_bytes += _download_file(
                    session, DARTIS_FILE_BASE + record.xml_file, annotation_path
                )
                load_voc_boxes(annotation_path)
        elif record.label == "oil":
            raise ValueError(f"Oil image {record.jpg_file} has no annotation file")

        manifest_rows.append(
            {
                **asdict(record),
                "label": record.label,
                "setting": record.setting,
                "image_path": image_path.relative_to(output_dir).as_posix(),
                "annotation_path": (
                    annotation_path.relative_to(output_dir).as_posix()
                    if annotation_path is not None
                    else ""
                ),
                "image_sha256": _file_sha256(image_path),
                "annotation_sha256": (
                    _file_sha256(annotation_path)
                    if annotation_path is not None
                    else ""
                ),
            }
        )

    manifest_path = output_dir / "external_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
    manifest_digest = _file_sha256(manifest_path)
    counts = {
        subset: sum(item.subset == subset for item in selected) for subset in DARTIS_SUBSETS
    }
    status = {
        "status": "PASS",
        "dataset_role": (
            DARTIS_EXTERNAL_AUDIT_ROLE
            if is_locked_audit
            else "custom development subset; not a locked external evaluation"
        ),
        "provider": "PANGAEA",
        "dataset": "DARTIS_2019",
        "doi": DARTIS_DOI,
        "license": "CC BY 4.0",
        "citation": DARTIS_CITATION,
        "selection_seed": seed,
        "selection_sha256": selected_digest,
        "manifest_sha256": manifest_digest,
        "selected_images": len(selected),
        "selected_by_subset": counts,
        "selected_sentinel_products": len({item.sentinel_id for item in selected}),
        "downloaded_bytes_this_run": downloaded_bytes,
        "reused_files": reused_files,
        "manifest": str(manifest_path.resolve()),
        "limitations": [
            "This is a deterministic balanced subset, not the complete DARTIS collection.",
            "DARTIS provides oil bounding boxes rather than pixel segmentation masks.",
            "Published images are 8-bit normalized JPEGs, not original calibrated dB rasters.",
        ],
    }
    (output_dir / "download_status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )
    return status


def dartis_jpeg_to_db(
    image: np.ndarray, *, target_scene_std_db: float = ESPADA_TRAIN_MEDIAN_SCENE_STD_DB
) -> np.ndarray:
    """Approximately invert DARTIS' documented sigmoid image normalization."""
    array = np.asarray(image, dtype=np.float32)
    if array.ndim == 3:
        array = array[..., :3].mean(axis=2)
    if array.ndim != 2:
        raise ValueError("DARTIS input must be a two-dimensional image")
    if not np.isfinite(array).all() or array.min() < 0 or array.max() > 255:
        raise ValueError("DARTIS JPEG values must be finite and within [0, 255]")
    unit = np.clip(array / 255.0, 0.5 / 255.0, 254.5 / 255.0)
    standardized = 3.0 * np.log(unit / (1.0 - unit))
    return (-22.0 + target_scene_std_db * standardized).astype(np.float32)


def component_boxes(
    mask: np.ndarray, *, min_component_pixels: int = 24
) -> list[tuple[int, int, int, int]]:
    labels, count = ndimage.label(np.asarray(mask, dtype=bool), structure=np.ones((3, 3)))
    boxes: list[tuple[int, int, int, int]] = []
    for label_id in range(1, count + 1):
        rows, columns = np.where(labels == label_id)
        if rows.size < min_component_pixels:
            continue
        boxes.append(
            (
                int(columns.min()),
                int(rows.min()),
                int(columns.max()) + 1,
                int(rows.max()) + 1,
            )
        )
    return boxes


def box_iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def match_boxes(
    predicted: Iterable[tuple[int, int, int, int]],
    truth: Iterable[tuple[int, int, int, int]],
    *,
    iou_threshold: float = 0.5,
) -> list[tuple[int, int, float]]:
    predicted = list(predicted)
    truth = list(truth)
    overlaps = np.asarray(
        [
            [box_iou(predicted_box, truth_box) for truth_box in truth]
            for predicted_box in predicted
        ],
        dtype=np.float64,
    )
    if not predicted or not truth:
        return []
    return _maximum_cardinality_overlap_matching(overlaps, iou_threshold=iou_threshold)


def _maximum_cardinality_overlap_matching(
    overlaps: np.ndarray, *, iou_threshold: float
) -> list[tuple[int, int, float]]:
    """Return a deterministic maximum-cardinality one-to-one threshold match."""
    overlaps = np.asarray(overlaps, dtype=np.float64)
    if overlaps.ndim != 2:
        raise ValueError("overlaps must be a two-dimensional matrix")
    predicted_count, truth_count = overlaps.shape
    adjacency = [
        sorted(
            (int(index) for index in np.flatnonzero(overlaps[row] >= iou_threshold)),
            key=lambda index: (-float(overlaps[row, index]), index),
        )
        for row in range(predicted_count)
    ]
    truth_to_prediction = [-1] * truth_count

    def augment(predicted_index: int, visited_truth: set[int]) -> bool:
        for truth_index in adjacency[predicted_index]:
            if truth_index in visited_truth:
                continue
            visited_truth.add(truth_index)
            previous = truth_to_prediction[truth_index]
            if previous == -1 or augment(previous, visited_truth):
                truth_to_prediction[truth_index] = predicted_index
                return True
        return False

    prediction_order = sorted(
        range(predicted_count), key=lambda index: (len(adjacency[index]), index)
    )
    for predicted_index in prediction_order:
        augment(predicted_index, set())

    return sorted(
        (
            predicted_index,
            truth_index,
            float(overlaps[predicted_index, truth_index]),
        )
        for truth_index, predicted_index in enumerate(truth_to_prediction)
        if predicted_index >= 0
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download a locked DARTIS external subset")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-subset", type=int, default=25)
    parser.add_argument("--seed", type=int, default=26143)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = download_dartis_subset(
            args.out, per_subset=args.per_subset, seed=args.seed
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
