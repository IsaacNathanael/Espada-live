from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def _read(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required demo artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_judge_demo(project_root: Path, output_path: Path) -> dict[str, object]:
    project_root = Path(project_root)
    scorecard = _read(project_root / "out/system_validation/system_scorecard.json")
    wakashio = _read(project_root / "out/wakashio/evaluation/historical_evaluation.json")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    system_pass = scorecard.get("status") == "PASS"
    status = "READY" if system_pass else "CHECK REQUIRED"
    status_class = "ready" if system_pass else "stop"
    links = [
        ("01", "System scorecard", "Three-layer validation summary", "../system_validation/system_scorecard.html", "Start here"),
        ("02", "Forensic replay", "Animated reverse-drift reconstruction", "../wakashio/replay/espada_forensic_replay.gif", "Show the idea"),
        ("03", "Known-source evidence", "Why Wakashio ranked #1", "../wakashio/dossier/evidence_dossier.html", "Inspect evidence"),
        ("04", "Safe abstention", "Why Princess Empress was not attributed", "../incidents/princess_empress_2023/run_official/dossier/evidence_dossier.html", "Inspect safety"),
        ("05", "Validation comparison", "Escalate versus abstain", "../validation_showcase/real_world_validation.html", "Compare cases"),
        ("06", "Interactive laboratory", "Controlled local evidence engine", "../demo/dashboard.html", "Explore"),
    ]
    cards = "".join(
        f"<a class='card' href='{href}'><div class='num'>{number}</div><div><small>{html.escape(action)}</small><h2>{html.escape(title)}</h2><p>{html.escape(copy)}</p></div><span>OPEN →</span></a>"
        for number, title, copy, href, action in links
    )
    document = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA Demo Command Center</title><style>:root{{--bg:#050d12;--panel:#0b1b23;--line:#1e3a44;--ink:#edfffb;--muted:#8ca8ae;--mint:#58eadd;--amber:#ffbd59;--red:#ff7888}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#14343d 0,transparent 32%),var(--bg);color:var(--ink);font:15px/1.5 Inter,Segoe UI,sans-serif}}main{{width:min(1080px,calc(100% - 28px));margin:auto;padding:38px 0 60px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:start}}.eyebrow,small{{color:var(--mint);font-size:10px;font-weight:900;letter-spacing:.16em;text-transform:uppercase}}h1{{font:500 clamp(42px,7vw,76px)/.96 Georgia,serif;margin:12px 0 16px;letter-spacing:-.045em}}h1 em{{color:var(--mint);font-style:normal}}.lead{{max-width:700px;color:#b7ccd1;font-size:17px}}.status{{padding:9px 13px;border:1px solid var(--line);border-radius:999px;font-size:11px;font-weight:900}}.status.ready{{color:var(--mint);border-color:#367c70}}.status.stop{{color:var(--red)}}.proof{{margin:25px 0;padding:17px 19px;border:1px solid #31505a;border-left:4px solid var(--mint);background:#091820}}.proof b{{color:var(--mint)}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.card{{position:relative;display:grid;grid-template-columns:42px 1fr auto;gap:14px;align-items:center;min-height:138px;padding:19px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(145deg,#0f2630,#09171e);color:var(--ink);text-decoration:none;transition:.16s ease}}.card:hover{{transform:translateY(-2px);border-color:var(--mint);box-shadow:0 12px 30px #0007}}.num{{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:#15353d;color:var(--mint);font-weight:900}}.card h2{{margin:5px 0 2px;font:500 25px Georgia,serif}}.card p{{margin:0;color:var(--muted)}}.card>span{{color:var(--amber);font-size:11px;font-weight:900}}footer{{margin-top:20px;color:var(--muted);font-size:12px}}@media(max-width:760px){{header{{display:block}}.status{{display:inline-block;margin-bottom:12px}}.grid{{grid-template-columns:1fr}}.card{{grid-template-columns:38px 1fr}}.card>span{{grid-column:2}}}}</style></head><body><main><header><div><div class='eyebrow'>ESPADA · DEMO COMMAND CENTER</div><h1>Trace the slick.<br><em>Test the evidence.</em></h1><p class='lead'>A guided five-minute route through the working Reverse Drift Attribution prototype and its safety boundaries.</p></div><div class='status {status_class}'>{status}</div></header><div class='proof'><b>Evaluator answer:</b> {html.escape(str(wakashio['answer']))} The same pipeline abstained on Princess Empress when the evidence was insufficient.</div><section class='grid'>{cards}</section><footer>All displayed results are generated from project artifacts. Comparative scores are not guilt probabilities, and every operational output remains subject to human review.</footer></main></body></html>"""
    output_path.write_text(document, encoding="utf-8")
    return {
        "status": "PASS",
        "system_validation": scorecard.get("status"),
        "output": str(output_path.resolve()),
        "views": len(links),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESPADA's judge demo command center")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_judge_demo(args.project_root.resolve(), args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
