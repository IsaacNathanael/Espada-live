(() => {
  'use strict';

  const core = window.ESPADA_WAKASHIO_EXERCISE;
  if (!core) throw new Error('The exercise calculation was not loaded.');
  const {caseData, evaluate, positionAt, legSpeedKnots} = core;
  const byId = id => document.getElementById(id);
  const stageButtons = [...document.querySelectorAll('[data-case-stage]')];
  const viewButtons = [...document.querySelectorAll('[data-view]')];
  const stageNames = ['Scene', 'Observe', 'Trace back', 'Compare ships', 'Decision'];
  const chartBounds = {west:57.70,east:58.05,south:-20.63,north:-20.22};
  const width = 900;
  const height = 530;
  let stage = 0;
  let view = 'image';
  let playback = null;

  const utc = time => new Date(time).toISOString().slice(11,16) + ' UTC';
  const km = number => `${number.toFixed(1)} km`;
  const coordinates = point => ({
    x: (point.longitude - chartBounds.west) / (chartBounds.east - chartBounds.west) * width,
    y: (chartBounds.north - point.latitude) / (chartBounds.north - chartBounds.south) * height
  });
  const pointString = point => { const projected = coordinates(point); return `${projected.x.toFixed(1)},${projected.y.toFixed(1)}`; };
  const clockTime = () => new Date(Date.parse('2020-08-06T03:00:00Z') + Number(byId('caseClock').value) * 15 * 60_000).toISOString();
  const currentResult = () => evaluate(Number(byId('caseAge').value));

  function renderChart(result) {
    const clock = clockTime();
    const lines = [];
    const gridLongitudes = [57.75,57.80,57.85,57.90,57.95,58.00];
    const gridLatitudes = [-20.60,-20.50,-20.40,-20.30];
    lines.push('<rect width="900" height="530" fill="#d9e6e8"/>');
    for (const longitude of gridLongitudes) {
      const x = coordinates({longitude,latitude:chartBounds.north}).x.toFixed(1);
      lines.push(`<path class="case-grid" d="M${x} 0V530"/><text class="case-grid-label" x="${Number(x)+4}" y="510">${longitude.toFixed(2)}°E</text>`);
    }
    for (const latitude of gridLatitudes) {
      const y = coordinates({longitude:chartBounds.west,latitude}).y.toFixed(1);
      lines.push(`<path class="case-grid" d="M0 ${y}H900"/><text class="case-grid-label" x="12" y="${Number(y)-5}">${Math.abs(latitude).toFixed(2)}°S</text>`);
    }
    lines.push('<text class="case-chart-title" x="24" y="34">MAURITIUS · GEOREFERENCED EXERCISE CHART</text>');
    lines.push('<text class="case-chart-subtitle" x="24" y="54">Routes and current/wind are synthetic; wreck position is from the official report.</text>');

    const wreck = coordinates(caseData.documentedSource.position);
    for (const [index, vessel] of caseData.exerciseVessels.entries()) {
      const points = vessel.waypoints.map(pointString).join(' ');
      const vesselPosition = positionAt(vessel, clock);
      lines.push(`<polyline class="case-route ${index ? 'second' : ''}" points="${points}"/>`);
      if (vesselPosition) {
        const marker = coordinates(vesselPosition);
        lines.push(`<circle class="case-ship ${index ? 'second' : ''}" cx="${marker.x.toFixed(1)}" cy="${marker.y.toFixed(1)}" r="8"/>`);
        lines.push(`<text class="case-route-label" x="${(marker.x+13).toFixed(1)}" y="${(marker.y-12).toFixed(1)}">${vessel.name}</text>`);
      }
    }
    lines.push(`<circle class="case-wreck" cx="${wreck.x.toFixed(1)}" cy="${wreck.y.toFixed(1)}" r="9"/>`);
    lines.push(`<text class="case-wreck-label" x="${(wreck.x+14).toFixed(1)}" y="${(wreck.y+22).toFixed(1)}">MV Wakashio · grounded</text>`);
    if (stage >= 2) {
      const origin = coordinates(result.origin);
      const oil = coordinates(result.illustratedObservation);
      lines.push(`<path class="case-drift" d="M${origin.x.toFixed(1)} ${origin.y.toFixed(1)}L${oil.x.toFixed(1)} ${oil.y.toFixed(1)}"/>`);
      lines.push(`<circle class="case-origin-ring" cx="${origin.x.toFixed(1)}" cy="${origin.y.toFixed(1)}" r="26"/>`);
      lines.push(`<circle class="case-origin" cx="${origin.x.toFixed(1)}" cy="${origin.y.toFixed(1)}" r="6"/>`);
      lines.push(`<circle class="case-oil" cx="${oil.x.toFixed(1)}" cy="${oil.y.toFixed(1)}" r="6"/>`);
      lines.push(`<text class="case-chart-note" x="24" y="480">Illustrative drift target, not a pixel-extracted spill centroid</text>`);
    }
    lines.push(`<text class="case-chart-note" x="24" y="495">Selected traffic time ${utc(clock)} · synthetic route positions interpolated between stated waypoints</text>`);
    byId('caseChart').innerHTML = lines.join('');
    byId('caseClockLabel').textContent = utc(clock);
  }

  function renderFacts(facts) {
    byId('caseStageFacts').innerHTML = facts.map(([term,value]) =>
      `<div class="wakashio-fact"><span>${term}</span><b>${value}</b></div>`).join('');
  }
  function renderRanking(result) {
    byId('caseRanking').innerHTML = '<p>PROXIMITY SCORE = 100 / [1 + (DISTANCE / 3 KM)²] · NOT GUILT PROBABILITY</p>' +
      result.candidates.map((candidate,index) =>
        `<div class="wakashio-rank"><span>#${index+1}</span><div><b>${candidate.name}</b><small>${candidate.evidence} · ${km(candidate.distanceKm)} from estimated origin</small></div><strong>${candidate.exerciseScore}/100</strong></div>`).join('');
  }
  function setView(nextView) {
    view = nextView;
    byId('caseImageView').hidden = view !== 'image';
    byId('caseChartView').hidden = view !== 'chart';
    byId('caseViewKicker').textContent = view === 'image' ? 'HISTORICAL IMAGE' : 'EXERCISE GEOMETRY';
    byId('caseViewTitle').textContent = view === 'image' ? 'The actual spill, before and after' : 'One fixed wreck. Two moving exercise ships.';
    byId('caseViewNote').textContent = view === 'image'
      ? 'European Union / Copernicus Sentinel-2 image. 1 and 6 August 2020; image credit remains embedded.'
      : 'Latitude/longitude chart only. Synthetic tracks and drift are not drawn over, or extracted from, the satellite pixels.';
    viewButtons.forEach(button => {
      const active = button.dataset.view === view;
      button.classList.toggle('active',active);
      button.setAttribute('aria-pressed',String(active));
    });
  }

  function render() {
    const result = currentResult();
    const leader = result.candidates[0];
    const second = result.candidates[1];
    const stages = [
      {
        title:'A spill with a documented source',
        body:'The real MV Wakashio had grounded off southeast Mauritius before oil was imaged. This exercise asks whether a comparison against two plausible, fictional offshore routes still places that known source first.',
        facts:[['Grounded vessel','MV Wakashio'],['Wreck position','20.4433°S, 57.7433°E'],['Comparison traffic','2 synthetic routes'],['Case type','Known-source exercise']]
      },
      {
        title:'See the real oil-spill image',
        body:'Copernicus Sentinel-2 shows the same reef on 1 August before the visible spill and on 6 August after it appeared. This is an external observation, not a new ESPADA SAR detection or a pixel-level model result.',
        facts:[['Sensor','Sentinel-2 optical'],['Before image','1 Aug 2020'],['Spill image','6 Aug 2020'],['Detection input','External image']]
      },
      {
        title:'Reverse an explicit drift hypothesis',
        body:'For this exercise, a 1.5-hour release age, stated current/wind vectors and a small synthetic observation offset produce an illustrative oil point. Reversing the forcing estimates an origin—not a pixel-derived measurement. Change the age below to test sensitivity.',
        facts:[['Satellite time',utc(caseData.observation)],['Assumed release',utc(result.releaseTime)],['Estimated origin',`${Math.abs(result.origin.latitude).toFixed(4)}°S, ${result.origin.longitude.toFixed(4)}°E`],['Distance to wreck',km(core.distanceKm(result.origin,caseData.documentedSource.position))]]
      },
      {
        title:'Compare space, time and routes',
        body:'The grounded vessel stays at its documented position; both other ships follow offshore exercise routes at plausible low coastal speeds. Each score is recomputed from distance to the modelled release origin, not pasted into the page.',
        facts:[['Compared ships',String(result.candidates.length)],['Release window',utc(result.releaseTime)],['Best match',leader.name],['Runner-up',`${second.name} · ${km(second.distanceKm)}`]]
      },
      {
        title:'One source prioritized, no automatic accusation',
        body:'MV Wakashio is the documented source and ranks first in this controlled comparison. The image itself already identifies the casualty, so this is a known-source workflow check—not blind discovery, proof of guilt, or a measured accuracy rate.',
        facts:[['Top-ranked',leader.name],['Exercise score',`${leader.exerciseScore}/100`],['Second-ranked',second.name],['Operator decision','Priority analyst review']]
      }
    ];
    const current = stages[stage];
    byId('caseStageKicker').textContent = `${String(stage+1).padStart(2,'0')} / 05 · ${stageNames[stage].toUpperCase()}`;
    byId('caseStageTitle').textContent = current.title;
    byId('caseStageBody').textContent = current.body;
    renderFacts(current.facts);
    renderRanking(result);
    byId('caseAgeControl').hidden = stage < 2;
    byId('caseAgeLabel').textContent = `${result.ageHours.toFixed(1)} h`;
    byId('caseRanking').hidden = stage < 3;
    byId('caseDecision').hidden = stage !== 4;
    byId('caseDecision').innerHTML = `<b>KNOWN SOURCE RECOVERED · EXERCISE ONLY</b><strong>${leader.name} ranks #1 of ${result.candidates.length}</strong><p>The official case identifies MV Wakashio. ESPADA's training calculation prioritizes review; it does not issue an enforcement finding.</p>`;
    byId('caseBack').disabled = stage === 0;
    byId('caseNext').disabled = stage === stages.length - 1;
    byId('caseNext').textContent = stage === stages.length - 1 ? 'CASE COMPLETE' : `NEXT: ${stageNames[stage+1].toUpperCase()} →`;
    stageButtons.forEach((button,index) => {
      const active = index === stage;
      button.classList.toggle('active',active);
      button.classList.toggle('complete',index < stage);
      button.setAttribute('aria-current',active ? 'step' : 'false');
    });
    renderChart(result);
  }

  function setStage(index) {
    stopPlayback();
    stage = Math.max(0,Math.min(4,index));
    setView(stage <= 1 ? 'image' : 'chart');
    render();
  }
  stageButtons.forEach(button => button.addEventListener('click',() => setStage(Number(button.dataset.caseStage))));
  viewButtons.forEach(button => button.addEventListener('click',() => setView(button.dataset.view)));
  byId('caseBack').addEventListener('click',() => setStage(stage-1));
  byId('caseNext').addEventListener('click',() => setStage(stage+1));
  byId('caseAge').addEventListener('input',render);
  byId('caseClock').addEventListener('input',() => renderChart(currentResult()));
  byId('caseRouteLedger').innerHTML = caseData.exerciseVessels.map(vessel => {
    const waypoints = vessel.waypoints.map(point => `${utc(point.time)} ${Math.abs(point.latitude).toFixed(3)}°S, ${point.longitude.toFixed(3)}°E`).join(' → ');
    const speeds = vessel.waypoints.slice(1).map((point,index) => legSpeedKnots(vessel.waypoints[index],point).toFixed(1)).join(' / ');
    return `<p><b>${vessel.name} · ${vessel.kind}</b><br>${waypoints}<br>Leg speeds: ${speeds} kn. Intervening positions are synthetic linear interpolation.</p>`;
  }).join('');
  function stopPlayback() {
    if (playback) window.clearInterval(playback);
    playback = null;
    byId('casePlay').textContent = 'Play routes ▶';
  }
  byId('casePlay').addEventListener('click',() => {
    if (playback) { stopPlayback(); return; }
    if (Number(byId('caseClock').value) >= 14) byId('caseClock').value = '0';
    byId('casePlay').textContent = 'Pause routes ▌▌';
    playback = window.setInterval(() => {
      const next = Number(byId('caseClock').value) + 1;
      byId('caseClock').value = String(Math.min(14,next));
      renderChart(currentResult());
      if (next >= 14) stopPlayback();
    }, 550);
  });
  setStage(0);
})();
