from __future__ import annotations

import base64
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _image_uri(path: Path) -> str:
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _number(value: object, suffix: str = "") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def generate_evidence_dossier(case_root: Path, output_dir: Path) -> dict[str, object]:
    """Build a portable, self-contained dossier from a completed ESPADA case."""
    case_root = Path(case_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    alignment = _read_json(case_root / "case_alignment.json")
    estimate = _read_json(case_root / "drift" / "release_estimate.json")
    slick = _read_json(case_root / "drift" / "slick_analysis.json")
    ais = _read_json(case_root / "ais" / "ais_quality.json")
    ranking = _read_json(case_root / "ranking" / "candidates.json")
    historical = _read_json(case_root / "evaluation" / "historical_evaluation.json")
    sensitivity = _read_json(case_root / "sensitivity" / "sensitivity_report.json")
    candidates = ranking.get("candidates", [])
    top = ranking.get("top_candidate", {})
    silence = ranking.get("silence_analysis", {})
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence_files = {
        "case_alignment": case_root / "case_alignment.json",
        "release_estimate": case_root / "drift" / "release_estimate.json",
        "slick_analysis": case_root / "drift" / "slick_analysis.json",
        "ais_quality": case_root / "ais" / "ais_quality.json",
        "candidate_ranking": case_root / "ranking" / "candidates.json",
        "historical_evaluation": case_root / "evaluation" / "historical_evaluation.json",
        "sensitivity_report": case_root / "sensitivity" / "sensitivity_report.json",
    }
    integrity = {
        key: {"path": str(path.resolve()), "sha256": digest}
        for key, path in evidence_files.items()
        if (digest := _sha256(path)) is not None
    }

    bundle = {
        "schema": "espada.evidence-dossier.v1",
        "generated_at_utc": created,
        "case_status": "READY_FOR_ANALYST_REVIEW"
        if alignment.get("status") == "PASS" and ranking.get("status") == "PASS"
        else "INCOMPLETE",
        "decision_scope": "Investigative shortlist only; not a finding of discharge, intent, identity or guilt.",
        "alignment": alignment,
        "release_estimate": estimate,
        "slick_analysis": slick,
        "ais_quality": ais,
        "ranking": ranking,
        "historical_evaluation": historical or None,
        "sensitivity_analysis": sensitivity or None,
        "artifact_integrity": integrity,
    }
    bundle_path = output_dir / "evidence_bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

    rows = "".join(
        "<tr>"
        f"<td><span class='rank'>{html.escape(str(item.get('rank', '—')))}</span></td>"
        f"<td><b>{html.escape(str(item.get('vessel_name', 'Unknown')))}</b><small>{html.escape(str(item.get('mmsi', '')))}</small></td>"
        f"<td class='score'>{_percent(item.get('total_score'))}</td>"
        f"<td>{_percent(item.get('presence_score'))}</td>"
        f"<td>{_number(item.get('forward_error_km'), ' km')}</td>"
        f"<td>{_percent(item.get('data_quality'))}</td>"
        f"<td>{html.escape(str(item.get('silence_classification', 'not evaluated')).replace('_', ' '))}</td>"
        "</tr>"
        for item in candidates[:10]
    )
    checks = alignment.get("checks", {})
    check_cards = "".join(
        f"<div class='check {'pass' if value else 'fail'}'><i></i><span>{html.escape(key.replace('_', ' ').title())}</span><b>{'PASS' if value else 'STOP'}</b></div>"
        for key, value in checks.items()
    )
    limitations = list(top.get("limitations", [])) + [
        "Candidate scores are comparative within this case and are not calibrated guilt probabilities.",
        "AIS gaps can have benign causes; silence never receives a suspicion bonus.",
        "Operational action requires independent imagery, logs, inspections and sampling evidence.",
    ]
    limitation_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in limitations)
    map_uri = _image_uri(case_root / "ranking" / "attribution_map.png")
    drift_uri = _image_uri(case_root / "drift" / "slick_reverse_analysis.png")
    quality_uri = _image_uri(case_root / "ais" / "ais_quality.png")
    sensitivity_uri = _image_uri(case_root / "sensitivity" / "sensitivity_rank_matrix.png")
    sensitivity_envelope_uri = _image_uri(case_root / "sensitivity" / "sensitivity_origin_envelope.png")
    incident = alignment.get("incident_window", {})
    origin = estimate.get("estimated_origin", {})
    historical_section = ""
    if historical:
        target = historical.get("target", {})
        sources = historical.get("sources", {})
        source_links = " · ".join(
            f"<a href='{html.escape(str(url), quote=True)}'>{html.escape(str(name).replace('_', ' ').title())}</a>"
            for name, url in sources.items()
            if str(url).startswith(("https://", "http://"))
        )
        historical_section = f"""<section class='proof-grid'><article class='panel proof-card'><div class='eyebrow'>Sealed known-source challenge</div><div class='proof-answer'>{html.escape(str(historical.get('answer', 'Result unavailable')))}</div><p>Identity: <b>{html.escape(str(target.get('vessel_name', 'Unknown')))}</b> · IMO {html.escape(str(target.get('imo', 'N/A')))}</p><div class='proof-kpis'><span><small>Rank</small><b>#{html.escape(str(historical.get('rank', '—')))} / {html.escape(str(historical.get('candidate_count', '—')))}</b></span><span><small>Comparative score</small><b>{_percent(historical.get('comparative_score'))}</b></span><span><small>Forward error</small><b>{_number(historical.get('forward_error_km'), ' km')}</b></span></div><p class='disclosure'>{html.escape(str(historical.get('disclosure', '')))}</p><p class='sources'>{source_links}</p></article>"""
        if sensitivity:
            visuals = (f"<img src='{sensitivity_uri}' alt='Physics sensitivity rank matrix'>" if sensitivity_uri else "") + (f"<img src='{sensitivity_envelope_uri}' alt='Assumption-aware origin envelope'>" if sensitivity_envelope_uri else "")
            historical_section += f"""<article class='panel proof-card'><div class='eyebrow'>Physics sensitivity audit</div><div class='proof-answer'>{html.escape(str(sensitivity.get('verdict', 'NOT RUN')))}</div><p>{html.escape(str(sensitivity.get('interpretation', '')))}</p><div class='proof-kpis'><span><small>Top-1 stability</small><b>{_percent(sensitivity.get('top_1_rate'))}</b></span><span><small>Top-3 retention</small><b>{_percent(sensitivity.get('top_3_rate'))}</b></span><span><small>Worst rank</small><b>#{html.escape(str(sensitivity.get('worst_rank', '—')))}</b></span></div>{visuals}<p class='disclosure'>One-case robustness, not population accuracy. Assumptions varied: spill age, current magnitude and windage.</p></article>"""
        historical_section += "</section>"
    integrity_rows = "".join(
        f"<tr><td>{html.escape(name.replace('_', ' ').title())}</td><td class='hash'>{html.escape(item['sha256'])}</td></tr>"
        for name, item in integrity.items()
    )

    dossier_html = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>ESPADA Evidence Dossier</title><style>
