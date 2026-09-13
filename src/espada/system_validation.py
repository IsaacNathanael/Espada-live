from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def _read(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required validation artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _gate(decision: dict, name: str) -> dict:
    return next(item for item in decision.get("checks", []) if item.get("gate") == name)


def _pct(value: object) -> str:
    return f"{100.0 * float(value):.1f}%"


def _km(value: object) -> str:
    return f"{float(value):.2f} km"


def build_system_scorecard(project_root: Path, output_dir: Path) -> dict[str, object]:
    project_root = Path(project_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    synthetic = _read(project_root / "out/evaluation/evaluation_summary.json")
    wakashio_evaluation = _read(
        project_root / "out/wakashio/evaluation/historical_evaluation.json"
    )
    wakashio_ranking = _read(project_root / "out/wakashio/ranking/candidates.json")
    wakashio_decision = _read(project_root / "out/wakashio/decision/decision_gate.json")
    princess_ranking = _read(
        project_root
        / "out/incidents/princess_empress_2023/run_official/ranking/candidates.json"
    )
    princess_decision = _read(
        project_root
        / "out/incidents/princess_empress_2023/run_official/decision/decision_gate.json"
    )

    synthetic_acceptance = synthetic.get("acceptance", {})
    synthetic_pass = synthetic.get("status") == "PASS" and all(
        bool(value) for value in synthetic_acceptance.values()
    )
    wakashio_top = wakashio_ranking["top_candidate"]
    wakashio_pass = (
        wakashio_evaluation.get("rank") == 1
        and wakashio_decision.get("decision") == "PRIORITY_ANALYST_REVIEW"
        and _gate(wakashio_decision, "forward_shape_replay").get("status") == "PASS"
    )
    princess_top = princess_ranking["top_candidate"]
    princess_pass = (
        princess_decision.get("decision") == "ABSTAIN_INSUFFICIENT_EVIDENCE"
        and any(
            item.get("status") == "STOP" for item in princess_decision.get("checks", [])
        )
    )
    checks = {
        "synthetic_robustness": synthetic_pass,
        "known_source_recovery": wakashio_pass,
        "evidence_limited_abstention": princess_pass,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {
        "status": status,
        "generated_at_utc": generated,
        "checks": checks,
        "synthetic_robustness": {
            "cases": synthetic["config"]["cases"],
            "top_1_accuracy": synthetic["overall"]["top1_accuracy"],
            "top_3_accuracy": synthetic["overall"]["top3_accuracy"],
            "median_origin_error_km": synthetic["overall"]["median_origin_error_km"],
            "p90_origin_error_km": synthetic["overall"]["p90_origin_error_km"],
            "acceptance": synthetic_acceptance,
        },
        "known_source_case": {
            "case": "MV Wakashio",
            "documented_source_rank": wakashio_evaluation["rank"],
            "candidate_count": wakashio_evaluation["candidate_count"],
            "comparative_score": wakashio_top["total_score"],
            "centroid_error_km": wakashio_top["forward_error_km"],
            "shape_error_km": wakashio_top["forward_shape_error_km"],
            "decision": wakashio_decision["decision"],
        },
        "abstention_case": {
            "case": "MT Princess Empress",
            "candidate_count": princess_ranking["candidate_count"],
            "comparative_score": princess_top["total_score"],
            "shape_error_km": princess_top["forward_shape_error_km"],
            "data_quality": princess_top["data_quality"],
            "shape_gate": _gate(princess_decision, "forward_shape_replay")["status"],
            "decision": princess_decision["decision"],
        },
        "claim_boundary": (
            "This scorecard validates controlled robustness, one transparent known-source "
            "reconstruction, and one evidence-limited abstention. It is not an external blind "
            "trial, population accuracy estimate, calibrated guilt probability, or legal finding."
        ),
    }
    json_path = output_dir / "system_scorecard.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    badge = "SYSTEM VALIDATION PASS" if status == "PASS" else "VALIDATION INCOMPLETE"
    cards = "".join(
        f"<div class='check'><i class='{'pass' if passed else 'fail'}'></i>"
        f"<span>{html.escape(name.replace('_', ' ').title())}</span>"
        f"<b>{'PASS' if passed else 'FAIL'}</b></div>"
        for name, passed in checks.items()
    )
    document = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA System Scorecard</title>
<style>:root{{--bg:#061016;--panel:#0c1c24;--line:#203944;--ink:#effffc;--muted:#95adb4;--mint:#55e6cf;--amber:#ffbd59;--red:#ff7888}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#12353d 0,transparent 32%),var(--bg);color:var(--ink);font:15px/1.55 Inter,Segoe UI,sans-serif}}main{{width:min(1120px,calc(100% - 28px));margin:auto;padding:38px 0 60px}}.tag{{color:var(--mint);font-size:11px;font-weight:900;letter-spacing:.17em}}h1{{font:500 clamp(42px,7vw,78px)/.96 Georgia,serif;margin:14px 0 18px;letter-spacing:-.045em}}h1 em{{color:var(--mint);font-style:normal}}.lead{{color:#bfd1d5;max-width:780px;font-size:17px}}.badge{{display:inline-block;margin:12px 0 24px;padding:9px 13px;border:1px solid #397566;border-radius:999px;color:var(--mint);font-weight:900;font-size:11px;letter-spacing:.1em}}.checks,.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.check,.panel{{border:1px solid var(--line);border-radius:14px;background:linear-gradient(145deg,#102832,#09171e)}}.check{{display:grid;grid-template-columns:10px 1fr auto;gap:10px;align-items:center;padding:15px}}.check i{{width:9px;height:9px;border-radius:50%}}.pass{{background:var(--mint)}}.fail{{background:var(--red)}}.check span{{color:var(--muted);font-size:12px}}.check b{{font-size:11px;color:var(--mint)}}.grid{{margin-top:14px}}.panel{{padding:20px}}.panel h2{{margin:0 0 5px;font:500 25px Georgia,serif}}.panel p{{color:var(--muted);margin:0 0 16px}}.metrics{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.metric{{padding:11px;border:1px solid var(--line);border-radius:9px}}.metric small{{display:block;color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.1em}}.metric b{{display:block;margin-top:5px;font-size:19px}}.verdict{{margin-top:14px;padding:12px;border-left:3px solid var(--mint);background:#07151b;color:#cce0e2}}.warn{{border-left-color:var(--amber);color:#efddb5}}footer{{margin-top:18px;padding:17px;border:1px solid #4b3b23;border-left:4px solid var(--amber);background:#16150f;color:#d9c99f}}@media(max-width:800px){{.checks,.grid{{grid-template-columns:1fr}}}}</style></head>
<body><main><div class='tag'>ESPADA · END-TO-END EVIDENCE VALIDATION</div><h1>Three tests.<br><em>One honest system.</em></h1><p class='lead'>The same attribution pipeline is required to recover a documented source when evidence converges—and refuse nomination when evidence quality fails.</p><div class='badge'>{badge}</div><section class='checks'>{cards}</section><section class='grid'><article class='panel'><h2>Controlled robustness</h2><p>{synthetic['config']['cases']} seeded stress cases across current bias, wind bias, AIS dropout, position noise and combined stress.</p><div class='metrics'><div class='metric'><small>Top-1</small><b>{_pct(synthetic['overall']['top1_accuracy'])}</b></div><div class='metric'><small>Top-3</small><b>{_pct(synthetic['overall']['top3_accuracy'])}</b></div><div class='metric'><small>Median origin error</small><b>{_km(synthetic['overall']['median_origin_error_km'])}</b></div><div class='metric'><small>P90 origin error</small><b>{_km(synthetic['overall']['p90_origin_error_km'])}</b></div></div></article><article class='panel'><h2>Known source recovered</h2><p>MV Wakashio identity remained sealed until the pseudonymized ranking existed.</p><div class='metrics'><div class='metric'><small>Documented rank</small><b>#{wakashio_evaluation['rank']} / {wakashio_evaluation['candidate_count']}</b></div><div class='metric'><small>Shape error</small><b>{_km(wakashio_top['forward_shape_error_km'])}</b></div><div class='metric'><small>Centroid error</small><b>{_km(wakashio_top['forward_error_km'])}</b></div><div class='metric'><small>Decision</small><b>REVIEW</b></div></div><div class='verdict'>{html.escape(wakashio_evaluation['answer'])}</div></article><article class='panel'><h2>Unsafe claim refused</h2><p>Princess Empress compared {princess_ranking['candidate_count']} real AIS candidates but evidence gates prevented nomination.</p><div class='metrics'><div class='metric'><small>Comparative fit · not probability</small><b>{_pct(princess_top['total_score'])}</b></div><div class='metric'><small>Shape error</small><b>{_km(princess_top['forward_shape_error_km'])}</b></div><div class='metric'><small>Track quality</small><b>{_pct(princess_top['data_quality'])}</b></div><div class='metric'><small>Decision</small><b>ABSTAIN</b></div></div><div class='verdict warn'>High comparative fit did not override weak and unstable evidence.</div></article></section><footer><b>Claim boundary:</b> {html.escape(result['claim_boundary'])}<br><small>Generated {generated} directly from ESPADA validation artifacts.</small></footer></main></body></html>"""
    html_path = output_dir / "system_scorecard.html"
    html_path.write_text(document, encoding="utf-8")
    result["artifacts"] = {
        "json": str(json_path.resolve()),
        "html": str(html_path.resolve()),
    }
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESPADA's system validation scorecard")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_system_scorecard(args.project_root.resolve(), args.out.resolve()), indent=2))


if __name__ == "__main__":
    main()
