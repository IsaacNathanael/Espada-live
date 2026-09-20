(() => {
  'use strict';

  const POLL_MS = 15000;
  const SNAPSHOT_ENDPOINT = '/api/live/snapshot';
  const REFRESH_ENDPOINT = '/api/live/refresh';
  const byId = id => document.getElementById(id);
  let lastSnapshot = null;

  const html = (id, value) => { byId(id).textContent = value; };
  const parseTime = value => {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const formatUtc = value => {
    const date = parseTime(value);
    return date ? date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC' : '—';
  };
  const ageSeconds = value => {
    const date = parseTime(value);
    return date ? Math.max(0, (Date.now() - date.getTime()) / 1000) : null;
  };
  const ageLabel = value => {
    const seconds = ageSeconds(value);
    if (seconds === null) return 'No timestamp';
    if (seconds < 60) return `${Math.floor(seconds)} sec ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${(seconds / 3600).toFixed(seconds < 10800 ? 1 : 0)} hr ago`;
    return `${(seconds / 86400).toFixed(seconds < 259200 ? 1 : 0)} days ago`;
  };
  const compactNumber = value => Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-IN').format(Number(value)) : '—';
  const finite = value => Number.isFinite(Number(value));
  const cardinal = (east, north) => {
    if (!finite(east) || !finite(north)) return 'Direction unavailable';
    const degrees = (Math.atan2(Number(east), Number(north)) * 180 / Math.PI + 360) % 360;
    const labels = ['N','NE','E','SE','S','SW','W','NW'];
    return `${labels[Math.round(degrees / 45) % 8]} · ${Math.round(degrees)}°`;
  };
  const normalizeState = status => {
    const value = String(status || 'WAITING').toUpperCase();
    if (value === 'PASS') return 'pass';
    if (['CONNECTING','SEARCHING','REFRESHING','RUNNING'].includes(value)) return 'active';
    if (value === 'STALE') return 'stale';
    if (['NO_DATA','NOT_CONFIGURED','WAITING'].includes(value)) return 'missing';
    if (['ERROR','FAIL','INTERRUPTED'].includes(value)) return 'error';
    return 'waiting';
  };
  const setCardState = (prefix, source) => {
    const status = String(source?.status || 'WAITING').toUpperCase();
    byId(`${prefix}Card`).dataset.state = normalizeState(status);
    html(`${prefix}Status`, status.replaceAll('_', ' '));
    html(`${prefix}Provider`, source?.provider || source?.source || 'Provider unavailable');
    const rawMessage = String(source?.message || 'No provider message received.');
    const displayMessage = /refresh failed/i.test(rawMessage)
      ? 'Refresh failed; displaying the last verified provider response.'
      : /(traceback|urlerror|winerror|gaierror|runtimeerror)/i.test(rawMessage)
        ? 'The provider request failed. Technical details are preserved in the server record.'
        : rawMessage;
    html(`${prefix}Message`, displayMessage);
  };

  function renderHeader(snapshot) {
    const region = snapshot.region || {};
    const bbox = Array.isArray(region.bbox) ? region.bbox : [];
    const center = Array.isArray(region.center) ? region.center : [];
    html('regionName', region.name || 'Unnamed watch region');
    html('regionBounds', bbox.length === 4 ? `${bbox[0].toFixed(2)}°–${bbox[2].toFixed(2)}° E · ${bbox[1].toFixed(2)}°–${bbox[3].toFixed(2)}° N` : 'Bounds unavailable');
    html('regionCenter', center.length === 2 ? `${center[0].toFixed(4)}°, ${center[1].toFixed(4)}°` : '—');
    const sourceTimes = Object.values(snapshot.sources || {}).flatMap(source => [source?.last_attempt_utc, source?.last_success_utc]).map(parseTime).filter(Boolean);
    const latestSourceTime = sourceTimes.length ? new Date(Math.max(...sourceTimes.map(date => date.getTime()))).toISOString() : null;
    const snapshotTime = snapshot.updated_at_utc || latestSourceTime;
    snapshot._display_snapshot_time = snapshotTime;
    html('snapshotAge', ageLabel(snapshotTime));
    html('snapshotTime', formatUtc(snapshotTime));
  }

  function renderAis(source = {}) {
    setCardState('ais', source);
    html('aisPositions', compactNumber(source.cached_positions));
    html('aisVessels', compactNumber(source.cached_vessels));
    html('aisObservation', source.latest_observation_utc ? `${ageLabel(source.latest_observation_utc)} · ${formatUtc(source.latest_observation_utc)}` : 'No position received');
    html('aisSuccess', source.last_success_utc ? `${ageLabel(source.last_success_utc)} · ${formatUtc(source.last_success_utc)}` : 'No successful connection');
  }

  function renderSentinel(source = {}) {
    setCardState('sentinel', source);
    const scene = source.latest_scene || {};
    html('sentinelScenes', compactNumber(source.scenes_returned));
    html('sentinelOverlap', finite(scene.aoi_overlap_fraction) ? `${(Number(scene.aoi_overlap_fraction) * 100).toFixed(1)}%` : '—');
    html('sentinelObservation', source.latest_observation_utc ? `${ageLabel(source.latest_observation_utc)} · ${formatUtc(source.latest_observation_utc)}` : 'No acquisition found');
    const platform = scene.platform ? String(scene.platform).toUpperCase() : '—';
    const orbit = scene.orbit_state ? `${scene.orbit_state} orbit` : 'orbit unavailable';
    html('sentinelPlatform', `${platform} · ${orbit}`);
  }

  function renderEnvironment(source = {}) {
    setCardState('environment', source);
    const current = source.current || {};
    html('currentSpeed', finite(current.current_speed_ms) ? `${Number(current.current_speed_ms).toFixed(2)} m/s` : '—');
    html('windSpeed', finite(current.wind_speed_ms) ? `${Number(current.wind_speed_ms).toFixed(2)} m/s` : '—');
    html('currentDirection', cardinal(current.current_east_ms, current.current_north_ms));
    html('windDirection', cardinal(current.wind_east_ms, current.wind_north_ms));
    html('environmentObservation', source.latest_observation_utc ? `${ageLabel(source.latest_observation_utc)} · ${formatUtc(source.latest_observation_utc)}` : 'No model time received');
    html('environmentResolution', source.temporal_resolution || '—');
  }

  function renderIntegrity(snapshot) {
    const sources = snapshot.sources || {};
    const entries = ['ais','sentinel','environment'].map(name => sources[name] || {});
    const states = entries.map(source => normalizeState(source.status));
    const current = states.filter(state => state === 'pass').length;
    const errors = states.filter(state => state === 'error').length;
    const stale = states.filter(state => state === 'stale').length;
    const missing = states.filter(state => state === 'missing').length;
    const active = states.filter(state => state === 'active').length;
    html('sourceCount', `${current} / 3 current`);
    const notes = [];
    if (errors) notes.push(`${errors} error${errors === 1 ? '' : 's'}`);
    if (stale) notes.push(`${stale} stale`);
    if (missing) notes.push(`${missing} without data`);
    if (active) notes.push(`${active} updating`);
    html('sourceSummary', notes.length ? notes.join(' · ') : 'All provider evidence is current');

    const connection = byId('connectionState');
    if (errors || stale || missing) {
      connection.dataset.tone = 'degraded';
      connection.querySelector('b').textContent = 'LIVE · DEGRADED';
      connection.querySelector('small').textContent = stale ? 'Some evidence is stale' : 'Some evidence is unavailable';
      html('integrityTitle', 'Live service connected with evidence gaps');
      html('integrityMessage', 'Unavailable sources remain explicit. Downstream analysis must respect their timestamps and coverage.');
    } else if (active) {
      connection.dataset.tone = 'live';
      connection.querySelector('b').textContent = 'LIVE · REFRESHING';
      connection.querySelector('small').textContent = 'Provider requests are active';
      html('integrityTitle', 'Live service connected; providers are refreshing');
      html('integrityMessage', 'Existing evidence remains timestamped while fresh provider responses are collected.');
    } else {
      connection.dataset.tone = 'live';
      connection.querySelector('b').textContent = 'LIVE · CONNECTED';
      connection.querySelector('small').textContent = 'All provider states received';
      html('integrityTitle', 'Provider state received and timestamped');
      html('integrityMessage', 'The next components can use these feeds while preserving their different evidence meanings.');
    }
  }

  function render(snapshot) {
    lastSnapshot = snapshot;
    renderHeader(snapshot);
    renderAis(snapshot.sources?.ais);
    renderSentinel(snapshot.sources?.sentinel);
    renderEnvironment(snapshot.sources?.environment);
    renderIntegrity(snapshot);
    html('pollState', `Last API response ${new Date().toISOString().replace('T',' ').slice(0,19)} UTC`);
  }

  function renderOffline(error) {
    const connection = byId('connectionState');
    connection.dataset.tone = 'offline';
    connection.querySelector('b').textContent = 'ENGINE OFFLINE';
    connection.querySelector('small').textContent = 'Live API did not respond';
    html('integrityTitle', 'Live engine is unreachable');
    html('integrityMessage', 'Start the ESPADA live operations service. This page will reconnect automatically without displaying saved data as live.');
    html('pollState', `API unavailable · ${error?.message || 'connection failed'}`);
  }

  async function poll() {
    try {
      const response = await fetch(`${SNAPSHOT_ENDPOINT}?t=${Date.now()}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      renderOffline(error);
    }
  }

  async function requestRefresh() {
    const button = byId('refreshButton');
    button.disabled = true;
    html('refreshNote', 'Refresh requested…');
    try {
      const response = await fetch(REFRESH_ENDPOINT, {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      html('refreshNote', result.message || 'Provider refresh queued');
      window.setTimeout(poll, 1200);
    } catch (error) {
      html('refreshNote', `Refresh failed: ${error.message}`);
    } finally {
      window.setTimeout(() => { button.disabled = false; }, 1200);
    }
  }

  function tickClock() {
    const now = new Date();
    html('utcClock', now.toISOString().slice(11,19) + ' UTC');
    html('calendarDate', now.toISOString().slice(0,10));
    if (lastSnapshot) html('snapshotAge', ageLabel(lastSnapshot._display_snapshot_time));
  }

  byId('refreshButton').addEventListener('click', requestRefresh);
  tickClock();
  poll();
  window.setInterval(tickClock, 1000);
  window.setInterval(poll, POLL_MS);
})();
