(() => {
  'use strict';
  const data = window.ESPADA_WAKASHIO_CASE;
  const root = document.getElementById('caseApp');
  if (!data || !root) return;

  const page = document.body.dataset.casePage || 'watch';
  const files = {watch:'index.html',detection:'detection.html',investigation:'investigation.html',cases:'cases.html','case-file':'case-file.html'};
  const titles = {watch:'Historical watch',detection:'Slick evidence',investigation:'Drift & vessels',cases:'Case register','case-file':'Evidence & decision'};
  const descriptions = {
    watch:'Revisit recorded traffic and the first mapped oil extent.',
    detection:'Inspect the independent satellite mapping used as the case input.',
    investigation:'See the saved reverse-drift estimate and compare five real vessel-presence records.',
    cases:'Follow the recorded evidence through each operational gate.',
    'case-file':'Read the saved review decision, provenance and claim boundary.'
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const formatTime = value => new Date(value).toLocaleString('en-GB',{timeZone:'UTC',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false})+' UTC';
  const dates = [...new Set([
    ...data.vessels.flatMap(vessel => vessel.positions.map(position => position.time)),
    data.release_time_utc,data.observation_time_utc
  ])].sort();
  const observationIndex = dates.indexOf(data.observation_time_utc);
  const savedTimeIndex = sessionStorage.getItem('espada-case-time-index');
  let timeIndex = savedTimeIndex === null ? observationIndex : Number(savedTimeIndex);
  if (!Number.isInteger(timeIndex) || timeIndex < 0 || timeIndex >= dates.length) timeIndex = observationIndex;
  let selectedMmsi = sessionStorage.getItem('espada-case-vessel') || data.ranking[0].mmsi;
  if (!data.vessels.some(vessel => vessel.mmsi === selectedMmsi)) selectedMmsi = data.ranking[0].mmsi;
  let timer = null;
  let showSlick = true;
  let showOrigin = page === 'investigation';
  const vesselById = Object.fromEntries(data.vessels.map(vessel => [vessel.mmsi,vessel]));
  const rankById = Object.fromEntries(data.ranking.map(candidate => [candidate.mmsi,candidate]));
  const colors = ['#bc7927','#256582','#7d5a88','#508371','#a3554a'];
  const colorById = Object.fromEntries(data.vessels.map((vessel,index) => [vessel.mmsi,colors[index]]));
  const sourceName = vessel => vessel.name || `Vessel ${vessel.mmsi}`;
  const bounds = {west:57.705,east:57.825,south:-20.485,north:-20.315};
  const point = (longitude,latitude) => [
    (longitude-bounds.west)/(bounds.east-bounds.west)*1000,
    (bounds.north-latitude)/(bounds.north-bounds.south)*580
  ];
  const pathPoint = coordinates => point(coordinates[0],coordinates[1]).map(number => number.toFixed(1)).join(',');

  function latestPosition(vessel,time) {
    const valid = vessel.positions.filter(position => position.time <= time);
    return valid.at(-1) || null;
  }
  function visiblePosition(vessel,time) {
    const latest = latestPosition(vessel,time);
    return latest && (Date.parse(time)-Date.parse(latest.time)) <= 90*60*1000 ? latest : null;
  }
  function trackSegments(vessel,time) {
    const valid = vessel.positions.filter(position => position.time <= time);
    const segments = [];
    let current = [];
    valid.forEach((position,index) => {
      if (index && Date.parse(position.time)-Date.parse(valid[index-1].time) > 90*60*1000) {
        if (current.length > 1) segments.push(current);
        current = [];
      }
      current.push(position);
    });
    if (current.length > 1) segments.push(current);
    return segments;
  }
  function slickPaths() {
    return data.slick.features.flatMap(feature => {
      const rings = feature.geometry.type === 'Polygon' ? feature.geometry.coordinates : feature.geometry.coordinates.flat();
      return rings.map(ring => `<polygon class="case-slick" points="${ring.map(pathPoint).join(' ')}"/>`);
    }).join('');
  }
  function map(time,{origin=false,slick=true,allTracks=true}={}) {
    const observationVisible = slick && time >= data.observation_time_utc;
    const wreckVisible = true;
    const [originX,originY] = point(data.release.estimated_origin.longitude,data.release.estimated_origin.latitude);
    const [wreckX,wreckY] = point(data.documented_wreck.longitude,data.documented_wreck.latitude);
    const grid = [0,1,2,3,4].map(index => `<path d="M${index*250} 0V580M0 ${index*145}H1000" class="case-grid"/>`).join('');
    const tracks = data.vessels.map(vessel => {
      if (!allTracks && vessel.mmsi !== selectedMmsi) return '';
      return trackSegments(vessel,time).map(segment => `<polyline class="case-track ${vessel.mmsi===selectedMmsi?'selected':''}" stroke="${colorById[vessel.mmsi]}" points="${segment.map(position => pathPoint([position.longitude,position.latitude])).join(' ')}"/>`).join('');
    }).join('');
    const vessels = data.vessels.map(vessel => {
      const latest = visiblePosition(vessel,time);
      if (!latest) return '';
      const [x,y] = point(latest.longitude,latest.latitude);
      return `<g class="case-vessel ${vessel.mmsi===selectedMmsi?'selected':''}" data-mmsi="${vessel.mmsi}" role="button" tabindex="0" aria-label="Inspect ${esc(sourceName(vessel))}"><circle class="case-vessel-halo" cx="${x}" cy="${y}" r="15"/><circle cx="${x}" cy="${y}" r="7" fill="${colorById[vessel.mmsi]}"/><text x="${x+13}" y="${y-11}">${esc(sourceName(vessel))}</text></g>`;
    }).join('');
    return `<div class="case-map"><svg viewBox="0 0 1000 580" role="img" aria-label="Geographic replay of recorded vessel positions, slick extent and modeled origin near Mauritius"><rect width="1000" height="580" fill="#d3e3e6"/>${grid}
      <text class="case-coordinate" x="24" y="34">MAURITIUS · 57.705–57.825°E · 20.315–20.485°S</text>
      ${tracks}${origin?`<circle class="case-origin-radius" cx="${originX}" cy="${originY}" r="36"/><circle class="case-origin" cx="${originX}" cy="${originY}" r="7"/><text class="case-origin-label" x="${originX+13}" y="${originY-15}">MODELED ORIGIN · 90% RADIUS</text>`:''}
      ${observationVisible?slickPaths():''}
      ${wreckVisible?`<path class="case-wreck" d="M${wreckX-8} ${wreckY-8}l16 16m0-16-16 16"/><text class="case-wreck-label" x="${wreckX+13}" y="${wreckY+15}">DOCUMENTED WRECK · NOT AIS</text>`:''}
      ${vessels}<text class="case-map-attribution" x="23" y="552">GFW hourly grid cells · lines join observations for orientation only · no live routes</text></svg></div>`;
  }
  function sourceChip(text,tone='recorded') {return `<span class="case-chip ${tone}">${esc(text)}</span>`;}
  function panel(title,body,kicker='EVIDENCE PANEL') {return `<section class="case-panel"><div class="case-panel-head"><p class="kicker">${kicker}</p><h2>${title}</h2></div><div class="case-panel-body">${body}</div></section>`;}
  function metric(label,value,note='') {return `<div class="case-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note?`<small>${esc(note)}</small>`:''}</div>`;}
  function vesselDetails() {
    const vessel = vesselById[selectedMmsi];
    const candidate = rankById[selectedMmsi];
    const position = latestPosition(vessel,dates[timeIndex]);
    const age = position ? Math.round((Date.parse(dates[timeIndex])-Date.parse(position.time))/60000) : null;
    return `<p class="kicker">SELECTED VESSEL</p><h3>${esc(sourceName(vessel))}</h3><p class="case-mono">MMSI ${esc(vessel.mmsi)}</p>
      <dl class="case-facts"><div><dt>At replay time</dt><dd>${position && age<=90?`${position.latitude.toFixed(4)}°, ${position.longitude.toFixed(4)}°`:'No recent AIS report'}</dd></div><div><dt>Last recorded</dt><dd>${position?formatTime(position.time):'Not yet observed'}</dd></div><div><dt>Saved rank</dt><dd>#${candidate.rank} of ${data.ranking.length}</dd></div></dl>
      <p class="case-note">${esc(vessel.note)} ${vessel.source?`<a href="${esc(vessel.source)}" target="_blank" rel="noopener noreferrer">Identity source ↗</a>`:''}</p>`;
  }
  function sourceLedger() {
    return `<div class="case-ledger"><div>${sourceChip('OBSERVED')}<b>Vessel presence</b><p>${data.provider.rows} recorded GFW hourly positions from ${data.provider.vessels} MMSIs. Grid-cell centres, not continuous AIS tracks.</p></div>
      <div>${sourceChip('EXTERNAL MAP')}<b>Oil extent</b><p>UNITAR-UNOSAT Sentinel-2 analyst mapping at ${formatTime(data.observation_time_utc)}. Not an ESPADA model detection.</p></div>
      <div>${sourceChip('MODELLED','modelled')}<b>Drift reconstruction</b><p>ESPADA reverse particles using saved Copernicus currents and historical wind, with an analyst-supplied slick age.</p></div></div>`;
  }
  function watch() {
    return `<section class="case-map-card"><div class="case-section-title"><div><p class="kicker">RECORDED MARITIME WATCH</p><h2>What was known, and when?</h2><p>Scrub the actual observation times. The slick only appears once its satellite mapping exists.</p></div>${sourceChip('NO LIVE FEEDS','offline')}</div>
      <div class="case-toolbar"><button id="replayStart" class="case-primary">▶ Replay from start</button><button id="replayPause">Pause</button><button id="stepBack">←</button><input id="timeSlider" type="range" min="0" max="${dates.length-1}" value="${timeIndex}" aria-label="Recorded case time"><button id="stepForward">→</button><strong id="timeLabel">${formatTime(dates[timeIndex])}</strong></div>
      <div class="case-map-layout"><div id="mapHost">${map(dates[timeIndex])}</div><aside class="case-map-side"><p class="kicker">CASE STATE</p><strong id="observationState">—</strong><p id="observationMessage"></p><div id="vesselDetail">${vesselDetails()}</div></aside></div>
      <div class="case-vessel-strip">${data.vessels.map(vessel => `<button class="case-vessel-button" data-mmsi="${vessel.mmsi}"><span style="background:${colorById[vessel.mmsi]}"></span><b>${esc(sourceName(vessel))}</b><small>${vessel.positions.length} reports</small></button>`).join('')}</div></section>
      ${panel('Three sources, three meanings',sourceLedger(),'PROVENANCE')}`;
  }
  function detection() {
    const source = data.slick_source;
    return `<div class="case-two-col"><section class="case-map-card"><div class="case-section-title"><div><p class="kicker">SATELLITE EVIDENCE</p><h2>Mapped oil extent</h2><p>The exact saved source polygon is displayed in geographic coordinates.</p></div><button id="slickToggle">Hide polygon</button></div><div id="mapHost">${map(data.observation_time_utc,{allTracks:false})}</div></section>
      <div class="case-stack">${panel('Independent observation',`${metric('Mapped area',(data.slick_area_km2).toFixed(3)+' km²')}${metric('Acquisition',formatTime(data.observation_time_utc))}${metric('Sensor','Sentinel-2')}${metric('Source confidence',source.source_confidence)}<p class="case-note">${esc(source.source)}. The map was not yet field-validated. ESPADA imports this reviewed slick; this replay does not claim our SAR model detected it.</p><a class="case-action" href="${esc(source.source_product)}" target="_blank" rel="noopener noreferrer">Open UNOSAT source ↗</a>`,'OBSERVED')}
      ${panel('Detection gate',`<ul class="case-checks"><li>✓ Source, observation time and polygon retained</li><li>✓ Extent passed to reverse-drift pipeline</li><li>! Oil-versus-lookalike decision comes from the external expert mapping</li></ul><a class="case-action" href="investigation.html">Continue to reconstruction →</a>`,'EVIDENCE HANDOFF')}</div></div>
      ${panel('Saved reconstruction output',`<figure class="case-figure"><img src="assets/slick_reverse_analysis.png" alt="Saved ESPADA result showing the imported mapped slick polygon beside the reverse-drift endpoint probability field"><figcaption>Left: externally mapped Sentinel-2 slick extent. Right: ESPADA’s modeled backward endpoint distribution. This is a saved analysis output, not a fresh API run.</figcaption></figure>`,'ACTUAL CASE ARTIFACT')}`;
  }
  function rankingRows() {
    return data.ranking.map(candidate => `<button class="case-rank-row ${candidate.mmsi===selectedMmsi?'selected':''}" data-mmsi="${candidate.mmsi}"><span>#${candidate.rank}</span><b>${esc(sourceName(vesselById[candidate.mmsi]))}</b><small>MMSI ${candidate.mmsi}</small><strong>${(candidate.score*100).toFixed(1)}%</strong></button>`).join('');
  }
  function investigation() {
    const release = data.release;
    return `<div class="case-metrics">${metric('Estimated release',formatTime(release.release_time_utc))}${metric('90% origin radius',release.credible_radius_90_km.toFixed(2)+' km')}${metric('Compared vessels',String(data.ranking.length))}${metric('Top forward error',data.ranking[0].forward_error_km.toFixed(2)+' km')}</div>
      <div class="case-two-col"><section class="case-map-card"><div class="case-section-title"><div><p class="kicker">REVERSE DRIFT + AIS CORRELATION</p><h2>Origin is a region, not a point</h2><p>Saved analysis at ${formatTime(release.observation_time_utc)}. Select a candidate to inspect its recorded track.</p></div>${sourceChip('MODELLED','modelled')}</div><div id="mapHost">${map(data.observation_time_utc,{origin:true})}</div><div class="case-map-caption">The amber extent is the independent UNOSAT mapping; the ring is ESPADA’s modeled origin uncertainty. Track lines join hourly GFW cell centres and must not be read as continuous vessel paths.</div></section>
      <div class="case-stack">${panel('Ranked investigative shortlist',`<div id="rankingList">${rankingRows()}</div><p class="case-note">Scores compare candidates inside this case; they are not guilt probabilities.</p>`,'ATTRIBUTION')}
      ${panel('Selected candidate',`<div id="vesselDetail">${vesselDetails()}</div><div id="candidateMetrics"></div>`,'EVIDENCE REVIEW')}</div></div>
      ${panel('Assumptions and checks',`<div class="case-ledger"><div>${sourceChip('MODELLED','modelled')}<b>Forcing</b><p>Copernicus Marine currents plus historical forecast wind, linearly interpolated from saved fields.</p></div><div>${sourceChip('ASSUMED','offline')}<b>Slick age</b><p>${release.assumed_age_hours} hours supplied by the analyst; this is not an age inferred from the satellite image.</p></div><div>${sourceChip('CHECKED')}<b>Forward replay</b><p>The leading candidate’s forward error was ${data.ranking[0].forward_error_km.toFixed(2)} km. This tests consistency, not causation.</p></div></div><a class="case-action" href="case-file.html">Read the review decision →</a>`,'METHODOLOGY')}`;
  }
  function cases() {
    const checks = data.decision.checks;
    return `<div class="case-two-col"><div class="case-stack">${panel('Mauritius · August 2020',`${sourceChip('READY FOR ANALYST REVIEW')}<h3>One saved historical case</h3><p>This is not an incoming live incident. It replays a completed ESPADA reconstruction of a mapped spill and five recorded vessel-presence tracks.</p><dl class="case-facts"><div><dt>Observed extent</dt><dd>${formatTime(data.observation_time_utc)}</dd></div><div><dt>Estimated release</dt><dd>${formatTime(data.release_time_utc)}</dd></div><div><dt>Case decision</dt><dd>${data.decision.value.replaceAll('_',' ')}</dd></div></dl><a class="case-action" href="case-file.html">Open case file →</a>`,'CASE REGISTER')}
      ${panel('Inputs in sequence',`<ol class="case-timeline"><li><b>Historical vessel presence</b><span>${data.provider.rows} grid-cell records</span></li><li><b>External slick mapping</b><span>${formatTime(data.observation_time_utc)}</span></li><li><b>Reverse-drift estimate</b><span>${data.release.assumed_age_hours} h assumed age</span></li><li><b>Five-way ranking</b><span>Saved comparative scores</span></li><li><b>Human review gate</b><span>No automated guilt finding</span></li></ol>`,'WORKFLOW')}</div>
      ${panel('Evidence gates',`<div class="case-gate-list">${checks.map(check => `<div><b class="${check.status==='PASS'?'pass':'warn'}">${esc(check.status)}</b><span>${esc(check.gate.replaceAll('_',' '))}</span><small>${esc(String(check.requirement))}</small></div>`).join('')}</div>`,'AUDIT')}
      </div>`;
  }
  function caseFile() {
    return `<div class="case-verdict"><span>RECORDED DECISION · ${esc(data.decision.value.replaceAll('_',' '))}</span><h2>Rank the source. Preserve the uncertainty.</h2><p>${esc(data.decision.recommended_action)}</p></div>
      <div class="case-metrics">${metric('Known source','MV Wakashio','Official casualty report')}${metric('Saved rank','#1 of 5','Known identity revealed after ranking')}${metric('Forward error',data.ranking[0].forward_error_km.toFixed(2)+' km')}${metric('Data quality',(data.ranking[0].data_quality*100).toFixed(1)+'%','Important limitation')}</div>
      <div class="case-two-col"><div class="case-stack">${panel('What the result means',`<p>The saved ranker received pseudonymized candidate IDs; the recorded truth was opened after the ranking artifact existed. The target’s AIS has only eight hourly reports through 5 August. Its later incident-time position is the <b>documented fixed wreck position</b>, not a fabricated AIS track.</p><p class="case-note">${esc(data.decision.policy_interpretation)}</p><button id="downloadCase" class="case-primary">Download frozen case data</button>`,'INTERPRETATION')}
      ${panel('Limits that matter',`<ul class="case-checks">${data.limitations.map(item => `<li>! ${esc(item)}</li>`).join('')}</ul>`,'CLAIM BOUNDARY')}</div>
      ${panel('Evidence provenance',`<div class="case-source-list"><a href="${esc(data.sources.official_casualty_report)}" target="_blank" rel="noopener noreferrer">Official Mauritius casualty report ↗</a><a href="${esc(data.sources.unosat_product)}" target="_blank" rel="noopener noreferrer">UNITAR-UNOSAT slick product ↗</a><a href="${esc(data.sources.sentinel_acquisition_time)}" target="_blank" rel="noopener noreferrer">Satellite acquisition chronology ↗</a><a href="${esc(data.sources.gfw_documentation)}" target="_blank" rel="noopener noreferrer">Global Fishing Watch historical vessel-presence dataset ↗</a></div><div class="case-hashes"><b>Frozen source SHA-256</b>${Object.entries(data.source_sha256).map(([key,value]) => `<div><span>${esc(key)}</span><code>${esc(value)}</code></div>`).join('')}</div>`,'AUDIT TRAIL')}</div>`;
  }
  const views = {watch,detection,investigation,cases,'case-file':caseFile};
  root.innerHTML = `<header class="masthead"><a class="identity" href="index.html"><svg class="identity-mark" viewBox="0 0 44 44" aria-hidden="true"><path d="M22 3 38 12v20L22 41 6 32V12Z"/><path d="m13 26 9-16 9 16-9 8Z"/><path d="M16 24h12M22 10v24"/></svg><span><b>ESPADA</b><small>Maritime Evidence Operations</small></span></a><div class="mission"><span class="label">HISTORICAL CASE</span><strong>Mauritius · August 2020</strong><span>Archived evidence · no provider calls</span></div><div class="clock-block"><span class="label">CASE TIME</span><strong>${formatTime(data.observation_time_utc)}</strong><span>Sentinel-2 mapping</span></div><div class="connection" data-tone="degraded"><span class="connection-light"></span><span><b>RECORDED REPLAY</b><small>Not live · no API keys required</small></span></div></header>
    <nav class="workflow" aria-label="Historical case pages">${Object.entries(files).map(([key,file],index) => `<a class="workflow-step ${key===page?'active':'available'}" href="${file}" ${key===page?'aria-current="page"':''}><span>0${index+1}</span><b>${titles[key]}</b><small>${['Map & sources','Reviewed slick','Drift & ranking','Saved record','Decision & proof'][index]}</small></a>`).join('')}</nav>
    <main><section class="briefing"><div><p class="kicker">ESPADA · SOURCE-BACKED HISTORICAL REPLAY</p><h1>${titles[page]}</h1><p class="lede">${descriptions[page]}</p></div><div class="case-briefing-actions"><span class="case-mode">NO LIVE DATA · FIXED 2020 CASE</span><a href="../live_command/index.html">Go to live operations ↗</a></div></section>${views[page]()}<footer class="case-footer">Historical reconstruction · comparative ranking only · human review required · <a href="${esc(data.sources.unosat_product)}" target="_blank" rel="noopener noreferrer">UNOSAT mapping</a> · <a href="https://globalfishingwatch.org" target="_blank" rel="noopener noreferrer">Powered by Global Fishing Watch.</a> Historical vessel-presence data used noncommercially (CC BY-NC 4.0).</footer></main>`;

  function refresh() {
    const time = dates[timeIndex];
    sessionStorage.setItem('espada-case-time-index',String(timeIndex));
    sessionStorage.setItem('espada-case-vessel',selectedMmsi);
    const host = document.getElementById('mapHost');
    if (host) host.innerHTML = map(page==='watch'?time:data.observation_time_utc,{origin:showOrigin,slick:showSlick,allTracks:page!=='detection'});
    const timeSlider = document.getElementById('timeSlider');
    if (timeSlider) timeSlider.value=String(timeIndex);
    const timeLabel = document.getElementById('timeLabel');
    if (timeLabel) timeLabel.textContent=formatTime(time);
    const observationState = document.getElementById('observationState');
    if (observationState) {
      observationState.textContent=time>=data.observation_time_utc?'MAPPED SLICK AVAILABLE':'NO SLICK OBSERVATION YET';
      document.getElementById('observationMessage').textContent=time>=data.observation_time_utc
        ? 'UNOSAT mapped the extent at the satellite observation time. It is not a continuously observed animation.'
        : 'A spill may have existed, but this archived input contains no mapped oil observation at this time.';
    }
    const detail = document.getElementById('vesselDetail');
    if (detail) detail.innerHTML=vesselDetails();
    document.querySelectorAll('[data-mmsi]').forEach(element => element.classList.toggle('selected',element.dataset.mmsi===selectedMmsi));
    const candidateMetrics = document.getElementById('candidateMetrics');
    if (candidateMetrics) {
      const candidate=rankById[selectedMmsi];
      candidateMetrics.innerHTML=`<dl class="case-facts"><div><dt>Comparative score</dt><dd>${(candidate.score*100).toFixed(1)}%</dd></div><div><dt>Forward error</dt><dd>${candidate.forward_error_km.toFixed(2)} km</dd></div><div><dt>Track quality</dt><dd>${(candidate.data_quality*100).toFixed(1)}%</dd></div><div><dt>AIS gap assessment</dt><dd>${esc(candidate.ais_gap.replaceAll('_',' '))}</dd></div></dl>`;
    }
  }
  function stop() { if (timer) window.clearInterval(timer); timer=null; }
  root.addEventListener('click',event => {
    const vessel = event.target.closest('[data-mmsi]');
    if (vessel) {selectedMmsi=vessel.dataset.mmsi; refresh(); return;}
    const id = event.target.id;
    if (id==='replayStart') {stop(); timeIndex=0; refresh(); timer=window.setInterval(()=>{if(timeIndex>=dates.length-1){stop();return;} timeIndex++;refresh();},550);}
    if (id==='replayPause') stop();
    if (id==='stepBack') {stop();timeIndex=Math.max(0,timeIndex-1);refresh();}
    if (id==='stepForward') {stop();timeIndex=Math.min(dates.length-1,timeIndex+1);refresh();}
    if (id==='slickToggle') {showSlick=!showSlick;event.target.textContent=showSlick?'Hide polygon':'Show polygon';refresh();}
    if (id==='downloadCase') {
      const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob);
      const anchor=document.createElement('a');anchor.href=url;anchor.download='espada-wakashio-historical-case.json';anchor.click();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  });
  root.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches('.case-vessel')){event.preventDefault();selectedMmsi=event.target.dataset.mmsi;refresh();}});
  root.addEventListener('input',event=>{if(event.target.id==='timeSlider'){stop();timeIndex=Number(event.target.value);refresh();}});
  refresh();
})();
