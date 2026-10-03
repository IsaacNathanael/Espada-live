const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const project = path.resolve(__dirname, '..');
const site = path.join(project, 'operator', 'live_command');
const exercise = require(path.join(site, 'wakashio-core.js'));

test('the image is the original Copernicus 1/6 August comparison', () => {
  const image = fs.readFileSync(path.join(site, 'assets', 'wakashio_sentinel2_20200806.jpg'));
  assert.equal(crypto.createHash('sha1').update(image).digest('hex'),
    'f3defe8ecf0a009ddf8927a1d40dbcb6bf1e019e');
  const html = fs.readFileSync(path.join(site, 'wakashio.html'), 'utf8');
  assert.match(html, /Sentinel-2/);
  assert.match(html, /synthetic comparison traffic/);
  assert.doesNotMatch(html, /Sentinel-1 SAR/);
});

test('geographic exercise routes stay plausible and do not invent AIS', () => {
  for (const vessel of exercise.caseData.exerciseVessels) {
    assert.match(vessel.kind, /Synthetic/);
    for (let index = 1; index < vessel.waypoints.length; index += 1) {
      const speed = exercise.legSpeedKnots(vessel.waypoints[index-1], vessel.waypoints[index]);
      assert.ok(speed >= 4 && speed <= 12, `${vessel.name}: ${speed.toFixed(1)} kn`);
      assert.ok(vessel.waypoints[index].longitude >= 57.78, 'exercise traffic stays east of the wreck/reef');
    }
  }
});

test('at the known age the reverse solution meets the documented source', () => {
  const result = exercise.evaluate(1.5);
  assert.equal(result.releaseTime, '2020-08-06T04:54:49.000Z');
  assert.ok(exercise.distanceKm(result.origin, exercise.caseData.documentedSource.position) < 0.6);
  assert.equal(result.candidates[0].name, 'MV Wakashio');
  assert.ok(result.candidates[0].forwardErrorKm < 0.6);
});

test('the source remains the priority across the displayed age sensitivity', () => {
  for (const age of [1, 1.5, 2, 2.5, 3]) {
    const result = exercise.evaluate(age);
    assert.equal(result.candidates[0].name, 'MV Wakashio');
    assert.ok(result.candidates[0].distanceKm < result.candidates[1].distanceKm);
  }
  assert.throws(() => exercise.evaluate(4), RangeError);
});

test('the linked recorded case and original demo remain untouched', () => {
  const original = fs.readFileSync(path.join(site, 'demo.html'), 'utf8');
  assert.match(original, /demo\.js/);
  assert.match(original, /wakashio\.html/);
  assert.ok(fs.existsSync(path.join(project, 'operator', 'historical_case', 'index.html')));
});
