from __future__ import annotations

import numpy as np


def confusion_from_arrays(
    probability: np.ndarray, truth: np.ndarray, threshold: float
) -> dict[str, int]:
    predicted = probability >= threshold
    labels = truth >= 0.5
    return {
        "true_positive": int(np.logical_and(predicted, labels).sum()),
        "true_negative": int(np.logical_and(~predicted, ~labels).sum()),
        "false_positive": int(np.logical_and(predicted, ~labels).sum()),
        "false_negative": int(np.logical_and(~predicted, labels).sum()),
    }


def metrics_from_confusion(confusion: dict[str, int]) -> dict:
    tp = confusion["true_positive"]
    tn = confusion["true_negative"]
    fp = confusion["false_positive"]
    fn = confusion["false_negative"]

    def divide(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator else 0.0

    return {
        "confusion_matrix": [[tn, fp], [fn, tp]],
        "confusion_counts": confusion,
        "accuracy": divide(tp + tn, tp + tn + fp + fn),
        "precision": divide(tp, tp + fp),
        "recall": divide(tp, tp + fn),
        "specificity": divide(tn, tn + fp),
        "false_positive_rate": divide(fp, fp + tn),
        "iou": divide(tp, tp + fp + fn),
        "dice_f1": divide(2 * tp, 2 * tp + fp + fn),
    }


class ProbabilityHistogram:
    def __init__(self, bins: int = 1000) -> None:
        self.bins = bins
        self.positive = np.zeros(bins, dtype=np.int64)
        self.negative = np.zeros(bins, dtype=np.int64)

    def update(self, probability: np.ndarray, truth: np.ndarray) -> None:
        indices = np.minimum((probability.ravel() * self.bins).astype(np.int64), self.bins - 1)
        labels = truth.ravel() >= 0.5
        self.positive += np.bincount(indices[labels], minlength=self.bins)
        self.negative += np.bincount(indices[~labels], minlength=self.bins)

    def average_precision(self) -> float:
        true_positive = np.cumsum(self.positive[::-1], dtype=np.float64)
        false_positive = np.cumsum(self.negative[::-1], dtype=np.float64)
        total_positive = float(self.positive.sum())
        if not total_positive:
            return 0.0
        precision = true_positive / np.maximum(true_positive + false_positive, 1.0)
        recall = true_positive / total_positive
        recall_step = np.diff(np.concatenate(([0.0], recall)))
        return float(np.sum(recall_step * precision))

    def metrics_at_threshold(self, threshold: float) -> dict:
        threshold_bin = min(max(int(threshold * self.bins), 0), self.bins - 1)
        confusion = {
            "true_positive": int(self.positive[threshold_bin:].sum()),
            "true_negative": int(self.negative[:threshold_bin].sum()),
            "false_positive": int(self.negative[threshold_bin:].sum()),
            "false_negative": int(self.positive[:threshold_bin].sum()),
        }
        result = metrics_from_confusion(confusion)
        result["average_precision"] = self.average_precision()
        return result

    def best_iou_threshold(
        self, *, minimum: float = 0.05, maximum: float = 0.95
    ) -> tuple[float, dict]:
        candidates = np.arange(
            max(1, int(minimum * self.bins)),
            min(self.bins - 1, int(maximum * self.bins)) + 1,
        )
        true_positive = np.array(
            [self.positive[index:].sum() for index in candidates], dtype=np.float64
        )
        false_positive = np.array(
            [self.negative[index:].sum() for index in candidates], dtype=np.float64
        )
        false_negative = float(self.positive.sum()) - true_positive
        iou = true_positive / np.maximum(
            true_positive + false_positive + false_negative, 1.0
        )
        best_index = int(candidates[int(np.argmax(iou))])
        threshold = best_index / self.bins
        return threshold, self.metrics_at_threshold(threshold)
