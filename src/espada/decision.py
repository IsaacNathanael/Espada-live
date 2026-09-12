from __future__ import annotations

import argparse
import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path


POLICY = {
    "name": "ESPADA analyst-escalation policy",
    "version": "1.0",
    "priority_review": {
        "minimum_top_score": 0.60,
        "minimum_score_margin": 0.15,
        "maximum_forward_error_km": 3.0,
        "minimum_data_quality": 0.40,
        "minimum_sensitivity_top3_rate": 0.80,
        "maximum_sensitivity_worst_rank": 3,
    },
    "limited_shortlist": {
        "minimum_top_score": 0.40,
        "minimum_score_margin": 0.05,
        "maximum_forward_error_km": 8.0,
    },
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _finite(value: object, default: float = math.inf) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def evaluate_case_decision(case_root: Path, output_dir: Path) -> dict[str, object]:
    """Apply a declared escalation policy without claiming guilt probability."""
    case_root = Path(case_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    alignment = _read(case_root / "case_alignment.json")
    ranking = _read(case_root / "ranking" / "candidates.json")
    sensitivity = _read(case_root / "sensitivity" / "sensitivity_report.json")
    candidates = list(ranking.get("candidates", []))
    top = ranking.get("top_candidate", candidates[0] if candidates else {})
    runner_up = candidates[1] if len(candidates) > 1 else {}
    top_score = _finite(top.get("total_score"), -math.inf)
    second_score = _finite(runner_up.get("total_score"), -math.inf)
    score_margin = top_score - second_score if math.isfinite(second_score) else 0.0
    forward_error = _finite(top.get("forward_error_km"))
    data_quality = _finite(top.get("data_quality"), 0.0)
    candidate_count = int(ranking.get("candidate_count", len(candidates)))
    top3_rate = _finite(sensitivity.get("top_3_rate"), -1.0)
    worst_rank = int(sensitivity.get("worst_rank", 999)) if sensitivity else 999
    priority = POLICY["priority_review"]
    limited = POLICY["limited_shortlist"]

    checks = [
        {
            "gate": "incident_alignment",
            "status": "PASS" if alignment.get("status") == "PASS" else "STOP",
            "observed": alignment.get("status", "MISSING"),
            "requirement": "all incident-time inputs aligned",
        },
        {
            "gate": "candidate_competition",
            "status": "PASS" if candidate_count >= 2 else "STOP",
            "observed": candidate_count,
            "requirement": "at least two candidates",
        },
        {
            "gate": "comparative_score",
            "status": "PASS" if top_score >= priority["minimum_top_score"] else ("WARN" if top_score >= limited["minimum_top_score"] else "STOP"),
            "observed": top_score,
            "requirement": f">={priority['minimum_top_score']:.2f} for priority review",
        },
        {
            "gate": "candidate_separation",
            "status": "PASS" if score_margin >= priority["minimum_score_margin"] else ("WARN" if score_margin >= limited["minimum_score_margin"] else "STOP"),
            "observed": score_margin,
            "requirement": f">={priority['minimum_score_margin']:.2f} score margin for priority review",
        },
        {
            "gate": "forward_replay",
            "status": "PASS" if forward_error <= priority["maximum_forward_error_km"] else ("WARN" if forward_error <= limited["maximum_forward_error_km"] else "STOP"),
            "observed": forward_error,
            "requirement": f"<={priority['maximum_forward_error_km']:.1f} km for priority review",
        },
        {
            "gate": "track_data_quality",
            "status": "PASS" if data_quality >= 0.60 else ("WARN" if data_quality >= priority["minimum_data_quality"] else "STOP"),
            "observed": data_quality,
            "requirement": f">={priority['minimum_data_quality']:.2f}; >=0.60 preferred",
        },
        {
            "gate": "assumption_stability",
            "status": "PASS" if sensitivity and top3_rate >= priority["minimum_sensitivity_top3_rate"] and worst_rank <= priority["maximum_sensitivity_worst_rank"] else ("WARN" if not sensitivity else "STOP"),
            "observed": {"top_3_rate": top3_rate if sensitivity else None, "worst_rank": worst_rank if sensitivity else None},
            "requirement": ">=0.80 Top-3 retention and worst rank <=3",
        },
    ]
    stopped = any(item["status"] == "STOP" for item in checks)
    priority_ready = (
        not stopped
        and top_score >= priority["minimum_top_score"]
        and score_margin >= priority["minimum_score_margin"]
        and forward_error <= priority["maximum_forward_error_km"]
        and data_quality >= priority["minimum_data_quality"]
        and bool(sensitivity)
    )
    limited_ready = (
        not stopped
        and top_score >= limited["minimum_top_score"]
        and score_margin >= limited["minimum_score_margin"]
        and forward_error <= limited["maximum_forward_error_km"]
    )
    if priority_ready:
        decision = "PRIORITY_ANALYST_REVIEW"
        action = "Escalate this ranked shortlist to a human investigator with the complete evidence bundle."
    elif limited_ready:
        decision = "LIMITED_SHORTLIST"
        action = "Retain the candidates for review, but collect stronger forcing, imagery or track evidence before escalation."
    else:
        decision = "ABSTAIN_INSUFFICIENT_EVIDENCE"
        action = "Do not nominate a priority vessel; collect missing or stronger evidence and rerun the case."
    result = {
        "status": "PASS",
        "decision": decision,
        "recommended_action": action,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": POLICY,
        "policy_interpretation": "Fixed engineering escalation thresholds; not statistically calibrated accuracy or a legal standard.",
        "candidate": {
            "blinded_id": top.get("mmsi"),
            "rank": top.get("rank"),
            "comparative_score": top_score,
            "score_margin": score_margin,
            "forward_error_km": forward_error,
            "data_quality": data_quality,
        },
        "checks": checks,
        "safety": [
            "The decision controls analyst workflow only; it never declares discharge, intent, identity or guilt.",
            "Comparative scores are not probabilities and cannot be compared across unrelated incidents.",
            "A priority-review result still requires independent imagery, logs, inspection and sampling evidence.",
        ],
    }
    json_path = output_dir / "decision_gate.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    rows = "".join(
        f"<tr><td>{html.escape(item['gate'].replace('_', ' ').title())}</td><td><span class='{item['status'].lower()}'>{item['status']}</span></td><td>{html.escape(str(item['observed']))}</td><td>{html.escape(item['requirement'])}</td></tr>"
        for item in checks
    )
    safety = "".join(f"<li>{html.escape(item)}</li>" for item in result["safety"])
    html_path = output_dir / "decision_gate.html"
    html_path.write_text(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA Decision Gate</title><style>:root{{--bg:#020a0c;--panel:#082128;--line:#17434b;--ink:#eafffb;--muted:#91aaa6;--mint:#61f2d1;--amber:#ffbd4a;--red:#fb7185}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 15% 0,#104047 0,transparent 35%),var(--bg);color:var(--ink);font:14px/1.55 Inter,Segoe UI,sans-serif}}main{{width:min(1040px,calc(100% - 28px));margin:auto;padding:36px 0 60px}}.tag{{color:var(--mint);font-size:10px;font-weight:900;letter-spacing:.17em}}h1{{font:500 clamp(37px,6vw,68px)/1 Georgia,serif;margin:14px 0}}h1 em{{color:var(--mint);font-style:normal}}p,li{{color:var(--muted)}}.panel{{padding:20px;border:1px solid var(--line);border-radius:17px;background:linear-gradient(145deg,#092a31,#05161a);margin-top:14px}}.decision{{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}}.decision strong{{display:block;color:var(--amber);font:500 30px Georgia,serif}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}.metrics div{{padding:12px;border:1px solid var(--line);border-radius:10px}}small{{display:block;color:var(--muted);font-size:9px;text-transform:uppercase}}.metrics b{{display:block;margin-top:5px;font-size:19px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-top:1px solid var(--line);text-align:left;font-size:11px}}th{{color:var(--muted)}}.pass{{color:var(--mint)}}.warn{{color:var(--amber)}}.stop{{color:var(--red)}}.warning{{border-left:3px solid var(--amber);padding:12px 15px;background:rgba(255,189,74,.06);color:#e8d5a9}}@media(max-width:720px){{.decision{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}}}</style></head><body><main><span class='tag'>ESPADA · HUMAN-IN-THE-LOOP CONTROL</span><h1>Know when to <em>stop.</em></h1><section class='decision'><div class='panel'><small>Operational decision</small><strong>{decision.replace('_', ' ')}</strong><p>{html.escape(action)}</p></div><div class='panel metrics'><div><small>Top score</small><b>{100*top_score:.1f}%</b></div><div><small>Separation</small><b>{100*score_margin:.1f} pts</b></div><div><small>Forward error</small><b>{forward_error:.2f} km</b></div></div></section><section class='panel'><h2>Evidence gates</h2><table><thead><tr><th>Gate</th><th>Status</th><th>Observed</th><th>Policy</th></tr></thead><tbody>{rows}</tbody></table></section><section class='panel'><h2>Safety boundary</h2><div class='warning'>These thresholds control escalation to an analyst. They are not learned confidence, accuracy, guilt probability or a legal standard.</div><ul>{safety}</ul></section></main></body></html>""",
        encoding="utf-8",
    )
    result["artifacts"] = {"json": str(json_path.resolve()), "html": str(html_path.resolve())}
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the ESPADA analyst-escalation policy")
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate_case_decision(args.case_root, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
