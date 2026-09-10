from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .dartis import (
    DARTIS_CITATION,
    DARTIS_DOI,
    DARTIS_EXTERNAL_AUDIT_ROLE,
    DARTIS_EXTERNAL_AUDIT_SEED,
    DARTIS_EXTERNAL_AUDIT_SELECTION_SHA256,
    ESPADA_TRAIN_MEDIAN_SCENE_STD_DB,
    DartisRecord,
    box_iou,
    component_boxes,
    dartis_jpeg_to_db,
    load_voc_boxes,
    match_boxes,
    selection_sha256,
    _file_sha256 as file_sha256,
)
from .ml_preprocess import FIXED_MINMAX

EVALUATOR_VERSION = "dartis-object-v2"


EXTERNAL_QUALITY_POLICY = {
    "object_f1": 0.50,
    "oil_patch_detection_rate": 0.70,
    "no_oil_image_specificity": 0.70,
}


def _divide(
    numerator: int | float, denominator: int | float
) -> float | None:
    return float(numerator / denominator) if denominator else None


def _format_percent(value: object) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.1f}%"


def _new_counts() -> dict[str, int]:
    return {
        "images": 0,
        "oil_images": 0,
        "no_oil_images": 0,
        "ground_truth_objects": 0,
        "predicted_objects": 0,
        "matched_objects": 0,
        "false_positive_objects": 0,
        "missed_objects": 0,
        "oil_images_detected": 0,
        "false_alarm_images": 0,
        "true_negative_images": 0,
    }


def _summarize(counts: dict[str, int]) -> dict[str, object]:
    tp = counts["matched_objects"]
    fp = counts["false_positive_objects"]
    fn = counts["missed_objects"]
    precision = _divide(tp, tp + fp)
    recall = _divide(tp, tp + fn)
    f1 = _divide(2 * tp, 2 * tp + fp + fn)
    return {
        **counts,
        "object_precision": precision,
        "object_recall": recall,
        "object_f1": f1,
        "oil_patch_detection_rate": _divide(
            counts["oil_images_detected"], counts["oil_images"]
        ),
        "no_oil_image_specificity": _divide(
            counts["true_negative_images"], counts["no_oil_images"]
        ),
        "no_oil_image_false_alarm_rate": _divide(
            counts["false_alarm_images"], counts["no_oil_images"]
        ),
    }


def _quality_gate(metrics: dict[str, object]) -> dict[str, object]:
    criteria: dict[str, object] = {}
    for metric, minimum in EXTERNAL_QUALITY_POLICY.items():
        observed = metrics.get(metric)
        passed = observed is not None and float(observed) >= minimum
        criteria[metric] = {
            "minimum": minimum,
            "observed": observed,
            "passed": passed,
        }
    passed = all(bool(item["passed"]) for item in criteria.values())
    return {
        "policy_version": 1,
        "status": "PASS" if passed else "FAIL",
        "criteria": criteria,
        "note": (
            "This screening policy was frozen after the first V6 external audit and "
            "must remain unchanged for challenger comparisons."
        ),
    }


def _resolve_dataset_file(dataset_root: Path, relative_value: str, field: str) -> Path:
    relative = Path(relative_value)
    if not relative_value or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe {field} in DARTIS manifest: {relative_value!r}")
    root = dataset_root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"DARTIS {field} escapes the dataset directory")
    if not candidate.is_file():
        raise FileNotFoundError(f"Missing DARTIS {field}: {candidate}")
    return candidate


