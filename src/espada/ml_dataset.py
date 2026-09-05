from __future__ import annotations

import csv
import hashlib
import itertools
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


DATE_PATTERN = re.compile(r"^(\d{4})_?(\d{2})_?(\d{2})")
DECLARED_SCENES = 23


@dataclass(frozen=True)
class SceneRecord:
    scene_id: str
    acquisition_group: str
    original_split: str
    image_path: str
    mask_path: str
    width: int
    height: int
    pixels: int
    oil_pixels: int
    oil_fraction: float
    image_min_db: float
    image_max_db: float


def acquisition_group(scene_id: str) -> str:
    match = DATE_PATTERN.match(Path(scene_id).stem)
    if not match:
        raise ValueError(f"Cannot infer acquisition date from scene name: {scene_id}")
    return "-".join(match.groups())


def _paired_paths(root: Path) -> list[tuple[str, Path, Path]]:
    pairs: list[tuple[str, Path, Path]] = []
    for original_split in ("train", "test"):
        image_dir = root / original_split / "images"
        mask_dir = root / original_split / "masks"
        if not image_dir.is_dir() or not mask_dir.is_dir():
            raise FileNotFoundError(
                f"Expected '{image_dir}' and '{mask_dir}'. Download and extract the dataset first."
            )
        images = {path.name: path for path in image_dir.glob("*.tif")}
        masks = {path.name: path for path in mask_dir.glob("*.tif")}
        if images.keys() != masks.keys():
            missing_masks = sorted(images.keys() - masks.keys())
            missing_images = sorted(masks.keys() - images.keys())
            raise ValueError(
                f"Unpaired dataset files. Missing masks={missing_masks}; missing images={missing_images}"
            )
        pairs.extend((original_split, images[name], masks[name]) for name in sorted(images))
    return pairs


def inspect_scenes(root: Path) -> tuple[list[SceneRecord], list[str]]:
    root = Path(root)
    records: list[SceneRecord] = []
    errors: list[str] = []
    for original_split, image_path, mask_path in _paired_paths(root):
        try:
            with Image.open(image_path) as image_file, Image.open(mask_path) as mask_file:
                image = np.asarray(image_file, dtype=np.float32)
                mask = np.asarray(mask_file, dtype=np.float32)
            if image.ndim != 2 or mask.ndim != 2:
                raise ValueError(f"expected 2-D rasters, got image={image.shape}, mask={mask.shape}")
            if image.shape != mask.shape:
                raise ValueError(f"image/mask shape mismatch: {image.shape} versus {mask.shape}")
            if not np.isfinite(image).all():
                raise ValueError("image contains NaN or infinity")
            if not np.isfinite(mask).all():
                raise ValueError("mask contains NaN or infinity")
            mask_values = np.unique(mask)
            if not np.isin(mask_values, (0.0, 1.0)).all():
                raise ValueError(f"mask is not binary: values={mask_values[:10].tolist()}")
            oil_pixels = int(np.count_nonzero(mask))
            pixels = int(mask.size)
            records.append(
                SceneRecord(
                    scene_id=image_path.name,
                    acquisition_group=acquisition_group(image_path.name),
                    original_split=original_split,
                    image_path=image_path.relative_to(root).as_posix(),
                    mask_path=mask_path.relative_to(root).as_posix(),
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                    pixels=pixels,
                    oil_pixels=oil_pixels,
                    oil_fraction=oil_pixels / pixels,
                    image_min_db=float(image.min()),
                    image_max_db=float(image.max()),
                )
            )
        except Exception as exc:
            errors.append(f"{image_path.name}: {exc}")
    return records, errors


def _csv_scene_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {Path(row["paths"].replace("\\", "/")).name for row in csv.DictReader(stream)}


def supplied_split_audit(root: Path, records: list[SceneRecord]) -> dict:
    train_csv = _csv_scene_names(root / "train" / "dataframe_train_dataset_256_90.csv")
    validation_csv = _csv_scene_names(root / "train" / "dataframe_val_dataset_256_90.csv")
    original_train_groups = {
        item.acquisition_group for item in records if item.original_split == "train"
    }
    original_test_groups = {
        item.acquisition_group for item in records if item.original_split == "test"
    }
    return {
        "declared_scene_pairs": DECLARED_SCENES,
        "archive_scene_pairs": len(records),
        "train_csv_scenes": len(train_csv),
        "validation_csv_scenes": len(validation_csv),
        "train_validation_scene_overlap": sorted(train_csv & validation_csv),
        "original_train_test_acquisition_overlap": sorted(
            original_train_groups & original_test_groups
        ),
        "safe_to_use_supplied_splits": not (
            train_csv & validation_csv or original_train_groups & original_test_groups
        ),
    }


def _subset_score(
    selected: tuple[str, ...],
    grouped: dict[str, list[SceneRecord]],
    totals: tuple[int, int, int],
    target_fraction: float,
    seed: int,
) -> tuple[float, str]:
    scenes = [item for group in selected for item in grouped[group]]
    total_scenes, total_pixels, total_oil = totals
    fractions = (
        len(scenes) / total_scenes,
        sum(item.pixels for item in scenes) / total_pixels,
        sum(item.oil_pixels for item in scenes) / total_oil,
    )
    score = 2.0 * abs(fractions[0] - target_fraction)
    score += abs(fractions[1] - target_fraction)
    score += abs(fractions[2] - target_fraction)
    tie_breaker = hashlib.sha256(
        f"{seed}:{','.join(selected)}".encode("utf-8")
    ).hexdigest()
    return score, tie_breaker


