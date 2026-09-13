from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def _read(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required demo artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(value: object) -> str:
    return f"{float(value or 0) * 100:.1f}%"


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
        f"<a class='card' target='_blank' rel='noopener' href='{href}'><div class='num'>{number}</div>"
        f"<div><small>{html.escape(action)}</small><h2>{html.escape(title)}</h2><p>{html.escape(copy)}</p></div>"
        "<span>OPEN →</span></a>"
        for number, title, copy, href, action in links
    )

    synthetic = scorecard.get("synthetic_robustness", {})
    known = scorecard.get("known_source_case", {})
    abstention = scorecard.get("abstention_case", {})
    presenter_steps = [
        {
            "title": "The enforcement gap",
            "cue": "25 seconds",
            "body": "A satellite can show a slick, but detection alone cannot identify who released it. ESPADA reconstructs where and when it may have originated, then tests vessel evidence.",
            "url": "../wakashio/replay/espada_forensic_replay.gif",
        },
        {
            "title": "The system is probabilistic",
            "cue": "45 seconds",
            "body": f"Across {synthetic.get('cases', 24)} controlled stress cases, Top-3 recovery was {_pct(synthetic.get('top_3_accuracy'))} and median origin error was {float(synthetic.get('median_origin_error_km', 0)):.2f} km. We preserve an origin field instead of pretending there is one certain point.",
            "url": "../system_validation/system_scorecard.html",
        },
        {
            "title": "Known source recovered",
            "cue": "60 seconds",
            "body": f"The MV Wakashio identity stayed sealed until ranking existed. The documented source ranked #{known.get('documented_source_rank', 1)} of {known.get('candidate_count', 5)}, answering the evaluator's central question directly.",
            "url": "../wakashio/dossier/evidence_dossier.html",
        },
        {
            "title": "Unsafe claim refused",
            "cue": "60 seconds",
            "body": f"Princess Empress compared {abstention.get('candidate_count', 52)} real AIS candidates. Even with {_pct(abstention.get('comparative_score'))} comparative fit, track quality was only {_pct(abstention.get('data_quality'))}, so the system abstained.",
            "url": "../incidents/princess_empress_2023/run_official/dossier/evidence_dossier.html",
        },
        {
            "title": "The honest claim",
            "cue": "30 seconds",
            "body": "ESPADA prioritizes analyst review; it does not declare guilt. The prototype demonstrates reverse drift, ranked attribution, evidence-quality gates and a reproducible dossier—not a legal finding.",
            "url": "../validation_showcase/real_world_validation.html",
        },
    ]
    steps_json = json.dumps(presenter_steps, ensure_ascii=False).replace("</", "<\\/")

    document = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>ESPADA Demo Command Center</title>
