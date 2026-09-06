from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from .ml_evaluate import file_sha256, infer_full_scene
from .ml_metrics import ProbabilityHistogram
from .ml_model import build_segmentation_model
from .ml_preprocess import FIXED_MINMAX


def calibrate_threshold(
    dataset_root: Path,
    manifest_path: Path,
    checkpoint_path: Path,
    output_dir: Path,
    *,
    batch_size: int = 8,
) -> dict:
    dataset_root = Path(dataset_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with Path(manifest_path).open("r", encoding="utf-8-sig", newline="") as stream:
        validation_scenes = [
            row for row in csv.DictReader(stream) if row["split"] == "validation"
        ]
    if not validation_scenes:
        raise ValueError("Manifest contains no validation scenes")

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_segmentation_model(
        checkpoint.get("model_config", {}), pretrained_encoder=False
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    inference_patch_size = int(checkpoint.get("training_config", {}).get("patch_size", 256))
    inference_stride = max(inference_patch_size * 3 // 4, 1)
    normalization_mode = str(checkpoint.get("normalization", {}).get("mode", FIXED_MINMAX))
    histogram = ProbabilityHistogram(bins=1000)
    for index, scene in enumerate(validation_scenes, start=1):
        print(
            f"Calibrating on validation scene {index}/{len(validation_scenes)}: {scene['scene_id']}",
            flush=True,
        )
        with Image.open(dataset_root / scene["image_path"]) as image_file:
            image = np.asarray(image_file, dtype=np.float32).copy()
        with Image.open(dataset_root / scene["mask_path"]) as mask_file:
            truth = np.asarray(mask_file, dtype=np.float32).copy()
        probability = infer_full_scene(
            model,
            image,
            device,
            patch_size=inference_patch_size,
            stride=inference_stride,
            batch_size=batch_size,
            normalization_mode=normalization_mode,
        )
        histogram.update(probability, truth)

    selected_threshold, calibrated_metrics = histogram.best_iou_threshold()
    fixed_metrics = histogram.metrics_at_threshold(0.5)
    result = {
        "status": "PASS",
        "calibration_partition": "validation only",
        "test_partition_accessed": False,
        "checkpoint_sha256": file_sha256(checkpoint_path),
        "checkpoint_epoch": int(checkpoint["epoch"]),
        "validation_scenes": len(validation_scenes),
        "validation_acquisition_groups": len(
            {scene["acquisition_group"] for scene in validation_scenes}
        ),
        "selected_threshold": selected_threshold,
        "fixed_threshold_0_5_metrics": fixed_metrics,
        "calibrated_metrics": calibrated_metrics,
        "selection_rule": "maximum oil-class IoU across thresholds 0.05 to 0.95",
        "limitations": [
            "Only validation data selected the threshold; the test data remains untouched.",
            "Three validation scenes from two acquisition dates make threshold uncertainty high.",
            "The selected threshold must be frozen before final test evaluation.",
        ],
    }
    (output_dir / "threshold_calibration.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate ESPADA's oil decision threshold")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = calibrate_threshold(
            args.dataset_root,
            args.manifest,
            args.checkpoint,
            args.out,
            batch_size=args.batch_size,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
