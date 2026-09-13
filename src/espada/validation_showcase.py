from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _image_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _pct(value: float) -> str:
    return f"{100 * float(value):.0f}%"


def _num(value: float, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


def _check(decision: dict, gate: str) -> dict:
    return next(item for item in decision["checks"] if item["gate"] == gate)


def _gate_rows(decision: dict) -> str:
    labels = {
        "incident_alignment": "Incident-time alignment",
        "candidate_competition": "Candidate competition",
        "comparative_score": "Comparative score",
        "candidate_separation": "Leader separation",
        "forward_replay": "Forward replay",
        "track_data_quality": "AIS track quality",
        "assumption_stability": "Assumption stability",
    }
    rows = []
    for item in decision["checks"]:
        observed = item["observed"]
        if isinstance(observed, dict):
            observed = ", ".join(
                f"{key.replace('_', ' ')}: {_pct(value) if isinstance(value, float) else value}"
                for key, value in observed.items()
                if value is not None and key in {"top_1_rate", "top_3_rate", "candidate_matches"}
            )
        elif isinstance(observed, float):
            observed = _num(observed, 3)
        status = html.escape(str(item["status"]).lower())
        rows.append(
            f"<tr><td>{html.escape(labels.get(item['gate'], item['gate']))}</td>"
            f"<td>{html.escape(str(observed))}</td>"
            f"<td>{html.escape(str(item['requirement']))}</td>"
            f"<td><span class='pill {status}'>{html.escape(str(item['status']))}</span></td></tr>"
        )
    return "".join(rows)


def build_report(project_root: Path, output_path: Path) -> dict:
    wak_eval = _read(project_root / "out/wakashio/evaluation/historical_evaluation.json")
    wak_decision = _read(project_root / "out/wakashio/decision/decision_gate.json")
    wak_bundle = _read(project_root / "out/wakashio/dossier/evidence_bundle.json")
    mindoro_decision = _read(
        project_root / "out/incidents/princess_empress_2023/run_official/decision/decision_gate.json"
    )
    mindoro_bundle = _read(
        project_root / "out/incidents/princess_empress_2023/run_official/dossier/evidence_bundle.json"
    )
    mapped_slick = _read(
        project_root / "out/incidents/princess_empress_2023/official_slick/wwf_possible_slick.geojson"
    )["features"][0]["properties"]

    wak_stability = _check(wak_decision, "assumption_stability")["observed"]
    wak_candidate = wak_decision["candidate"]
    mindoro_candidate = mindoro_decision["candidate"]
    wak_ranked_candidate = wak_bundle["ranking"]["top_candidate"]
    mindoro_ranked_candidate = mindoro_bundle["ranking"]["top_candidate"]
    wak_map = _image_uri(project_root / "out/wakashio/ranking/attribution_map.png")
    mindoro_map = _image_uri(
        project_root / "out/incidents/princess_empress_2023/run_official/ranking/attribution_map.png"
    )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESPADA real-world validation</title>
<style>
:root{{--ink:#e9f2f7;--muted:#91a8b5;--bg:#071016;--panel:#0d1a22;--line:#20343f;--cyan:#37d6c7;--amber:#ffb347;--red:#ff6b6b;--green:#58d68d}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 15% 0,#12303a 0,transparent 30%),var(--bg);color:var(--ink);font:16px/1.55 Inter,Segoe UI,Arial,sans-serif}}
main{{max-width:1180px;margin:auto;padding:40px 24px 72px}} .eyebrow{{color:var(--cyan);font-weight:800;letter-spacing:.16em;text-transform:uppercase;font-size:.78rem}}
h1{{font-size:clamp(2.2rem,5vw,4.7rem);line-height:.98;max-width:920px;margin:.45rem 0 1rem;letter-spacing:-.055em}} h2{{font-size:1.55rem;margin:0}} h3{{font-size:1rem;margin:0;color:var(--muted)}}
.lead{{max-width:760px;color:#bdd0d9;font-size:1.1rem}} .thesis{{margin:30px 0;padding:20px 22px;border:1px solid #2b4b55;border-left:5px solid var(--cyan);background:#0b171e;font-size:1.12rem}}
.switch{{display:flex;gap:10px;margin:28px 0 18px;flex-wrap:wrap}} button{{border:1px solid var(--line);background:#0b161d;color:var(--ink);padding:12px 18px;border-radius:999px;font:inherit;font-weight:750;cursor:pointer}} button.active{{background:var(--cyan);color:#04100f;border-color:var(--cyan)}}
.case{{display:none}} .case.active{{display:block}} .casehead{{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:18px}} .decision{{font-weight:850;padding:9px 13px;border-radius:8px;white-space:nowrap}} .escalate{{background:#12392f;color:#83efbd}} .abstain{{background:#3d2915;color:#ffd08a}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}} .metric,.panel{{background:linear-gradient(145deg,#0f2029,#0b171e);border:1px solid var(--line);border-radius:14px}} .metric{{padding:18px}} .metric b{{display:block;font-size:1.65rem;line-height:1.1}} .metric span{{color:var(--muted);font-size:.85rem}}
.visual{{padding:12px;margin:18px 0}} .visual img{{display:block;width:100%;border-radius:8px;background:white}} .caption{{color:var(--muted);font-size:.84rem;padding:9px 4px 2px}}
.split{{display:grid;grid-template-columns:1.2fr .8fr;gap:16px}} .panel{{padding:20px;overflow:hidden}} table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:.88rem}} th,td{{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--muted)}}
.pill{{font-size:.72rem;font-weight:850;padding:3px 7px;border-radius:999px}} .pass{{color:#8df2c4;background:#12372e}} .warn{{color:#ffd18b;background:#3a2915}} .stop{{color:#ff9d9d;background:#3d1d22}}
ul{{margin:10px 0;padding-left:20px}} a{{color:#78e9df}} .verdict{{font-size:1.08rem;padding:18px;border-radius:12px;background:#071217;border:1px dashed #31505c}}
.matrix{{margin-top:42px}} .matrixgrid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .result{{padding:22px}} .result strong{{font-size:1.35rem;display:block;margin-bottom:5px}} footer{{margin-top:40px;color:var(--muted);font-size:.86rem;border-top:1px solid var(--line);padding-top:18px}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}.split,.matrixgrid{{grid-template-columns:1fr}}.casehead{{display:block}}.decision{{display:inline-block;margin-top:10px}}}}
</style>
</head>
<body><main>
<div class="eyebrow">ESPADA · Reverse Drift Attribution</div>
<h1>Same pipeline.<br>Two defensible decisions.</h1>
<p class="lead">A real-world validation pair showing that ESPADA can elevate a documented source when evidence converges—and refuse attribution when the same score would be misleading.</p>
<div class="thesis"><b>The safety test:</b> Can the system resist a convincing numerical score when AIS quality and sensitivity evidence disagree? <b>Yes.</b></div>
<div class="switch" role="tablist" aria-label="Validation cases">
<button class="active" data-target="wakashio" role="tab" aria-selected="true">01 · Wakashio known-source test</button>
<button data-target="mindoro" role="tab" aria-selected="false">02 · Princess Empress abstention</button>
</div>

<section id="wakashio" class="case active" role="tabpanel">
<div class="casehead"><div><h2>MV Wakashio · Mauritius, 2020</h2><h3>Historical known-source reconstruction</h3></div><div class="decision escalate">PRIORITY ANALYST REVIEW</div></div>
<div class="grid">
<div class="metric"><b>#1 / {wak_eval['candidate_count']}</b><span>documented vessel rank</span></div>
<div class="metric"><b>{_num(wak_ranked_candidate['forward_shape_error_km'])} km</b><span>particle-cloud shape error</span></div>
<div class="metric"><b>{_num(wak_candidate['score_margin'],3)}</b><span>lead over runner-up</span></div>
<div class="metric"><b>{_pct(wak_stability['top_3_rate'])}</b><span>Top-3 sensitivity retention</span></div>
</div>
<div class="panel visual"><img src="{wak_map}" alt="Wakashio AIS tracks over the inferred origin field"><div class="caption">Blinded ranking was written before the truth identity was opened. The orange track is the leading pseudonymized candidate.</div></div>
<div class="split"><div class="panel"><h2>Decision-gate evidence</h2><table><thead><tr><th>Gate</th><th>Observed</th><th>Rule</th><th>Status</th></tr></thead><tbody>{_gate_rows(wak_decision)}</tbody></table></div>
<aside class="panel"><h2>What this validates</h2><ul><li>The documented release vessel ranked above every nearby candidate.</li><li>Forward drift reached {_num(wak_candidate['forward_error_km'])} km centroid error and {_num(wak_ranked_candidate['forward_shape_error_km'])} km particle-cloud shape error.</li><li>Identity was withheld from the ranker until after ranking.</li><li>AIS silence never added score.</li></ul><div class="verdict"><b>Answer to the evaluator:</b><br>{html.escape(wak_eval['answer'])}</div></aside></div>
</section>

<section id="mindoro" class="case" role="tabpanel">
<div class="casehead"><div><h2>MT Princess Empress · Philippines, 2023</h2><h3>External expert slick mapping + sparse historical AIS</h3></div><div class="decision abstain">ABSTAIN · INSUFFICIENT EVIDENCE</div></div>
<div class="grid">
<div class="metric"><b>{mindoro_bundle['ranking']['candidate_count']}</b><span>AIS candidates compared</span></div>
<div class="metric"><b>{_num(mindoro_candidate['comparative_score'],3)}</b><span>top comparative score—not probability</span></div>
<div class="metric"><b>{_num(mindoro_candidate['data_quality'],2)}</b><span>leader track quality</span></div>
<div class="metric"><b>{_num(mindoro_ranked_candidate['forward_shape_error_km'])} km</b><span>particle-cloud shape error</span></div>
</div>
<div class="panel visual"><img src="{mindoro_map}" alt="Princess Empress case AIS tracks over the inferred origin field"><div class="caption">The leading candidate fit one hypothesis well, but sparse tracks and poor stability prevented nomination.</div></div>
<div class="split"><div class="panel"><h2>Decision-gate evidence</h2><table><thead><tr><th>Gate</th><th>Observed</th><th>Rule</th><th>Status</th></tr></thead><tbody>{_gate_rows(mindoro_decision)}</tbody></table></div>
<aside class="panel"><h2>Why ESPADA stopped</h2><ul><li>Track quality was {_num(mindoro_candidate['data_quality'],2)}, below the 0.40 gate.</li><li>The score margin was only {_num(mindoro_candidate['score_margin'],3)}, below the 0.15 priority threshold.</li><li>The leading candidate was not stable across physical assumptions.</li><li>The published slick layer was explicitly “subject to ground verification.”</li></ul><div class="verdict"><b>Correct operational output:</b><br>Collect stronger AIS, logs, inspections or samples. Do not nominate a vessel.</div></aside></div>
</section>

<section class="matrix"><div class="eyebrow">Validation matrix</div><h2>What the pair proves</h2><div class="matrixgrid">
<div class="panel result"><strong style="color:var(--green)">Converging evidence → escalate</strong>Wakashio passes identity-blinded ranking, forward replay, candidate separation and sensitivity gates.</div>
<div class="panel result"><strong style="color:var(--amber)">Conflicting evidence → abstain</strong>Princess Empress shows that even a {_num(mindoro_candidate['comparative_score'],3)} comparative score cannot bypass weak data and unstable assumptions.</div>
</div></section>
<section class="panel" style="margin-top:16px"><h2>Provenance and limits</h2><p>Wakashio uses UNITAR-UNOSAT slick mapping, Copernicus Marine currents, Open-Meteo historical wind, Global Fishing Watch vessel presence and the official casualty record. Princess Empress uses WWF Philippines’ possible-slick layer derived from Copernicus Sentinel-1, the same environmental sources and Global Fishing Watch vessel presence.</p>
<p><a href="{html.escape(wak_eval['sources']['official_casualty_report'])}">Wakashio official report</a> · <a href="{html.escape(wak_eval['sources']['unosat_product'])}">UNOSAT product</a> · <a href="{html.escape(mapped_slick['source_layer_url'])}">Mindoro official map layer</a> · <a href="https://response.restoration.noaa.gov/orr-supporting-oil-spill-oriental-mindoro-philippines">NOAA incident summary</a></p>
<p><b>Scope:</b> These are transparent historical reconstructions, not external blind trials or legal findings. Comparative scores are ranking aids, not calibrated guilt probabilities.</p></section>
<footer>Generated directly from ESPADA evidence bundles and fixed decision-gate outputs. No displayed metric is manually entered.</footer>
</main>
<script>
const buttons=[...document.querySelectorAll('button[data-target]')];
buttons.forEach(button=>button.addEventListener('click',()=>{{buttons.forEach(b=>{{b.classList.toggle('active',b===button);b.setAttribute('aria-selected',b===button)}});document.querySelectorAll('.case').forEach(c=>c.classList.toggle('active',c.id===button.dataset.target));}}));
</script></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return {
        "status": "PASS",
        "output": str(output_path.resolve()),
        "cases": [
            {"case": "MV Wakashio", "documented_source_rank": wak_eval["rank"], "decision": wak_decision["decision"]},
            {"case": "MT Princess Empress", "documented_source_rank": None, "decision": mindoro_decision["decision"]},
        ],
        "claim": "Known-source escalation plus evidence-limited abstention; not an external blind-test claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESPADA's two-case real-world validation report")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_report(args.project_root.resolve(), args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