def _verify_locked_dataset(
    dataset_root: Path,
    manifest_path: Path,
    rows: list[dict[str, str]],
    download_status: dict[str, object],
) -> list[tuple[dict[str, str], Path, Path | None]]:
    if download_status.get("dataset_role") != DARTIS_EXTERNAL_AUDIT_ROLE:
        raise ValueError("DARTIS data is not marked as the locked external audit")
    if int(download_status.get("selection_seed", -1)) != DARTIS_EXTERNAL_AUDIT_SEED:
        raise ValueError("DARTIS external-audit seed does not match the pinned seed")
    if download_status.get("selection_sha256") != DARTIS_EXTERNAL_AUDIT_SELECTION_SHA256:
        raise ValueError("DARTIS selection digest does not match the pinned audit")
    if int(download_status.get("selected_images", -1)) != len(rows):
        raise ValueError("DARTIS manifest row count differs from download status")
    if download_status.get("manifest_sha256") != file_sha256(manifest_path):
        raise ValueError(
            "DARTIS manifest integrity check failed; rerun the download command"
        )

    records: list[DartisRecord] = []
    verified: list[tuple[dict[str, str], Path, Path | None]] = []
    for row in rows:
        record = DartisRecord(
            subset=row["subset"],
            jpg_file=row["jpg_file"],
            xml_file=row["xml_file"],
            patch_name=row["patch_name"],
            start_time=row["start_time"],
            sentinel_id=row["sentinel_id"],
            width=int(row["width"]),
            height=int(row["height"]),
        )
        if row.get("label") != record.label or row.get("setting") != record.setting:
            raise ValueError(f"DARTIS manifest label mismatch for {record.jpg_file}")
        image_path = _resolve_dataset_file(
            dataset_root, row.get("image_path", ""), "image path"
        )
        expected_image_digest = row.get("image_sha256", "")
        if not expected_image_digest or file_sha256(image_path) != expected_image_digest:
            raise ValueError(f"DARTIS image integrity check failed: {record.jpg_file}")

        annotation_path: Path | None = None
        annotation_value = row.get("annotation_path", "")
        expected_annotation_digest = row.get("annotation_sha256", "")
        if annotation_value:
            annotation_path = _resolve_dataset_file(
                dataset_root, annotation_value, "annotation path"
            )
            if (
                not expected_annotation_digest
                or file_sha256(annotation_path) != expected_annotation_digest
            ):
                raise ValueError(
                    f"DARTIS annotation integrity check failed: {record.xml_file}"
                )
        elif expected_annotation_digest or record.label == "oil":
            raise ValueError(f"DARTIS annotation is inconsistent for {record.jpg_file}")
        records.append(record)
        verified.append((row, image_path, annotation_path))

    if selection_sha256(records) != DARTIS_EXTERNAL_AUDIT_SELECTION_SHA256:
        raise ValueError("DARTIS manifest records do not reproduce the pinned selection")
    return verified


def _update_counts(
    counts: dict[str, int], *, label: str, predicted: int, truth: int, matched: int
) -> None:
    counts["images"] += 1
    counts["predicted_objects"] += predicted
    counts["ground_truth_objects"] += truth
    counts["matched_objects"] += matched
    counts["false_positive_objects"] += predicted - matched
    counts["missed_objects"] += truth - matched
    if label == "oil":
        counts["oil_images"] += 1
        counts["oil_images_detected"] += int(matched > 0)
    else:
        counts["no_oil_images"] += 1
        counts["false_alarm_images"] += int(predicted > 0)
        counts["true_negative_images"] += int(predicted == 0)


def _save_review_overlay(
    output_path: Path,
    image: np.ndarray,
    predicted: list[tuple[int, int, int, int]],
    truth: list[tuple[int, int, int, int]],
) -> None:
    display = Image.fromarray(np.asarray(image, dtype=np.uint8), mode="L").convert("RGB")
    draw = ImageDraw.Draw(display)
    for box in truth:
        draw.rectangle(box, outline=(255, 191, 62), width=3)
    for box in predicted:
        draw.rectangle(box, outline=(27, 211, 192), width=3)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    display.save(output_path, quality=90)


