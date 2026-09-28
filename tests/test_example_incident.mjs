import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const script = fs.readFileSync(new URL('../operator/live_command/example_incident.js', import.meta.url), 'utf8');
const values = new Map();
const sessionStorage = {
  getItem: key => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value))
};
let providerRequests = 0;
const window = {};
vm.runInNewContext(script, {
  window,
  sessionStorage,
  URL:{createObjectURL:()=> 'blob:exercise-dossier',revokeObjectURL:()=>{}},
  Blob,
  fetch:()=>{ providerRequests++; throw new Error('Exercise attempted a provider request'); }
});
const exercise = window.espadaExample;
assert.equal(exercise.enabled(), false);
exercise.setMode(true);
assert.equal(exercise.enabled(), true);

let snap = exercise.snapshot();
assert.equal(snap.exercise, true);
assert.equal(snap.analysis.status, 'NOT_RUN');
assert.equal(snap.sources.sentinel.scenes.length, 1);
assert.ok(snap.ais.positions.every(item => item.source === 'SYNTHETIC EXERCISE'));
for (const position of snap.ais.positions) {
  const track=snap.ais.tracks[position.mmsi];
  assert.equal(track.length,5);
  const hourlyKm=Math.hypot((track[1][0]-track[0][0])*111.3,(track[1][1]-track[0][1])*111.3);
  assert.ok(Math.abs(hourlyKm/1.852-position.sog)<.15,`${position.vessel_name} speed should match its track`);
}
assert.match(snap.coastline_url,/example_coast\.geojson$/);

exercise.analyze();
snap = exercise.snapshot();
assert.equal(snap.analysis.status, 'REVIEW_REQUIRED');
assert.equal(snap.analysis.physics_screen.contrast_gate_passed, true);
assert.equal(snap.case_register.case_count, 1);

exercise.review('APPROVE', 4);
exercise.reconstruct();
snap = exercise.snapshot();
assert.equal(snap.review.status, 'APPROVED');
assert.equal(snap.attribution.status, 'COMPLETE');
const fourHourOrigin=snap.attribution.estimated_origin;
assert.equal(snap.attribution.candidates[0].mmsi, '999000101');
assert.equal(snap.attribution.ais_filter.raw_vessels, 6);
assert.equal(snap.attribution.ais_filter.retained_vessels, snap.attribution.candidate_count);
assert.ok(snap.attribution.origin_particles.length > 100);
assert.ok(snap.attribution.forward_closure.centroid_error_km < .01);
assert.equal(snap.attribution.decision, 'LIMITED_SHORTLIST', JSON.stringify({gates:snap.attribution.nomination_assessment.gates,candidates:snap.attribution.candidates.map(({rank,mmsi,total_score,forward_error_km})=>({rank,mmsi,total_score,forward_error_km}))}));

exercise.packageCase();
snap = exercise.snapshot();
assert.equal(snap.response.status, 'READY');
assert.equal(snap.response.dossier_url, 'blob:exercise-dossier');
assert.equal(snap.case_register.cases[0].stage, 'EVIDENCE_PACKAGE_READY');
assert.equal(providerRequests, 0);

exercise.reset();
assert.equal(exercise.snapshot().analysis.status, 'NOT_RUN');
exercise.analyze();
exercise.review('APPROVE',1);
exercise.reconstruct();
snap=exercise.snapshot();
assert.equal(snap.attribution.assumed_age_hours,1);
assert.notDeepEqual(snap.attribution.estimated_origin,fourHourOrigin,'Changing slick age must recompute the origin');
assert.equal(snap.attribution.candidates_compared,6);
exercise.setMode(false);
assert.equal(exercise.enabled(), false);
console.log('Example incident: mode isolation and end-to-end stages passed');