:root{{--bg:#020a0c;--panel:#082128;--line:rgba(157,221,214,.18);--text:#ecfffb;--muted:#8fa9a6;--mint:#5eead4;--amber:#fbbf24;--green:#4ade80;--red:#fb7185}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,rgba(29,188,169,.2),transparent 29%),var(--bg);color:var(--text);font-family:Inter,'Segoe UI',Arial,sans-serif}}main{{width:min(1220px,calc(100% - 30px));margin:auto;padding:28px 0 55px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:center;padding-bottom:19px;border-bottom:1px solid var(--line)}}.brand{{display:flex;align-items:center;gap:12px}}.mark{{display:grid;place-items:center;width:43px;height:43px;border-radius:12px;background:var(--mint);color:#03211d;font-weight:950;font-size:22px}}h1{{margin:0;font:500 22px Georgia,serif}}header p,.muted{{margin:4px 0 0;color:var(--muted);font-size:10px}}.status{{padding:8px 11px;border:1px solid rgba(74,222,128,.3);border-radius:999px;color:var(--green);font-size:9px;font-weight:900;letter-spacing:.1em}}.hero,.proof-grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin:18px 0}}.proof-grid{{grid-template-columns:1fr 1fr;margin:14px 0}}.panel{{border:1px solid var(--line);border-radius:14px;background:linear-gradient(150deg,rgba(9,39,45,.96),rgba(4,20,24,.97));overflow:hidden}}.summary{{padding:22px}}.eyebrow{{color:var(--mint);font-size:9px;font-weight:900;letter-spacing:.17em;text-transform:uppercase}}.summary h2{{margin:12px 0 8px;font:500 39px/1.02 Georgia,serif}}.summary h2 span{{color:var(--mint)}}.summary p,.proof-card p{{max-width:690px;color:#adc3c0;font-size:12px;line-height:1.6}}.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:17px}}.kpi{{padding:12px;border:1px solid var(--line);border-radius:9px;background:rgba(255,255,255,.02)}}.kpi small,.proof-kpis small{{display:block;color:var(--muted);font-size:7px;text-transform:uppercase;letter-spacing:.12em}}.kpi b{{display:block;margin-top:6px;font:500 21px Georgia,serif}}.top{{padding:22px;display:flex;flex-direction:column;justify-content:center}}.top .rank-big{{color:var(--amber);font-size:10px;font-weight:900;letter-spacing:.15em}}.top h2{{margin:7px 0 2px;font:500 30px Georgia,serif}}.top .score-big{{margin-top:15px;color:var(--mint);font:500 42px Georgia,serif}}.proof-card{{padding:20px}}.proof-answer{{margin:9px 0;font:500 26px Georgia,serif;color:var(--amber)}}.proof-kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}}.proof-kpis span{{padding:10px;border:1px solid var(--line);border-radius:9px}}.proof-kpis b{{display:block;margin-top:5px;color:var(--mint)}}.disclosure{{padding:10px;border-left:3px solid var(--amber);background:rgba(251,191,36,.05)}}.sources a{{color:var(--mint)}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}.head{{padding:14px 16px 10px}}.head h3{{margin:0;font-size:13px}}.head p{{margin:4px 0 0;color:var(--muted);font-size:9px}}img{{display:block;width:100%;padding:0 12px 12px;filter:saturate(.88) brightness(.9)}}.checks{{display:grid;gap:7px;padding:0 14px 14px}}.check{{display:grid;grid-template-columns:9px 1fr auto;gap:9px;align-items:center;padding:10px;border:1px solid var(--line);border-radius:9px;font-size:9px}}.check i{{width:7px;height:7px;border-radius:50%}}.check.pass i{{background:var(--green)}}.check.fail i{{background:var(--red)}}.check b{{color:var(--green);font-size:8px}}.check.fail b{{color:var(--red)}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px 13px;border-top:1px solid rgba(157,221,214,.11);text-align:left;font-size:9px}}th{{color:var(--muted);font-size:7px;text-transform:uppercase;letter-spacing:.1em}}td small{{display:block;margin-top:3px;color:var(--muted)}}.hash{{font-family:ui-monospace,Consolas,monospace;overflow-wrap:anywhere;color:var(--muted)}}.rank{{display:grid;place-items:center;width:22px;height:22px;border-radius:6px;background:#14363d;font-weight:900}}tr:first-child .rank{{background:var(--amber);color:#241500}}.score{{color:var(--mint);font-weight:900}}.limits{{padding:0 17px 15px;color:#d6c893;font-size:9px;line-height:1.55}}.footer{{margin-top:14px;padding:13px 15px;border-left:3px solid var(--amber);background:rgba(251,191,36,.055);color:#d9cca0;font-size:10px;line-height:1.5}}@media(max-width:800px){{.hero,.grid,.proof-grid{{grid-template-columns:1fr}}.kpis{{grid-template-columns:1fr 1fr}}header{{align-items:flex-start}}}}
</style></head><body><main><header><div class='brand'><div class='mark'>E</div><div><h1>ESPADA Evidence Dossier</h1><p>Reverse Drift Attribution · generated {created}</p></div></div><div class='status'>{html.escape(str(bundle['case_status']))}</div></header>
<section class='hero'><article class='panel summary'><div class='eyebrow'>Probabilistic origin reconstruction</div><h2>From observed slick to <span>reviewable shortlist.</span></h2><p>ESPADA propagates an analyst-approved slick backward through environmental forcing, compares candidate vessel presence, and forward-replays each candidate before ranking.</p><div class='kpis'><div class='kpi'><small>Estimated release</small><b>{html.escape(str(incident.get('estimated_release_time_utc', estimate.get('release_time_utc', 'N/A'))))}</b></div><div class='kpi'><small>Origin longitude</small><b>{_number(origin.get('longitude'))}</b></div><div class='kpi'><small>Origin latitude</small><b>{_number(origin.get('latitude'))}</b></div><div class='kpi'><small>90% radius</small><b>{_number(estimate.get('credible_radius_90_km'), ' km')}</b></div><div class='kpi'><small>AIS positions</small><b>{html.escape(str(ais.get('valid_rows', 'N/A')))}</b></div><div class='kpi'><small>Candidate vessels</small><b>{html.escape(str(ranking.get('candidate_count', len(candidates))))}</b></div></div></article><aside class='panel top'><div class='rank-big'>TOP COMPARATIVE CANDIDATE</div><h2>{html.escape(str(top.get('vessel_name', 'No candidate')))}</h2><div class='muted'>MMSI {html.escape(str(top.get('mmsi', 'N/A')))}</div><div class='score-big'>{_percent(top.get('total_score'))}</div><div class='muted'>Forward replay error: {_number(top.get('forward_error_km'), ' km')}</div></aside></section>
{historical_section}
<section class='grid'><article class='panel'><div class='head'><h3>Case alignment gate</h3><p>Processing stops if satellite, forcing and AIS do not cover one incident window.</p></div><div class='checks'>{check_cards or '<div class="muted">No alignment report found.</div>'}</div></article><article class='panel'><div class='head'><h3>Attribution map</h3><p>Reverse origin probability and reconstructed vessel tracks.</p></div>{f"<img src='{map_uri}' alt='Attribution map'>" if map_uri else ''}</article></section>
<section class='grid'><article class='panel'><div class='head'><h3>Reverse-drift analysis</h3><p>Uncertainty ensemble, not a single deterministic release point.</p></div>{f"<img src='{drift_uri}' alt='Reverse drift analysis'>" if drift_uri else ''}</article><article class='panel'><div class='head'><h3>AIS data-quality audit</h3><p>Coverage gaps and implausible jumps are recorded before ranking.</p></div>{f"<img src='{quality_uri}' alt='AIS quality report'>" if quality_uri else ''}</article></section>
<article class='panel' style='margin-top:14px'><div class='head'><h3>Coverage-aware AIS silence test</h3><p>Nearby peer reception distinguishes a broad coverage uncertainty from a vessel-specific data gap.</p></div><div class='kpis' style='padding:0 14px 14px;margin-top:4px'><div class='kpi'><small>Vessels analysed</small><b>{html.escape(str(silence.get('vessels_analyzed', 'N/A')))}</b></div><div class='kpi'><small>Significant gaps</small><b>{html.escape(str(silence.get('vessels_with_significant_gaps', 'N/A')))}</b></div><div class='kpi'><small>Peer-supported gaps</small><b>{html.escape(str(silence.get('vessels_with_peer_supported_gaps', 'N/A')))}</b></div></div><div class='limits'>{html.escape(str(silence.get('safety_rule', 'Silence classification never increases the attribution score.')))}</div></article>
<article class='panel' style='margin-top:14px'><div class='head'><h3>Candidate evidence ledger</h3><p>Scores are comparative inside this incident—not probabilities of guilt.</p></div><div style='overflow:auto'><table><thead><tr><th>Rank</th><th>Vessel</th><th>Total</th><th>Presence</th><th>Forward error</th><th>Data quality</th><th>AIS silence</th></tr></thead><tbody>{rows}</tbody></table></div></article>
<article class='panel' style='margin-top:14px'><div class='head'><h3>Evidence integrity register</h3><p>SHA-256 fingerprints make later file changes detectable.</p></div><div style='overflow:auto'><table><thead><tr><th>Artifact</th><th>SHA-256</th></tr></thead><tbody>{integrity_rows}</tbody></table></div></article>
<article class='panel' style='margin-top:14px'><div class='head'><h3>Mandatory limitations</h3><p>These cautions travel with every exported case.</p></div><ul class='limits'>{limitation_items}</ul></article><div class='footer'>HUMAN REVIEW GATE · This ranking is not a finding of discharge, intent, identity or guilt. ESPADA supplies an auditable investigative shortlist and does not autonomously accuse a vessel.</div></main></body></html>"""
    dossier_path = output_dir / "evidence_dossier.html"
    dossier_path.write_text(dossier_html, encoding="utf-8")
    return {
        "status": "PASS",
        "case_status": bundle["case_status"],
        "dossier": str(dossier_path.resolve()),
        "evidence_bundle": str(bundle_path.resolve()),
        "candidate_count": ranking.get("candidate_count", len(candidates)),
        "top_candidate": top,
        "historical_result": historical.get("answer") if historical else None,
        "sensitivity_verdict": sensitivity.get("verdict") if sensitivity else None,
    }
