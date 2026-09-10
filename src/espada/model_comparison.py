"""Compare completed DARTIS audit reports without rerunning inference."""
import argparse
import html
import json
from pathlib import Path

from .ml_external_eval import EVALUATOR_VERSION, _format_percent

METRICS = {
    "object_precision": "Object precision",
    "object_recall": "Object recall",
    "object_f1": "Object F1",
    "oil_patch_detection_rate": "Oil-patch detection",
    "no_oil_image_specificity": "No-oil specificity",
    "no_oil_image_false_alarm_rate": "False-alarm image rate (lower is better)",
}


def compare_reports(baseline: dict, challenger: dict) -> dict:
    for report in (baseline, challenger):
        if report.get("execution_status") != "PASS" or report.get("evaluator_version") != EVALUATOR_VERSION:
            raise ValueError("Both models must complete the current evaluator; replay old reports")
    for key in ("selection_sha256", "manifest_sha256"):
        value = baseline["dataset"].get(key)
        if not value or value != challenger["dataset"].get(key):
            raise ValueError(f"Incompatible datasets: {key}")
    for key in ("matching_iou_threshold", "minimum_component_pixels"):
        if baseline.get(key) is None or baseline[key] != challenger.get(key):
            raise ValueError(f"Incompatible evaluation rule: {key}")
    for key in ("images", "oil_images", "no_oil_images", "ground_truth_objects"):
        if baseline["external_metrics"][key] != challenger["external_metrics"][key]:
            raise ValueError(f"Different evaluation counts: {key}")
    return {
        "execution_status": "PASS",
        "evaluation": "Development comparison on DARTIS External Audit A",
        "dataset": baseline["dataset"],
        "evaluator_version": EVALUATOR_VERSION,
        "models": {
            "V6": {"model": baseline["model"], "quality_status": baseline["quality_status"],
                   "metrics": baseline["external_metrics"], "subsets": baseline["per_subset_metrics"]},
            "POSEatSea": {"model": challenger["model"], "quality_status": challenger["quality_status"],
                         "metrics": challenger["external_metrics"], "subsets": challenger["per_subset_metrics"]},
        },
        "automatic_promotion": False,
        "limitations": [
            "Audit A informed development; this is not a fresh blind test.",
            "DARTIS JPEG preprocessing differs from calibrated satellite rasters.",
            "Third-party training overlap has not been independently ruled out.",
            "Models use their own frozen published decision rules; scores are object metrics, not pixel IoU.",
            "Passing this development screening policy does not establish operational reliability.",
        ],
    }


def write_comparison(baseline_path: Path, challenger_path: Path, output_dir: Path) -> dict:
    result = compare_reports(json.loads(baseline_path.read_text(encoding="utf-8")),
                             json.loads(challenger_path.read_text(encoding="utf-8")))
    def rows_for(subset=None):
        rows = []
        for key, title in METRICS.items():
            values = [entry["metrics"] if subset is None else entry["subsets"][subset]
                      for entry in result["models"].values()]
            rows.append(f"<tr><td>{html.escape(title)}</td>" + "".join(
                f"<td>{_format_percent(value[key])}</td>" for value in values) + "</tr>")
        return "".join(rows)
    def table(rows):
        return "<table><thead><tr><th>Metric</th><th>V6</th><th>POSEatSea</th></tr></thead><tbody>" + rows + "</tbody></table>"
    subsets = "".join(f"<details><summary>{title}</summary>{table(rows_for(subset))}</details>"
                      for subset, title in (("ow", "Oil / open water"), ("oc", "Oil / coast"),
                                            ("nw", "No oil / open water"), ("nc", "No oil / coast")))
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in result["limitations"])
    gates = " · ".join(f"{name}: {value['quality_status']}" for name, value in result["models"].items())
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ESPADA model comparison</title>
<style>body{{font:16px/1.6 Segoe UI,sans-serif;background:#edf4f3;color:#102e36;margin:0}}
main{{max-width:950px;margin:auto;padding:28px}}table{{width:100%;border-collapse:collapse;background:white}}
td,th{{padding:12px;text-align:left;border-bottom:1px solid #d4e3e0}}details{{margin-top:16px;background:white;padding:12px}}
summary{{cursor:pointer}}h1{{line-height:1.2}}a{{color:#087b77}}.note{{background:#fff2d6;padding:16px}}</style>
<main><p>ESPADA · MODEL COMPARISON</p><h1>V6 versus POSEatSea</h1>
<p>100 locked DARTIS images · identical object matching · frozen model decisions</p>
<p class="note">Development quality gate — {html.escape(gates)}. No model was automatically promoted.</p>
{table(rows_for())}{subsets}<h2>How to read this</h2>
<p>Precision measures how many proposed objects match oil annotations. Recall measures how many annotated objects were found.
False-alarm image rate measures how often a no-oil image contains any proposed object. Lower is better for false alarms.</p>
<ul>{notes}</ul><p><a href="v6/external_evaluation_report.html">V6 details</a> ·
<a href="poseatsea/external_evaluation_report.html">POSEatSea details</a> ·
<a href="https://huggingface.co/23f2003521/poseatsea-weights">POSEatSea attribution and model card</a></p></main></html>"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (output_dir / "comparison.html").write_text(document, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--challenger", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = write_comparison(args.baseline, args.challenger, args.out)
    print(json.dumps({"execution_status": result["execution_status"],
                      "models": {name: data["metrics"] for name, data in result["models"].items()},
                      "report": str((args.out / "comparison.html").resolve())}, indent=2))


if __name__ == "__main__":
    main()
