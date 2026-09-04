from __future__ import annotations

import base64
import json
from pathlib import Path


def _data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def generate_dashboard(output_dir: Path) -> Path:
    """Generate a completely self-contained offline investigation dashboard."""
    output_dir = Path(output_dir)
    candidates = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    verification = json.loads(
        (output_dir / "physics" / "verification_result.json").read_text(encoding="utf-8")
    )
    estimate = json.loads(
        (output_dir / "physics" / "release_estimate.json").read_text(encoding="utf-8")
    )
    environment = json.loads(
        (output_dir / "environment" / "environment_status.json").read_text(encoding="utf-8")
    )
    payload = {
        "candidates": candidates["candidates"],
        "topCandidate": candidates["top_candidate"],
        "metrics": verification["metrics"],
        "estimate": estimate,
        "environment": environment,
    }
    data_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    attribution_map = _data_uri(output_dir / "attribution_map.png")
    ranking_chart = _data_uri(output_dir / "candidate_ranking.png")
    environment_chart = _data_uri(output_dir / "environment" / "environment_timeseries.png")
    evaluation_plot_path = output_dir.parent / "evaluation" / "evaluation_overview.png"
    evaluation_summary_path = output_dir.parent / "evaluation" / "evaluation_summary.json"
    evaluation_section = ""
    if evaluation_plot_path.exists() and evaluation_summary_path.exists():
        evaluation_image = _data_uri(evaluation_plot_path)
        evaluation_summary = json.loads(evaluation_summary_path.read_text(encoding="utf-8"))
        evaluation_metrics = evaluation_summary["overall"]
        evaluation_section = f"""
    <section class="panel chart" id="evaluation" style="margin-bottom:14px">
      <div class="panel-head"><div><h2>Synthetic robustness evaluation</h2><p>{evaluation_summary['config']['cases']} hidden-truth cases · Top-1 {evaluation_metrics['top1_accuracy']*100:.1f}% · Top-3 {evaluation_metrics['top3_accuracy']*100:.1f}% · Median origin error {evaluation_metrics['median_origin_error_km']:.2f} km</p></div></div>
      <img src="{evaluation_image}" alt="Synthetic evaluation robustness graphs">
    </section>"""
    slick_section = ""
    slick_plot_path = output_dir.parent / "slick" / "slick_reverse_analysis.png"
    slick_summary_path = output_dir.parent / "slick" / "slick_analysis.json"
    if slick_plot_path.exists() and slick_summary_path.exists():
        slick_image = _data_uri(slick_plot_path)
        slick_summary = json.loads(slick_summary_path.read_text(encoding="utf-8"))
        slick_section = f"""
    <section class="panel chart" id="slick" style="margin-bottom:14px">
      <div class="panel-head"><div><h2>Slick polygon → probable release zone</h2><p>Validated GeoJSON · {slick_summary['input']['area_km2']:.2f} km² · {slick_summary['environment']['forcing_steps']} real-data forcing steps · 90% radius {slick_summary['credible_radius_90_km']:.2f} km</p></div></div>
      <img src="{slick_image}" alt="Observed slick polygon and backward origin probability">
    </section>"""
    sar_section = ""
    sar_plot_path = output_dir.parent / "sar" / "sar_segmentation_overview.png"
    sar_result_path = output_dir.parent / "sar" / "sar_result.json"
    if sar_plot_path.exists() and sar_result_path.exists():
        sar_image = _data_uri(sar_plot_path)
        sar_result = json.loads(sar_result_path.read_text(encoding="utf-8"))
        sar_metrics = sar_result.get("synthetic_evaluation")
        metric_copy = (
            f"Synthetic holdout · IoU {sar_metrics['iou']*100:.1f}% · Dice {sar_metrics['dice']*100:.1f}% · Precision {sar_metrics['precision']*100:.1f}%"
            if sar_metrics
            else "Analyst review required · no ground-truth mask supplied"
        )
        sar_section = f"""
    <section class="panel chart" id="sar" style="margin-bottom:14px">
      <div class="panel-head"><div><h2>Sentinel-1 slick segmentation baseline</h2><p>{metric_copy} · classical dark-anomaly fallback, not the planned U-Net</p></div></div>
      <img src="{sar_image}" alt="SAR preprocessing, anomaly score, and candidate slick boundary">
    </section>"""
    ais_quality_section = ""
    ais_quality_plot = output_dir / "ais" / "ais_quality.png"
    ais_quality_report = output_dir / "ais" / "ais_quality.json"
    if ais_quality_plot.exists() and ais_quality_report.exists():
        ais_image = _data_uri(ais_quality_plot)
        ais_quality = json.loads(ais_quality_report.read_text(encoding="utf-8"))
        ais_quality_section = f"""
    <section class="panel chart" id="ais-quality" style="margin-bottom:14px">
      <div class="panel-head"><div><h2>AIS ingestion quality</h2><p>{ais_quality['valid_rows']} validated positions · {ais_quality['vessel_count']} vessels · {ais_quality.get('vessels_with_gaps_over_threshold', ais_quality.get('vessels_with_gaps_over_30_minutes', 0))} vessel with a cadence-adjusted gap · {ais_quality['suspicious_jumps_over_60_knots']} suspicious jumps</p></div></div>
      <img src="{ais_image}" alt="Validated AIS position coverage by vessel">
    </section>"""
    top = candidates["top_candidate"]
    metrics = verification["metrics"]
    estimate_origin = estimate["estimated_origin"]
    real_environment = environment["active_mode"] in {"live", "cache"}
    mode_label = (
        f"REAL ENVIRONMENT · {environment['active_mode'].upper()}"
        if real_environment
        else "OFFLINE SYNTHETIC VALIDATION"
    )
    validation_label = (
        "Real model currents and wind drive a synthetic spill/AIS test. Rankings do not establish guilt."
        if real_environment
        else "Synthetic inputs are used. Rankings support investigation and do not establish guilt."
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Espada offline reverse-drift attribution dashboard">
  <title>Espada | Reverse Drift Attribution</title>
  <style>
    :root {{
      --ink:#0b1f2a; --muted:#60717b; --line:#d9e3e7; --paper:#f3f6f5;
      --white:#fff; --navy:#082832; --teal:#087f7b; --aqua:#42c8b7;
      --orange:#f49a24; --red:#d35a4a; --shadow:0 12px 35px rgba(8,40,50,.10);
    }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--paper); font-family:Inter,Segoe UI,Arial,sans-serif; }}
    button,input {{ font:inherit; }}
    .shell {{ min-height:100vh; display:grid; grid-template-columns:235px 1fr; }}
    aside {{ position:sticky; top:0; height:100vh; color:#e9ffff; background:var(--navy); padding:28px 20px; display:flex; flex-direction:column; }}
    .brand {{ display:flex; align-items:center; gap:12px; margin-bottom:38px; }}
    .mark {{ width:38px; height:38px; border:1px solid rgba(255,255,255,.3); border-radius:11px; display:grid; place-items:center; color:var(--orange); font-weight:900; font-size:20px; }}
    .brand strong {{ letter-spacing:.14em; font-size:16px; }}
    .brand small {{ display:block; color:#8fb3ba; margin-top:3px; letter-spacing:.04em; }}
    nav a {{ display:block; text-decoration:none; color:#99b8be; padding:11px 12px; border-radius:9px; margin-bottom:5px; font-size:14px; }}
    nav a:hover,nav a.active {{ color:white; background:rgba(66,200,183,.12); }}
    .side-status {{ margin-top:auto; border-top:1px solid rgba(255,255,255,.12); padding-top:18px; color:#a8c4c9; font-size:12px; line-height:1.6; }}
    .pulse {{ width:8px; height:8px; border-radius:99px; display:inline-block; margin-right:7px; background:var(--aqua); box-shadow:0 0 0 4px rgba(66,200,183,.12); }}
    main {{ padding:28px 34px 48px; min-width:0; }}
    .topbar {{ display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:18px; }}
    .eyebrow {{ color:var(--teal); font-size:12px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }}
    h1 {{ margin:7px 0 4px; font-family:Georgia,serif; font-size:34px; font-weight:600; letter-spacing:-.025em; }}
    .subtitle {{ color:var(--muted); font-size:14px; }}
    .mode {{ padding:9px 13px; border:1px solid #b8d3d0; color:#176a68; background:#e8f7f4; border-radius:99px; font-size:12px; font-weight:800; letter-spacing:.05em; white-space:nowrap; }}
    .warning {{ display:flex; gap:11px; align-items:center; margin:18px 0; padding:12px 15px; border-left:4px solid var(--orange); background:#fff4e5; color:#71420b; font-size:13px; border-radius:5px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:14px; }}
    .kpi {{ background:var(--white); border:1px solid var(--line); padding:15px 17px; border-radius:12px; }}
    .kpi span {{ display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.09em; margin-bottom:8px; }}
    .kpi strong {{ font-family:Georgia,serif; font-size:25px; font-weight:600; }}
    .kpi em {{ margin-left:5px; color:var(--muted); font-style:normal; font-size:11px; }}
    .grid {{ display:grid; grid-template-columns:minmax(0,1.75fr) minmax(280px,.75fr); gap:14px; margin-bottom:14px; }}
    .panel {{ background:var(--white); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow); overflow:hidden; }}
    .panel-head {{ padding:16px 18px 11px; display:flex; align-items:flex-start; justify-content:space-between; gap:15px; }}
    .panel h2 {{ margin:0 0 4px; font-size:15px; }}
    .panel p {{ margin:0; color:var(--muted); font-size:12px; line-height:1.45; }}
    .map-wrap {{ position:relative; padding:0 14px 14px; }}
    .map-wrap img {{ width:100%; display:block; border-radius:10px; border:1px solid #e1e8e9; }}
    .legend {{ position:absolute; left:28px; bottom:28px; padding:8px 10px; border-radius:7px; background:rgba(255,255,255,.92); font-size:11px; box-shadow:0 4px 16px rgba(0,0,0,.1); }}
    .legend i {{ display:inline-block; width:18px; height:3px; background:var(--orange); vertical-align:middle; margin-right:5px; }}
    .candidate {{ padding:10px 18px 20px; }}
    .rank-label {{ color:var(--orange); text-transform:uppercase; letter-spacing:.12em; font-weight:900; font-size:11px; }}
    .candidate h3 {{ margin:7px 0 3px; font-family:Georgia,serif; font-size:25px; font-weight:600; }}
    .mmsi {{ color:var(--muted); font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; }}
    .score-row {{ display:flex; align-items:center; gap:18px; margin:22px 0; }}
    .ring {{ --score:{top['total_score'] * 100:.1f}; width:92px; height:92px; flex:0 0 auto; display:grid; place-items:center; border-radius:50%; background:conic-gradient(var(--teal) calc(var(--score)*1%),#dfe9e9 0); position:relative; }}
    .ring:after {{ content:""; position:absolute; inset:9px; border-radius:50%; background:white; }}
    .ring strong {{ position:relative; z-index:1; font-size:21px; }}
    .score-copy strong {{ display:block; font-size:13px; }}
    .score-copy span {{ display:block; margin-top:5px; color:var(--muted); font-size:11px; line-height:1.5; }}
    .meter {{ margin-top:13px; }}
    .meter label {{ display:flex; justify-content:space-between; margin-bottom:5px; color:#425861; font-size:11px; }}
    .track {{ height:6px; border-radius:6px; background:#e4ecec; overflow:hidden; }}
    .fill {{ height:100%; background:var(--teal); border-radius:6px; }}
    .mini-note {{ margin-top:18px; padding:11px 12px; color:#5c4b2a; background:#fbf6e9; border-radius:8px; font-size:11px; line-height:1.5; }}
    .lower {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }}
    .chart img {{ width:100%; display:block; padding:0 10px 12px; }}
    .steps {{ padding:6px 18px 19px; }}
    .step {{ position:relative; padding:0 0 18px 31px; color:var(--muted); font-size:12px; line-height:1.45; }}
    .step:not(:last-child):before {{ content:""; position:absolute; left:8px; top:17px; bottom:0; width:1px; background:#cadada; }}
    .dot {{ position:absolute; left:0; top:1px; width:17px; height:17px; border-radius:50%; background:#e4f4f1; border:5px solid var(--teal); }}
    .step strong {{ display:block; color:var(--ink); margin-bottom:2px; font-size:12px; }}
    .table-tools {{ display:flex; gap:8px; align-items:center; }}
    .search {{ width:210px; padding:8px 11px; border:1px solid var(--line); border-radius:8px; outline:none; color:var(--ink); background:#fbfcfc; font-size:12px; }}
    .search:focus {{ border-color:var(--teal); box-shadow:0 0 0 3px rgba(8,127,123,.1); }}
    .table-wrap {{ overflow:auto; }}
    table {{ border-collapse:collapse; width:100%; font-size:12px; }}
    th {{ text-align:left; color:var(--muted); padding:10px 18px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); font-size:10px; letter-spacing:.08em; text-transform:uppercase; }}
    td {{ padding:12px 18px; border-bottom:1px solid #edf1f2; white-space:nowrap; }}
    tbody tr {{ cursor:pointer; }}
    tbody tr:hover {{ background:#f3faf8; }}
    .badge {{ padding:4px 8px; border-radius:99px; color:#116763; background:#e5f5f2; font-weight:800; }}
    .rank {{ width:25px; height:25px; border-radius:7px; display:grid; place-items:center; background:#edf3f3; font-weight:800; }}
    .rank.one {{ color:white; background:var(--orange); }}
    .method {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; padding:17px 18px; }}
    .method h3 {{ margin:0 0 6px; font-size:12px; }}
    .method p {{ font-size:11px; }}
    footer {{ margin-top:18px; color:#71838b; font-size:10px; text-align:center; }}
    @media(max-width:1000px) {{ .shell{{grid-template-columns:1fr}} aside{{display:none}} main{{padding:22px}} .grid,.lower{{grid-template-columns:1fr}} }}
    @media(max-width:650px) {{ main{{padding:14px}} .topbar{{display:block}} .mode{{display:inline-block;margin-top:12px}} .kpis{{grid-template-columns:1fr 1fr}} h1{{font-size:28px}} .panel-head{{display:block}} .table-tools{{margin-top:10px}} .search{{width:100%}} .method{{grid-template-columns:1fr}} }}
  </style>
</head>
<body>
<div class="shell">
  <aside>
    <div class="brand"><div class="mark">E</div><div><strong>ESPADA</strong><small>Team Espada</small></div></div>
    <nav>
      <a class="active" href="#overview">Investigation</a>
      <a href="#ranking">Candidate ranking</a>
      <a href="#slick">Slick analysis</a>
      <a href="#sar">SAR segmentation</a>
      <a href="#ais-quality">AIS quality</a>
      <a href="#evaluation">Evaluation</a>
      <a href="#method">Method & limits</a>
    </nav>
    <div class="side-status"><span class="pulse"></span>Analysis complete<br>{environment['active_mode'].title()} environmental input<br>Run ID: SIH26143-001</div>
  </aside>
  <main id="overview">
    <div class="topbar">
      <div><div class="eyebrow">Investigation workspace · SIH26143</div><h1>Reverse Drift Attribution</h1><div class="subtitle">From observed slick to probable release zone and ranked vessel tracks</div></div>
      <div class="mode">{mode_label}</div>
    </div>
    <div class="warning"><strong>Validation:</strong> {validation_label}</div>
    <section class="kpis">
      <div class="kpi"><span>Top candidate score</span><strong>{top['total_score'] * 100:.1f}%</strong></div>
      <div class="kpi"><span>Vessels assessed</span><strong>{candidates['candidate_count']}</strong><em>tracks</em></div>
      <div class="kpi"><span>Origin error</span><strong>{metrics['origin_error_km']:.2f}</strong><em>km</em></div>
      <div class="kpi"><span>90% uncertainty</span><strong>{metrics['credible_radius_90_km']:.2f}</strong><em>km</em></div>
    </section>
    <section class="grid">
      <article class="panel">
        <div class="panel-head"><div><h2>Probable origin and vessel tracks</h2><p>Backward ensemble density over six hours of AIS movement</p></div><p>{estimate_origin['latitude']:.4f}° N · {estimate_origin['longitude']:.4f}° E</p></div>
        <div class="map-wrap"><img src="{attribution_map}" alt="Inferred origin probability with AIS vessel tracks"><div class="legend"><i></i> Highest-ranked track</div></div>
      </article>
      <article class="panel">
        <div class="panel-head"><div><h2>Lead candidate</h2><p>Evidence-weighted result</p></div></div>
        <div class="candidate">
          <div class="rank-label">Rank 01 of {candidates['candidate_count']}</div><h3>{top['vessel_name']}</h3><div class="mmsi">MMSI {top['mmsi']}</div>
          <div class="score-row"><div class="ring"><strong>{top['total_score'] * 100:.0f}%</strong></div><div class="score-copy"><strong>High consistency</strong><span>Closest joint match across location, time and forward drift.</span></div></div>
          <div class="meter"><label><span>Space-time presence</span><b>{top['presence_score'] * 100:.1f}%</b></label><div class="track"><div class="fill" style="width:{top['presence_score'] * 100:.1f}%"></div></div></div>
          <div class="meter"><label><span>Forward consistency</span><b>{top['forward_consistency'] * 100:.1f}%</b></label><div class="track"><div class="fill" style="width:{top['forward_consistency'] * 100:.1f}%"></div></div></div>
          <div class="meter"><label><span>Data quality</span><b>{top['data_quality'] * 100:.0f}%</b></label><div class="track"><div class="fill" style="width:{top['data_quality'] * 100:.0f}%"></div></div></div>
          <div class="mini-note">Best match: {top['best_match_time_utc']}<br>Forward centroid error: {top['forward_error_km']:.2f} km</div>
        </div>
      </article>
    </section>
    <section class="lower">
      <article class="panel chart"><div class="panel-head"><div><h2>Candidate separation</h2><p>Weighted comparison of the five strongest vessel tracks</p></div></div><img src="{ranking_chart}" alt="Top five vessel candidate scores"></article>
      <article class="panel"><div class="panel-head"><div><h2>Evidence chain</h2><p>Auditable path from observation to ranking</p></div></div><div class="steps">
        <div class="step"><span class="dot"></span><strong>1 · Observe slick</strong>2,000 synthetic particles at {estimate['observation_time_utc']}</div>
        <div class="step"><span class="dot"></span><strong>2 · Reverse drift</strong>20 uncertainty members use {environment['source']}.</div>
        <div class="step"><span class="dot"></span><strong>3 · Correlate AIS</strong>14 tracks checked near the inferred release time and location.</div>
        <div class="step"><span class="dot"></span><strong>4 · Forward verify</strong>Each candidate position is advected toward the observed slick.</div>
        <div class="step"><span class="dot"></span><strong>5 · Rank with limits</strong>Evidence and data quality are reported without assigning guilt.</div>
      </div></article>
    </section>
    <section class="panel chart" style="margin-bottom:14px">
      <div class="panel-head"><div><h2>Environmental forcing</h2><p>{environment['source']} · {environment['sample_count']} samples · {environment.get('temporal_resolution', 'hourly')} · {environment['active_mode'].upper()} mode</p></div></div>
      <img src="{environment_chart}" alt="Ocean current and wind forcing time series">
    </section>
    {slick_section}
    {sar_section}
    {ais_quality_section}
    {evaluation_section}
    <section class="panel" id="ranking">
      <div class="panel-head"><div><h2>All candidate tracks</h2><p>Click a row to view its evidence summary</p></div><div class="table-tools"><input class="search" id="search" type="search" placeholder="Search vessel or MMSI" aria-label="Search vessel candidates"></div></div>
      <div class="table-wrap"><table><thead><tr><th>Rank</th><th>Vessel</th><th>MMSI</th><th>Total</th><th>Presence</th><th>Forward error</th><th>Data quality</th></tr></thead><tbody id="candidateRows"></tbody></table></div>
      <div class="method" id="selectedEvidence"><div><h3>Evidence summary</h3><p>Select any candidate row.</p></div><div><h3>Interpretation</h3><p>Scores compare tracks inside this case only; they are not legal probabilities.</p></div></div>
    </section>
    <section class="panel method" id="method" style="margin-top:14px">
      <div><h3>Method</h3><p>Time-varying current and wind advection-diffusion, backward ensemble uncertainty, space-time AIS proximity, and deterministic forward verification.</p></div>
      <div><h3>Current limits</h3><p>Point release, fixed spill age and synthetic AIS. Forcing varies in time but is sampled at one analysis location. Sentinel-1 segmentation and live AIS remain next.</p></div>
    </section>
    <footer>ESPADA · Investigation-support prototype · Team Espada · SIH26143</footer>
  </main>
</div>
<script>
  const DATA={data_json};
  const rows=document.getElementById('candidateRows');
  const evidence=document.getElementById('selectedEvidence');
  function pct(v){{return (v*100).toFixed(1)+'%'}}
  function render(filter=''){{
    const q=filter.trim().toLowerCase();
    const visible=DATA.candidates.filter(c=>c.vessel_name.toLowerCase().includes(q)||c.mmsi.includes(q));
    rows.innerHTML=visible.map(c=>`<tr data-rank="${{c.rank}}"><td><span class="rank ${{c.rank===1?'one':''}}">${{c.rank}}</span></td><td><strong>${{c.vessel_name}}</strong></td><td class="mmsi">${{c.mmsi}}</td><td><span class="badge">${{pct(c.total_score)}}</span></td><td>${{pct(c.presence_score)}}</td><td>${{c.forward_error_km.toFixed(2)}} km</td><td>${{pct(c.data_quality)}}</td></tr>`).join('');
    rows.querySelectorAll('tr').forEach(row=>row.addEventListener('click',()=>select(Number(row.dataset.rank))));
  }}
  function select(rank){{
    const c=DATA.candidates.find(item=>item.rank===rank);
    evidence.innerHTML=`<div><h3>${{c.vessel_name}} · evidence</h3><p>${{c.evidence.join(' ')}} Best match: ${{c.best_match_time_utc}}.</p></div><div><h3>Limits</h3><p>${{c.limitations.join(' ')}}</p></div>`;
  }}
  document.getElementById('search').addEventListener('input',e=>render(e.target.value));
  render(); select(1);
</script>
</body>
</html>"""
    dashboard_path = output_dir / "dashboard.html"
    dashboard_path.write_text(html, encoding="utf-8")
    return dashboard_path
