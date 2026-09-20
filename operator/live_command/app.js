(() => {
  'use strict';

  const POLL_MS = 15000;
  const SNAPSHOT_ENDPOINT = '/api/live/snapshot';
  const REFRESH_ENDPOINT = '/api/live/refresh';
  const ANALYZE_ENDPOINT = '/api/live/analyze-latest-sar';
  const REVIEW_ENDPOINT = '/api/live/review';
  const byId = id => document.getElementById(id);
  let lastSnapshot = null;
  let mapMode = 'live';
  let mapGeometry = {coast: null, footprints: null, slick: null, origin: null, candidateTracks: null};
  const geometryCache = new Map();
  let geometryLoadKey = '';
  let sarView = 'overview';
  let selectedSceneId = null;

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
    const scenes = Array.isArray(snapshot.sources?.sentinel?.scenes) ? snapshot.sources.sentinel.scenes : [];
    const preferred = selectedSceneId || snapshot.analysis?.requested_scene_id || snapshot.analysis?.scene_id || scenes[0]?.id;
    select.replaceChildren();
    scenes.forEach(scene => {
      const option = document.createElement('option');
      option.value = scene.id;
      const acquired = parseTime(scene.acquisition_time_utc);
      const ageHours = acquired ? (Date.now() - acquired.getTime()) / 3600000 : null;
      option.textContent = `${formatUtc(scene.acquisition_time_utc)} · ${String(scene.platform || 'Sentinel-1').toUpperCase()} · ${ageHours !== null && ageHours >= 96 ? 'AIS READY' : 'AIS DELAY'}`;
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
    const scene = (snapshot.sources?.sentinel?.scenes || []).find(item => item.id === selectedSceneId);
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
  updateMapMode('live');
  tickClock();
  poll();
  window.setInterval(tickClock, 1000);
  window.setInterval(poll, POLL_MS);
})();
