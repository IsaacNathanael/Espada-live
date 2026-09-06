from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from .ml_metrics import ProbabilityHistogram, confusion_from_arrays, metrics_from_confusion
from .ml_model import ResNet34UNet
from .ml_train import tile_positions


def file_sha256(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def infer_full_scene(
    model: torch.nn.Module,
    image: np.ndarray,
    device: torch.device,
    *,
    patch_size: int = 256,
    stride: int = 192,
    batch_size: int = 8,
) -> np.ndarray:
    height, width = image.shape
    coordinates = [
        (x, y)
        for y in tile_positions(height, patch_size, stride)
        for x in tile_positions(width, patch_size, stride)
    ]
    probability_sum = np.zeros((height, width), dtype=np.float32)
    prediction_count = np.zeros((height, width), dtype=np.uint16)
    for start in range(0, len(coordinates), batch_size):
        batch_coordinates = coordinates[start : start + batch_size]
        patches = np.stack(
            [
                np.clip(
                    (image[y : y + patch_size, x : x + patch_size] + 35.0) / 40.0,
                    0.0,
                    1.0,
                )
                for x, y in batch_coordinates
            ]
        ).astype(np.float32)
        tensor = torch.from_numpy(patches[:, None]).to(device, non_blocking=True)
        with torch.no_grad(), torch.amp.autocast(
            device_type=device.type, enabled=device.type == "cuda"
        ):
            prediction = torch.sigmoid(model(tensor)).float().cpu().numpy()[:, 0]
        for (x, y), patch_probability in zip(batch_coordinates, prediction, strict=True):
            probability_sum[y : y + patch_size, x : x + patch_size] += patch_probability
            prediction_count[y : y + patch_size, x : x + patch_size] += 1
    if not np.all(prediction_count):
        raise RuntimeError("Sliding-window inference left uncovered scene pixels")
    return probability_sum / prediction_count


def _save_overlay(
    path: Path, image: np.ndarray, probability: np.ndarray, truth: np.ndarray, threshold: float
) -> None:
    base = np.uint8(np.clip((image + 35.0) / 40.0, 0.0, 1.0) * 255)
    rgb = np.repeat(base[..., None], 3, axis=2)
    predicted = probability >= threshold
    truth = truth >= 0.5
    true_positive = np.logical_and(predicted, truth)
    false_positive = np.logical_and(predicted, ~truth)
    false_negative = np.logical_and(~predicted, truth)
    rgb[true_positive] = (27, 211, 192)
    rgb[false_positive] = (234, 78, 78)
    rgb[false_negative] = (255, 191, 62)
    quicklook = Image.fromarray(rgb, mode="RGB")
    quicklook.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    quicklook.save(path)


def _write_report(path: Path, result: dict) -> None:
    metrics = result["held_out_test_metrics"]
    confusion = metrics["confusion_matrix"]
    scene_rows = "".join(
        f"<tr><td>{html.escape(row['scene_id'])}</td><td>{row['iou']*100:.1f}%</td>"
        f"<td>{row['dice_f1']*100:.1f}%</td><td>{row['precision']*100:.1f}%</td>"
        f"<td>{row['recall']*100:.1f}%</td></tr>"
        for row in result["per_scene_metrics"]
    )
    report = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ESPADA held-out SAR evaluation</title><style>
    :root{{--ink:#0a1d27;--muted:#60747d;--paper:#eef3f2;--card:#fff;--line:#d5e1df;--teal:#087f7b;--red:#ea4e4e;--amber:#ffbf3e}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,Segoe UI,Arial,sans-serif}}main{{max-width:1100px;margin:auto;padding:28px}}h1{{margin:4px 0;font:600 34px Georgia,serif}}.eyebrow{{color:var(--teal);font-weight:800;font-size:12px;letter-spacing:.13em}}.note{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}.card,.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}.card small{{display:block;color:var(--muted);text-transform:uppercase}}.card strong{{font:600 27px Georgia,serif}}.matrix{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;max-width:420px}}.cell{{padding:18px;text-align:center;border-radius:10px;background:#e8f4f2}}.cell.bad{{background:#fff0e8}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:11px;color:var(--muted);text-transform:uppercase}}.legend span{{margin-right:18px}}.dot{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr 1fr}}main{{padding:14px}}.panel{{overflow:auto}}}}
    </style></head><body><main><div class="eyebrow">ESPADA · RESNET34 U-NET</div><h1>Held-out full-scene evaluation</h1><p class="note">{result['test_scenes']} scenes from acquisition groups never used for training or model selection. Oil IoU—not background-dominated accuracy—is the primary metric.</p><section class="grid"><div class="card"><small>Oil IoU</small><strong>{metrics['iou']*100:.1f}%</strong></div><div class="card"><small>Dice / F1</small><strong>{metrics['dice_f1']*100:.1f}%</strong></div><div class="card"><small>Precision</small><strong>{metrics['precision']*100:.1f}%</strong></div><div class="card"><small>Recall</small><strong>{metrics['recall']*100:.1f}%</strong></div></section><section class="panel"><h2>Pixel confusion matrix</h2><div class="matrix"><div class="cell">True background<br><strong>{confusion[0][0]:,}</strong></div><div class="cell bad">False oil alarm<br><strong>{confusion[0][1]:,}</strong></div><div class="cell bad">Missed oil<br><strong>{confusion[1][0]:,}</strong></div><div class="cell">Detected oil<br><strong>{confusion[1][1]:,}</strong></div></div><p>Approximate oil-class PR-AUC: <strong>{metrics['average_precision']*100:.1f}%</strong> · Overall pixel accuracy: {metrics['accuracy']*100:.1f}%</p></section><section class="panel"><h2>Per-scene results</h2><table><thead><tr><th>Scene</th><th>IoU</th><th>Dice</th><th>Precision</th><th>Recall</th></tr></thead><tbody>{scene_rows}</tbody></table></section><section class="panel"><h2>Overlay legend</h2><p class="legend"><span><i class="dot" style="background:#1bd3c0"></i>correct oil</span><span><i class="dot" style="background:#ea4e4e"></i>false alarm</span><span><i class="dot" style="background:#ffbf3e"></i>missed oil</span></p><p class="note">This small Gulf of Mexico dataset cannot establish universal operational accuracy. Real Sentinel-1 results retain the analyst approval gate.</p></section></main></body></html>"""
    path.write_text(report, encoding="utf-8")


def evaluate_checkpoint(
    dataset_root: Path,
    manifest_path: Path,
    checkpoint_path: Path,
    output_dir: Path,
    *,
    batch_size: int = 8,
    calibration_path: Path | None = None,
) -> dict:
    dataset_root = Path(dataset_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with Path(manifest_path).open("r", encoding="utf-8-sig", newline="") as stream:
        test_scenes = [row for row in csv.DictReader(stream) if row["split"] == "test"]
    if not test_scenes:
        raise ValueError("Manifest contains no held-out test scenes")
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = ResNet34UNet(
        pretrained_encoder=False,
        attention_decoder=bool(checkpoint.get("model_config", {}).get("attention_decoder", False)),
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    inference_patch_size = int(checkpoint.get("training_config", {}).get("patch_size", 256))
    inference_stride = max(inference_patch_size * 3 // 4, 1)
    if calibration_path is None:
        raise ValueError("A validation-only threshold calibration is required before test evaluation")
    calibration = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
    checkpoint_digest = file_sha256(checkpoint_path)
    if calibration.get("checkpoint_sha256") != checkpoint_digest:
        raise ValueError("Threshold calibration was produced for a different model checkpoint")
    threshold = float(calibration["selected_threshold"])
    aggregate = {name: 0 for name in ("true_positive", "true_negative", "false_positive", "false_negative")}
    histogram = ProbabilityHistogram()
    scene_metrics = []
    for index, scene in enumerate(test_scenes, start=1):
        print(f"Evaluating test scene {index}/{len(test_scenes)}: {scene['scene_id']}", flush=True)
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
        )
        confusion = confusion_from_arrays(probability, truth, threshold)
        for name, value in confusion.items():
            aggregate[name] += value
        histogram.update(probability, truth)
        metrics = metrics_from_confusion(confusion)
        metrics["scene_id"] = scene["scene_id"]
        metrics["acquisition_group"] = scene["acquisition_group"]
        scene_metrics.append(metrics)
        _save_overlay(
            output_dir / f"{Path(scene['scene_id']).stem}_overlay.png",
            image,
            probability,
            truth,
            threshold,
        )
    overall = metrics_from_confusion(aggregate)
    overall["average_precision"] = histogram.average_precision()
    model_config = checkpoint.get("model_config", {})
    architecture = str(model_config.get("architecture", "ResNet34 U-Net"))
    if model_config.get("attention_decoder"):
        architecture = f"attention-gated {architecture}"
    result = {
        "status": "PASS",
        "evaluation": "untouched acquisition-group-isolated full-scene test",
        "architecture": architecture,
        "checkpoint_epoch": int(checkpoint["epoch"]),
        "threshold": threshold,
        "threshold_source": "validation-only IoU calibration",
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "test_scenes": len(test_scenes),
        "test_acquisition_groups": len({row["acquisition_group"] for row in test_scenes}),
        "held_out_test_metrics": overall,
        "per_scene_metrics": scene_metrics,
        "limitations": [
            "Only four scenes from three acquisition dates are in the held-out test partition.",
            "The source data covers the Gulf of Mexico and cannot prove cross-ocean generalization.",
            "SAR lookalikes can produce false alarms; operational outputs require analyst approval.",
        ],
        "artifacts": ["test_evaluation.json", "test_evaluation_report.html", "*_overlay.png"],
    }
    (output_dir / "test_evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_report(output_dir / "test_evaluation_report.html", result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate ESPADA on untouched full SAR scenes")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = evaluate_checkpoint(
            args.dataset_root,
            args.manifest,
            args.checkpoint,
            args.out,
            batch_size=args.batch_size,
            calibration_path=args.calibration,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