def create_group_safe_split(
    records: list[SceneRecord], *, seed: int = 26143
) -> dict[str, str]:
    grouped: dict[str, list[SceneRecord]] = {}
    for item in records:
        grouped.setdefault(item.acquisition_group, []).append(item)
    groups = sorted(grouped)
    if len(groups) < 5:
        raise ValueError("At least five acquisition groups are required for train/validation/test")

    total = (
        len(records),
        sum(item.pixels for item in records),
        sum(item.oil_pixels for item in records),
    )
    test_count = max(1, round(len(groups) * 0.20))
    validation_count = max(1, round(len(groups) * 0.15))
    if test_count + validation_count >= len(groups):
        raise ValueError("Not enough acquisition groups remain for training")

    test_groups = min(
        itertools.combinations(groups, test_count),
        key=lambda choice: _subset_score(choice, grouped, total, 0.20, seed),
    )
    remaining = [group for group in groups if group not in test_groups]
    validation_groups = min(
        itertools.combinations(remaining, validation_count),
        key=lambda choice: _subset_score(choice, grouped, total, 0.15, seed + 1),
    )

    assignment: dict[str, str] = {}
    for group in groups:
        split = "test" if group in test_groups else "validation" if group in validation_groups else "train"
        for item in grouped[group]:
            assignment[item.scene_id] = split
    return assignment


def _split_metrics(records: list[SceneRecord], assignment: dict[str, str]) -> list[dict]:
    rows = []
    for split in ("train", "validation", "test"):
        selected = [item for item in records if assignment[item.scene_id] == split]
        pixels = sum(item.pixels for item in selected)
        oil = sum(item.oil_pixels for item in selected)
        rows.append(
            {
                "split": split,
                "scenes": len(selected),
                "acquisition_groups": len({item.acquisition_group for item in selected}),
                "pixels": pixels,
                "oil_pixels": oil,
                "oil_fraction": oil / pixels,
            }
        )
    return rows


def _write_audit_plot(
    path: Path, records: list[SceneRecord], assignment: dict[str, str], metrics: list[dict]
) -> None:
    colors = {"train": "#087f7b", "validation": "#f49a24", "test": "#d05a4a"}
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4), constrained_layout=True)
    names = [row["split"] for row in metrics]
    axes[0].bar(names, [row["scenes"] for row in metrics], color=[colors[name] for name in names])
    axes[0].set_title("Scene allocation")
    axes[0].set_ylabel("Paired scenes")
    axes[0].grid(axis="y", alpha=0.2)

    axes[1].bar(
        names,
        [100 * row["oil_fraction"] for row in metrics],
        color=[colors[name] for name in names],
    )
    axes[1].set_title("Oil-pixel prevalence")
    axes[1].set_ylabel("Oil pixels (%)")
    axes[1].grid(axis="y", alpha=0.2)

    for split in names:
        selected = [item for item in records if assignment[item.scene_id] == split]
        axes[2].scatter(
            [item.pixels / 1_000_000 for item in selected],
            [100 * item.oil_fraction for item in selected],
            label=split,
            color=colors[split],
            alpha=0.85,
        )
    axes[2].set_title("Scene diversity")
    axes[2].set_xlabel("Scene size (million pixels)")
    axes[2].set_ylabel("Oil pixels (%)")
    axes[2].legend(frameon=False)
    axes[2].grid(alpha=0.2)
    fig.suptitle("ESPADA · leakage-safe SAR dataset audit", fontsize=15)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def audit_dataset(root: Path, output_dir: Path, *, seed: int = 26143) -> dict:
    root = Path(root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records, errors = inspect_scenes(root)
    if not records:
        raise ValueError("No valid image/mask pairs were found")
    source_audit = supplied_split_audit(root, records)
    assignment = create_group_safe_split(records, seed=seed)
    metrics = _split_metrics(records, assignment)

    groups_by_split = {
        split: {
            item.acquisition_group
            for item in records
            if assignment[item.scene_id] == split
        }
        for split in ("train", "validation", "test")
    }
    safe_overlap = sorted(
        (groups_by_split["train"] & groups_by_split["validation"])
        | (groups_by_split["train"] & groups_by_split["test"])
        | (groups_by_split["validation"] & groups_by_split["test"])
    )
    manifest_path = output_dir / "scene_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as stream:
        fieldnames = list(asdict(records[0])) + ["split"]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for item in records:
            writer.writerow({**asdict(item), "split": assignment[item.scene_id]})

    warnings = []
    if source_audit["archive_scene_pairs"] != source_audit["declared_scene_pairs"]:
        warnings.append(
            "Zenodo describes 23 pairs, but the published v1 archive contains 21 paired TIFF scenes."
        )
    if not source_audit["safe_to_use_supplied_splits"]:
        warnings.append(
            "The supplied splits leak scenes/acquisition dates; ESPADA replaced them with a group-safe split."
        )
    result = {
        "status": "PASS" if not errors and not safe_overlap else "FAIL",
        "dataset": "Oil Spill Segmentation",
        "doi": "10.5281/zenodo.4672426",
        "license": "CC BY 4.0",
        "source_audit": source_audit,
        "validation_errors": errors,
        "safe_split": {
            "method": "deterministic acquisition-date grouping balanced by scenes, pixels and oil pixels",
            "seed": seed,
            "acquisition_overlap": safe_overlap,
            "metrics": metrics,
        },
        "warnings": warnings,
        "artifacts": ["scene_manifest.csv", "dataset_audit.json", "dataset_audit.png"],
    }
    (output_dir / "dataset_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_audit_plot(output_dir / "dataset_audit.png", records, assignment, metrics)
    return result
