from copy import deepcopy
import unittest

import numpy as np

from espada.ml_external_eval import EVALUATOR_VERSION, _new_counts, _summarize, _quality_gate, _write_report
from espada.model_comparison import compare_reports
from espada.poseatsea import verify_checkpoint


class ComparisonTests(unittest.TestCase):
    def report(self):
        counts = _new_counts()
        counts.update(images=100, oil_images=50, no_oil_images=50, ground_truth_objects=95,
                      missed_objects=95, true_negative_images=50)
        return {"execution_status": "PASS", "evaluator_version": EVALUATOR_VERSION,
                "dataset": {"selection_sha256": "test-selection", "manifest_sha256": "test-files"},
                "matching_iou_threshold": 0.5, "minimum_component_pixels": 24,
                "model": {"backend": "fixture", "threshold": None, "decision_rule": "argmax"},
                "external_metrics": _summarize(counts), "per_subset_metrics": {}, "quality_status": "FAIL"}

    def test_missing_predictions_are_zero_f1(self):
        report = self.report()
        self.assertEqual(report["external_metrics"]["object_f1"], 0.0)
        self.assertIsNone(report["external_metrics"]["object_precision"])
        self.assertEqual(_quality_gate(report["external_metrics"])["status"], "FAIL")

    def test_incompatible_reports_are_refused(self):
        report = self.report()
        other = deepcopy(report)
        other["minimum_component_pixels"] = 1
        with self.assertRaises(ValueError):
            compare_reports(report, other)
        other = deepcopy(report)
        other["dataset"]["manifest_sha256"] = "different-files"
        with self.assertRaises(ValueError):
            compare_reports(report, other)
        other = deepcopy(report)
        other.pop("evaluator_version")
        with self.assertRaises(ValueError):
            compare_reports(report, other)

    def test_comparison_does_not_promote(self):
        report = self.report()
        self.assertFalse(compare_reports(report, deepcopy(report))["automatic_promotion"])

    def test_report_accepts_argmax_and_undefined_metrics(self):
        import tempfile
        from pathlib import Path
        report = self.report()
        report["quality_gate"] = _quality_gate(report["external_metrics"])
        report["input_adapter"] = {"method": "fixture"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.html"
            _write_report(path, report)
            self.assertIn("argmax", path.read_text(encoding="utf-8"))
            self.assertIn("N/A", path.read_text(encoding="utf-8"))
            checkpoint = Path(folder) / "wrong.pth"
            checkpoint.write_bytes(b"incorrect weights")
            with self.assertRaises(ValueError):
                verify_checkpoint(checkpoint)

    def test_challenger_evaluation_writes_scores_and_comparison(self):
        # Exercise the complete evaluator/report path with a deterministic fake
        # predictor, without downloading weights or running GPU inference.
        try:
            import torch
        except (ImportError, OSError):
            self.skipTest("Torch integration runs in the working GPU environment")
        import csv
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from PIL import Image
        from espada.ml_external_eval import evaluate_dartis
        from espada.model_comparison import write_comparison

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.fromarray(np.zeros((10, 10), dtype=np.uint8)).save(root / "oil.png")
            Image.fromarray(np.zeros((10, 10), dtype=np.uint8)).save(root / "water.png")
            (root / "oil.xml").write_text("<annotation><object><bndbox><xmin>2</xmin><ymin>2</ymin><xmax>8</xmax><ymax>8</ymax></bndbox></object></annotation>")
            rows = [{"jpg_file": "oil.png", "subset": "ow", "label": "oil", "sentinel_id": "a"},
                    {"jpg_file": "water.png", "subset": "nw", "label": "no_oil", "sentinel_id": "b"}]
            with (root / "external_manifest.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            (root / "download_status.json").write_text(json.dumps({"selection_sha256": "fixture", "selection_seed": 1, "manifest_sha256": "fixture"}))
            checkpoint = root / "fixture.pt"
            checkpoint.write_bytes(b"fixture")
            verified = [(rows[0], root / "oil.png", root / "oil.xml"), (rows[1], root / "water.png", None)]
            def fake_predict(model, path, device):
                mask = np.zeros((10, 10), dtype=bool)
                if path.name == "oil.png":
                    mask[2:8, 2:8] = True
                return mask, mask.astype(np.float32)
            with patch("espada.ml_external_eval._verify_locked_dataset", return_value=verified), patch("espada.poseatsea.load_model", return_value=object()), patch("espada.poseatsea.predict", side_effect=fake_predict):
                result = evaluate_dartis(root, checkpoint, None, root / "audit", backend="poseatsea")
            self.assertEqual(result["external_metrics"]["object_f1"], 1.0)
            self.assertEqual(result["external_metrics"]["false_alarm_images"], 0)
            self.assertEqual(result["quality_status"], "PASS")
            report_path = root / "audit" / "external_evaluation.json"
            # Populate absent subsets solely for the report fixture.
            for subset in ("oc", "nc"):
                result["per_subset_metrics"][subset] = _summarize(_new_counts())
            report_path.write_text(json.dumps(result))
            write_comparison(report_path, report_path, root / "comparison")
            self.assertTrue((root / "comparison" / "comparison.html").is_file())


class PoseAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cv2
        except ImportError:
            raise unittest.SkipTest("OpenCV tested in the GPU environment")

    def test_preprocessing_and_original_size(self):
        from espada.poseatsea import preprocess_rgb, decode_logits
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        image[..., 0] = 255
        tensor = preprocess_rgb(image)
        self.assertEqual(tensor.shape, (3, 512, 512))
        self.assertTrue(np.all(tensor[0] == 1))
        self.assertTrue(np.all(tensor[1:] == 0))
        logits = np.zeros((5, 512, 512), dtype=np.float32)
        logits[1] = 0.1  # oil wins argmax despite probability below 0.5
        mask, probability = decode_logits(logits, (40, 80))
        self.assertEqual(mask.shape, (40, 80))
        self.assertTrue(mask.all())
        self.assertTrue((probability < 0.5).all())
        logits[4, :, 256:] = 1.0
        mask, _ = decode_logits(logits, (40, 80))
        self.assertTrue(mask[:, :40].all())
        self.assertFalse(mask[:, 40:].any())

    def test_invalid_outputs_fail(self):
        from espada.poseatsea import decode_logits, preprocess_rgb
        with self.assertRaises(ValueError):
            decode_logits(np.zeros((5, 256, 256)), (40, 80))
        with self.assertRaises(ValueError):
            decode_logits(np.full((5, 512, 512), np.nan), (40, 80))
        with self.assertRaises(ValueError):
            preprocess_rgb(np.zeros((40, 80), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
