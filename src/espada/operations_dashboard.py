from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required operations artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _display_name(mmsi: str, original: str, rank: int | None) -> str:
    if str(original).lower().startswith("blinded synthetic"):
        return "Synthetic source α"
    if str(original).lower().startswith("blinded"):
        return f"Vessel {str(mmsi)[-4:]}"
    if original and original.upper() != "UNKNOWN":
        return original
    return f"Vessel {str(mmsi)[-4:]}" if rank else f"Traffic {str(mmsi)[-4:]}"


def build_operations_dashboard(project_root: Path, output_path: Path) -> dict[str, object]:
    root = Path(project_root)
    case = root / "out/external_validation/corsica_2018"
    challenge = root / "out/challenge"
    use_challenge = all(
        path.exists()
        for path in (
            challenge / "challenge_result.json",
            challenge / "synthetic_slick.geojson",
            challenge / "ranking/candidates.json",
            challenge / "ais/ais_normalized.csv",
        )
    )
    challenge_result = _read_json(challenge / "challenge_result.json") if use_challenge else {}
    scenario = _read_json(challenge / "scenario_used.json") if use_challenge else {}
    run = challenge if use_challenge else case / "counterfactual_ais/run"
    ranking = _read_json(run / "ranking/candidates.json")
    decision = _read_json(run / "decision/decision_gate.json")
    estimate = _read_json(run / "drift/release_estimate.json")
    # The counterfactual replay deliberately swaps only AIS. Its reviewed slick
    # remains in the parent Corsica run, so accept either layout.
    slick_path = run / "synthetic_slick.geojson" if use_challenge else run / "sar/slick_observation.geojson"
    if not slick_path.exists():
        slick_path = case / "run/sar/slick_observation.geojson"
    slick = _read_json(slick_path)
    environment = _read_json(case / "environment/environment.json")
    satellite = _read_json(case / "sar_input/sentinel1_subset_status.json")
    twin = _read_json(root / "out/digital_twin/digital_twin_summary.json")
    ais = pd.read_csv(run / "ais/ais_normalized.csv", dtype={"mmsi": str})
    candidate_lookup = {str(item["mmsi"]): item for item in ranking["candidates"]}
    known_source_id = str(challenge_result.get("truth_reveal", {}).get("source_id", ""))
    tracks = []
    for mmsi, frame in ais.groupby("mmsi", sort=False):
        candidate = candidate_lookup.get(str(mmsi), {})
        rank = int(candidate["rank"]) if candidate.get("rank") else None
        original_name = str(frame["vessel_name"].iloc[0])
        tracks.append(
            {
                "mmsi": str(mmsi),
                "name": _display_name(str(mmsi), original_name, rank),
                "rank": rank,
                "score": candidate.get("total_score"),
                "presence": candidate.get("presence_score"),
                "forward": candidate.get("forward_consistency"),
                "forwardError": candidate.get("forward_error_km"),
                "shapeError": candidate.get("forward_shape_error_km"),
                "quality": candidate.get("data_quality"),
                "bestTime": candidate.get("best_match_time_utc"),
                "silence": candidate.get("silence_classification"),
                "interpolated": candidate.get("release_position_interpolated", False),
                "synthetic": original_name.lower().startswith("blinded synthetic"),
                "knownSource": bool(known_source_id and str(mmsi) == known_source_id),
                "points": [
                    {
                        "t": row.timestamp_utc,
                        "lon": float(row.longitude),
                        "lat": float(row.latitude),
                    }
                    for row in frame.itertuples()
                ],
            }
        )
    with np.load(run / "drift/reverse_endpoints.npz") as bundle:
        lon = np.asarray(bundle["lon"], dtype=float)
        lat = np.asarray(bundle["lat"], dtype=float)
    sample = np.linspace(0, len(lon) - 1, min(420, len(lon)), dtype=int)
    reverse_points = [[float(lon[index]), float(lat[index])] for index in sample]
    image = base64.b64encode((case / "sar_input/sentinel1_vv_quicklook.png").read_bytes()).decode("ascii")
    top_candidates = []
    for item in ranking["candidates"][:10]:
        track = next(value for value in tracks if value["mmsi"] == str(item["mmsi"]))
        top_candidates.append(track)
    bbox = satellite["bbox"]
    payload = {
        "case": {
            "id": "corsica-2018-sealed-challenge" if use_challenge else "corsica-2018-hybrid",
            "name": "Corsica · Sealed Challenge" if use_challenge else "Corsica · 2018",
            "mode": "CONTROLLED DIGITAL TWIN" if use_challenge else "HYBRID SIMULATION",
            "observationTime": estimate["observation_time_utc"],
            "releaseTime": estimate["release_time_utc"],
            "decision": decision["decision"],
            "candidateCount": ranking["candidate_count"],
        },
        "bbox": bbox,
        "satelliteImage": f"data:image/png;base64,{image}",
        "satellite": satellite,
        "slick": slick,
        "release": {
            **estimate["estimated_origin"],
            "radius90": estimate["credible_radius_90_km"],
        },
        "reverse": reverse_points,
        "tracks": tracks,
        "candidates": top_candidates,
        "environment": environment["samples"],
        "environmentSource": environment["source"],
        "decision": decision,
        "digitalTwin": twin,
        "challenge": challenge_result,
        "scenario": scenario,
        "dossier": (
            "../challenge/dossier/evidence_dossier.html"
            if use_challenge
            else "../external_validation/corsica_2018/counterfactual_ais/run/dossier/evidence_dossier.html"
        ),
        "trafficSummary": (
            f"{len(tracks)} pseudonymized historical GFW tracks"
            if use_challenge
            else "76 real historical tracks + 1 disclosed synthetic source track"
        ),
        "slickSource": (
            "Controlled hidden-source slick generated with real environmental forcing"
            if use_challenge
            else "Analyst-reviewed counterfactual slick observation"
        ),
        "disclosure": (
            "Real Sentinel-1 context, real Copernicus/Open-Meteo forcing and pseudonymized real GFW traffic; "
            "the slick is a sealed-ground-truth controlled simulation."
            if use_challenge
            else "Real Sentinel-1 image, real Copernicus/Open-Meteo forcing and 76 real GFW background tracks; "
            "one disclosed synthetic source track tests observability."
        ),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    document = _document(data)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return {
        "status": "PASS",
        "output": str(output_path.resolve()),
        "ships": len(tracks),
        "candidate_cards": len(top_candidates),
        "mode": payload["case"]["mode"],
        "source": "sealed challenge" if use_challenge else "counterfactual replay",
    }


def _document(data: str) -> str:
    return r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESPADA Operations</title><style>
:root{--bg:#030b10;--panel:#081820;--panel2:#0c222c;--line:#173541;--ink:#ecfffb;--muted:#87a2aa;--cyan:#52e2dc;--amber:#ffbd59;--red:#ff7187;--green:#67e8a5}
*{box-sizing:border-box}body{margin:0;overflow:hidden;background:var(--bg);color:var(--ink);font:13px/1.45 Inter,Segoe UI,Arial,sans-serif}button{font:inherit;color:inherit}button:focus-visible,input:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}.app{height:100vh;display:grid;grid-template-rows:62px minmax(0,1fr) 88px;background:radial-gradient(circle at 10% 0,#103640 0,transparent 28%),var(--bg)}
header{display:flex;align-items:center;gap:18px;padding:0 18px;border-bottom:1px solid var(--line);background:#041118e8;backdrop-filter:blur(12px);z-index:20}.brand{display:flex;align-items:center;gap:10px;font-weight:900;letter-spacing:.15em}.mark{width:32px;height:32px;display:grid;place-items:center;border:1px solid #3a7777;border-radius:9px;color:var(--cyan)}.case{border-left:1px solid var(--line);padding-left:18px}.case b{display:block;font-size:14px}.case span,.source{color:var(--muted);font-size:10px}.badge{padding:6px 9px;border:1px solid #8d6d2c;border-radius:999px;color:var(--amber);font-size:9px;font-weight:900;letter-spacing:.1em}.spacer{flex:1}.server{font-size:9px;color:var(--muted)}.server.on{color:var(--green)}.primary,.soft{border-radius:9px;padding:9px 13px;cursor:pointer;font-weight:800}.primary{border:1px solid var(--cyan);background:var(--cyan);color:#031013}.soft{border:1px solid var(--line);background:#0a2028}.primary:disabled{opacity:.55;cursor:wait}
.workspace{min-height:0;display:grid;grid-template-columns:minmax(0,1.28fr) minmax(340px,.72fr);gap:10px;padding:10px}.map-card,.side{min-height:0;border:1px solid var(--line);border-radius:16px;background:var(--panel);overflow:hidden}.map-card{position:relative}.map{position:absolute;inset:0;background:#06212a}.sar{width:100%;height:100%;opacity:.68;filter:contrast(1.22) saturate(.55);transition:.3s}.map-shade{position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,29,37,.32),rgba(0,22,30,.02)),radial-gradient(circle at 55% 45%,transparent,#00101899);pointer-events:none}.overlay{position:absolute;inset:0;width:100%;height:100%}.gridline{stroke:#75d9d41c;stroke-width:1}.slick{fill:#ffbd5925;stroke:var(--amber);stroke-width:2;filter:drop-shadow(0 0 5px #ffbd59aa);opacity:0;transition:.5s}.slick.show{opacity:1}.release{fill:#52e2dc22;stroke:var(--cyan);stroke-width:2;opacity:0;transform-origin:center;filter:drop-shadow(0 0 12px #52e2dc)}.release.show{opacity:1;animation:pulse 2.2s infinite}.particle{fill:#8affee;opacity:0}.particle.show{opacity:.48}.track{fill:none;stroke:#d7ffff;stroke-width:1.2;opacity:0;stroke-dasharray:5 4}.track.show{opacity:.5}.track.top{stroke:var(--amber);stroke-width:2;opacity:.9}.ship{cursor:pointer;transition:opacity .2s}.ship path{fill:#79eee8;stroke:#01262c;stroke-width:1.2;filter:drop-shadow(0 2px 3px #001)}.ship.top path{fill:var(--amber)}.ship.dim{opacity:.18}.ship.hidden{display:none}.ship.selected path{stroke:white;stroke-width:2.2;filter:drop-shadow(0 0 6px white)}
@keyframes pulse{50%{stroke-width:5;opacity:.6}}.map-tools{position:absolute;top:14px;left:14px;display:flex;gap:7px;z-index:5}.tool{border:1px solid #31515d;background:#04151dde;padding:8px 10px;border-radius:8px;cursor:pointer;font-weight:800}.tool[aria-pressed=true]{border-color:var(--cyan);color:var(--cyan)}.legend{position:absolute;left:14px;bottom:14px;display:flex;gap:12px;padding:8px 10px;border:1px solid #31515d;border-radius:9px;background:#04151dde;color:#bcd0d4;font-size:9px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}.map-status{position:absolute;right:14px;top:14px;padding:9px 11px;border-radius:9px;background:#04151dde;border:1px solid #31515d;text-align:right}.map-status b{display:block;color:var(--cyan)}.map-status span{font-size:9px;color:var(--muted)}
.tooltip{position:absolute;display:none;pointer-events:none;z-index:10;width:190px;padding:10px;border:1px solid #3a6570;border-radius:10px;background:#04151df2;box-shadow:0 12px 30px #0008}.tooltip.show{display:block}.tooltip b{display:block;font-size:13px}.tooltip span{display:block;color:var(--muted);font-size:10px;margin-top:3px}.tooltip em{color:var(--amber);font-style:normal}
.side{display:grid;grid-template-rows:auto auto minmax(0,1fr);overflow:hidden}.side-head{padding:15px 16px 12px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:end}.eyebrow{font-size:9px;letter-spacing:.15em;color:var(--cyan);font-weight:900}.side-head h2{margin:3px 0 0;font:500 23px Georgia,serif}.count{color:var(--muted);font-size:10px}.decision-summary{margin:10px 11px 0;padding:11px 12px;border:1px solid var(--line);border-radius:11px;background:#06171d}.decision-summary small{display:block;color:var(--muted);font-size:8px;letter-spacing:.12em}.decision-summary b{display:block;margin:3px 0;color:var(--cyan);font-size:14px}.decision-summary span{color:var(--muted);font-size:9px}.decision-summary.abstain{border-color:#806532}.decision-summary.abstain b{color:var(--amber)}.side-scroll{overflow:auto;padding:11px}.candidates{display:grid;gap:7px}.candidate{display:grid;grid-template-columns:31px 1fr auto;gap:9px;align-items:center;width:100%;padding:10px;border:1px solid var(--line);border-radius:11px;background:#071a22;text-align:left;cursor:pointer}.candidate:hover,.candidate.active{border-color:#4e858c;background:#0b252e}.rank{width:28px;height:28px;display:grid;place-items:center;border-radius:8px;background:#102f38;color:var(--cyan);font-weight:900}.candidate:first-child .rank{background:#4b3b1c;color:var(--amber)}.candidate b{display:block}.candidate small{color:var(--muted)}.score{color:var(--amber);font-weight:900}.more{width:100%;border:0;background:none;color:var(--muted);padding:9px;cursor:pointer}.detail{margin-top:11px;padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}.detail-placeholder{text-align:center;color:var(--muted);padding:20px}.detail-top{display:flex;justify-content:space-between;gap:12px}.detail h3{margin:2px 0;font:500 20px Georgia,serif}.detail-id{color:var(--muted);font:10px monospace}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:11px 0}.metric{padding:9px;border-radius:9px;background:#06171d}.metric span{display:block;color:var(--muted);font-size:9px}.metric b{display:block;margin-top:3px}.bar{height:4px;background:#14313a;border-radius:5px;margin-top:5px;overflow:hidden}.bar i{display:block;height:100%;background:var(--cyan)}.detail-actions{display:flex;gap:6px}.detail-actions button,.detail-actions a{flex:1;padding:8px;border:1px solid var(--line);border-radius:8px;background:#09232b;color:var(--ink);text-align:center;text-decoration:none;cursor:pointer;font-size:10px;font-weight:800}.notice{margin-top:9px;color:#c9b77f;font-size:9px;border-left:2px solid var(--amber);padding-left:8px}
.bottom{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:10px;padding:0 10px 10px}.timeline,.weather{border:1px solid var(--line);border-radius:14px;background:var(--panel);min-width:0}.timeline{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;padding:12px 14px}.play{width:38px;height:38px;border-radius:50%;border:1px solid #39717a;background:#0a2931;cursor:pointer}.timebox{font:11px monospace;color:var(--cyan)}input[type=range]{width:100%;accent-color:var(--cyan)}.range-labels{display:flex;justify-content:space-between;color:var(--muted);font-size:8px}.weather{display:grid;grid-template-columns:1fr 1fr 1fr}.weather button{border:0;border-right:1px solid var(--line);background:transparent;text-align:left;padding:12px;cursor:pointer}.weather button:last-child{border:0}.weather span{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}.weather b{display:block;font-size:15px;margin-top:4px}.arrow{display:inline-block;color:var(--cyan);margin-right:4px}
.popover{position:absolute;left:14px;top:54px;display:none;padding:9px;border:1px solid #31515d;border-radius:10px;background:#04151df5;z-index:8}.popover.show{display:grid;gap:7px}.popover label{display:flex;gap:8px;align-items:center;font-size:11px}.phase{position:absolute;inset:auto 20px 22px 20px;display:none;grid-template-columns:repeat(4,1fr);gap:6px;z-index:7}.phase.show{display:grid}.phase div{padding:8px;border:1px solid #31515d;background:#04151ded;border-radius:8px;color:var(--muted);font-size:9px}.phase div.on{border-color:var(--cyan);color:var(--cyan)}.phase div.done{color:var(--green)}
dialog{width:min(620px,calc(100% - 24px));padding:0;border:1px solid #37616c;border-radius:16px;background:#071820;color:var(--ink);box-shadow:0 30px 90px #000c}dialog::backdrop{background:#010609cc;backdrop-filter:blur(4px)}.modal{padding:19px}.modal-head{display:flex;justify-content:space-between}.modal h2{margin:4px 0 12px;font:500 28px Georgia,serif}.close{border:0;background:none;color:var(--muted);font-size:24px;cursor:pointer}.fact{display:grid;grid-template-columns:160px 1fr;gap:10px;padding:9px 0;border-top:1px solid var(--line)}.fact span{color:var(--muted)}.disclosure{padding:11px;border-left:3px solid var(--amber);background:#2e250f55;color:#dec991;margin-top:12px}.spark{width:100%;height:150px;background:#05141a;border-radius:10px}.hidden{display:none!important}
@media(max-width:900px){body{overflow:auto}.app{height:auto;min-height:100vh;grid-template-rows:auto auto auto}header{padding:10px;flex-wrap:wrap}.workspace{grid-template-columns:1fr}.map-card{height:58vh}.side{max-height:none}.bottom{grid-template-columns:1fr}.weather{min-height:70px}.server{display:none}}
</style></head><body><div class="app"><header><div class="brand"><div class="mark">E</div>ESPADA</div><div class="case"><b id="caseName"></b><span id="caseSub">Sealed evidence replay</span></div><div class="badge" id="modeBadge"></div><div class="spacer"></div><span class="server" id="server">SAVED EVIDENCE MODE</span><button class="soft" id="details">Case details</button><button class="primary" id="run">▶ Run analysis</button></header>
<main class="workspace"><section class="map-card" id="mapCard"><img class="sar" id="sar" alt="Real Sentinel-1 context"><div class="map-shade"></div><svg class="overlay" id="overlay" viewBox="0 0 1000 650" preserveAspectRatio="none"><g id="grid"></g><g id="slickLayer"></g><g id="particleLayer"></g><g id="releaseLayer"></g><g id="trackLayer"></g><g id="shipLayer"></g></svg><div class="map-tools"><button class="tool" id="layers">☷ Layers</button><button class="tool" id="satellite" aria-pressed="true">◐ Satellite</button><button class="tool" id="reset">↺ Reset</button></div><div class="popover" id="layerMenu"><label><input type="checkbox" data-layer="slick" checked> Oil slick</label><label><input type="checkbox" data-layer="reverse" checked> Reverse drift</label><label><input type="checkbox" data-layer="ships" checked> Ships</label><label><input type="checkbox" data-layer="tracks"> Selected track</label></div><div class="map-status"><b id="mapStatus">REAL SENTINEL-1 CONTEXT</b><span id="mapSub">Run analysis to trace the controlled slick</span></div><div class="legend"><span><i class="dot" style="background:#52e2dc"></i>Ships</span><span><i class="dot" style="background:#ffbd59"></i>Oil</span><span><i class="dot" style="background:#72f1e6"></i>Probable origin</span></div><div class="tooltip" id="tooltip"></div><div class="phase" id="phase"><div>01 · Observe</div><div>02 · Reverse</div><div>03 · Rank</div><div>04 · Verify</div></div></section>
<aside class="side"><div class="side-head"><div><div class="eyebrow">INVESTIGATION SHORTLIST</div><h2>Candidate vessels</h2></div><div class="count"><span id="candidateCount"></span><br>Top 3 after analysis</div></div><div class="decision-summary" id="decisionSummary"><small>OPERATIONAL DECISION</small><b id="decisionState">AWAITING ANALYSIS</b><span id="decisionReason">No vessel has been nominated.</span></div><div class="side-scroll"><div class="candidates" id="candidateList"></div><button class="more hidden" id="more">Show more candidates</button><div class="detail" id="detail"><div class="detail-placeholder">Run the analysis to create a reviewable shortlist.</div></div></div></aside></main>
<footer class="bottom"><section class="timeline"><button class="play" id="play" aria-label="Play timeline">▶</button><div><input id="time" type="range" min="0" max="1000" value="520"><div class="range-labels"><span id="startTime"></span><span>Drag to replay ship movement</span><span id="endTime"></span></div></div><div class="timebox" id="timeBox"></div></section><section class="weather"><button data-env="wind"><span>Wind</span><b><i class="arrow" id="windArrow">→</i><span id="windValue" style="display:inline"></span></b></button><button data-env="current"><span>Surface current</span><b><i class="arrow" id="currentArrow">→</i><span id="currentValue" style="display:inline"></span></b></button><button data-env="coverage"><span>AIS coverage</span><b id="coverage">76 vessels</b></button></section></footer></div>
<dialog id="modal"><div class="modal"><div class="modal-head"><div><div class="eyebrow" id="modalEyebrow">CASE DETAILS</div><h2 id="modalTitle"></h2></div><button class="close" id="close">×</button></div><div id="modalBody"></div></div></dialog>
<script>const DATA=__DATA__;
const $=id=>document.getElementById(id),bbox=DATA.bbox,W=1000,H=650;let selected=null,showCount=3,playing=false,ran=false,analysisComplete=false,engine=false,runTimer=[];const times=DATA.tracks.flatMap(t=>t.points.map(p=>Date.parse(p.t))),minT=Math.min(...times),maxT=Math.max(...times);const releaseT=Date.parse(DATA.case.releaseTime),obsT=Date.parse(DATA.case.observationTime);
function pct(v){return v==null?'N/A':(v*100).toFixed(1)+'%'}function num(v,d=1){return v==null?'N/A':Number(v).toFixed(d)}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function point(lon,lat){return{x:(lon-bbox[0])/(bbox[2]-bbox[0])*W,y:(bbox[3]-lat)/(bbox[3]-bbox[1])*H}}function geoPaths(g){const polys=g.type==='Polygon'?[g.coordinates]:g.coordinates;return polys.map(poly=>poly.map(ring=>ring.map((c,i)=>{const p=point(c[0],c[1]);return(i?'L':'M')+p.x.toFixed(1)+' '+p.y.toFixed(1)}).join(' ')+' Z').join(' '))}
function init(){ $('caseName').textContent=DATA.case.name;$('caseSub').textContent=DATA.case.mode==='CONTROLLED DIGITAL TWIN'?'Sealed-ground-truth attribution test':'Historical evidence replay';$('modeBadge').textContent=DATA.case.mode;$('sar').src=DATA.satelliteImage;$('candidateCount').textContent=DATA.case.candidateCount+' compared';$('coverage').textContent=DATA.case.candidateCount+' tracked';$('startTime').textContent=new Date(minT).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit'});$('endTime').textContent=new Date(maxT).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit'});grid();slick();particles();ships();renderCandidates();setTime(releaseT);health()}
function grid(){let s='';for(let i=1;i<6;i++)s+=`<line class="gridline" x1="${i*W/6}" y1="0" x2="${i*W/6}" y2="${H}"/><line class="gridline" x1="0" y1="${i*H/6}" x2="${W}" y2="${i*H/6}"/>`;$('grid').innerHTML=s}
function slick(){ $('slickLayer').innerHTML=geoPaths(DATA.slick.features[0].geometry).map(d=>`<path class="slick" d="${d}"/>`).join('')}
function particles(){const c=centroid();$('particleLayer').innerHTML=DATA.reverse.map((v,i)=>{const e=point(v[0],v[1]);return`<circle class="particle" cx="${c.x}" cy="${c.y}" r="${1.5+(i%3)*.35}" data-x="${e.x}" data-y="${e.y}"/>`}).join('');const r=point(DATA.release.longitude,DATA.release.latitude);const px=Math.max(16,DATA.release.radius90/90*W/(bbox[2]-bbox[0]));$('releaseLayer').innerHTML=`<circle class="release" cx="${r.x}" cy="${r.y}" r="${Math.min(95,px)}"/><circle class="release" cx="${r.x}" cy="${r.y}" r="5"/>`}
function centroid(){let x=0,y=0,n=0;const g=DATA.slick.features[0].geometry,polys=g.type==='Polygon'?[g.coordinates]:g.coordinates;polys.forEach(p=>p[0].forEach(c=>{const q=point(c[0],c[1]);x+=q.x;y+=q.y;n++}));return{x:x/n,y:y/n}}
const boat='<path d="M0 -10 L6 7 L0 4 L-6 7 Z"/>';
function ships(){ $('shipLayer').innerHTML=DATA.tracks.map(t=>`<g class="ship" data-id="${t.mmsi}" tabindex="0" role="button" aria-label="${esc(t.name)}">${boat}</g>`).join('');document.querySelectorAll('.ship').forEach(el=>{el.addEventListener('mouseenter',e=>tip(e,el.dataset.id));el.addEventListener('mousemove',moveTip);el.addEventListener('mouseleave',()=> $('tooltip').classList.remove('show'));el.addEventListener('click',()=>select(el.dataset.id));el.addEventListener('keydown',e=>{if(e.key==='Enter')select(el.dataset.id)})})}
function positionAt(track,t){const p=track.points,q=p.map(x=>Date.parse(x.t));if(t<q[0]-5400000||t>q[q.length-1]+5400000)return null;let i=q.findIndex(v=>v>=t);if(i===-1)i=q.length-1;if(i===0)return Math.abs(q[0]-t)<=5400000?{...p[0],h:0}:null;const a=p[i-1],b=p[i],ta=q[i-1],tb=q[i];if(tb-ta>21600000)return Math.min(t-ta,tb-t)<=5400000?{...(t-ta<tb-t?a:b),h:0}:null;const f=(t-ta)/(tb-ta),dx=b.lon-a.lon,dy=b.lat-a.lat;return{lon:a.lon+dx*f,lat:a.lat+dy*f,h:Math.atan2(dx,-dy)*180/Math.PI}}
function setTime(t){const val=Math.max(minT,Math.min(maxT,t));$('time').value=((val-minT)/(maxT-minT)*1000).toFixed(0);$('timeBox').textContent=new Date(val).toISOString().slice(0,16).replace('T',' ')+' UTC';DATA.tracks.forEach(track=>{const el=document.querySelector(`.ship[data-id="${track.mmsi}"]`),pos=positionAt(track,val);if(!pos){el.classList.add('hidden');return}el.classList.remove('hidden');const p=point(pos.lon,pos.lat);el.setAttribute('transform',`translate(${p.x} ${p.y}) rotate(${pos.h}) scale(${track.rank&&track.rank<=3?1.15:.82})`)});environment(val);if(selected)drawTrack(DATA.tracks.find(t=>t.mmsi===selected))}
function tip(e,id){const t=DATA.tracks.find(x=>x.mmsi===id),box=$('tooltip');box.innerHTML=analysisComplete?`<b>${esc(t.name)}</b><span>${t.rank?'Rank #'+t.rank+' · <em>'+pct(t.score)+'</em> evidence':'Background traffic'}</span><span>${t.knownSource?'Sealed truth match':'Pseudonymized historical AIS'}</span><span>Click for evidence</span>`:`<b>${esc(t.name)}</b><span>Pseudonymized historical AIS track</span><span>Run analysis to calculate evidence</span>`;box.classList.add('show');moveTip(e)}function moveTip(e){const card=$('mapCard').getBoundingClientRect(),box=$('tooltip');box.style.left=Math.min(card.width-205,e.clientX-card.left+12)+'px';box.style.top=Math.min(card.height-95,e.clientY-card.top+12)+'px'}
function renderCandidates(){if(!analysisComplete){$('candidateList').innerHTML='<div class="detail-placeholder">No ranking yet. Run the analysis to compare every vessel.</div>';$('more').classList.add('hidden');return}const list=DATA.candidates.slice(0,showCount);$('candidateList').innerHTML=list.map(t=>`<button class="candidate ${selected===t.mmsi?'active':''}" data-id="${t.mmsi}"><span class="rank">${t.rank}</span><span><b>${esc(t.name)}</b><small>${t.knownSource?'Sealed source recovered':'Pseudonymized AIS vessel'} · ${num(t.forwardError,2)} km replay</small></span><span class="score">${pct(t.score)}</span></button>`).join('');document.querySelectorAll('.candidate').forEach(b=>b.onclick=()=>select(b.dataset.id));$('more').classList.remove('hidden');$('more').textContent=showCount===3?'Show more candidates':'Show Top 3 only'}
function select(id){selected=id;const t=DATA.tracks.find(x=>x.mmsi===id);document.querySelectorAll('.ship').forEach(s=>{s.classList.toggle('selected',s.dataset.id===id);s.classList.toggle('dim',s.dataset.id!==id)});drawTrack(t);if(!analysisComplete){$('detail').innerHTML=`<div class="detail-top"><div><div class="eyebrow">TRACKED VESSEL</div><h3>${esc(t.name)}</h3><div class="detail-id">ID ${esc(t.mmsi)}</div></div></div><div class="notice">The vessel is visible in AIS, but no attribution score exists until the analysis runs.</div>`;return}renderCandidates();$('detail').innerHTML=`<div class="detail-top"><div><div class="eyebrow">${t.knownSource?'SEALED SOURCE · RANK #'+t.rank:'RANK #'+t.rank}</div><h3>${esc(t.name)}</h3><div class="detail-id">ID ${esc(t.mmsi)}</div></div><div class="score">${pct(t.score)}</div></div><div class="metrics"><div class="metric"><span>Origin presence</span><b>${pct(t.presence)}</b><div class="bar"><i style="width:${(t.presence||0)*100}%"></i></div></div><div class="metric"><span>Forward agreement</span><b>${pct(t.forward)}</b><div class="bar"><i style="width:${(t.forward||0)*100}%"></i></div></div><div class="metric"><span>Track quality</span><b>${pct(t.quality)}</b></div><div class="metric"><span>AIS gap</span><b>${esc((t.silence||'not evaluated').replaceAll('_',' '))}</b></div></div><div class="detail-actions"><button id="replayVessel">Replay track</button><button id="why">Why ranked?</button><a href="${DATA.dossier}" target="_blank">Dossier ↗</a></div><div class="notice">Candidate evidence supports analyst review only. It is not a guilt probability.</div>`;$('replayVessel').onclick=()=>{setTime(Math.max(minT,Date.parse(t.bestTime||DATA.case.releaseTime)-7200000));if(!playing)togglePlay()};$('why').onclick=()=>why(t)}
function drawTrack(t){if(!t){$('trackLayer').innerHTML='';return}const pts=t.points.map(p=>point(p.lon,p.lat));$('trackLayer').innerHTML=`<polyline class="track show ${t.rank===1?'top':''}" points="${pts.map(p=>p.x+','+p.y).join(' ')}"/>`}
function environment(t){const e=DATA.environment.reduce((a,b)=>Math.abs(Date.parse(b.time_utc)-t)<Math.abs(Date.parse(a.time_utc)-t)?b:a);vector('wind',e.wind_east_ms,e.wind_north_ms,'m/s');vector('current',e.current_east_ms,e.current_north_ms,'m/s')}
function vector(name,e,n,unit){const speed=Math.hypot(e,n),deg=Math.atan2(e,-n)*180/Math.PI;$(name+'Arrow').style.transform=`rotate(${deg}deg)`;$(name+'Value').textContent=speed.toFixed(name==='wind'?1:2)+' '+unit}
function togglePlay(){playing=!playing;$('play').textContent=playing?'❚❚':'▶';let last=performance.now();function frame(now){if(!playing)return;let t=minT+Number($('time').value)/1000*(maxT-minT);t+=(now-last)*80;last=now;if(t>=maxT){playing=false;$('play').textContent='▶';t=minT}setTime(t);requestAnimationFrame(frame)}if(playing)requestAnimationFrame(frame)}
function animateParticles(duration=2400){const start=performance.now(),nodes=[...document.querySelectorAll('.particle')];nodes.forEach(n=>n.classList.add('show'));function f(now){const p=Math.min(1,(now-start)/duration),ease=1-Math.pow(1-p,3);nodes.forEach(n=>{const sx=Number(n.getAttribute('cx')),sy=Number(n.getAttribute('cy')),ex=Number(n.dataset.x),ey=Number(n.dataset.y);n.setAttribute('transform',`translate(${(ex-sx)*ease} ${(ey-sy)*ease})`)});if(p<1)requestAnimationFrame(f)}requestAnimationFrame(f)}
function phase(i,state='on'){const nodes=[...$('phase').children];nodes.forEach((n,j)=>{n.className=j<i?'done':j===i?state:''})}
async function run(){if(ran)resetRun();ran=true;analysisComplete=false;renderCandidates();$('run').disabled=true;$('phase').classList.add('show');$('decisionSummary').classList.remove('abstain');$('decisionState').textContent='ANALYSIS RUNNING';$('decisionReason').textContent='Comparing drift, timing, shape and AIS quality.';$('mapStatus').textContent='ANALYSIS RUNNING';phase(0);document.querySelectorAll('.slick').forEach(x=>x.classList.add('show'));runTimer.push(setTimeout(()=>{phase(1);$('mapStatus').textContent='REVERSING DRIFT';animateParticles();setTime(releaseT)},650));let result=null;try{if(engine){const r=await fetch('/api/run-operations',{method:'POST'});if(!r.ok)throw new Error('engine response');result=await r.json()}}catch(e){result=null}runTimer.push(setTimeout(()=>{phase(2);document.querySelectorAll('.release').forEach(x=>x.classList.add('show'));$('mapStatus').textContent='MATCHING VESSELS'},3200));runTimer.push(setTimeout(()=>{phase(3);$('mapStatus').textContent='FORWARD VERIFYING'},4100));runTimer.push(setTimeout(()=>{phase(4,'done');analysisComplete=true;const abstain=DATA.case.decision.startsWith('ABSTAIN'),label=abstain?'ABSTAIN · INSUFFICIENT EVIDENCE':DATA.case.decision.replaceAll('_',' ');$('mapStatus').textContent=abstain?'RANKED · NO AUTOMATIC NOMINATION':label;$('mapSub').textContent=(result?'Fresh local ranking':'Saved evidence replay')+' · '+DATA.case.candidateCount+' candidates';$('decisionState').textContent=label;$('decisionReason').textContent=abstain?'The shortlist is visible, but evidence gates prevent escalation.':'Evidence gates permit human analyst review.';$('decisionSummary').classList.toggle('abstain',abstain);document.querySelectorAll('.ship').forEach(s=>{const t=DATA.tracks.find(x=>x.mmsi===s.dataset.id);s.classList.toggle('top',t&&t.rank===1);s.classList.toggle('truth',t&&t.knownSource)});renderCandidates();select(DATA.candidates[0].mmsi);$('run').disabled=false;$('run').textContent='Run again'},4900))}
function resetRun(){runTimer.forEach(clearTimeout);runTimer=[];ran=false;analysisComplete=false;playing=false;showCount=3;$('play').textContent='▶';$('run').disabled=false;$('run').textContent='▶ Run analysis';document.querySelectorAll('.slick,.particle,.release').forEach(x=>{x.classList.remove('show');x.style.display='';if(x.classList.contains('particle'))x.removeAttribute('transform')});$('shipLayer').style.display='';$('trackLayer').style.display='';$('phase').classList.remove('show');$('layerMenu').classList.remove('show');document.querySelectorAll('[data-layer]').forEach(c=>c.checked=c.dataset.layer!=='tracks');$('satellite').setAttribute('aria-pressed','true');$('sar').style.opacity='.68';$('mapStatus').textContent='REAL SENTINEL-1 CONTEXT';$('mapSub').textContent='Select Run analysis to trace the controlled slick';$('decisionSummary').classList.remove('abstain');$('decisionState').textContent='AWAITING ANALYSIS';$('decisionReason').textContent='No vessel has been nominated.';selected=null;document.querySelectorAll('.ship').forEach(s=>s.classList.remove('selected','dim','top','truth'));$('trackLayer').innerHTML='';$('detail').innerHTML='<div class="detail-placeholder">Run the analysis to create a reviewable shortlist.</div>';renderCandidates();setTime(releaseT)}
function modal(title,body,eyebrow='DETAILS'){$('modalTitle').textContent=title;$('modalEyebrow').textContent=eyebrow;$('modalBody').innerHTML=body;$('modal').showModal()}
function why(t){modal('Why this vessel?',`<div class="fact"><span>Release-zone presence</span><b>${pct(t.presence)}</b></div><div class="fact"><span>Forward slick agreement</span><b>${pct(t.forward)}</b></div><div class="fact"><span>Centroid error</span><b>${num(t.forwardError,2)} km</b></div><div class="fact"><span>Shape error</span><b>${num(t.shapeError,2)} km</b></div><div class="fact"><span>AIS quality</span><b>${pct(t.quality)}</b></div><div class="fact"><span>Gap interpolation</span><b>${t.interpolated?'Used and penalized':'Not used'}</b></div><div class="disclosure">Scores compare vessels within this case. They are not probabilities of guilt.</div>`,'EVIDENCE BREAKDOWN')}
function details(){const measured=(DATA.challenge||{}).measured_result||{};modal(DATA.case.name,`<div class="fact"><span>SAR context</span><b>Sentinel-1 VV · ${DATA.satellite.acquisition_time_utc}</b></div><div class="fact"><span>Slick input</span><b>${esc(DATA.slickSource)}</b></div><div class="fact"><span>Environment</span><b>Copernicus currents + Open-Meteo wind</b></div><div class="fact"><span>Traffic</span><b>${esc(DATA.trafficSummary)}</b></div><div class="fact"><span>Sealed-source result</span><b>${measured.source_rank?'Rank #'+measured.source_rank+' of '+measured.candidate_count:'Not available'}</b></div><div class="fact"><span>Operational decision</span><b>${esc(DATA.case.decision.replaceAll('_',' '))}</b></div><div class="fact"><span>Digital-twin Top-3</span><b>${pct(DATA.digitalTwin.top_3_rate)}</b></div><div class="disclosure">${esc(DATA.disclosure)} This is controlled validation, not a real accusation.</div>`,'PROVENANCE & CLAIM BOUNDARY')}
function envDetails(type){const title=type==='wind'?'Historical wind':'Surface current',vals=DATA.environment.map(e=>type==='wind'?Math.hypot(e.wind_east_ms,e.wind_north_ms):Math.hypot(e.current_east_ms,e.current_north_ms)),max=Math.max(...vals),pts=vals.map((v,i)=>`${i/(vals.length-1)*560},${130-v/max*105}`).join(' ');modal(title,`<svg class="spark" viewBox="0 0 560 150"><polyline points="${pts}" fill="none" stroke="#52e2dc" stroke-width="3"/><line x1="0" y1="130" x2="560" y2="130" stroke="#31515d"/></svg><div class="fact"><span>Source</span><b>${esc(DATA.environmentSource)}</b></div><div class="fact"><span>Meaning</span><b>Modelled forcing used by reverse drift</b></div><div class="disclosure">Environmental values are model estimates, not direct observations.</div>`,'ENVIRONMENT DETAILS')}
async function health(){try{const r=await fetch('/api/health',{cache:'no-store'});if(r.ok){engine=true;$('server').textContent='LOCAL ENGINE CONNECTED';$('server').classList.add('on')}}catch(e){}}
$('run').onclick=run;$('reset').onclick=resetRun;$('play').onclick=togglePlay;$('time').oninput=e=>setTime(minT+Number(e.target.value)/1000*(maxT-minT));$('layers').onclick=()=> $('layerMenu').classList.toggle('show');$('satellite').onclick=e=>{const on=e.currentTarget.getAttribute('aria-pressed')==='true';e.currentTarget.setAttribute('aria-pressed',String(!on));$('sar').style.opacity=on?'.18':'.68'};$('more').onclick=()=>{showCount=showCount===3?10:3;renderCandidates()};$('details').onclick=details;$('close').onclick=()=> $('modal').close();document.querySelectorAll('[data-env]').forEach(b=>b.onclick=()=>b.dataset.env==='coverage'?modal('AIS coverage',`<div class="fact"><span>Compared</span><b>${DATA.case.candidateCount} candidate tracks</b></div><div class="fact"><span>Evidence source</span><b>${esc(DATA.trafficSummary)}</b></div><div class="fact"><span>Missing reports</span><b>Counted as uncertainty</b></div><div class="disclosure">Missing AIS remains missing evidence. Silence never increases attribution score.</div>`,'DATA QUALITY'):envDetails(b.dataset.env));document.querySelectorAll('[data-layer]').forEach(c=>c.onchange=()=>{const name=c.dataset.layer;if(name==='slick')document.querySelectorAll('.slick').forEach(x=>x.style.display=c.checked?'':'none');if(name==='reverse'){document.querySelectorAll('.particle,.release').forEach(x=>x.style.display=c.checked?'':'none')}if(name==='ships')$('shipLayer').style.display=c.checked?'':'none';if(name==='tracks')$('trackLayer').style.display=c.checked?'':'none'});init();
</script></body></html>'''.replace("__DATA__", data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESPADA's map-first operations dashboard")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_operations_dashboard(args.project_root, args.output), indent=2))


if __name__ == "__main__":
    main()
