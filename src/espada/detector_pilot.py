"""Train and validate the DARTIS oil-object detector pilot."""
import argparse
import csv
import html
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from .dartis import DARTIS_CITATION, _file_sha256, box_iou, load_voc_boxes, match_boxes
from .ml_external_eval import _new_counts, _quality_gate, _summarize

ARCHITECTURE = "Faster R-CNN MobileNetV3-Large 320 FPN"
PRETRAINED_WEIGHTS = "FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1"
PRETRAINED_URL = "https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth"


class PilotDataset:
    def __init__(self, root: Path, rows, *, augment=False):
        self.root, self.rows, self.augment = Path(root), list(rows), augment

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        import torch
        from torchvision.transforms.functional import pil_to_tensor

        row = self.rows[index]
        with Image.open(self.root / row["image_path"]) as source:
            image = pil_to_tensor(source.convert("RGB")).float() / 255.0
        boxes = load_voc_boxes(self.root / row["annotation_path"]) if row["annotation_path"] else []
        boxes = torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4)
        height, width = image.shape[-2:]
        if self.augment and random.random() < 0.5:
            image = image.flip(-1)
            if len(boxes):
                left, right = boxes[:, 0].clone(), boxes[:, 2].clone()
                boxes[:, 0], boxes[:, 2] = width - right, width - left
        if self.augment and random.random() < 0.5:
            image = image.flip(-2)
            if len(boxes):
                top, bottom = boxes[:, 1].clone(), boxes[:, 3].clone()
                boxes[:, 1], boxes[:, 3] = height - bottom, height - top
        target = {
            "boxes": boxes,
            "labels": torch.ones(len(boxes), dtype=torch.int64),
            "image_id": torch.tensor(index, dtype=torch.int64),
            "area": (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]),
            "iscrowd": torch.zeros(len(boxes), dtype=torch.int64),
        }
        return image, target


def build_model(*, pretrained=True):
    from torchvision.models.detection import (
        FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
        fasterrcnn_mobilenet_v3_large_320_fpn,
    )
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1 if pretrained else None
    model = fasterrcnn_mobilenet_v3_large_320_fpn(
        weights=weights, weights_backbone=None, trainable_backbone_layers=3
    )
    features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(features, 2)
    return model


def collate(batch):
    return tuple(zip(*batch))


def average_precision_50(predictions, truths):
    total = sum(len(items) for items in truths)
    if not total:
        return 0.0
    ranked = sorted(((float(score), image_index, tuple(map(float, box)))
                     for image_index, items in enumerate(predictions)
                     for box, score in items), reverse=True)
    matched = [set() for _ in truths]
    tp, fp, precision_at_hits = 0, 0, []
    for _, image_index, predicted in ranked:
        candidates = [(box_iou(predicted, truth), truth_index)
                      for truth_index, truth in enumerate(truths[image_index])
                      if truth_index not in matched[image_index]]
        best = max(candidates, default=(0.0, -1))
        if best[0] >= 0.5:
            matched[image_index].add(best[1])
            tp += 1
            precision_at_hits.append(tp / (tp + fp))
        else:
            fp += 1
    return float(sum(precision_at_hits) / total)


def metrics_at_threshold(predictions, truths, rows, threshold):
    aggregate = _new_counts()
    subsets = defaultdict(_new_counts)
    for items, truth, row in zip(predictions, truths, rows):
        predicted = [tuple(map(int, box)) for box, score in items if score >= threshold]
        matched = len(match_boxes(predicted, truth, iou_threshold=0.5))
        for counts in (aggregate, subsets[row["subset"]]):
            counts["images"] += 1
            counts["predicted_objects"] += len(predicted)
            counts["ground_truth_objects"] += len(truth)
            counts["matched_objects"] += matched
            counts["false_positive_objects"] += len(predicted) - matched
            counts["missed_objects"] += len(truth) - matched
            if truth:
                counts["oil_images"] += 1
                counts["oil_images_detected"] += int(matched > 0)
            else:
                counts["no_oil_images"] += 1
                counts["false_alarm_images"] += int(bool(predicted))
                counts["true_negative_images"] += int(not predicted)
    return _summarize(aggregate), {key: _summarize(subsets[key]) for key in ("ow", "oc", "nw", "nc")}


