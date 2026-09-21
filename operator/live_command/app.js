(() => {
  'use strict';

  const POLL_MS = 15000;
  const SNAPSHOT_ENDPOINT = '/api/live/snapshot';
  const REFRESH_ENDPOINT = '/api/live/refresh';
  const ANALYZE_ENDPOINT = '/api/live/analyze-latest-sar';
  const REVIEW_ENDPOINT = '/api/live/review';
  const ATTRIBUTION_ENDPOINT = '/api/live/build-attribution';
  const RESPONSE_ENDPOINT = '/api/live/build-response';
  const byId = id => document.getElementById(id);
  let lastSnapshot = null;
  let mapMode = 'live';
  let mapGeometry = {coast: null, footprints: null, slick: null, origin: null, candidateTracks: null};
  const geometryCache = new Map();
  let geometryLoadKey = '';
  let sarView = 'overview';
  let selectedSceneId = null;
  let driftView = 'comparison';
  let selectedCandidateMmsi = null;

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

  const escapeText = value => String(value ?? '—');
  const escapeMarkup = value => escapeText(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const evidenceTime = snapshot => mapMode === 'incident'
    ? snapshot?.analysis?.acquisition_time_utc
    : snapshot?.sources?.ais?.latest_observation_utc || snapshot?.generated_at_utc;
  const bboxPolygon = bbox => ({
    type: 'Feature',
    properties: {},
    geometry: {type: 'Polygon', coordinates: [[[bbox[0],bbox[1]],[bbox[0],bbox[3]],[bbox[2],bbox[3]],[bbox[2],bbox[1]],[bbox[0],bbox[1]]]]}
  });
  const layerVisible = name => byId('layerControls').querySelector(`[data-layer="${name}"]`)?.checked;
  const mapFeatureCount = value => value?.type === 'FeatureCollection' ? value.features?.length || 0 : value ? 1 : 0;
  const setDetail = ({type='MAP READOUT', title='Select evidence on the map', summary='Click a map feature to inspect its provenance.', fields=[], noteTitle='TIME INTEGRITY', note='Live and incident-time evidence remain separated.'}={}) => {
    html('detailType', type);
    html('detailTitle', title);
    html('detailSummary', summary);
    byId('detailFields').innerHTML = fields.map(([term,value]) => `<div><dt>${escapeText(term)}</dt><dd>${escapeText(value)}</dd></div>`).join('');
    byId('detailNote').innerHTML = `<b>${escapeText(noteTitle)}</b>${escapeText(note)}`;
  };

  function normalizeCoordinates(value) {
    if (Array.isArray(value)) {
      if (value.length >= 2 && !Array.isArray(value[0])) return value.map(Number);
      return value.map(normalizeCoordinates);
    }
    return value;
  }

  function normalizeGeojson(value) {
    if (!value || typeof value !== 'object') return value;
    const copy = JSON.parse(JSON.stringify(value));
    const normalizeGeometry = geometry => { if (geometry?.coordinates) geometry.coordinates = normalizeCoordinates(geometry.coordinates); };
    if (copy.type === 'FeatureCollection') copy.features.forEach(feature => normalizeGeometry(feature.geometry));
    else if (copy.type === 'Feature') normalizeGeometry(copy.geometry);
    else normalizeGeometry(copy);
    return copy;
  }

  async function loadGeometry(url) {
    if (!url) return null;
    if (geometryCache.has(url)) return geometryCache.get(url);
    const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`Geometry HTTP ${response.status}`);
    const geometry = normalizeGeojson(await response.json());
    geometryCache.set(url, geometry);
    return geometry;
  }

  async function syncMapGeometry(snapshot) {
    const urls = {
      coast: snapshot.coastline_url,
      footprints: snapshot.sources?.sentinel?.footprints_url,
      slick: snapshot.review?.status === 'APPROVED' ? snapshot.review?.approved_slick_url : null,
      origin: snapshot.attribution?.origin_zone_url,
      candidateTracks: snapshot.attribution?.candidate_tracks_url
    };
    const key = JSON.stringify(urls);
    if (key === geometryLoadKey) return;
    geometryLoadKey = key;
    byId('mapLoading').hidden = false;
    const entries = await Promise.all(Object.entries(urls).map(async ([name,url]) => {
      try { return [name, await loadGeometry(url)]; }
      catch (error) { return [name, null, error]; }
    }));
    const failures = [];
    entries.forEach(([name,geometry,error]) => {
      mapGeometry[name] = geometry;
      if (error) failures.push(name);
    });
    byId('mapLoading').hidden = true;
    if (failures.length) html('mapLayerStatus', `Unavailable geometry: ${failures.join(', ')}`);
    renderMap(snapshot);
    renderDriftMap(snapshot);
    renderCandidateMap(snapshot);
  }

  function appendMapTitle(selection, value) {
    selection.append('title').text(value);
    return selection;
  }

  function detailForVessel(vessel, trackPoints) {
    setDetail({
      type: 'OBSERVED · AIS POSITION',
      title: vessel.vessel_name && vessel.vessel_name !== 'UNKNOWN' ? vessel.vessel_name : `MMSI ${vessel.mmsi}`,
      summary: 'Latest provider-supplied position inside the active live reception window.',
      fields: [
        ['MMSI', vessel.mmsi],
        ['Received', formatUtc(vessel.timestamp_utc)],
        ['Speed over ground', finite(vessel.sog) ? `${Number(vessel.sog).toFixed(1)} kn` : 'Not supplied'],
        ['Course over ground', finite(vessel.cog) ? `${Math.round(Number(vessel.cog))}°` : 'Not supplied'],
        ['Track observations', trackPoints || 1],
        ['Source', vessel.source || 'AIS provider']
      ],
      noteTitle: 'OBSERVATION BOUNDARY',
      note: 'A received AIS position is evidence of a broadcast, not proof of identity, intent or conduct.'
    });
  }

  function renderMap(snapshot) {
    const svgElement = byId('evidenceMap');
    if (!svgElement) return;
    if (!window.d3) {
      byId('mapLoading').hidden = false;
      html('mapLoading', 'Map renderer unavailable — evidence data remains unchanged.');
      return;
    }
    const d3 = window.d3;
    const svg = d3.select(svgElement);
    svg.selectAll('*').remove();
    const bbox = snapshot.region?.bbox;
    if (!Array.isArray(bbox) || bbox.length !== 4) return;
    const width = 1100, height = 650;
    const boundsFeature = bboxPolygon(bbox.map(Number));
    const projection = d3.geoMercator().fitExtent([[30,30],[width-30,height-30]], boundsFeature);
    const path = d3.geoPath(projection);
    const defs = svg.append('defs');
    [['currentArrow','#17687c'],['windArrow','#9a6116']].forEach(([id,color]) => {
      defs.append('marker').attr('id',id).attr('viewBox','0 0 10 10').attr('refX',8).attr('refY',5).attr('markerWidth',6).attr('markerHeight',6).attr('orient','auto-start-reverse')
        .append('path').attr('d','M 0 0 L 10 5 L 0 10 z').attr('fill',color);
    });
    svg.append('rect').attr('class','ocean-field').attr('width',width).attr('height',height);
    const graticule = d3.geoGraticule().extent([[bbox[0],bbox[1]],[bbox[2],bbox[3]]]).step([0.1,0.1]);
    svg.append('path').datum(graticule()).attr('class','map-grid').attr('d',path);

    if (mapGeometry.coast) {
      svg.append('path').datum(mapGeometry.coast).attr('class','coast-halo').attr('d',path);
      svg.append('path').datum(mapGeometry.coast).attr('class','land-shape').attr('d',path);
    }
    svg.append('path').datum(boundsFeature).attr('class','region-border').attr('d',path);

    if (layerVisible('satellite')) {
      if (mapMode === 'live' && mapGeometry.footprints) {
        const features = mapGeometry.footprints.type === 'FeatureCollection' ? mapGeometry.footprints.features : [mapGeometry.footprints];
        svg.append('g').selectAll('path').data(features).join('path').attr('class','satellite-footprint').attr('tabindex',0).attr('d',path)
          .each(function(feature){ appendMapTitle(d3.select(this), `Sentinel-1 acquisition · ${formatUtc(feature.properties?.acquisition_time_utc)}`); })
          .on('click keydown', (event,feature) => {
            if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
            const properties = feature.properties || {};
            setDetail({type:'CATALOGUE · SENTINEL-1',title:'Radar acquisition footprint',summary:'Published catalogue coverage intersecting the active watch region.',fields:[['Acquired',formatUtc(properties.acquisition_time_utc)],['Orbit',properties.orbit_state || 'Not supplied'],['Polarisation',Array.isArray(properties.polarizations) ? properties.polarizations.join(' + ') : 'Not supplied'],['AOI overlap',finite(properties.aoi_overlap_fraction) ? `${(Number(properties.aoi_overlap_fraction)*100).toFixed(1)}%` : 'Not supplied']],noteTitle:'CATALOGUE BOUNDARY',note:'A footprint proves an acquisition exists; it does not prove an oil slick is present.'});
          });
      } else if (mapMode === 'incident' && Array.isArray(snapshot.analysis?.bbox)) {
        const incidentFootprint = bboxPolygon(snapshot.analysis.bbox.map(Number));
        svg.append('path').datum(incidentFootprint).attr('class','satellite-footprint').attr('tabindex',0).attr('d',path)
          .call(selection => appendMapTitle(selection, `Analysed Sentinel-1 scene · ${formatUtc(snapshot.analysis?.acquisition_time_utc)}`))
          .on('click keydown', event => {
            if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
            setDetail({type:'OBSERVED · SAR ACQUISITION',title:'Analysed radar scene',summary:'The Sentinel-1 image used for the approved oil-candidate review.',fields:[['Scene',snapshot.analysis?.scene_id || 'Not supplied'],['Acquired',formatUtc(snapshot.analysis?.acquisition_time_utc)],['Provider',snapshot.sources?.sentinel?.provider || 'Copernicus Data Space'],['Bounds',snapshot.analysis.bbox.map(Number).map(value=>value.toFixed(3)).join(', ')]],noteTitle:'IMAGE BOUNDARY',note:'The radar image is an observation. Oil classification remains a model result requiring analyst review.'});
          });
      }
    }

    if (mapMode === 'incident' && layerVisible('slick') && mapGeometry.slick) {
      svg.append('path').datum(mapGeometry.slick).attr('class','slick-shape').attr('tabindex',0).attr('d',path)
        .call(selection => appendMapTitle(selection,'Analyst-approved slick geometry'))
        .on('click keydown', event => {
          if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
          const feature = mapGeometry.slick.type === 'FeatureCollection' ? mapGeometry.slick.features?.[0] : mapGeometry.slick;
          const p = feature?.properties || {};
          setDetail({type:'APPROVED · OIL CANDIDATE',title:'Analyst-approved slick',summary:'SAR segmentation retained for reverse-drift attribution after the human decision gate.',fields:[['Observed',formatUtc(p.observation_time_utc || snapshot.analysis?.acquisition_time_utc)],['Review status',String(p.review_status || snapshot.review?.status || 'approved').replaceAll('_',' ')],['Assumed slick age',finite(p.assumed_age_hours) ? `${Number(p.assumed_age_hours)} hours` : 'Not supplied'],['Detection confidence',finite(p.detection_confidence) ? `${(Number(p.detection_confidence)*100).toFixed(1)}%` : 'Not supplied']],noteTitle:'REVIEW BOUNDARY',note:'Approval permits investigation. It does not establish the pollutant type or identify a responsible vessel.'});
        });
    }

    if (mapMode === 'incident' && layerVisible('origin') && mapGeometry.origin) {
      svg.append('path').datum(mapGeometry.origin).attr('class','origin-shape').attr('tabindex',0).attr('d',path)
        .call(selection => appendMapTitle(selection,'Reverse-drift 90% origin zone'))
        .on('click keydown', event => {
          if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
          const feature = mapGeometry.origin.type === 'FeatureCollection' ? mapGeometry.origin.features?.[0] : mapGeometry.origin;
          const p = feature?.properties || {};
          setDetail({type:'INFERRED · REVERSE DRIFT',title:'Probable release zone',summary:'Ensemble reconstruction propagated uncertainty backward from the approved slick.',fields:[['Release time',formatUtc(p.release_time_utc || snapshot.attribution?.top_candidate?.best_match_time_utc)],['90% credible radius',finite(p.credible_radius_90_km) ? `${Number(p.credible_radius_90_km).toFixed(2)} km` : 'Not supplied'],['Method',p.source || 'ESPADA reverse particle ensemble'],['Decision',String(snapshot.attribution?.decision || 'Not available').replaceAll('_',' ')]],noteTitle:'INFERENCE BOUNDARY',note:'This is a probability region, not a single proven discharge point.'});
        });
      const originValue = snapshot.attribution?.estimated_origin;
      const origin = Array.isArray(originValue) ? originValue : [originValue?.longitude, originValue?.latitude];
      if (origin.length === 2 && origin.every(finite)) {
        const [x,y] = projection(origin.map(Number));
        svg.append('circle').attr('class','origin-point').attr('cx',x).attr('cy',y).attr('r',5);
      }
      if (mapGeometry.candidateTracks) {
        const features = (mapGeometry.candidateTracks.features || []).filter(feature => Number(feature.properties?.rank) <= 10);
        const showCandidateDetail = feature => {
          const p=feature.properties||{};
          setDetail({type:'OBSERVED TRACK · RANKED',title:p.vessel_name && p.vessel_name !== 'UNKNOWN' ? p.vessel_name : `MMSI ${p.mmsi}`,summary:'A historical candidate track compared with the inferred release zone.',fields:[['Candidate rank',p.rank || 'Not supplied'],['MMSI',p.mmsi || 'Not supplied'],['Comparative score',finite(p.total_score) ? `${(Number(p.total_score)*100).toFixed(1)}%` : 'Not supplied'],['Operational decision',String(snapshot.attribution?.decision || 'Not supplied').replaceAll('_',' ')]],noteTitle:'ATTRIBUTION BOUNDARY',note:'Candidate scores prioritize review. They are not guilt probabilities, and this case currently abstains.'});
        };
        svg.append('g').selectAll('path').data(features.sort((a,b)=>Number(b.properties?.rank)-Number(a.properties?.rank))).join('path')
          .attr('class',feature => `candidate-track${Number(feature.properties?.rank) === 1 ? ' top-rank' : ''}`).attr('d',path).attr('tabindex',feature => Number(feature.properties?.rank) === 1 ? 0 : null)
          .each(function(feature){ appendMapTitle(d3.select(this), `Rank ${feature.properties?.rank} · MMSI ${feature.properties?.mmsi}`); })
          .on('click keydown',(event,feature)=>{
            if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
            showCandidateDetail(feature);
          });
        const topCandidateFeature = features.find(feature => Number(feature.properties?.rank) === 1);
        const topCandidateCoordinate = topCandidateFeature?.geometry?.coordinates?.[0];
        if (Array.isArray(topCandidateCoordinate)) {
          const [candidateX,candidateY] = projection(topCandidateCoordinate.map(Number));
          const marker = svg.append('circle').attr('class','candidate-marker').attr('tabindex',0).attr('aria-label','Top-ranked historical candidate position').attr('cx',candidateX).attr('cy',candidateY).attr('r',7);
          appendMapTitle(marker, `Rank 1 · MMSI ${topCandidateFeature.properties?.mmsi}`).on('click keydown',event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;showCandidateDetail(topCandidateFeature);});
        }
      }
    }

    const positions = Array.isArray(snapshot.ais?.positions) ? snapshot.ais.positions : [];
    if (mapMode === 'live' && layerVisible('vessels')) {
      const tracks = snapshot.ais?.tracks || {};
      const trackFeatures = Object.entries(tracks).filter(([,points]) => Array.isArray(points) && points.length > 1).map(([mmsi,points]) => ({type:'Feature',properties:{mmsi},geometry:{type:'LineString',coordinates:points.map(point=>[Number(point[0]),Number(point[1])])}}));
      svg.append('g').selectAll('path').data(trackFeatures).join('path').attr('class','ais-track').attr('d',path);
      const vesselGroup = svg.append('g');
      positions.forEach(vessel => {
        if (!finite(vessel.longitude) || !finite(vessel.latitude)) return;
        const point = projection([Number(vessel.longitude),Number(vessel.latitude)]);
        if (!point) return;
        const mark = vesselGroup.append('path').attr('class','vessel-mark').attr('tabindex',0).attr('aria-label',`AIS vessel ${vessel.vessel_name || vessel.mmsi}`)
          .attr('d','M0,-10 C4,-7 5,3 3,8 L0,11 L-3,8 C-5,3 -4,-7 0,-10 Z M-3,2 L3,2')
          .attr('transform',`translate(${point[0]},${point[1]}) rotate(${finite(vessel.cog) ? Number(vessel.cog) : 0})`);
        appendMapTitle(mark, `${vessel.vessel_name || 'UNKNOWN'} · MMSI ${vessel.mmsi}`)
          .on('click keydown',event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;detailForVessel(vessel,tracks[vessel.mmsi]?.length);});
      });
    }

    if (mapMode === 'live' && layerVisible('environment')) {
      const values = snapshot.sources?.environment?.current || {};
      const centre = snapshot.region?.center;
      if (Array.isArray(centre) && finite(values.current_east_ms) && finite(values.current_north_ms)) {
        const [cx,cy] = projection(centre.map(Number));
        const vectors = [
          {kind:'current',east:Number(values.current_east_ms),north:Number(values.current_north_ms),speed:Number(values.current_speed_ms),length:58,label:'CURRENT',marker:'currentArrow'},
          {kind:'wind',east:Number(values.wind_east_ms),north:Number(values.wind_north_ms),speed:Number(values.wind_speed_ms),length:78,label:'WIND',marker:'windArrow'}
        ];
        vectors.forEach((vector,index)=>{
          const magnitude=Math.hypot(vector.east,vector.north)||1, x=cx+vector.length*vector.east/magnitude, y=cy-vector.length*vector.north/magnitude, offset=index*17;
          svg.append('line').attr('class',`vector-line ${vector.kind}`).attr('x1',cx).attr('y1',cy+offset).attr('x2',x).attr('y2',y+offset).attr('marker-end',`url(#${vector.marker})`);
          svg.append('text').attr('class','vector-label').attr('x',cx+5).attr('y',cy+offset-7).attr('fill',vector.kind==='current'?'#17687c':'#9a6116').text(`${vector.label} ${finite(vector.speed)?vector.speed.toFixed(vector.kind==='current'?2:1):'—'} m/s`);
        });
      }
    }

    const [labelX,labelY] = projection([bbox[0]+(bbox[2]-bbox[0])*.025,bbox[3]-(bbox[3]-bbox[1])*.035]);
    svg.append('text').attr('class','place-label').attr('x',labelX).attr('y',labelY).text(String(snapshot.region?.name || 'WATCH REGION').toUpperCase());
    const centreLat = Number(snapshot.region?.center?.[1] ?? (bbox[1]+bbox[3])/2);
    const tenKmDegrees = 10/(111.32*Math.cos(centreLat*Math.PI/180));
    const p0=projection([bbox[0],centreLat]),p1=projection([bbox[0]+tenKmDegrees,centreLat]);
    const scalePixels=Math.max(40,Math.min(180,Math.abs(p1[0]-p0[0])));
    byId('mapScale').style.width=`${scalePixels}px`;
    byId('mapScale').dataset.label='10 km';

    const empty = mapMode === 'live' && layerVisible('vessels') && positions.length === 0;
    byId('mapEmpty').hidden = !empty;
    const visibleLabels=[];
    if(mapMode==='live'){
      visibleLabels.push(`${positions.length} AIS vessel${positions.length===1?'':'s'}`);
      if(layerVisible('satellite'))visibleLabels.push(`${mapFeatureCount(mapGeometry.footprints)} catalogue footprint${mapFeatureCount(mapGeometry.footprints)===1?'':'s'}`);
    }else{
      if(layerVisible('slick')&&mapGeometry.slick)visibleLabels.push('approved slick');
      if(layerVisible('origin')&&mapGeometry.origin)visibleLabels.push('origin uncertainty');
      if(mapGeometry.candidateTracks)visibleLabels.push('top 10 candidate tracks');
    }
    html('mapLayerStatus',visibleLabels.length?visibleLabels.join(' · '):'No evidence layers available');
  }

  function updateMapMode(mode) {
    mapMode = mode;
    const incident = mode === 'incident';
    byId('liveModeButton').classList.toggle('active',!incident);
    byId('incidentModeButton').classList.toggle('active',incident);
    byId('liveModeButton').setAttribute('aria-pressed',String(!incident));
    byId('incidentModeButton').setAttribute('aria-pressed',String(incident));
    byId('mapModeChip').classList.toggle('incident',incident);
    html('mapModeChip',incident?'INCIDENT EVIDENCE':'LIVE WATCH');
    html('mapEvidenceTime',lastSnapshot ? formatUtc(evidenceTime(lastSnapshot)) : 'Waiting for evidence time');
    html('mapTimeRule',incident?'Only evidence tied to the selected SAR investigation is shown.':'Only recent provider AIS and current source state are shown.');
    for (const name of ['slick','origin']) byId('layerControls').querySelector(`[data-layer="${name}"]`).disabled=!incident;
    byId('layerControls').querySelector('[data-layer="environment"]').disabled=incident;
    setDetail(incident?{type:'INCIDENT TIMELINE',title:'Historical evidence isolated',summary:'Approved slick, reconstructed origin and temporally matched candidate tracks share the selected investigation timeline.',fields:[['SAR observation',formatUtc(lastSnapshot?.analysis?.acquisition_time_utc)],['Estimated release',formatUtc(lastSnapshot?.attribution?.top_candidate?.best_match_time_utc)],['Candidate population',compactNumber(lastSnapshot?.attribution?.candidate_count || lastSnapshot?.attribution?.candidates_compared)],['Decision',String(lastSnapshot?.attribution?.decision || 'Not available').replaceAll('_',' ')]],noteTitle:'NO LIVE OVERLAY',note:'Present-day AIS is intentionally hidden here.'}:{type:'LIVE WATCH',title:'Current maritime picture',summary:'Only positions actually received in the active provider window are eligible for display.',fields:[['Latest AIS',formatUtc(lastSnapshot?.sources?.ais?.latest_observation_utc)],['Received vessels',compactNumber(lastSnapshot?.ais?.vessel_count)],['Satellite catalogue',`${compactNumber(lastSnapshot?.sources?.sentinel?.scenes_returned)} scene records`],['Environment time',formatUtc(lastSnapshot?.sources?.environment?.latest_observation_utc)]],noteTitle:'NO SYNTHETIC FALLBACK',note:'If the live provider returns nothing, the map remains empty.'});
    if(lastSnapshot) renderMap(lastSnapshot);
  }

  const analysisBusy = status => ['QUEUED','DOWNLOADING','INFERENCE','INTERPRETING'].includes(String(status || '').toUpperCase());
  const setGate = (id, state, label, description) => {
    const gate = byId(id);
    gate.dataset.state = state;
    gate.querySelector('em').textContent = label;
    if (description) gate.querySelector('small').textContent = description;
  };

  function setDetectionStatus(state, title, message) {
    const status = byId('detectionStatus');
    status.dataset.state = state;
    status.querySelector('b').textContent = title;
    status.querySelector('small').textContent = message;
  }

  function renderSceneSelector(snapshot) {
    const select = byId('sarSceneSelect');
    const scenes = Array.isArray(snapshot.sources?.sentinel?.scenes) ? [...snapshot.sources.sentinel.scenes] : [];
    if (snapshot.analysis?.scene_id && !scenes.some(scene => scene.id === snapshot.analysis.scene_id)) {
      scenes.push({
        id: snapshot.analysis.scene_id,
        acquisition_time_utc: snapshot.analysis.acquisition_time_utc,
        platform: 'Sentinel-1',
        polarizations: ['VV'],
        processed_evidence: true
      });
    }
    const preferred = selectedSceneId || snapshot.analysis?.requested_scene_id || snapshot.analysis?.scene_id || scenes[0]?.id;
    select.replaceChildren();
    scenes.forEach(scene => {
      const option = document.createElement('option');
      option.value = scene.id;
      const acquired = parseTime(scene.acquisition_time_utc);
      const ageHours = acquired ? (Date.now() - acquired.getTime()) / 3600000 : null;
      option.textContent = `${formatUtc(scene.acquisition_time_utc)} · ${String(scene.platform || 'Sentinel-1').toUpperCase()} · ${scene.processed_evidence ? 'PROCESSED EVIDENCE' : ageHours !== null && ageHours >= 96 ? 'AIS READY' : 'AIS DELAY'}`;
      select.append(option);
    });
    if (scenes.some(scene => scene.id === preferred)) select.value = preferred;
    selectedSceneId = select.value || null;
    select.disabled = scenes.length === 0 || analysisBusy(snapshot.analysis?.status);
    const scene = scenes.find(item => item.id === selectedSceneId);
    if (!scene) {
      html('sceneAvailability','NO CATALOGUE SCENE');
      html('sceneDelayNote','Refresh the Sentinel catalogue to continue');
      return null;
    }
    const acquired = parseTime(scene.acquisition_time_utc);
    const ageHours = acquired ? (Date.now() - acquired.getTime()) / 3600000 : null;
    const ready = ageHours !== null && ageHours >= 96;
    html('sceneAvailability',ready?'FULL EVIDENCE PATH READY':'SAR READY · AIS DELAYED');
    html('sceneDelayNote',ready?'Historical AIS availability window has elapsed':`Historical AIS expected in about ${Math.max(1,Math.ceil(96-(ageHours||0)))} h`);
    return scene;
  }

  function setSarView(view) {
    sarView = view;
    document.querySelectorAll('[data-sar-view]').forEach(button => {
      const active = button.dataset.sarView === view;
      button.classList.toggle('active',active);
      button.setAttribute('aria-pressed',String(active));
    });
    if (lastSnapshot) renderSarImage(lastSnapshot);
  }

  function renderSarImage(snapshot) {
    const analysis = snapshot.analysis || {};
    const scene = (snapshot.sources?.sentinel?.scenes || []).find(item => item.id === selectedSceneId) || (analysis.scene_id === selectedSceneId ? {
      id: analysis.scene_id,
      acquisition_time_utc: analysis.acquisition_time_utc,
      platform: 'Sentinel-1',
      polarizations: ['VV'],
      processed_evidence: true
    } : null);
    const matchesAnalysis = Boolean(selectedSceneId && analysis.scene_id === selectedSceneId);
    const urls = {input: analysis.input_url, overview: analysis.overview_url, mask: analysis.mask_url};
    const labels = {input:'CALIBRATED VV BACKSCATTER',overview:'MODEL PROBABILITY + CANDIDATE BOUNDARY',mask:'THRESHOLDED BINARY CANDIDATE'};
    const image = byId('sarEvidenceImage');
    const empty = byId('sarImageEmpty');
    const viewUrl = matchesAnalysis ? urls[sarView] : null;
    document.querySelectorAll('[data-sar-view]').forEach(button => { button.disabled = !matchesAnalysis || !urls[button.dataset.sarView]; });
    html('viewerLabel',matchesAnalysis ? labels[sarView] : 'SELECTED SCENE HAS NOT BEEN PROCESSED');
    if (viewUrl) {
      if (image.dataset.url !== viewUrl) {
        image.dataset.url = viewUrl;
        image.src = viewUrl;
      }
      image.classList.toggle('mask-view',sarView === 'mask');
      image.alt = `${labels[sarView]} for ${analysis.scene_id}`;
      image.hidden = false;
      empty.hidden = true;
    } else {
      image.hidden = true;
      empty.hidden = false;
      empty.querySelector('strong').textContent = scene ? 'No processed pixels for this selection' : 'No Sentinel-1 scene available';
      empty.querySelector('span').textContent = scene ? 'Run the calibrated analysis to create an inspectable result.' : 'Refresh the satellite catalogue to continue.';
    }
    html('sarSceneId',scene?.id || '—');
    html('sarAcquired',formatUtc(scene?.acquisition_time_utc));
    html('sarPolarisation',Array.isArray(scene?.polarizations) ? scene.polarizations.join(' + ') : '—');
    html('sarFootprint',finite(scene?.aoi_overlap_fraction) ? `${(Number(scene.aoi_overlap_fraction)*100).toFixed(1)}% AOI overlap` : '—');
  }

  function renderDetectionWorkbench(snapshot) {
    const scene = renderSceneSelector(snapshot);
    const analysis = snapshot.analysis || {status:'NOT_RUN'};
    const review = snapshot.review || {status:'NOT_REVIEWED'};
    const current = Boolean(scene && analysis.scene_id === scene.id);
    const busy = analysisBusy(analysis.status);
    const reviewable = current && analysis.status === 'REVIEW_REQUIRED' && !['APPROVED','REJECTED'].includes(review.status);
    const approved = current && review.status === 'APPROVED';
    const rejected = current && review.status === 'REJECTED';
    const physics = current ? analysis.physics_screen || {} : {};

    const analyzeButton = byId('analyzeSarButton');
    analyzeButton.disabled = !scene || busy || current && ['REVIEW_REQUIRED','NO_DETECTION'].includes(analysis.status);
    analyzeButton.textContent = busy ? String(analysis.status).replaceAll('_',' ') + '…' : current ? 'Analysis already available' : 'Analyze selected scene';
    if (busy) setDetectionStatus('busy',String(analysis.status).replaceAll('_',' '),'Calibrated scene processing is active');
    else if (approved) setDetectionStatus('approved','APPROVED FOR RECONSTRUCTION',`Recorded ${formatUtc(review.reviewed_at_utc)}`);
    else if (rejected) setDetectionStatus('rejected','CANDIDATE REJECTED','Reverse drift remains blocked');
    else if (reviewable) setDetectionStatus('review','ANALYST REVIEW REQUIRED','Automation has stopped at the human gate');
    else if (current && analysis.status === 'NO_DETECTION') setDetectionStatus('approved','NO CANDIDATE RETAINED','No review or attribution required');
    else setDetectionStatus('waiting','READY TO ANALYZE',scene?'Selected catalogue scene has no model result':'No usable Sentinel-1 scene');

    if (!current) {
      setGate('modelGate','waiting','NOT RUN','Selected scene has no calibrated inference');
      setGate('physicsGate','waiting','WAITING','Requires a model candidate');
      setGate('analystGate','waiting','LOCKED','Requires completed automated gates');
    } else if (busy) {
      setGate('modelGate','review',String(analysis.status).replaceAll('_',' '),'Calibrated inference is running');
      setGate('physicsGate','waiting','WAITING','Runs after candidate extraction');
      setGate('analystGate','waiting','LOCKED','Human review remains unavailable');
    } else {
      setGate('modelGate','pass',analysis.status === 'NO_DETECTION'?'NO CANDIDATE':'CANDIDATE',analysis.status === 'NO_DETECTION'?'No pixels exceeded the frozen threshold':`${compactNumber(analysis.detected_components)} regions exceeded the frozen threshold`);
      const physicsPass = String(physics.status || '').includes('PLAUSIBLE') || physics.contrast_gate_passed && physics.wind_gate_passed;
      setGate('physicsGate',physicsPass?'pass':physics.status?'fail':'waiting',physicsPass?'PLAUSIBLE':physics.status?'REJECTED':'NOT RUN',physics.method || 'Contrast and acquisition wind plausibility');
      setGate('analystGate',approved?'pass':rejected?'fail':reviewable?'review':'waiting',approved?'APPROVED':rejected?'REJECTED':reviewable?'REVIEW':'NOT REQUIRED',approved?'Decision recorded with assumed slick age':rejected?'Attribution is blocked':reviewable?'Automation paused for a human decision':'No model candidate requires review');
    }

    html('candidateFraction',current && finite(analysis.detected_pixel_fraction) ? `${(Number(analysis.detected_pixel_fraction)*100).toFixed(3)}%` : '—');
    html('candidateComponents',current ? compactNumber(analysis.detected_components) : '—');
    html('maximumProbability',current && finite(analysis.probability_summary?.maximum) ? `${(Number(analysis.probability_summary.maximum)*100).toFixed(1)}%` : '—');
    html('localContrast',current && finite(physics.weighted_local_contrast_db) ? `${Number(physics.weighted_local_contrast_db).toFixed(2)} dB` : '—');
    html('physicsStatus',physics.status ? String(physics.status).replaceAll('_',' ') : 'NOT ASSESSED');
    html('physicsInterpretation',physics.interpretation || 'The screen will check whether candidate pixels are locally dark and whether acquisition-time wind is broadly compatible with an oil signature.');
    html('physicsWind',finite(physics.wind_speed_ms) ? `${Number(physics.wind_speed_ms).toFixed(2)} m/s · ${formatUtc(physics.wind_time_utc)}` : '—');
    html('contrastGate',physics.contrast_gate_passed === true?'PASS':physics.contrast_gate_passed === false?'FAIL':'—');
    html('windGate',physics.wind_gate_passed === true?'PASS':physics.wind_gate_passed === false?'FAIL':'—');

    html('reviewHeading',approved?'Candidate approved':rejected?'Candidate rejected':reviewable?'Decision required':'No reviewable candidate');
    html('reviewMessage',review.message || analysis.message || 'A decision becomes available only after the model and physics gates complete.');
    const age = finite(review.assumed_age_hours) ? Number(review.assumed_age_hours) : Number(byId('releaseAgeInput').value || 19);
    byId('releaseAgeInput').value = String(age);
    html('releaseAgeValue',`${age} h`);
    byId('releaseAgeInput').disabled = !reviewable;
    byId('approveCandidateButton').disabled = !reviewable;
    byId('rejectCandidateButton').disabled = !reviewable;
    renderSarImage(snapshot);
  }

  async function analyzeSelectedSar() {
    const button = byId('analyzeSarButton');
    button.disabled = true;
    button.textContent = 'QUEUED…';
    try {
      const response = await fetch(ANALYZE_ENDPOINT,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:selectedSceneId})});
      const result = await response.json().catch(()=>({}));
      if(!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      setDetectionStatus('busy','ANALYSIS QUEUED','The server is downloading and processing real SAR pixels');
      window.setTimeout(poll,1000);
    } catch(error) {
      setDetectionStatus('error','ANALYSIS COULD NOT START',error.message);
      button.disabled = false;
      button.textContent = 'Analyze selected scene';
    }
  }

  async function submitCandidateReview(decision) {
    for(const id of ['approveCandidateButton','rejectCandidateButton']) byId(id).disabled = true;
    try {
      const response = await fetch(REVIEW_ENDPOINT,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision,age_hours:Number(byId('releaseAgeInput').value)})});
      const result = await response.json().catch(()=>({}));
      if(!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      setDetectionStatus(decision === 'APPROVE'?'approved':'rejected',decision === 'APPROVE'?'DECISION RECORDED':'CANDIDATE REJECTED',result.message || 'Review state updated');
      window.setTimeout(poll,600);
    } catch(error) {
      setDetectionStatus('error','REVIEW COULD NOT BE RECORDED',error.message);
      if(lastSnapshot) renderDetectionWorkbench(lastSnapshot);
    }
  }

  const coordinatePair = value => Array.isArray(value) ? value.map(Number) : [Number(value?.longitude), Number(value?.latitude)];
  const distanceKm = (left, right) => {
    if (!left?.every(finite) || !right?.every(finite)) return null;
    const radians = degrees => degrees * Math.PI / 180;
    const dLat = radians(right[1] - left[1]);
    const dLon = radians(right[0] - left[0]);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(radians(left[1])) * Math.cos(radians(right[1])) * Math.sin(dLon / 2) ** 2;
    return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  };

  function setDriftDetail(type, title, copy) {
    html('driftDetailType', type);
    html('driftDetailTitle', title);
    html('driftDetailCopy', copy);
  }

  function setDriftView(view) {
    driftView = view;
    document.querySelectorAll('[data-drift-view]').forEach(button => {
      const active = button.dataset.driftView === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (lastSnapshot) renderDriftMap(lastSnapshot);
  }

  function renderDriftMap(snapshot) {
    const svgElement = byId('driftMap');
    if (!svgElement || !window.d3) return;
    const d3 = window.d3;
    const svg = d3.select(svgElement);
    svg.selectAll('*').remove();
    const attribution = snapshot.attribution || {};
    const particles = (Array.isArray(attribution.origin_particles) ? attribution.origin_particles : [])
      .map(coordinatePair).filter(point => point.every(finite));
    const observed = coordinatePair(attribution.observed_centroid);
    const origin = coordinatePair(attribution.estimated_origin);
    const complete = String(attribution.status || '').toUpperCase() === 'COMPLETE' && particles.length > 0;
    byId('driftMapEmpty').hidden = complete;
    if (!complete) return;

    const bbox = Array.isArray(snapshot.region?.bbox) ? snapshot.region.bbox.map(Number) : null;
    if (!bbox || bbox.length !== 4) return;
    const width = 1040, height = 650;
    const projection = d3.geoMercator().fitExtent([[28,28],[width-28,height-28]], bboxPolygon(bbox));
    const path = d3.geoPath(projection);
    svg.append('rect').attr('class','drift-ocean').attr('width',width).attr('height',height);
    svg.append('path').datum(d3.geoGraticule().extent([[bbox[0],bbox[1]],[bbox[2],bbox[3]]]).step([0.1,0.1])()).attr('class','drift-grid').attr('d',path);
    if (mapGeometry.coast) {
      svg.append('path').datum(mapGeometry.coast).attr('class','drift-coast-halo').attr('d',path);
      svg.append('path').datum(mapGeometry.coast).attr('class','drift-land').attr('d',path);
    }
    svg.append('path').datum(bboxPolygon(bbox)).attr('class','drift-region').attr('d',path);

    const showObserved = driftView !== 'origin';
    const showOrigin = driftView !== 'observed';
    if (showObserved && mapGeometry.slick) {
      svg.append('path').datum(mapGeometry.slick).attr('class','drift-slick-halo').attr('d',path);
      const slick = svg.append('path').datum(mapGeometry.slick).attr('class','drift-slick').attr('tabindex',0).attr('aria-label','Analyst-approved observed slick').attr('d',path);
      appendMapTitle(slick,'Analyst-approved slick at satellite observation time').on('click keydown',event=>{
        if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
        setDriftDetail('OBSERVED · SENTINEL-1','Approved slick geometry',`Observed ${formatUtc(snapshot.analysis?.acquisition_time_utc)} after model, physics and analyst review. This is the reconstruction seed, not proof of pollutant identity.`);
      });
      if (observed.every(finite)) {
        const point=projection(observed);
        const mark=svg.append('circle').attr('class','drift-observed-point').attr('tabindex',0).attr('aria-label','Observed slick centroid').attr('cx',point[0]).attr('cy',point[1]).attr('r',6);
        appendMapTitle(mark,`Observed centroid · ${observed[0].toFixed(4)}, ${observed[1].toFixed(4)}`).on('click keydown',event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;setDriftDetail('OBSERVED · CENTROID','Observed slick centre',`${observed[0].toFixed(5)}°, ${observed[1].toFixed(5)}° at the Sentinel-1 acquisition time.`);});
      }
    }

    if (showOrigin) {
      if (mapGeometry.origin) {
        const zone=svg.append('path').datum(mapGeometry.origin).attr('class','drift-origin-zone').attr('tabindex',0).attr('aria-label','Ninety percent probable origin zone').attr('d',path);
        appendMapTitle(zone,`90% origin zone · ${Number(attribution.credible_radius_90_km).toFixed(2)} km credible radius`).on('click keydown',event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;setDriftDetail('INFERRED · UNCERTAINTY','Probable release zone',`This contour contains the central reverse-drift ensemble. Its ${Number(attribution.credible_radius_90_km).toFixed(2)} km radius expresses model uncertainty; it is not an accuracy claim.`);});
      }
      const sampleStep=Math.max(1,Math.ceil(particles.length/500));
      svg.append('g').selectAll('circle').data(particles.filter((_,index)=>index%sampleStep===0)).join('circle').attr('class','drift-particle').attr('cx',point=>projection(point)[0]).attr('cy',point=>projection(point)[1]).attr('r',2.5);
      if (origin.every(finite)) {
        const point=projection(origin);
        svg.append('circle').attr('class','drift-origin-ring').attr('cx',point[0]).attr('cy',point[1]).attr('r',13);
        const mark=svg.append('circle').attr('class','drift-origin-point').attr('tabindex',0).attr('aria-label','Estimated release origin').attr('cx',point[0]).attr('cy',point[1]).attr('r',6);
        appendMapTitle(mark,`Estimated origin · ${origin[0].toFixed(4)}, ${origin[1].toFixed(4)}`).on('click keydown',event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;setDriftDetail('INFERRED · ENSEMBLE CENTRE','Estimated release origin',`${origin[0].toFixed(5)}°, ${origin[1].toFixed(5)}°. This centre is passed to candidate correlation together with the full uncertainty field.`);});
      }
    }

    if (driftView === 'comparison' && observed.every(finite) && origin.every(finite)) {
      const a=projection(origin),b=projection(observed);
      svg.append('line').attr('class','drift-displacement').attr('x1',a[0]).attr('y1',a[1]).attr('x2',b[0]).attr('y2',b[1]);
      svg.append('text').attr('class','drift-displacement-label').attr('x',(a[0]+b[0])/2).attr('y',(a[1]+b[1])/2-9).attr('text-anchor','middle').text(`${distanceKm(origin,observed).toFixed(2)} km centroid displacement`);
    }
    const [labelX,labelY]=projection([bbox[0]+(bbox[2]-bbox[0])*.028,bbox[3]-(bbox[3]-bbox[1])*.055]);
    svg.append('text').attr('class','drift-map-title').attr('x',labelX).attr('y',labelY).text(String(snapshot.region?.name||'WATCH REGION').toUpperCase());
    svg.append('text').attr('class','drift-map-subtitle').attr('x',labelX).attr('y',labelY+18).text(driftView==='observed'?'SATELLITE OBSERVATION':driftView==='origin'?'INFERRED RELEASE DISTRIBUTION':'OBSERVATION ↔ INFERENCE');
    html('driftMapLabel',driftView==='observed'?'OBSERVED SLICK':driftView==='origin'?'RELEASE ENSEMBLE':'EVIDENCE COMPARISON');
  }

  function renderReverseDrift(snapshot) {
    const review=snapshot.review||{};
    const attribution=snapshot.attribution||{};
    const status=String(attribution.status||'NOT_RUN').toUpperCase();
    const approved=String(review.status||'').toUpperCase()==='APPROVED';
    const complete=status==='COMPLETE';
    const running=['QUEUED','RUNNING','RECONSTRUCTING','ATTRIBUTING'].includes(status);
    const statusBox=byId('driftStatus');
    statusBox.dataset.state=complete?'complete':running?'running':approved?'ready':status==='FAIL'?'error':'waiting';
    statusBox.querySelector('b').textContent=complete?'RECONSTRUCTION COMPLETE':running?`${status.replaceAll('_',' ')}…`:approved?'READY TO BUILD':'WAITING FOR APPROVAL';
    statusBox.querySelector('small').textContent=complete?`${compactNumber(attribution.origin_particles?.length)} ensemble endpoints · ${Number(attribution.credible_radius_90_km).toFixed(2)} km radius`:running?'Physics and evidence correlation are running':approved?'Approved slick can enter reverse drift':'Human-reviewed slick required';
    const button=byId('buildAttributionButton');
    button.disabled=!approved||complete||running;
    button.textContent=complete?'Reconstruction available':running?'Building reconstruction…':'Build reconstruction';

    const releaseTime=attribution.release_time_utc||attribution.top_candidate?.best_match_time_utc;
    html('driftReleaseTime',formatUtc(releaseTime));
    html('driftObservationTime',formatUtc(attribution.observation_time_utc||snapshot.analysis?.acquisition_time_utc));
    html('driftDuration',finite(attribution.assumed_age_hours||review.assumed_age_hours)?`${Number(attribution.assumed_age_hours||review.assumed_age_hours)} HOURS`:'—');
    html('driftParticleCount',compactNumber(attribution.origin_particles?.length));
    html('driftRadius',finite(attribution.credible_radius_90_km)?`${Number(attribution.credible_radius_90_km).toFixed(2)} km`:'—');
    const origin=coordinatePair(attribution.estimated_origin),observed=coordinatePair(attribution.observed_centroid);
    html('driftOrigin',origin.every(finite)?`${origin[0].toFixed(4)}°, ${origin[1].toFixed(4)}°`:'—');
    const displacement=distanceKm(origin,observed);
    html('driftDisplacement',finite(displacement)?`${displacement.toFixed(2)} km`:'—');
    html('driftForcing',attribution.forcing_source||'No reconstruction forcing record is available.');
    const forcing=String(attribution.forcing_source||'');
    html('driftCurrentSource',forcing.includes('Copernicus')?'Copernicus Marine':'—');
    html('driftWindSource',forcing.includes('Open-Meteo')?'Open-Meteo historical':'—');
    html('driftMethodStatus',complete?`Computed ${formatUtc(attribution.completed_at_utc)} · ${compactNumber(attribution.origin_particles?.length)} retained endpoints`:'Reverse-drift method has not completed');
    const link=byId('driftDiagnosticLink');
    link.hidden=!complete||!attribution.reverse_analysis_url;
    if(!link.hidden)link.href=attribution.reverse_analysis_url;
    if(!complete)setDriftDetail('INFERENCE READOUT',approved?'Ready to reconstruct':'Review gate is closed',approved?'The approved slick can now be propagated backward through the recorded environmental fields.':'Approve a physically plausible SAR candidate before the system estimates a release region.');
    renderDriftMap(snapshot);
  }

  async function buildAttribution() {
    const button=byId('buildAttributionButton');
    button.disabled=true;
    button.textContent='QUEUED…';
    try{
      const response=await fetch(ATTRIBUTION_ENDPOINT,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const result=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
      byId('driftStatus').dataset.state='running';
      byId('driftStatus').querySelector('b').textContent='RECONSTRUCTION QUEUED';
      byId('driftStatus').querySelector('small').textContent=result.message||'Physics and evidence correlation started';
      window.setTimeout(poll,1000);
    }catch(error){
      byId('driftStatus').dataset.state='error';
      byId('driftStatus').querySelector('b').textContent='COULD NOT START';
      byId('driftStatus').querySelector('small').textContent=error.message;
      if(lastSnapshot)renderReverseDrift(lastSnapshot);
    }
  }

  const candidateDisplayName = candidate => candidate?.vessel_name && candidate.vessel_name !== 'UNKNOWN'
    ? candidate.vessel_name
    : 'Unverified vessel identity';

  function selectedCandidate(snapshot) {
    const candidates = Array.isArray(snapshot.attribution?.candidates) ? snapshot.attribution.candidates : [];
    if (!candidates.length) return null;
    const selected = candidates.find(candidate => String(candidate.mmsi) === String(selectedCandidateMmsi)) || candidates[0];
    selectedCandidateMmsi = String(selected.mmsi);
    return selected;
  }

  function renderCandidateMap(snapshot) {
    const svgElement=byId('candidateMap');
    if(!svgElement||!window.d3)return;
    const d3=window.d3;
    const svg=d3.select(svgElement);
    svg.selectAll('*').remove();
    const candidates=Array.isArray(snapshot.attribution?.candidates)?snapshot.attribution.candidates:[];
    const selected=selectedCandidate(snapshot);
    const candidateIds=new Set(candidates.map(candidate=>String(candidate.mmsi)));
    const allFeatures=mapGeometry.candidateTracks?.features||[];
    const tracks=allFeatures.filter(feature=>candidateIds.has(String(feature.properties?.mmsi)));
    const complete=String(snapshot.attribution?.status||'').toUpperCase()==='COMPLETE'&&tracks.length>0;
    byId('candidateMapEmpty').hidden=complete;
    if(!complete)return;
    const bbox=Array.isArray(snapshot.region?.bbox)?snapshot.region.bbox.map(Number):null;
    if(!bbox||bbox.length!==4)return;
    const width=1040,height=590;
    const projection=d3.geoMercator().fitExtent([[28,28],[width-28,height-28]],bboxPolygon(bbox));
    const path=d3.geoPath(projection);
    svg.append('rect').attr('class','candidate-ocean').attr('width',width).attr('height',height);
    svg.append('path').datum(d3.geoGraticule().extent([[bbox[0],bbox[1]],[bbox[2],bbox[3]]]).step([0.1,0.1])()).attr('class','candidate-grid').attr('d',path);
    if(mapGeometry.coast){svg.append('path').datum(mapGeometry.coast).attr('class','candidate-coast-halo').attr('d',path);svg.append('path').datum(mapGeometry.coast).attr('class','candidate-land').attr('d',path);}
    svg.append('path').datum(bboxPolygon(bbox)).attr('class','candidate-region').attr('d',path);
    if(mapGeometry.origin)svg.append('path').datum(mapGeometry.origin).attr('class','candidate-origin-zone').attr('d',path);
    if(mapGeometry.slick)svg.append('path').datum(mapGeometry.slick).attr('class','candidate-slick-context').attr('d',path);

    const ordered=[...tracks].sort((left,right)=>Number(right.properties?.rank)-Number(left.properties?.rank));
    const group=svg.append('g');
    ordered.forEach(feature=>{
      const rank=Number(feature.properties?.rank);
      const mmsi=String(feature.properties?.mmsi);
      const active=mmsi===String(selected?.mmsi);
      const geometry=feature.geometry||{};
      const mark=group.append('path').datum(feature).attr('class',`candidate-track-line${rank<=5?' top-five':''}${active?' selected':''}`).attr('tabindex',0).attr('aria-label',`Candidate rank ${rank}, MMSI ${mmsi}`).attr('d',path);
      appendMapTitle(mark,`Rank ${rank} · MMSI ${mmsi}`).on('click keydown',event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;selectCandidate(mmsi);});
      const coordinates=geometry.type==='LineString'?geometry.coordinates:geometry.type==='Point'?[geometry.coordinates]:[];
      const start=coordinates[0];
      if(Array.isArray(start)&&start.every(finite)&&rank<=12){
        const point=projection(start.map(Number));
        svg.append('circle').attr('class',`candidate-track-start${active?' selected':''}`).attr('cx',point[0]).attr('cy',point[1]).attr('r',active?6:3.5);
        if(active||rank<=5)svg.append('text').attr('class','candidate-map-rank').attr('x',point[0]+8).attr('y',point[1]-7).text(`#${rank}`);
      }
    });
    html('candidateMapCaption',selected?`SELECTED · RANK ${selected.rank} · MMSI ${selected.mmsi}`:'TOP EVIDENCE MATCHES');
  }

  function renderCandidateInspector(snapshot,candidate) {
    if(!candidate){
      html('selectedCandidateRank','SELECT A CANDIDATE');html('selectedCandidateName','No vessel selected');html('selectedCandidateMmsi','MMSI —');html('selectedCandidateScore','—');
      return;
    }
    html('selectedCandidateRank',`RANK ${candidate.rank} OF ${compactNumber(snapshot.attribution?.candidate_count)}`);
    html('selectedCandidateName',candidateDisplayName(candidate));
    html('selectedCandidateMmsi',`MMSI ${candidate.mmsi}`);
    html('selectedCandidateScore',finite(candidate.total_score)?`${(Number(candidate.total_score)*100).toFixed(1)}%`:'—');
    const measures=[['presence',candidate.presence_score],['forward',candidate.forward_consistency],['quality',candidate.data_quality]];
    measures.forEach(([name,value])=>{html(`${name}ScoreLabel`,finite(value)?`${(Number(value)*100).toFixed(1)}%`:'—');byId(`${name}ScoreBar`).style.width=finite(value)?`${Math.max(0,Math.min(100,Number(value)*100))}%`:'0%';});
    html('candidateForwardError',finite(candidate.forward_error_km)?`${Number(candidate.forward_error_km).toFixed(2)} km`:'—');
    html('candidatePositionType',candidate.release_position_interpolated?`INTERPOLATED · ${Number(candidate.interpolation_gap_hours||0).toFixed(1)} h gap`:'RECEIVED AIS FIX');
    html('candidateMatchTime',formatUtc(candidate.best_match_time_utc));
    const gapCount=Number(candidate.significant_gaps||0);
    const silenceClass=String(candidate.silence_classification||'not_assessed').replaceAll('_',' ').toUpperCase();
    html('candidateSilence',gapCount?`${gapCount} GAP${gapCount===1?'':'S'} · ${silenceClass}`:silenceClass);
    html('candidateSilenceNote',gapCount?`${Number(candidate.gaps_with_local_peer_reception||0)} gap(s) occurred while nearby peers were still received. This triggers scrutiny but adds no score.`:'No gap exceeded the source-aware threshold. Silence contributes no positive score.');
  }

  function renderCandidateRows(snapshot) {
    const candidates=Array.isArray(snapshot.attribution?.candidates)?snapshot.attribution.candidates:[];
    const body=byId('candidateRows');
    if(!candidates.length){body.innerHTML='<tr><td colspan="6">Waiting for completed attribution.</td></tr>';return;}
    body.innerHTML=candidates.map(candidate=>{
      const selected=String(candidate.mmsi)===String(selectedCandidateMmsi);
      const gaps=Number(candidate.significant_gaps||0);
      return `<tr tabindex="0" data-candidate-mmsi="${escapeMarkup(candidate.mmsi)}" class="${selected?'selected':''}" aria-selected="${selected}"><td>${escapeMarkup(candidate.rank)}</td><td><strong>${escapeMarkup(candidateDisplayName(candidate))}</strong><small>MMSI ${escapeMarkup(candidate.mmsi)}</small></td><td class="score-value">${finite(candidate.total_score)?(Number(candidate.total_score)*100).toFixed(1)+'%':'—'}</td><td>${finite(candidate.forward_error_km)?Number(candidate.forward_error_km).toFixed(2)+' km':'—'}</td><td>${finite(candidate.data_quality)?(Number(candidate.data_quality)*100).toFixed(0)+'%':'—'}</td><td><span class="continuity${gaps?' gap':''}">${gaps?`${gaps} GAP${gaps===1?'':'S'}`:'CONTINUOUS'}</span></td></tr>`;
    }).join('');
    body.querySelectorAll('tr[data-candidate-mmsi]').forEach(row=>{
      const choose=()=>selectCandidate(row.dataset.candidateMmsi);
      row.addEventListener('click',choose);
      row.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();choose();}});
    });
  }

  function setNominationGate(id,passed,description) {
    const gate=byId(id);gate.dataset.state=passed?'pass':'fail';gate.querySelector('em').textContent=passed?'PASS':'FAIL';if(description)gate.querySelector('small').textContent=description;
  }

  function renderNominationGate(snapshot) {
    const attribution=snapshot.attribution||{};
    const candidates=Array.isArray(attribution.candidates)?attribution.candidates:[];
    const top=candidates[0];
    const complete=String(attribution.status||'').toUpperCase()==='COMPLETE'&&top;
    if(!complete)return;
    const score=Number(top.total_score||0),margin=Number(attribution.score_margin||0),quality=Number(top.data_quality||0),error=Number(top.forward_error_km??999);
    setNominationGate('gateTopScore',score>=.4,`${(score*100).toFixed(1)}% comparative score`);
    setNominationGate('gateScoreMargin',margin>=.05,`${(margin*100).toFixed(1)} point lead`);
    setNominationGate('gateTrackQuality',quality>=.4,`${(quality*100).toFixed(1)}% data quality`);
    setNominationGate('gateForwardError',error<=8,`${error.toFixed(2)} km replay error`);
    const decision=String(attribution.decision||'ABSTAIN_INSUFFICIENT_EVIDENCE');
    const abstain=decision.startsWith('ABSTAIN');
    const verdict=byId('gateVerdict');verdict.className=`gate-verdict ${abstain?'abstain':'shortlist'}`;
    verdict.querySelector('b').textContent=abstain?'ABSTAIN · INSUFFICIENT SEPARATION':'LIMITED SHORTLIST';
    const tieCount=candidates.filter(candidate=>Math.abs(Number(candidate.total_score)-score)<1e-9).length;
    verdict.querySelector('p').textContent=abstain?`${tieCount} displayed candidates share the highest score; a ${Number(margin*100).toFixed(1)}-point lead cannot support nomination.`:'All minimum gates passed. Human investigation and independent verification remain required.';
  }

  function renderCandidateWorkspace(snapshot) {
    const attribution=snapshot.attribution||{};
    const candidates=Array.isArray(attribution.candidates)?attribution.candidates:[];
    const complete=String(attribution.status||'').toUpperCase()==='COMPLETE';
    const decision=String(attribution.decision||'NOT_EVALUATED');
    const abstain=decision.startsWith('ABSTAIN');
    const status=byId('attributionDecision');status.dataset.state=complete?(abstain?'abstain':'shortlist'):'waiting';status.querySelector('b').textContent=complete?(abstain?'ABSTAIN · INSUFFICIENT EVIDENCE':'LIMITED SHORTLIST'):'WAITING FOR RECONSTRUCTION';status.querySelector('small').textContent=complete?(abstain?'Evidence gates prevent vessel nomination':'Candidate separation passed minimum gates'):'No candidate result available';
    const top=candidates[0];
    html('candidatePopulation',compactNumber(attribution.candidate_count));
    html('highestCandidateScore',top&&finite(top.total_score)?`${(Number(top.total_score)*100).toFixed(1)}%`:'—');
    html('candidateScoreMargin',finite(attribution.score_margin)?`${(Number(attribution.score_margin)*100).toFixed(1)} pts`:'—');
    html('candidateSafeOutput',complete?(abstain?'NO NOMINATION':'LIMITED SHORTLIST'):'—');
    html('candidateSafeReason',complete?(abstain?'Ambiguous evidence remains unresolved':'All minimum evidence gates passed'):'Evidence gate not evaluated');
    html('candidateMapTime',`${formatUtc(attribution.release_time_utc)} → ${formatUtc(attribution.observation_time_utc)}`);
    if(!selectedCandidateMmsi&&top)selectedCandidateMmsi=String(top.mmsi);
    const selected=selectedCandidate(snapshot);
    renderCandidateInspector(snapshot,selected);
    renderCandidateRows(snapshot);
    renderNominationGate(snapshot);
    const rankingLink=byId('rankingChartLink');rankingLink.hidden=!attribution.ranking_chart_url;if(!rankingLink.hidden)rankingLink.href=attribution.ranking_chart_url;
    const mapLink=byId('attributionMapLink');mapLink.hidden=!attribution.attribution_map_url;if(!mapLink.hidden)mapLink.href=attribution.attribution_map_url;
    renderCandidateMap(snapshot);
  }

  function selectCandidate(mmsi) {
    selectedCandidateMmsi=String(mmsi);
    if(!lastSnapshot)return;
    renderCandidateInspector(lastSnapshot,selectedCandidate(lastSnapshot));
    renderCandidateRows(lastSnapshot);
    renderCandidateMap(lastSnapshot);
  }

  function setResponseChain(id, state, label) {
    const item = byId(id);
    item.dataset.state = state;
    item.querySelector('em').textContent = label || (state === 'verified' ? 'VERIFIED' : state === 'missing' ? 'MISSING' : 'WAITING');
  }

  function renderResponseWorkspace(snapshot) {
    const analysis = snapshot.analysis || {};
    const review = snapshot.review || {};
    const attribution = snapshot.attribution || {};
    const response = snapshot.response || {};
    const attributionComplete = String(attribution.status || '').toUpperCase() === 'COMPLETE';
    const responseStatus = String(response.status || 'NOT_BUILT').toUpperCase();
    const ready = responseStatus === 'READY';
    const building = responseStatus === 'BUILDING';
    const error = responseStatus === 'ERROR';
    const decision = String(response.operational_decision || attribution.decision || 'NOT_EVALUATED');
    const abstain = decision.startsWith('ABSTAIN');
    const packageIncomplete = ready && Number(response.missing_required_files || 0) > 0;
    const status = byId('responseStatus');
    status.dataset.state = error || packageIncomplete ? 'error' : ready ? (abstain ? 'abstain' : 'ready') : building ? 'ready' : 'waiting';
    status.querySelector('b').textContent = error ? 'PACKAGE FAILED' : packageIncomplete ? 'PACKAGE INCOMPLETE' : ready ? (abstain ? 'SAFE ABSTENTION READY' : 'ANALYST SHORTLIST READY') : building ? 'BUILDING EVIDENCE PACKAGE…' : attributionComplete ? 'READY TO PACKAGE' : 'WAITING FOR ATTRIBUTION';
    status.querySelector('small').textContent = error ? String(response.message || 'The response package could not be generated.') : ready ? `${compactNumber(response.verified_files)} evidence files integrity-checked` : building ? 'Hashing artifacts and assembling the dossier' : attributionComplete ? 'Candidate attribution is complete' : 'No package is available';

    const button = byId('buildResponseButton');
    button.disabled = !attributionComplete || building;
    button.textContent = building ? 'Building package…' : ready ? 'Refresh evidence package' : 'Build evidence package';

    html('responseDecision', attributionComplete ? (abstain ? 'NO NOMINATION' : 'LIMITED SHORTLIST') : '—');
    html('responseVerified', ready ? compactNumber(response.verified_files) : '—');
    html('responseRequired', ready ? `${compactNumber(response.required_files)} required files checked` : 'required evidence not checked');
    html('responseMissing', ready ? compactNumber(response.missing_required_files) : '—');
    html('responseAction', ready ? String(response.permitted_action || 'HUMAN REVIEW ONLY').replaceAll('_', ' ') : '—');
    html('responseCaseReference', analysis.scene_id ? `CASE · ${analysis.scene_id}` : 'CASE NOT READY');

    const callout = byId('responseDecisionCallout');
    callout.dataset.state = attributionComplete ? (abstain ? 'abstain' : 'shortlist') : 'waiting';
    html('responseCalloutTitle', attributionComplete ? (abstain ? 'No vessel nominated' : 'Human review shortlist only') : 'Awaiting evidence');
    html('responseRationale', response.rationale || (attributionComplete ? (abstain ? 'The evidence does not safely separate one vessel from the alternatives. The system preserves the case and requests stronger corroboration.' : 'Minimum ranking gates passed, but independent corroboration and accountable human review remain mandatory.') : 'The response layer activates after candidate attribution completes.'));

    const reviewControl = byId('controlReview');
    reviewControl.dataset.state = ready ? 'ready' : 'waiting';
    reviewControl.querySelector('em').textContent = ready ? 'READY' : 'WAITING';
    reviewControl.querySelector('small').textContent = ready ? 'Auditable package available to an accountable analyst' : 'Requires a complete evidence package';
    const evidenceControl = byId('controlEvidence');
    evidenceControl.dataset.state = ready && abstain ? 'ready' : 'waiting';
    evidenceControl.querySelector('em').textContent = ready && abstain ? 'RECOMMENDED' : 'WAITING';
    evidenceControl.querySelector('small').textContent = ready && abstain ? 'Seek independent AIS, SAR, optical, port or sampling evidence' : 'Driven by the decision gate';

    const observationVerified = Boolean(analysis.scene_id) && ['REVIEW_REQUIRED', 'COMPLETE'].includes(String(analysis.status || '').toUpperCase());
    const inferenceVerified = observationVerified && Boolean(analysis.physics_screen);
    const reviewVerified = String(review.status || '').toUpperCase() === 'APPROVED';
    const driftVerified = attributionComplete && Boolean(attribution.origin_zone_url) && Boolean(attribution.release_time_utc);
    const correlationVerified = attributionComplete && Array.isArray(attribution.candidates) && Boolean(attribution.candidate_tracks_url);
    const decisionVerified = attributionComplete && Boolean(attribution.decision);
    setResponseChain('chainObservation', observationVerified ? 'verified' : 'waiting');
    setResponseChain('chainInference', inferenceVerified ? 'verified' : 'waiting');
    setResponseChain('chainReview', reviewVerified ? 'verified' : 'waiting');
    setResponseChain('chainDrift', driftVerified ? 'verified' : 'waiting');
    setResponseChain('chainCorrelation', correlationVerified ? 'verified' : 'waiting');
    setResponseChain('chainDecision', decisionVerified ? 'verified' : 'waiting');

    html('responseDigest', ready ? response.chain_digest_sha256 || 'Digest unavailable' : 'Not generated');
    html('responseGenerated', ready ? `Generated ${formatUtc(response.generated_at_utc)} · ${packageIncomplete ? 'required evidence is missing' : 'integrity register complete'}` : 'Every included file receives an integrity fingerprint.');
    const links = [
      ['dossierLink', response.dossier_url],
      ['bundleLink', response.bundle_url],
      ['manifestLink', response.manifest_url],
      ['responseSummaryLink', response.summary_url],
    ];
    links.forEach(([id, url]) => { const link = byId(id); link.hidden = !ready || !url; if (!link.hidden) link.href = url; });
    html('responseMessage', response.message || (attributionComplete ? 'The completed attribution can now be sealed into an auditable evidence package.' : 'No response package has been generated for this case.'));

    const recommendations = Array.isArray(response.recommended_actions) && response.recommended_actions.length
      ? response.recommended_actions
      : attributionComplete
        ? ['Generate the evidence package.', 'Preserve the case record.', 'Obtain independent corroborating evidence.', 'Re-run only when new evidence changes the record.']
        : ['Complete attribution before issuing a response plan.'];
    byId('responseRecommendations').innerHTML = recommendations.map(item => `<li>${escapeMarkup(item)}</li>`).join('');
    const blocked = Array.isArray(response.blocked_actions) && response.blocked_actions.length
      ? response.blocked_actions
      : ['Automatic vessel accusation', 'Enforcement notification without accountable human review', 'Treating comparative scores as guilt probabilities'];
    byId('responseBlockedActions').innerHTML = blocked.map(item => `<li>${escapeMarkup(item)}</li>`).join('');
  }

  async function buildResponsePackage() {
    const button = byId('buildResponseButton');
    button.disabled = true;
    button.textContent = 'Building package…';
    try {
      const response = await fetch(RESPONSE_ENDPOINT, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      if (lastSnapshot) renderResponseWorkspace({...lastSnapshot, response: result});
      window.setTimeout(poll, 400);
    } catch (error) {
      byId('responseStatus').dataset.state = 'error';
      byId('responseStatus').querySelector('b').textContent = 'PACKAGE FAILED';
      byId('responseStatus').querySelector('small').textContent = error.message;
      html('responseMessage', `Evidence package failed: ${error.message}`);
      button.disabled = false;
      button.textContent = 'Retry evidence package';
    }
  }

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
    renderDetectionWorkbench(snapshot);
    renderReverseDrift(snapshot);
    renderCandidateWorkspace(snapshot);
    renderResponseWorkspace(snapshot);
    html('mapEvidenceTime', formatUtc(evidenceTime(snapshot)));
    updateMapMode(mapMode);
    syncMapGeometry(snapshot).catch(error => {
      byId('mapLoading').hidden = false;
      html('mapLoading', 'Published map geometry could not be loaded.');
      html('mapLayerStatus', `Map data unavailable · ${error.message}`);
    });
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
  byId('liveModeButton').addEventListener('click', () => updateMapMode('live'));
  byId('incidentModeButton').addEventListener('click', () => updateMapMode('incident'));
  byId('layerControls').addEventListener('change', () => { if (lastSnapshot) renderMap(lastSnapshot); });
  byId('sarSceneSelect').addEventListener('change', event => {
    selectedSceneId = event.target.value;
    if(lastSnapshot) renderDetectionWorkbench(lastSnapshot);
  });
  document.querySelectorAll('[data-sar-view]').forEach(button => button.addEventListener('click',()=>setSarView(button.dataset.sarView)));
  byId('sarEvidenceImage').addEventListener('error',()=>{
    byId('sarEvidenceImage').hidden = true;
    byId('sarImageEmpty').hidden = false;
    byId('sarImageEmpty').querySelector('strong').textContent = 'Evidence image unavailable';
    byId('sarImageEmpty').querySelector('span').textContent = 'The result record is preserved, but this image could not be loaded.';
  });
  byId('releaseAgeInput').addEventListener('input',event=>html('releaseAgeValue',`${event.target.value} h`));
  byId('analyzeSarButton').addEventListener('click',analyzeSelectedSar);
  byId('approveCandidateButton').addEventListener('click',()=>submitCandidateReview('APPROVE'));
  byId('rejectCandidateButton').addEventListener('click',()=>submitCandidateReview('REJECT'));
  document.querySelectorAll('[data-drift-view]').forEach(button=>button.addEventListener('click',()=>setDriftView(button.dataset.driftView)));
  byId('buildAttributionButton').addEventListener('click',buildAttribution);
  byId('buildResponseButton').addEventListener('click',buildResponsePackage);
  updateMapMode('live');
  tickClock();
  poll();
  window.setInterval(tickClock, 1000);
  window.setInterval(poll, POLL_MS);
})();