<style>
:root{{--bg:#050d12;--panel:#0b1b23;--line:#1e3a44;--ink:#edfffb;--muted:#8ca8ae;--mint:#58eadd;--amber:#ffbd59;--red:#ff7888}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#14343d 0,transparent 32%),var(--bg);color:var(--ink);font:15px/1.5 Inter,Segoe UI,sans-serif}}button{{font:inherit}}
main{{width:min(1080px,calc(100% - 28px));margin:auto;padding:38px 0 60px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:start}}.eyebrow,small{{color:var(--mint);font-size:10px;font-weight:900;letter-spacing:.16em;text-transform:uppercase}}
h1{{font:500 clamp(42px,7vw,76px)/.96 Georgia,serif;margin:12px 0 16px;letter-spacing:-.045em}}h1 em{{color:var(--mint);font-style:normal}}.lead{{max-width:700px;color:#b7ccd1;font-size:17px}}
.actions{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;justify-content:flex-end}}.status,.guide{{padding:9px 13px;border:1px solid var(--line);border-radius:999px;font-size:11px;font-weight:900}}.status.ready{{color:var(--mint);border-color:#367c70}}.status.stop{{color:var(--red)}}.guide{{cursor:pointer;color:#061016;background:var(--mint);border-color:var(--mint)}}
.proof{{margin:25px 0;padding:17px 19px;border:1px solid #31505a;border-left:4px solid var(--mint);background:#091820}}.proof b{{color:var(--mint)}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{position:relative;display:grid;grid-template-columns:42px 1fr auto;gap:14px;align-items:center;min-height:138px;padding:19px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(145deg,#0f2630,#09171e);color:var(--ink);text-decoration:none;transition:.16s ease}}.card:hover{{transform:translateY(-2px);border-color:var(--mint);box-shadow:0 12px 30px #0007}}
.num{{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:#15353d;color:var(--mint);font-weight:900}}.card h2{{margin:5px 0 2px;font:500 25px Georgia,serif}}.card p{{margin:0;color:var(--muted)}}.card>span{{color:var(--amber);font-size:11px;font-weight:900}}footer{{margin-top:20px;color:var(--muted);font-size:12px}}
dialog{{width:min(720px,calc(100% - 26px));padding:0;border:1px solid #39626c;border-radius:18px;background:#08161d;color:var(--ink);box-shadow:0 28px 90px #000c}}dialog::backdrop{{background:#02080dcc;backdrop-filter:blur(5px)}}.presenter{{padding:24px}}.presenter-top{{display:flex;justify-content:space-between;align-items:center;gap:14px}}.timer{{font:700 22px/1 monospace;color:var(--amber)}}
.close{{border:0;background:transparent;color:var(--muted);font-size:25px;cursor:pointer}}.progress{{display:flex;gap:6px;margin:22px 0}}.progress i{{height:5px;flex:1;border-radius:5px;background:#173039}}.progress i.on{{background:var(--mint)}}.presenter h2{{font:500 36px Georgia,serif;margin:8px 0}}.cue{{color:var(--mint);font-size:11px;font-weight:900;text-transform:uppercase;letter-spacing:.12em}}.talk{{font-size:19px;color:#c9dade;min-height:118px}}
.presenter-actions{{display:flex;gap:9px;justify-content:space-between;flex-wrap:wrap}}.presenter-actions button,.presenter-actions a{{padding:10px 14px;border-radius:9px;border:1px solid var(--line);background:#102832;color:var(--ink);text-decoration:none;cursor:pointer;font-weight:800}}.presenter-actions .primary{{background:var(--mint);color:#051016;border-color:var(--mint)}}
@media(max-width:760px){{header{{display:block}}.actions{{justify-content:flex-start;margin:16px 0}}.grid{{grid-template-columns:1fr}}.card{{grid-template-columns:38px 1fr}}.card>span{{grid-column:2}}}}
</style></head><body><main>
<header><div><div class='eyebrow'>ESPADA · DEMO COMMAND CENTER</div><h1>Trace the slick.<br><em>Test the evidence.</em></h1><p class='lead'>A guided five-minute route through the working Reverse Drift Attribution prototype and its safety boundaries.</p></div><div class='actions'><div class='status {status_class}'>{status}</div><button class='guide' id='startGuide'>▶ START PRESENTER MODE</button></div></header>
<div class='proof'><b>Evaluator answer:</b> {html.escape(str(wakashio['answer']))} The same pipeline abstained on Princess Empress when the evidence was insufficient.</div><section class='grid'>{cards}</section>
<footer>All displayed results are generated from project artifacts. Comparative scores are not guilt probabilities, and every operational output remains subject to human review.</footer></main>
<dialog id='presenter'><div class='presenter'><div class='presenter-top'><div><small>Guided five-minute demonstration</small><div class='timer' id='timer'>05:00</div></div><button class='close' id='closeGuide' aria-label='Close'>×</button></div><div class='progress' id='progress'></div><div class='cue' id='cue'></div><h2 id='stepTitle'></h2><p class='talk' id='stepBody'></p><div class='presenter-actions'><button id='previous'>← Previous</button><a id='evidence' target='_blank' rel='noopener'>Open this evidence ↗</a><button class='primary' id='next'>Next →</button></div></div></dialog>
<script>
const steps={steps_json};let step=0,remaining=300,tick=null;
const modal=document.getElementById('presenter'),title=document.getElementById('stepTitle'),body=document.getElementById('stepBody'),cue=document.getElementById('cue'),evidence=document.getElementById('evidence'),progress=document.getElementById('progress'),timer=document.getElementById('timer');
function render(){{const item=steps[step];title.textContent=item.title;body.textContent=item.body;cue.textContent=`Step ${{step+1}} of ${{steps.length}} · ${{item.cue}}`;evidence.href=item.url;document.getElementById('previous').disabled=step===0;document.getElementById('next').textContent=step===steps.length-1?'Finish ✓':'Next →';progress.innerHTML=steps.map((_,i)=>`<i class='${{i<=step?'on':''}}'></i>`).join('')}}
function time(){{const m=String(Math.floor(remaining/60)).padStart(2,'0'),s=String(remaining%60).padStart(2,'0');timer.textContent=`${{m}}:${{s}}`;if(remaining>0)remaining--}}
document.getElementById('startGuide').onclick=()=>{{step=0;remaining=300;render();time();clearInterval(tick);tick=setInterval(time,1000);modal.showModal()}};
document.getElementById('closeGuide').onclick=()=>{{clearInterval(tick);modal.close()}};document.getElementById('previous').onclick=()=>{{if(step>0)step--;render()}};
document.getElementById('next').onclick=()=>{{if(step<steps.length-1){{step++;render()}}else{{clearInterval(tick);modal.close()}}}};modal.addEventListener('cancel',()=>clearInterval(tick));
</script></body></html>"""
    output_path.write_text(document, encoding="utf-8")
    return {
        "status": "PASS",
        "system_validation": scorecard.get("status"),
        "output": str(output_path.resolve()),
        "views": len(links),
        "presenter_steps": len(presenter_steps),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESPADA's judge demo command center")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_judge_demo(args.project_root.resolve(), args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
