"""Select and download a leakage-aware DARTIS detector pilot."""
import argparse
import csv
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from .dartis import (
    DARTIS_CITATION,
    DARTIS_DOI,
    DARTIS_FILE_BASE,
    DARTIS_SUBSETS,
    _download_file,
    _file_sha256,
    _validate_image,
    load_voc_boxes,
)

PILOT_SEED = 2614311


def _stable(seed: int, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def select_pilot_rows(plan: dict, *, train_per_subset: int = 60,
                      validation_per_subset: int = 20, seed: int = PILOT_SEED):
    if min(train_per_subset, validation_per_subset) < 1:
        raise ValueError("Pilot quotas must be positive")
    if plan.get("report", {}).get("status") != "PLAN_READY":
        raise ValueError("A successful partition plan is required")
    rows = plan.get("rows", [])
    selected = []
    for partition, quota in (("train", train_per_subset),
                             ("validation", validation_per_subset)):
        for subset in DARTIS_SUBSETS:
            groups = defaultdict(list)
            for row in rows:
                if row.get("partition") == partition and row.get("subset") == subset:
                    groups[row["group_id"]].append(row)
            ordered_groups = sorted(groups, key=lambda value: _stable(seed, f"{partition}:{subset}:{value}"))
            queues = {group: sorted(groups[group], key=lambda row: _stable(seed, row["jpg_file"]))
                      for group in ordered_groups}
            chosen = []
            while len(chosen) < quota:
                progressed = False
                for group in ordered_groups:
                    if queues[group] and len(chosen) < quota:
                        chosen.append(queues[group].pop(0))
                        progressed = True
                if not progressed:
                    break
            if len(chosen) != quota:
                raise ValueError(f"Only {len(chosen)} rows available for {partition}/{subset}; need {quota}")
            selected.extend(chosen)
    names = [row["jpg_file"] for row in selected]
    if len(names) != len(set(names)):
        raise ValueError("Pilot selection contains duplicate filenames")
    group_partitions = defaultdict(set)
    for row in selected:
        group_partitions[row["group_id"]].add(row["partition"])
    if any(len(value) > 1 for value in group_partitions.values()):
        raise ValueError("An acquisition group crosses pilot partitions")
    return sorted(selected, key=lambda row: (row["partition"], DARTIS_SUBSETS.index(row["subset"]), row["jpg_file"]))


def selection_sha256(rows) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def difference_hash(path: Path) -> str:
    with Image.open(path) as source:
        image = np.asarray(source.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.uint8)
    bits = image[:, 1:] > image[:, :-1]
    value = sum(int(bit) << index for index, bit in enumerate(bits.ravel()))
    return f"{value:016x}"


def duplicate_audit(rows):
    exact = defaultdict(list)
    perceptual = defaultdict(list)
    for row in rows:
        identity = (row["partition"], row["jpg_file"])
        exact[row["image_sha256"]].append(identity)
        perceptual[row["difference_hash"]].append(identity)
    def conflicts(index):
        return [{"digest": digest, "files": values} for digest, values in sorted(index.items())
                if len(values) > 1 and len({item[0] for item in values}) > 1]
    exact_conflicts = conflicts(exact)
    perceptual_conflicts = conflicts(perceptual)
    return {
        "status": "FAIL" if exact_conflicts else "REVIEW_REQUIRED" if perceptual_conflicts else "PASS",
        "exact_cross_partition_conflicts": exact_conflicts,
        "identical_difference_hash_cross_partition_flags": perceptual_conflicts,
        "interpretation": "Exact conflicts invalidate the split. Difference-hash matches are review flags, not automatic duplicates.",
    }


def prepare_pilot(plan_path: Path, output_dir: Path, *, train_per_subset=60,
                  validation_per_subset=20, plan_only=False):
    plan_path, output_dir = Path(plan_path), Path(output_dir)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    selected = select_pilot_rows(plan, train_per_subset=train_per_subset,
                                 validation_per_subset=validation_per_subset)
    selection_digest = selection_sha256(selected)
    output_dir.mkdir(parents=True, exist_ok=True)
    selection_path = output_dir / "pilot_selection.json"
    selection_payload = json.dumps({
        "seed": PILOT_SEED, "partition_plan_sha256": _file_sha256(plan_path),
        "selection_sha256": selection_digest, "rows": selected,
    }, indent=2) + "\n"
    if selection_path.exists() and selection_path.read_text(encoding="utf-8") != selection_payload:
        raise ValueError("Existing pilot selection differs; use a new output directory")
    selection_path.write_text(selection_payload, encoding="utf-8")
    counts = Counter((row["partition"], row["subset"]) for row in selected)
    if plan_only:
        result = {"status": "PLAN_READY", "selected_images": len(selected),
                  "selection_sha256": selection_digest,
                  "selected_by_partition_subset": {f"{p}/{s}": n for (p, s), n in sorted(counts.items())},
                  "download_started": False}
        print(json.dumps(result, indent=2))
        return result

    import requests
    session = requests.Session()
    session.headers["User-Agent"] = "ESPADA research client/0.7"
    manifest_rows = []
    downloaded_bytes = reused_files = 0
    for index, row in enumerate(selected, 1):
        print(f"Preparing pilot image {index}/{len(selected)}: {row['jpg_file']}", flush=True)
        image_path = output_dir / "images" / row["partition"] / row["subset"] / row["jpg_file"]
        try:
            _validate_image(image_path, int(row["width"]), int(row["height"]))
            reused_files += 1
        except (FileNotFoundError, OSError, ValueError):
            downloaded_bytes += _download_file(session, DARTIS_FILE_BASE + row["jpg_file"], image_path)
            _validate_image(image_path, int(row["width"]), int(row["height"]))
        annotation_path = None
        if row.get("xml_file"):
            annotation_path = output_dir / "annotations" / row["partition"] / row["subset"] / row["xml_file"]
            try:
                load_voc_boxes(annotation_path)
                reused_files += 1
            except (FileNotFoundError, OSError, ValueError, ET.ParseError):
                downloaded_bytes += _download_file(session, DARTIS_FILE_BASE + row["xml_file"], annotation_path)
                load_voc_boxes(annotation_path)
        elif row["subset"].startswith("o"):
            raise ValueError(f"Oil image {row['jpg_file']} has no annotation")
        manifest_rows.append({
            **row,
            "image_path": image_path.relative_to(output_dir).as_posix(),
            "annotation_path": annotation_path.relative_to(output_dir).as_posix() if annotation_path else "",
            "image_sha256": _file_sha256(image_path),
            "annotation_sha256": _file_sha256(annotation_path) if annotation_path else "",
            "difference_hash": difference_hash(image_path),
        })
    audit = duplicate_audit(manifest_rows)
    manifest_path = output_dir / "pilot_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
    (output_dir / "duplicate_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    status = {
        "status": "PASS" if audit["status"] == "PASS" else audit["status"],
        "provider": "PANGAEA", "dataset": "DARTIS_2019", "doi": DARTIS_DOI,
        "license": "CC BY 4.0", "citation": DARTIS_CITATION,
        "partition_plan_sha256": _file_sha256(plan_path), "selection_sha256": selection_digest,
        "selected_images": len(selected),
        "selected_by_partition_subset": {f"{p}/{s}": n for (p, s), n in sorted(counts.items())},
        "downloaded_bytes_this_run": downloaded_bytes, "reused_files": reused_files,
        "manifest_sha256": _file_sha256(manifest_path), "duplicate_audit": audit,
        "reserved_test_accessed": False,
        "limitations": ["Object boxes are not pixel segmentation masks.",
                        "This pilot is for development; the reserved test allocation remains unopened."],
    }
    (output_dir / "download_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--train-per-subset", type=int, default=60)
    parser.add_argument("--validation-per-subset", type=int, default=20)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        prepare_pilot(args.plan, args.out, train_per_subset=args.train_per_subset,
                      validation_per_subset=args.validation_per_subset, plan_only=args.plan_only)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
