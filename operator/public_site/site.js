(() => {
  const data = window.ESPADA_SITE_DATA || {};
  const score = data.scorecard || {};
  const twin = data.digitalTwin || {};
  const known = score.known_source_case || {};
  const abstain = score.abstention_case || {};
  const robust = score.synthetic_robustness || {};
  const live = data.liveCase || {};
  const fmtPct = value => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : '—';
  const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  const set = (name, value) => document.querySelectorAll(`[data-bind="${name}"]`).forEach(node => node.textContent = value);
  set('top1', fmtPct(robust.top_1_accuracy));
  set('top3', fmtPct(robust.top_3_accuracy));
  set('originMedian', `${fmt(robust.median_origin_error_km)} km`);
  set('knownRank', `#${known.documented_source_rank || '—'} / ${known.candidate_count || '—'}`);
  set('knownShape', `${fmt(known.shape_error_km)} km`);
  set('abstainCount', String(abstain.candidate_count || '—'));
  set('liveCandidates', String(live.candidate_count || '—'));
  set('liveMargin', `${fmt(Number(live.score_margin || 0) * 100, 2)} points`);
  set('chainDigest', data.integrity?.chain_digest_sha256 || '—');
  set('verifiedFiles', `${data.integrity?.matched_files || data.integrity?.verified_files || '—'} / ${data.integrity?.artifact_count || 16}`);

  const lab = document.getElementById('stressLab');
  if (lab) {
    const cases = Array.isArray(data.digitalTwinCases) ? data.digitalTwinCases : [];
    const inputs = [...lab.querySelectorAll('input[type=range]')];
    const profiles = {
      nominal: 'nominal', dropout: 'source dropout', noise: 'position noise',
      wind: 'windage mismatch', diffusion: 'diffusion mismatch', combined: 'combined stress'
    };
    const choose = values => {
      if (values.dropout >= 45 && (values.noise >= 45 || values.wind >= 45)) return profiles.combined;
      if (values.dropout >= 45) return profiles.dropout;
      if (values.noise >= 45) return profiles.noise;
      if (values.wind >= 45) return profiles.wind;
      if (values.diffusion >= 45) return profiles.diffusion;
      return profiles.nominal;
    };
    const renderChart = selected => {
      const svg = document.getElementById('trialChart');
      if (!svg || !cases.length) return;
      const width = 720, height = 230, base = 190, left = 48, step = 102, maxError = Math.max(8, ...cases.map(item => Number(item.origin_error_km || 0)));
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      const marks = cases.map((item, index) => {
        const x = left + index * step;
        const error = Number(item.origin_error_km || 0);
        const h = Math.max(3, error / maxError * 135);
        const cls = item.condition === selected ? 'bar selected' : 'bar';
        return `<rect class="${cls}" x="${x}" y="${base-h}" width="54" height="${h}"><title>${item.condition}: ${error.toFixed(2)} km origin error</title></rect><text x="${x+27}" y="${base+18}" text-anchor="middle">T${index+1}</text><text x="${x+27}" y="${base-h-7}" text-anchor="middle">${error.toFixed(1)} km</text>`;
      }).join('');
      svg.innerHTML = `<title>Digital-twin origin error across six stress trials</title><line class="axis" x1="38" y1="${base}" x2="690" y2="${base}"/>${marks}`;
    };
    const update = () => {
      const values = Object.fromEntries(inputs.map(input => [input.name, Number(input.value)]));
      inputs.forEach(input => { const out = lab.querySelector(`[data-output="${input.name}"]`); if (out) out.textContent = `${input.value}%`; });
      const selected = choose(values);
      const trial = cases.find(item => item.condition === selected) || cases[0] || {};
      document.getElementById('profileName').textContent = String(selected || 'No trial').toUpperCase();
      document.getElementById('sourceRank').textContent = trial.source_rank ? `#${trial.source_rank} / ${trial.real_candidates}` : '—';
      document.getElementById('originError').textContent = trial.origin_error_km != null ? `${fmt(trial.origin_error_km)} km` : '—';
      document.getElementById('forwardError').textContent = trial.forward_error_km != null ? `${fmt(trial.forward_error_km)} km` : '—';
      document.getElementById('candidateScore').textContent = trial.comparative_score != null ? fmtPct(trial.comparative_score) : '—';
      const decision = document.getElementById('labDecision');
      const correct = Number(trial.source_rank) === 1;
      const safe = String(trial.unsafe_false_priority).toLowerCase() !== 'true';
      decision.className = `decision ${correct ? '' : safe ? 'warn' : 'stop'}`.trim();
      decision.textContent = correct ? 'KNOWN SOURCE RANKED #1' : safe ? 'SAFE · NO FALSE PRIORITY' : 'UNSAFE RESULT';
      renderChart(selected);
    };
    inputs.forEach(input => input.addEventListener('input', update));
    update();
  }

  const judge = document.getElementById('judgeMode');
  if (judge) {
    const steps = [
      {k:'01 · PROBLEM',t:'A slick is visible. The source is not.',p:'ESPADA closes the enforcement gap between satellite detection and defensible vessel attribution.',a:'investigate.html',m:['Sentinel-1 SAR','Historic AIS','Ocean + wind']},
      {k:'02 · METHOD',t:'Reverse the drift, preserve the uncertainty.',p:'A particle ensemble reconstructs a probable release zone and time instead of inventing one exact origin.',a:'investigate.html#investigation',m:['Probability field','Coast blocking','Forward replay']},
      {k:'03 · EVALUATOR TEST',t:'The documented source ranked first.',p:`MV Wakashio ranked #${known.documented_source_rank || 1} of ${known.candidate_count || 5}; identity stayed sealed until the ranking existed.`,a:'known_source_dossier.html',m:[`${fmt(known.shape_error_km)} km shape error`,`${fmt(known.centroid_error_km)} km centroid error`,'Pseudonymized rank']},
      {k:'04 · SAFETY',t:'A high score is not an accusation.',p:`Princess Empress compared ${abstain.candidate_count || 52} candidates and still abstained because the evidence quality failed.`,a:'abstention_dossier.html',m:[`${fmtPct(abstain.comparative_score)} fit`,`${fmtPct(abstain.data_quality)} track quality`,'ABSTAIN']},
      {k:'05 · PROOF',t:'One pipeline. Three kinds of evidence.',p:`${robust.cases || 24} controlled stress trials, one known-source reconstruction, and one evidence-limited abstention test the system from different directions.`,a:'evidence.html',m:[`${fmtPct(robust.top_3_accuracy)} Top-3`,`${fmt(robust.median_origin_error_km)} km median`,'Human gate']}
    ];
    let index = 0;
    const render = () => {
      const item = steps[index];
      document.getElementById('judgeStep').textContent = item.k;
      document.getElementById('judgeTitle').textContent = item.t;
      document.getElementById('judgeCopy').textContent = item.p;
      document.getElementById('judgeEvidence').href = item.a;
      document.getElementById('judgeMetrics').innerHTML = item.m.map((value,i)=>`<div><span>${['Evidence','Result','Decision'][i]}</span><b>${value}</b></div>`).join('');
      document.getElementById('judgeDots').innerHTML = steps.map((_,i)=>`<i class="${i<=index?'on':''}"></i>`).join('');
      document.getElementById('judgePrev').disabled = index === 0;
      document.getElementById('judgeNext').textContent = index === steps.length - 1 ? 'Restart' : 'Next';
    };
    document.getElementById('judgePrev').addEventListener('click',()=>{if(index>0)index--;render();});
    document.getElementById('judgeNext').addEventListener('click',()=>{index=index===steps.length-1?0:index+1;render();});
    render();
  }
})();
