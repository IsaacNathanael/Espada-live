(() => {
  'use strict';
  const stages = [
    {
      kicker:'STAGE 01 · BRIEFING', title:'Observe before inferring',
      body:'Four exercise vessels cross the monitored offshore sector. Identity, position and motion are available, but no spill evidence exists yet.',
      time:'01:30 UTC', note:'Traffic baseline before the simulated release', next:'NEXT: DETECT →',
      metrics:[['Scenario','KESTREL-01'],['Exercise vessels','4'],['Known release','Sealed'],['Evidence state','Traffic only']]
    },
    {
      kicker:'STAGE 02 · DETECT', title:'A dark SAR feature appears',
      body:'Sentinel-1 imagery is segmented, screened for broad physical plausibility and passed to an analyst. The result is an oil candidate—not automatic proof of oil.',
      time:'06:00 UTC', note:'Synthetic Sentinel-1 observation', next:'NEXT: RECONSTRUCT →',
      metrics:[['Candidate area','3.8 km²'],['Retained regions','1'],['Local contrast','−3.1 dB'],['Analyst gate','Approved for exercise']]
    },
    {
      kicker:'STAGE 03 · RECONSTRUCT', title:'Trace uncertainty backward',
      body:'An ensemble propagates the approved slick backward through time-matched current and wind fields. ESPADA returns a probable release region, not a single magical point.',
      time:'02:10 UTC', note:'Most-supported release time', next:'NEXT: CORRELATE →',
      metrics:[['Backward interval','3 h 50 min'],['Ensemble particles','2,000'],['90% origin radius','1.7 km'],['Forward closure error','1.3 km']]
    },
    {
      kicker:'STAGE 04 · CORRELATE', title:'Filter traffic, then rank evidence',
      body:'Only tracks overlapping the release window and origin region survive. Direction, speed and AIS continuity explain each track; silence never adds suspicion.',
      time:'02:10 UTC', note:'AIS tracks at inferred release time', next:'NEXT: DECIDE →', ranking:true,
      metrics:[['Raw tracks','14'],['Time-overlap tracks','8'],['Origin-area tracks','4'],['Ranked candidates','4']]
    },
    {
      kicker:'STAGE 05 · DECIDE', title:'Known source recovered—without overclaiming',
      body:'The ranking is frozen before the synthetic truth is unsealed. MERIDIAN-7 is recovered at rank #1. In operations this would authorize review, never an automatic accusation.',
      time:'RUN COMPLETE', note:'Ground truth revealed after ranking', next:'RUN COMPLETE', ranking:true, verdict:true,
      metrics:[['Known-source rank','#1 of 4'],['Comparative score','87%'],['Runner-up margin','39 points'],['System output','LIMITED SHORTLIST']]
    }
  ];
  let stage = 0;
  const byId = id => document.getElementById(id);
  const buttons = [...document.querySelectorAll('.demo-steps button')];
  function render() {
    const data = stages[stage];
    byId('demoConsole').dataset.stage = String(stage);
    byId('stageKicker').textContent = data.kicker;
    byId('stageTitle').textContent = data.title;
    byId('stageBody').textContent = data.body;
    byId('demoTime').textContent = data.time;
    byId('demoTimeNote').textContent = data.note;
    byId('stageMetrics').innerHTML = data.metrics.map(([term,value]) => `<div><dt>${term}</dt><dd>${value}</dd></div>`).join('');
    byId('demoRanking').hidden = !data.ranking;
    byId('demoVerdict').hidden = !data.verdict;
    byId('truthSeal').textContent = data.verdict ? 'GROUND TRUTH REVEALED' : 'GROUND TRUTH SEALED';
    byId('truthSeal').classList.toggle('open', Boolean(data.verdict));
    byId('previousStage').disabled = stage === 0;
    byId('nextStage').disabled = stage === stages.length - 1;
    byId('nextStage').textContent = data.next;
    buttons.forEach((button,index) => {
      button.classList.toggle('active', index === stage);
      button.classList.toggle('complete', index < stage);
      button.setAttribute('aria-current', index === stage ? 'step' : 'false');
    });
  }
  function setStage(value) {
    stage = Math.max(0, Math.min(stages.length - 1, Number(value)));
    byId('demoIntro').classList.add('started');
    render();
  }
  byId('startDemo').addEventListener('click', () => setStage(1));
  byId('previousStage').addEventListener('click', () => setStage(stage - 1));
  byId('nextStage').addEventListener('click', () => setStage(stage + 1));
  byId('restartDemo').addEventListener('click', () => { stage = 0; byId('demoIntro').classList.remove('started'); render(); window.scrollTo({top:0,behavior:'smooth'}); });
  buttons.forEach(button => button.addEventListener('click', () => setStage(button.dataset.stage)));
  render();
})();