def _write_report(path: Path, result: dict[str, object]) -> None:
    metrics = result["external_metrics"]
    subset_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{values['images']}</td>"
        f"<td>{_format_percent(values['object_precision'])}</td>"
        f"<td>{_format_percent(values['object_recall'])}</td>"
        f"<td>{_format_percent(values['oil_patch_detection_rate'])}</td>"
        f"<td>{_format_percent(values['no_oil_image_specificity'])}</td>"
        "</tr>"
        for name, values in result["per_subset_metrics"].items()
    )
    quality = result["quality_gate"]
    quality_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name.replace('_', ' ').title())}</td>"
        f"<td>{_format_percent(values['minimum'])}</td>"
        f"<td>{_format_percent(values['observed'])}</td>"
        f"<td>{'PASS' if values['passed'] else 'FAIL'}</td>"
        "</tr>"
        for name, values in quality["criteria"].items()
    )
    report = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ESPADA DARTIS external evaluation</title><style>
    :root{{--ink:#0a1d27;--muted:#60747d;--paper:#eef3f2;--card:#fff;--line:#d5e1df;--teal:#087f7b;--amber:#ffbf3e}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,Segoe UI,Arial,sans-serif}}main{{max-width:1100px;margin:auto;padding:28px}}h1{{margin:5px 0;font:600 34px Georgia,serif}}.eyebrow{{color:var(--teal);font-weight:800;font-size:12px;letter-spacing:.13em}}.note{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}.card,.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}.card small{{display:block;color:var(--muted);text-transform:uppercase}}.card strong{{font:600 27px Georgia,serif}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:11px;color:var(--muted);text-transform:uppercase}}code{{background:#e4eeec;padding:2px 5px;border-radius:5px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr 1fr}}main{{padding:14px}}.panel{{overflow:auto}}}}
    </style></head><body><main><div class="eyebrow">ESPADA · EXTERNAL GENERALIZATION CHECK</div><h1>{html.escape(str(result['model'].get('backend', 'v6')).upper())} · DARTIS comparison</h1><p class="note">Execution {result['execution_status']} · Quality <strong>{result['quality_status']}</strong> · {metrics['images']} locked images · IoU≥{result['matching_iou_threshold']:.2f} object matching · {html.escape(str(result['model'].get('decision_rule', 'threshold ' + str(result['model'].get('threshold')))))}</p><section class="grid"><div class="card"><small>Object precision</small><strong>{_format_percent(metrics['object_precision'])}</strong></div><div class="card"><small>Object recall</small><strong>{_format_percent(metrics['object_recall'])}</strong></div><div class="card"><small>Oil-patch detection</small><strong>{_format_percent(metrics['oil_patch_detection_rate'])}</strong></div><div class="card"><small>No-oil specificity</small><strong>{_format_percent(metrics['no_oil_image_specificity'])}</strong></div></section><section class="panel"><h2>Quality gate: {quality['status']}</h2><table><thead><tr><th>Metric</th><th>Minimum</th><th>Observed</th><th>Result</th></tr></thead><tbody>{quality_rows}</tbody></table><p class="note">{html.escape(quality['note'])}</p></section><section class="panel"><h2>What these numbers mean</h2><p>This dataset supplies oil bounding boxes, not pixel masks. Therefore this report measures whether ESPADA finds oil objects and avoids lookalike/no-oil patches; it does not report segmentation IoU or Dice.</p><p><strong>{metrics['matched_objects']}</strong> oil objects matched, <strong>{metrics['missed_objects']}</strong> missed, and <strong>{metrics['false_positive_objects']}</strong> unmatched detections.</p></section><section class="panel"><h2>Subsets</h2><table><thead><tr><th>Subset</th><th>Images</th><th>Object precision</th><th>Object recall</th><th>Oil patch detection</th><th>No-oil specificity</th></tr></thead><tbody>{subset_rows}</tbody></table></section><section class="panel"><h2>Evidence boundary</h2><p>{html.escape(str(result['input_adapter']['method']))}. Audit A now informs development; future claims require a fresh test. Review images show truth boxes in amber and predictions in teal.</p><p class="note">These results do not certify autonomous operational use. Human approval remains mandatory.</p></section></main></body></html>"""
    path.write_text(report, encoding="utf-8")


def evaluate_dartis(
    dataset_root: Path,
    checkpoint_path: Path,
    calibration_path: Path | None,
    output_dir: Path,
    *,
    batch_size: int = 4,
    matching_iou_threshold: float = 0.5,
    min_component_pixels: int = 24,
    backend: str = "v6",
) -> dict[str, object]:
    import torch

    if backend not in {"v6", "poseatsea"}:
        raise ValueError("Unknown DARTIS model backend")
    if batch_size < 1 or not 0 < matching_iou_threshold <= 1 or min_component_pixels < 1:
        raise ValueError("Invalid batch size, matching IoU or component size")
    dataset_root = Path(dataset_root)
    manifest_path = dataset_root / "external_manifest.csv"
    download_status_path = dataset_root / "download_status.json"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("DARTIS external manifest is empty")
    download_status = json.loads(download_status_path.read_text(encoding="utf-8"))
    verified_rows = _verify_locked_dataset(
        dataset_root, manifest_path, rows, download_status
    )

    checkpoint_path = Path(checkpoint_path)
    checkpoint_digest = file_sha256(checkpoint_path)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if backend == "poseatsea":
        from .poseatsea import load_model, predict, MODEL_SPEC

        model = load_model(checkpoint_path, device)
        model_details = {**MODEL_SPEC, "threshold": None,
                         "decision_rule": "argmax of five logits; oil class 1",
                         "test_time_augmentation": "none"}
        adapter_details = {
            "method": "RGB uint8, OpenCV linear resize to 512x512, divide by 255; "
                      "argmax labels resized back with nearest neighbour",
            "external_labels_used_for_adapter": False,
        }

        def infer(image_path, image):
            return predict(model, image_path, device)
    else:
        from .ml_evaluate import infer_full_scene
        from .ml_model import build_segmentation_model

        if calibration_path is None:
            raise ValueError("V6 requires its frozen calibration")
        calibration_path = Path(calibration_path)
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        if calibration.get("checkpoint_sha256") != checkpoint_digest:
            raise ValueError("Threshold calibration belongs to a different model checkpoint")
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        model = build_segmentation_model(checkpoint.get("model_config", {}), pretrained_encoder=False)
        model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        model = model.to(device).eval()
        patch_size = int(checkpoint.get("training_config", {}).get("patch_size", 256))
        normalization_mode = str(checkpoint.get("normalization", {}).get("mode", FIXED_MINMAX))
        threshold = float(calibration["selected_threshold"])
        tta_mode = str(calibration.get("test_time_augmentation", "none"))
        model_details = {
            "checkpoint_epoch": int(checkpoint.get("epoch", 0)),
            "calibration": str(calibration_path.resolve()),
            "calibration_sha256": file_sha256(calibration_path),
            "threshold": threshold, "test_time_augmentation": tta_mode,
        }
        adapter_details = {
            "method": "documented DARTIS sigmoid normalization approximately inverted",
            "target_scene_std_db": ESPADA_TRAIN_MEDIAN_SCENE_STD_DB,
            "target_scale_source": "median standard deviation of ESPADA's 14 training scenes only",
            "external_labels_used_for_adapter": False,
        }

        def infer(image_path, image):
            probability = infer_full_scene(
                model, dartis_jpeg_to_db(image), device, patch_size=patch_size,
                stride=max(patch_size * 3 // 4, 1), batch_size=batch_size,
                normalization_mode=normalization_mode, tta_mode=tta_mode,
            )
            return probability >= threshold, probability

    aggregate = _new_counts()
    subset_counts: dict[str, dict[str, int]] = defaultdict(_new_counts)
    per_image: list[dict[str, object]] = []
    review_candidates: list[tuple[int, np.ndarray, list, list, str]] = []
    for index, (row, image_path, annotation_path) in enumerate(verified_rows, start=1):
        print(f"Evaluating DARTIS image {index}/{len(rows)}: {row['jpg_file']}", flush=True)
        with Image.open(image_path) as image_file:
            image = np.asarray(image_file.convert("L"), dtype=np.uint8)
        mask, probability = infer(image_path, image)
        if mask.shape != image.shape or probability.shape != image.shape or not np.isfinite(probability).all():
            raise ValueError("Model returned invalid prediction shape or non-finite probabilities")
        predicted = component_boxes(
            mask, min_component_pixels=min_component_pixels
        )
        truth = load_voc_boxes(annotation_path) if annotation_path is not None else []
        matches = match_boxes(predicted, truth, iou_threshold=matching_iou_threshold)
        matched = len(matches)
        _update_counts(
            aggregate,
            label=row["label"],
            predicted=len(predicted),
            truth=len(truth),
            matched=matched,
        )
        _update_counts(
            subset_counts[row["subset"]],
            label=row["label"],
            predicted=len(predicted),
            truth=len(truth),
            matched=matched,
        )
        best_iou = max(
            (box_iou(predicted_box, truth_box) for predicted_box in predicted for truth_box in truth),
            default=0.0,
        )
        item = {
            "subset": row["subset"],
            "label": row["label"],
            "jpg_file": row["jpg_file"],
            "sentinel_id": row["sentinel_id"],
            "ground_truth_objects": len(truth),
            "predicted_objects": len(predicted),
            "matched_objects": matched,
            "false_positive_objects": len(predicted) - matched,
            "missed_objects": len(truth) - matched,
            "best_box_iou": best_iou,
            "maximum_probability": float(probability.max()),
            "predicted_boxes_json": json.dumps(predicted),
            "truth_boxes_json": json.dumps(truth),
        }
        per_image.append(item)
        error_weight = item["missed_objects"] + item["false_positive_objects"]
        if error_weight:
            review_candidates.append((int(error_weight), image, predicted, truth, row["jpg_file"]))
            review_candidates.sort(key=lambda item: (-item[0], item[4]))
            del review_candidates[12:]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    review_candidates.sort(key=lambda item: (-item[0], item[4]))
    for _, image, predicted, truth, filename in review_candidates[:12]:
        _save_review_overlay(
            output_dir / "review_samples" / f"{Path(filename).stem}_review.jpg",
            image,
            predicted,
            truth,
        )
    per_image_path = output_dir / "per_image_results.csv"
    with per_image_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(per_image[0]))
        writer.writeheader()
        writer.writerows(per_image)

    external_metrics = _summarize(aggregate)
    quality_gate = _quality_gate(external_metrics)
    result: dict[str, object] = {
        "execution_status": "PASS",
        "quality_status": quality_gate["status"],
        "evaluator_version": EVALUATOR_VERSION,
        "evaluation": "DARTIS External Audit A; development comparison, not a fresh blind test",
        "dataset": {
            "name": "DARTIS_2019",
            "doi": DARTIS_DOI,
            "citation": DARTIS_CITATION,
            "region": "Eastern Mediterranean Sea",
            "selection_sha256": download_status["selection_sha256"],
            "selection_seed": download_status["selection_seed"],
            "manifest_sha256": download_status["manifest_sha256"],
            "role": "External Audit A, now used for development comparisons",
        },
        "model": {
            **model_details,
            "backend": backend,
            "checkpoint": str(checkpoint_path.resolve()),
            "checkpoint_sha256": checkpoint_digest,
            "device": str(device),
            "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        },
        "input_adapter": adapter_details,
        "matching_iou_threshold": matching_iou_threshold,
        "minimum_component_pixels": min_component_pixels,
        "external_metrics": external_metrics,
        "per_subset_metrics": {
            name: _summarize(subset_counts[name]) for name in sorted(subset_counts)
        },
        "quality_gate": quality_gate,
        "evidence_interpretation": (
            "Object-detection evidence only; pixel IoU and Dice are unavailable because "
            "DARTIS publishes bounding boxes rather than segmentation masks."
        ),
        "limitations": [
            "The deterministic download is a balanced subset, not all 3655 DARTIS images.",
            "DARTIS JPEG normalization cannot recover exact calibrated backscatter values.",
            "A single external region cannot establish global or autonomous operational validity.",
            "The result must not be used to retune V6 while still being called a blind test.",
            "Third-party training overlap has not been independently excluded; a fresh test is required.",
            "The quality gate is a development screening policy, not operational certification.",
        ],
        "artifacts": [
            "external_evaluation.json",
            "external_evaluation_report.html",
            "per_image_results.csv",
            "review_samples/*_review.jpg",
        ],
    }
    (output_dir / "external_evaluation.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    _write_report(output_dir / "external_evaluation_report.html", result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate V6 on locked external DARTIS data")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--backend", choices=["v6", "poseatsea"], default="v6")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--matching-iou", type=float, default=0.5)
    parser.add_argument("--min-component-pixels", type=int, default=24)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = evaluate_dartis(
            args.dataset_root,
            args.checkpoint,
            args.calibration,
            args.out,
            batch_size=args.batch_size,
            matching_iou_threshold=args.matching_iou,
            min_component_pixels=args.min_component_pixels,
            backend=args.backend,
        )
    except Exception as exc:
        print(
            json.dumps({"execution_status": "FAIL", "error": str(exc)}, indent=2),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
