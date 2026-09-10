import unittest

from espada.detector_pilot import average_precision_50, metrics_at_threshold, select_threshold


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


if __name__ == "__main__":
    unittest.main()
