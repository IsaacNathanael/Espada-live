from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from .ml_evaluate import file_sha256, infer_full_scene
from .ml_model import build_segmentation_model
from .ml_preprocess import FIXED_MINMAX
from .sar_input import load_sar_image, sar_to_decibels


def predict_scene(
    input_path: Path,
    checkpoint_path: Path,
    calibration_path: Path,
    output_path: Path,
    *,
    batch_size: int = 4,
) -> dict[str, object]:
    checkpoint_path = Path(checkpoint_path)
    calibration_path = Path(calibration_path)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    checkpoint_digest = file_sha256(checkpoint_path)
    if calibration.get("checkpoint_sha256") != checkpoint_digest:
        raise ValueError("Threshold calibration belongs to a different model checkpoint")

    model_config = checkpoint.get("model_config", {})
    model = build_segmentation_model(model_config, pretrained_encoder=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device).eval()
    image_db, input_transform = sar_to_decibels(load_sar_image(input_path))
    patch_size = int(checkpoint.get("training_config", {}).get("patch_size", 256))
    normalization_mode = str(checkpoint.get("normalization", {}).get("mode", FIXED_MINMAX))
    tta_mode = str(calibration.get("test_time_augmentation", "none"))
    probability = infer_full_scene(
        model,
        image_db,
        device,
        patch_size=patch_size,
        stride=max(patch_size * 3 // 4, 1),
        batch_size=batch_size,
        normalization_mode=normalization_mode,
        tta_mode=tta_mode,
    )
    threshold = float(calibration["selected_threshold"])
    augmentation_profile = str(
        checkpoint.get("training_config", {}).get("augmentation_profile", "v5")
    )
    model_generation = "V6" if augmentation_profile == "sar_v6" else "V5"
    architecture = str(model_config.get("architecture", "segmentation model"))
    if model_config.get("attention_decoder"):
        architecture = f"attention-gated {architecture}"
    metadata = {
        "method": f"calibrated {model_generation} SAR semantic segmentation",
        "model_type": "deep-learning binary oil-candidate segmentation",
        "model_generation": model_generation,
        "architecture": architecture,
        "checkpoint_epoch": int(checkpoint.get("epoch", 0)),
        "checkpoint_sha256": checkpoint_digest,
        "checkpoint": str(checkpoint_path.resolve()),
        "calibration": str(calibration_path.resolve()),
        "threshold": threshold,
        "threshold_source": "validation-only IoU calibration",
        "test_time_augmentation": tta_mode,
        "preprocessing": input_transform,
        "normalization": checkpoint.get("normalization", {}),
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "probability_summary": {
            "minimum": float(probability.min()),
            "mean": float(probability.mean()),
            "maximum": float(probability.max()),
        },
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        probability=probability.astype(np.float32),
        metadata_json=np.asarray(json.dumps(metadata)),
    )
    return {
        "status": "PASS",
        "prediction_bundle": str(output_path.resolve()),
        "image_shape": list(probability.shape),
        "device": str(device),
        "gpu": metadata["gpu"],
        "threshold": threshold,
        "test_time_augmentation": tta_mode,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GPU SAR inference and save a portable result")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    print(
        json.dumps(
            predict_scene(
                args.input,
                args.checkpoint,
                args.calibration,
                args.output,
                batch_size=args.batch_size,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
