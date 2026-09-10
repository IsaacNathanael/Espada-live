import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from espada.detector_pilot import _write_report, average_precision_50, metrics_at_threshold, select_threshold


class DetectorMetricTests(unittest.TestCase):
    def fixture(self):
        rows = [{"subset": "ow"}, {"subset": "nw"}]
        truths = [[(0, 0, 10, 10)], []]
        predictions = [[((0, 0, 10, 10), .9)], [((20, 20, 30, 30), .4)]]
        return predictions, truths, rows

    def test_ap_and_threshold_metrics(self):
        predictions, truths, rows = self.fixture()
        self.assertEqual(average_precision_50(predictions, truths), 1.0)
        high, _ = metrics_at_threshold(predictions, truths, rows, .5)
        self.assertEqual(high["object_f1"], 1.0)
        self.assertEqual(high["no_oil_image_specificity"], 1.0)
        low, _ = metrics_at_threshold(predictions, truths, rows, .3)
        self.assertLess(low["object_f1"], high["object_f1"])

    def test_threshold_calibration_uses_balanced_gate_margin(self):
        predictions, truths, rows = self.fixture()
        threshold, metrics, subsets = select_threshold(predictions, truths, rows)
        self.assertGreater(threshold, .4)
        self.assertEqual(metrics["object_f1"], 1.0)
        self.assertIn("ow", subsets)

    def test_false_prediction_before_true_reduces_ap(self):
        predictions = [[((20, 20, 30, 30), .9), ((0, 0, 10, 10), .8)]]
        self.assertEqual(average_precision_50(predictions, [[(0, 0, 10, 10)]]), .5)

    def test_report_renders_undefined_subset_metrics_as_na(self):
        empty = {"images": 0, "object_f1": 0.0, "oil_patch_detection_rate": None,
                 "no_oil_image_specificity": None}
        result = {"architecture": "fixture", "status": "PASS", "quality_status": "FAIL",
                  "selected_threshold": .5, "validation_ap50": 0.0,
                  "validation_metrics": {"object_precision": None, "object_recall": 0.0,
                                         "object_f1": 0.0, "oil_patch_detection_rate": 0.0,
                                         "no_oil_image_specificity": 1.0},
                  "per_subset_metrics": {key: empty for key in ("ow", "oc", "nw", "nc")}}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "report.html"
            _write_report(path, result)
            self.assertIn("N/A", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
