import numpy as np
import pytest

from espada.ml_metrics import ProbabilityHistogram, confusion_from_arrays, metrics_from_confusion


def test_binary_confusion_and_oil_metrics() -> None:
    probability = np.array([[0.9, 0.1], [0.8, 0.2]], dtype=np.float32)
    truth = np.array([[1, 0], [0, 1]], dtype=np.float32)
    confusion = confusion_from_arrays(probability, truth, 0.5)
    assert confusion == {
        "true_positive": 1,
        "true_negative": 1,
        "false_positive": 1,
        "false_negative": 1,
    }
    assert metrics_from_confusion(confusion)["iou"] == pytest.approx(1 / 3)


def test_threshold_selection_maximizes_histogram_iou() -> None:
    histogram = ProbabilityHistogram(bins=1000)
    histogram.update(
        np.array([0.95, 0.85, 0.60, 0.20, 0.10]),
        np.array([1, 1, 0, 1, 0]),
    )
    threshold, metrics = histogram.best_iou_threshold()
    assert threshold == pytest.approx(0.101)
    assert metrics["iou"] == pytest.approx(0.75)
    assert metrics["average_precision"] == pytest.approx(11 / 12)