def select_threshold(predictions, truths, rows):
    candidates = []
    for threshold in np.linspace(0.05, 0.95, 91):
        metrics, subsets = metrics_at_threshold(predictions, truths, rows, float(threshold))
        margins = (metrics["object_f1"] / 0.50,
                   metrics["oil_patch_detection_rate"] / 0.70,
                   metrics["no_oil_image_specificity"] / 0.70)
        candidates.append((min(margins), metrics["object_f1"], metrics["oil_patch_detection_rate"],
                           float(threshold), metrics, subsets))
    winner = max(candidates, key=lambda item: item[:4])
    return winner[3], winner[4], winner[5]


def predict_dataset(model, dataset, device, *, batch_size=2):
    import torch
    from torch.utils.data import DataLoader

    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0, collate_fn=collate)
    predictions, truths = [], []
    model.eval()
    with torch.inference_mode():
        for images, targets in loader:
            output = model([image.to(device) for image in images])
            for result, target in zip(output, targets):
                predictions.append([(box.tolist(), float(score)) for box, score in
                                    zip(result["boxes"].cpu(), result["scores"].cpu())])
                truths.append([tuple(map(int, box)) for box in target["boxes"].tolist()])
    return predictions, truths


def balanced_limit(rows, maximum):
    if not maximum or maximum >= len(rows):
        return rows
    groups = defaultdict(list)
    for row in rows:
        groups[row["subset"]].append(row)
    selected = []
    while len(selected) < maximum:
        progressed = False
        for subset in ("ow", "oc", "nw", "nc"):
            if groups[subset] and len(selected) < maximum:
                selected.append(groups[subset].pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def _write_report(path, result):
    metrics = result["validation_metrics"]
    def percent(value):
        return "N/A" if value is None else f"{value:.1%}"

    rows = "".join(f"<tr><td>{html.escape(name)}</td><td>{percent(value)}</td></tr>" for name, value in (
        ("Object precision", metrics["object_precision"]), ("Object recall", metrics["object_recall"]),
        ("Object F1", metrics["object_f1"]), ("Oil-image detection", metrics["oil_patch_detection_rate"]),
        ("No-oil specificity", metrics["no_oil_image_specificity"]), ("AP50", result["validation_ap50"])))
    subset_rows = "".join(f"<tr><td>{key}</td><td>{value['images']}</td><td>{percent(value['object_f1'])}</td><td>{percent(value['oil_patch_detection_rate'])}</td><td>{percent(value['no_oil_image_specificity'])}</td></tr>"
                          for key, value in result["per_subset_metrics"].items())
    document = f"""<!doctype html><meta charset='utf-8'><title>ESPADA detector pilot</title>
<style>body{{font:16px/1.5 Segoe UI,sans-serif;background:#edf4f3;color:#102e36;max-width:850px;margin:30px auto}}table{{width:100%;border-collapse:collapse;background:white;margin:16px 0}}td,th{{padding:10px;border-bottom:1px solid #d4e3e0;text-align:left}}.note{{background:#fff2d6;padding:14px}}</style>
<h1>Oil-object detector pilot</h1><p>{html.escape(result['architecture'])} · validation-only threshold {result['selected_threshold']:.2f}</p>
<p class='note'>Execution {result['status']} · development quality {result['quality_status']}. The reserved test allocation was not accessed.</p>
<table><tr><th>Metric</th><th>Validation</th></tr>{rows}</table>
<h2>Setting breakdown</h2><table><tr><th>Subset</th><th>Images</th><th>Object F1</th><th>Oil detection</th><th>Specificity</th></tr>{subset_rows}</table>
<p>Boxes locate candidate regions; they do not measure oil boundaries or establish attribution. Threshold selection and checkpoint selection both used this pilot validation set.</p>"""
    path.write_text(document, encoding="utf-8")


def train(manifest, status_path, output_dir, *, epochs=8, batch_size=2,
          learning_rate=1e-4, smoke=False):
    import torch
    from torch.utils.data import DataLoader

    random.seed(26143); np.random.seed(26143); torch.manual_seed(26143)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(26143)
    root, output_dir = Path(manifest).parent, Path(output_dir)
    status = json.loads(Path(status_path).read_text(encoding="utf-8"))
    if status.get("status") != "PASS" or status.get("reserved_test_accessed") is not False:
        raise ValueError("A clean pilot download with untouched reserved test is required")
    if status.get("manifest_sha256") != _file_sha256(manifest):
        raise ValueError("Pilot manifest differs from its verified download status")
    with Path(manifest).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if {row["partition"] for row in rows} != {"train", "validation"}:
        raise ValueError("Pilot manifest must contain train and validation only")
    train_rows = balanced_limit([row for row in rows if row["partition"] == "train"], 16 if smoke else 0)
    validation_rows = balanced_limit([row for row in rows if row["partition"] == "validation"], 8 if smoke else 0)
    train_data = PilotDataset(root, train_rows, augment=True)
    validation_data = PilotDataset(root, validation_rows)
    generator = torch.Generator().manual_seed(26143)
    loader = DataLoader(train_data, batch_size=batch_size, shuffle=True, generator=generator,
                        num_workers=0, collate_fn=collate)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(pretrained=True).to(device)
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),
                                  lr=learning_rate, weight_decay=1e-4)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "detector_best.pt"
    history, best_ap = [], -1.0
    for epoch in range(1, epochs + 1):
        model.train(); losses = []
        for images, targets in loader:
            images = [image.to(device) for image in images]
            targets = [{key: value.to(device) for key, value in target.items()} for target in targets]
            loss = sum(model(images, targets).values())
            if not torch.isfinite(loss): raise ValueError("Non-finite detector loss")
            optimizer.zero_grad(set_to_none=True); loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0); optimizer.step()
            losses.append(float(loss.detach().cpu()))
        predictions, truths = predict_dataset(model, validation_data, device, batch_size=batch_size)
        ap50 = average_precision_50(predictions, truths)
        entry = {"epoch": epoch, "training_loss": float(np.mean(losses)), "validation_ap50": ap50}
        history.append(entry); print(json.dumps(entry), flush=True)
        if ap50 > best_ap:
            best_ap = ap50
            torch.save({"model_state_dict": model.state_dict(), "epoch": epoch,
                        "architecture": ARCHITECTURE, "pretrained_weights": PRETRAINED_WEIGHTS,
                        "manifest_sha256": _file_sha256(manifest), "validation_ap50": ap50}, checkpoint_path)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    model.to(device)
    predictions, truths = predict_dataset(model, validation_data, device, batch_size=batch_size)
    threshold, metrics, subsets = select_threshold(predictions, truths, validation_rows)
    quality = _quality_gate(metrics)
    result = {
        "status": "PASS", "run_type": "smoke" if smoke else "pilot",
        "architecture": ARCHITECTURE, "pretrained_weights": PRETRAINED_WEIGHTS,
        "pretrained_url": PRETRAINED_URL, "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "training_images": len(train_rows), "validation_images": len(validation_rows),
        "epochs_completed": epochs, "checkpoint_epoch": int(checkpoint["epoch"]),
        "checkpoint": str(checkpoint_path.resolve()), "checkpoint_sha256": _file_sha256(checkpoint_path),
        "checkpoint_selection": "highest validation AP50",
        "validation_ap50": average_precision_50(predictions, truths),
        "selected_threshold": threshold,
        "threshold_selection": "maximize minimum normalized margin to frozen F1/detection/specificity gates; tie-break by F1 and recall",
        "validation_metrics": metrics, "per_subset_metrics": subsets,
        "quality_status": quality["status"], "quality_gate": quality, "history": history,
        "reserved_test_accessed": False, "citation": DARTIS_CITATION,
        "limitations": ["Pilot validation selects both checkpoint and confidence threshold.",
                        "DARTIS boxes locate objects but do not provide oil segmentation masks.",
                        "This result cannot establish Indian Ocean or global operational reliability."],
    }
    (output_dir / "training_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_report(output_dir / "training_report.html", result)
    print(json.dumps(result, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.epochs < 1 or args.batch_size < 1: raise ValueError("Epochs and batch size must be positive")
        train(args.manifest, args.status, args.out, epochs=1 if args.smoke else args.epochs,
              batch_size=args.batch_size, learning_rate=args.learning_rate, smoke=args.smoke)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
