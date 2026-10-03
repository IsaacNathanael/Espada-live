/* A small, deterministic training exercise. Its traffic and forcing are not historical observations. */
(() => {
  'use strict';

  const caseData = Object.freeze({
    observation: '2020-08-06T06:24:49Z',
    knownAgeHours: 1.5,
    documentedSource: {
      name: 'MV Wakashio',
      kind: 'Documented grounded vessel',
      position: {longitude: 57.743333, latitude: -20.443333},
      source: 'Mauritius Court of Investigation report'
    },
    // The following vectors are exercise inputs, not measurements for this scene.
    forcing: {
      currentEastMs: -0.05, currentNorthMs: 0.06,
      windEastMs: -6.5, windNorthMs: 3.2, windage: 0.02
    },
    // Small synthetic observation displacement prevents a mathematically perfect fit.
    observationOffsetMeters: {east: -220, north: -270},
    // Named exercise vessels are fictional. All waypoints are synthetic and offshore.
    exerciseVessels: [
      {
        name: 'MV Aster Point', kind: 'Synthetic bulk carrier',
        waypoints: [
          {time: '2020-08-06T03:00:00Z', longitude: 57.94, latitude: -20.58},
          {time: '2020-08-06T04:30:00Z', longitude: 57.79, latitude: -20.47},
          {time: '2020-08-06T06:30:00Z', longitude: 57.80, latitude: -20.27}
        ]
      },
      {
        name: 'MV Coral Trader', kind: 'Synthetic cargo vessel',
        waypoints: [
          {time: '2020-08-06T03:00:00Z', longitude: 57.81, latitude: -20.24},
          {time: '2020-08-06T04:30:00Z', longitude: 57.90, latitude: -20.40},
          {time: '2020-08-06T06:30:00Z', longitude: 58.02, latitude: -20.59}
        ]
      }
    ]
  });

  const radians = degrees => degrees * Math.PI / 180;
  const hoursBetween = (a, b) => (Date.parse(b) - Date.parse(a)) / 3_600_000;
  function distanceKm(a, b) {
    const deltaLat = radians(b.latitude - a.latitude);
    const deltaLon = radians(b.longitude - a.longitude);
    const h = Math.sin(deltaLat / 2) ** 2 + Math.cos(radians(a.latitude)) *
      Math.cos(radians(b.latitude)) * Math.sin(deltaLon / 2) ** 2;
    return 6371 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
  }
  function advect(point, hours) {
    const seconds = hours * 3600;
    const east = caseData.forcing.currentEastMs + caseData.forcing.windEastMs * caseData.forcing.windage;
    const north = caseData.forcing.currentNorthMs + caseData.forcing.windNorthMs * caseData.forcing.windage;
    return {
      longitude: point.longitude + east * seconds / (111_320 * Math.cos(radians(point.latitude))),
      latitude: point.latitude + north * seconds / 111_320
    };
  }
  function positionAt(vessel, time) {
    const moment = Date.parse(time);
    const points = vessel.waypoints;
    if (moment < Date.parse(points[0].time) || moment > Date.parse(points.at(-1).time)) return null;
    for (let index = 0; index < points.length - 1; index += 1) {
      const a = points[index];
      const b = points[index + 1];
      const start = Date.parse(a.time);
      const end = Date.parse(b.time);
      if (moment >= start && moment <= end) {
        const share = (moment - start) / (end - start);
        return {
          longitude: a.longitude + share * (b.longitude - a.longitude),
          latitude: a.latitude + share * (b.latitude - a.latitude)
        };
      }
    }
    return null;
  }
  function legSpeedKnots(a, b) {
    return distanceKm(a, b) / hoursBetween(a.time, b.time) / 1.852;
  }

  const unperturbedObservation = advect(caseData.documentedSource.position, caseData.knownAgeHours);
  const illustratedObservation = {
    longitude: unperturbedObservation.longitude + caseData.observationOffsetMeters.east /
      (111_320 * Math.cos(radians(unperturbedObservation.latitude))),
    latitude: unperturbedObservation.latitude + caseData.observationOffsetMeters.north / 111_320
  };
  function evaluate(ageHours = caseData.knownAgeHours) {
    const age = Number(ageHours);
    if (!Number.isFinite(age) || age < 1 || age > 3) throw new RangeError('Exercise age must be 1–3 hours');
    const releaseTime = new Date(Date.parse(caseData.observation) - age * 3_600_000).toISOString();
    const origin = advect(illustratedObservation, -age);
    const candidates = [
      {name: caseData.documentedSource.name, kind: caseData.documentedSource.kind,
        position: caseData.documentedSource.position, evidence: 'Official fixed grounding position'},
      ...caseData.exerciseVessels.map(vessel => ({
        name: vessel.name, kind: vessel.kind,
        position: positionAt(vessel, releaseTime), evidence: 'Synthetic interpolated exercise route'
      }))
    ].map(candidate => {
      const distance = candidate.position ? distanceKm(candidate.position, origin) : Infinity;
      const forwardError = candidate.position ? distanceKm(advect(candidate.position, age), illustratedObservation) : Infinity;
      return {...candidate, distanceKm: distance, forwardErrorKm: forwardError,
        exerciseScore: Number.isFinite(distance) ? Math.round(100 / (1 + (distance / 3) ** 2)) : 0};
    }).sort((a, b) => b.exerciseScore - a.exerciseScore || a.distanceKm - b.distanceKm);
    return {ageHours: age, releaseTime, origin, illustratedObservation, candidates};
  }

  const core = Object.freeze({caseData, distanceKm, advect, positionAt, legSpeedKnots, evaluate});
  if (typeof module !== 'undefined' && module.exports) module.exports = core;
  if (typeof window !== 'undefined') window.ESPADA_WAKASHIO_EXERCISE = core;
})();
