/* An isolated, deterministic exercise. No live provider or case API is called here. */
(() => {
  'use strict';

  const MODE_KEY = 'espada-example-mode-v1';
  const STATE_KEY = 'espada-example-state-v1';
  const SCENE_ID = 'EXERCISE_SAR_20260918T060000';
  const RELEASE_TIME = '2026-09-18T02:00:00Z';
  const OBSERVATION_TIME = '2026-09-18T06:00:00Z';
  const REGION = {name:'East Singapore Offshore Watch · EXERCISE',bbox:[104.02,1.20,104.23,1.31],center:[104.125,1.255]};
  const CURRENT = {east:0.13,north:0.025};
  const WIND = {east:4,north:1};
  const WINDAGE = 0.02;
  const TRUTH = [104.095,1.245];
  const DRIFT = [CURRENT.east + WINDAGE * WIND.east,CURRENT.north + WINDAGE * WIND.north];
  const hoursToDegrees = (vector,hours) => [vector[0] * hours * 3600 / 111300,vector[1] * hours * 3600 / 111300];
  const add = (a,b) => [a[0]+b[0],a[1]+b[1]];
  const OBSERVED = add(TRUTH,hoursToDegrees(DRIFT,4));
  const when = hours => new Date(Date.parse(RELEASE_TIME)+hours*3600000).toISOString();
  const km = (a,b) => Math.hypot((a[0]-b[0])*111.3*Math.cos(a[1]*Math.PI/180),(a[1]-b[1])*111.3);
  const round = value => Number(value.toFixed(4));
  const polygon = (centre, scale=1) => {
    const [x,y] = centre;
    const points = [[-.007,-.002],[-.005,.002],[0,.003],[.008,.0025],[.011,.0005],[.006,-.0015],[.001,-.003],[-.007,-.002]];
    return {type:'Feature',properties:{source:'SYNTHETIC EXERCISE'},geometry:{type:'Polygon',coordinates:[points.map(([dx,dy])=>[round(x+dx*scale),round(y+dy*scale)])]}};
  };
  const circle = (centre,radiusKm) => ({type:'Feature',properties:{source:'MODELLED EXERCISE'},geometry:{type:'Polygon',coordinates:[Array.from({length:49},(_,index)=>{
    const angle=2*Math.PI*index/48;
    return [round(centre[0]+radiusKm*Math.cos(angle)/111.3),round(centre[1]+radiusKm*Math.sin(angle)/111.3)];
  })]}});
  const vessels = [
    {mmsi:'999000101',vessel_name:'MV ASTER VALE · EXERCISE',origin:[104.095,1.245],delta:[.018,.002],sog:1.1,cog:84,quality:.97},
    {mmsi:'999000102',vessel_name:'MV PACIFIC WREN · EXERCISE',origin:[104.126,1.239],delta:[-.013,.002],sog:.8,cog:278,quality:.91},
    {mmsi:'999000103',vessel_name:'MV BLUE LANTERN · EXERCISE',origin:[104.070,1.278],delta:[.014,-.001],sog:.85,cog:94,quality:.93},
    {mmsi:'999000104',vessel_name:'MV EASTERN TERN · EXERCISE',origin:[104.184,1.229],delta:[-.012,.001],sog:.75,cog:275,quality:.87},
    {mmsi:'999000105',vessel_name:'MV HARBOR CEDAR · EXERCISE',origin:[104.206,1.294],delta:[0,0],sog:0,cog:null,quality:.88,motion_state:'stationary'},
    {mmsi:'999000106',vessel_name:'MV SILVER HORIZON · EXERCISE',origin:[104.119,1.255],delta:[.014,.001],sog:.85,cog:86,quality:.42,offset_hours:3}
  ];
  let dossierUrl=null;
  let dossierKey='';
  const defaultState = () => ({stage:0,age_hours:4,rejected:false});
  function state() {
    try {
      const saved=JSON.parse(sessionStorage.getItem(STATE_KEY)||'null');
      return saved && Number.isInteger(saved.stage) && saved.stage>=0 && saved.stage<=4 && Number.isFinite(Number(saved.age_hours)) ? saved : defaultState();
    } catch { return defaultState(); }
  }
  const save = value => sessionStorage.setItem(STATE_KEY,JSON.stringify(value));
  const enabled = () => sessionStorage.getItem(MODE_KEY)==='true';
  function setMode(value) {
    sessionStorage.setItem(MODE_KEY,value?'true':'false');
    if (value && !sessionStorage.getItem(STATE_KEY)) save(defaultState());
  }
  function reset() { save(defaultState()); }
  function analyze() { const s=state(); s.stage=1; s.rejected=false; save(s); }
  function review(decision,age) {
    const s=state();
    if (s.stage<1) throw new Error('Analyze the exercise scene first.');
    if (decision==='REJECT') { s.rejected=true; s.stage=1; }
    else {
      if (!Number.isFinite(age)||age<1||age>4) throw new Error('Choose a release age from 1 to 4 hours, the range covered by these exercise tracks.');
      s.age_hours=age; s.rejected=false; s.stage=2;
    }
    save(s);
  }
  function estimateOrigin(age) { return add(OBSERVED,hoursToDegrees(DRIFT,-age)); }
  function estimateAtHours(vessel,hours) { return add(vessel.origin,vessel.delta.map(value=>value*hours)); }
  function ranked(age) {
    const origin=estimateOrigin(age);
    return vessels.filter(v=>!v.offset_hours).map(v=>{
      const report=estimateAtHours(v,4-age);
      const distance=km(report,origin);
      const presence=Math.exp(-distance/2.4);
      const forward=Math.exp(-distance/2.8);
      const score=.48*presence+.32*forward+.20*v.quality;
      return {
        mmsi:v.mmsi,vessel_name:v.vessel_name,total_score:score,presence_score:presence,
        forward_consistency:forward,data_quality:v.quality,forward_error_km:distance,
        best_match_time_utc:new Date(Date.parse(OBSERVATION_TIME)-age*3600000).toISOString(),
        motion_state:v.motion_state||'underway',track_bearing_deg:v.cog,
        release_position_interpolated:false,significant_gaps:0,
        silence_classification:'no_significant_gap',identity_status:'synthetic_identity'
      };
    }).sort((a,b)=>b.total_score-a.total_score).map((v,index)=>({...v,rank:index+1}));
  }
  function reconstruct() {
    const s=state();
    if (s.stage<2) throw new Error('Approve the exercise slick first.');
    s.stage=3;save(s);
  }
  function packageCase() {
    const s=state();
    if (s.stage<3) throw new Error('Complete reconstruction first.');
    s.stage=4;save(s);
  }
  function geo(key) {
    const s=state(),origin=estimateOrigin(s.age_hours);
    if (key==='footprint') return {type:'FeatureCollection',features:[{type:'Feature',properties:{scene_id:SCENE_ID},geometry:{type:'Polygon',coordinates:[[[104.04,1.215],[104.04,1.295],[104.21,1.295],[104.21,1.215],[104.04,1.215]]]}}]};
    if (key==='slick') return polygon(OBSERVED);
    if (key==='origin') return circle(origin,1.7);
    if (key==='tracks') return {type:'FeatureCollection',features:ranked(s.age_hours).map(v=>{
      const ship=vessels.find(item=>item.mmsi===v.mmsi);
      return {type:'Feature',properties:{mmsi:v.mmsi,rank:v.rank,vessel_name:v.vessel_name},geometry:{type:'LineString',coordinates:[0,1,2,3,4].map(hour=>estimateAtHours(ship,hour).map(round))}};
    })};
    return null;
  }
  function sarImage(view) {
    // The displayed dark pixels, boundary, mask and geographic polygon share one geometry.
    const bounds=[OBSERVED[0]-.018,OBSERVED[1]-.013,OBSERVED[0]+.018,OBSERVED[1]+.013];
    const points=polygon(OBSERVED).geometry.coordinates[0];
    const contour=points.map(([lon,lat],index)=>`${index?'L':'M'}${((lon-bounds[0])/(bounds[2]-bounds[0])*800).toFixed(1)} ${((bounds[3]-lat)/(bounds[3]-bounds[1])*480).toFixed(1)}`).join(' ')+' Z';
    const mask=view==='mask';
    const overview=view==='overview';
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 480" role="img">
      <defs><filter id="noise"><feTurbulence type="fractalNoise" baseFrequency=".14" numOctaves="3" seed="19"/></filter></defs>
      <rect width="800" height="480" fill="${mask?'#111':'#58636a'}"/>
      ${mask?'':`<rect width="800" height="480" filter="url(#noise)" opacity=".31"/><path d="${contour}" fill="#171e21" opacity=".87"/>`}
      ${mask?`<path d="${contour}" fill="#fff"/>`:overview?`<path d="${contour}" fill="none" stroke="#efad4f" stroke-width="5"/>`:''}
      <rect x="18" y="18" width="250" height="36" fill="#101c23" opacity=".88"/>
      <text x="30" y="43" fill="#fff" font-family="Arial" font-size="15">SYNTHETIC SAR-LIKE EXERCISE</text>
      <text x="30" y="449" fill="#fff" font-family="Arial" font-size="14">Illustrative scene; polygon and mask use the same shape.</text>
    </svg>`;
    return 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);
  }
  function dossier(s,ranking) {
    const key=`${s.stage}:${s.age_hours}`;
    if (dossierUrl && dossierKey===key) return dossierUrl;
    if (dossierUrl) URL.revokeObjectURL(dossierUrl);
    const top=ranking[0],truthRank=ranking.find(v=>v.mmsi==='999000101')?.rank;
    const page=`<!doctype html><html lang="en"><meta charset="utf-8"><title>ESPADA example incident dossier</title><style>body{font:16px/1.55 Segoe UI,Arial,sans-serif;background:#f3f0e7;color:#17232c;max-width:850px;margin:45px auto;padding:0 24px}header{background:#0b2538;color:white;padding:25px}h1{margin:0}section{background:white;border:1px solid #c8c6bd;padding:22px;margin:20px 0}b{color:#0b2538}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #ddd;padding:9px}.flag{background:#f2dfbd;padding:12px}</style><header><small>ESPADA · SYNTHETIC EXERCISE ONLY</small><h1>Example incident evidence dossier</h1></header><p class="flag">All ships, AIS reports, SAR-like imagery and environmental values in this document are generated for demonstration. No real vessel is implicated.</p><section><h2>Observation and reconstruction</h2><p>Fixed exercise observation: ${OBSERVATION_TIME}. Selected age: ${s.age_hours} h. Assumed current: 0.13 m/s east, 0.025 m/s north; wind: 4 m/s east, 1 m/s north; windage: 2%.</p><p>Estimated origin: ${estimateOrigin(s.age_hours).map(v=>v.toFixed(4)).join(', ')}. The observed candidate is a synthetic dark polygon, subject to an analyst exercise approval.</p></section><section><h2>Incident-time candidate ranking</h2><table><tr><th>Rank</th><th>Exercise vessel</th><th>Comparative score</th><th>Release-area distance</th></tr>${ranking.map(v=>`<tr><td>${v.rank}</td><td>${v.vessel_name}</td><td>${(v.total_score*100).toFixed(1)}%</td><td>${v.forward_error_km.toFixed(2)} km</td></tr>`).join('')}</table><p>Scores compare this exercise's tracks; they are not probabilities of guilt.</p></section><section><h2>Known answer revealed after ranking</h2><p>The configured exercise release vessel was MV ASTER VALE. Its rank is <b>#${truthRank} of ${ranking.length}</b>; the displayed top rank is <b>${top.vessel_name}</b>.</p><p>This checks one designed example. It does not establish real-world accuracy, oil identity, or legal responsibility.</p></section>`;
    dossierUrl=URL.createObjectURL(new Blob([page],{type:'text/html'}));
    dossierKey=key;
    return dossierUrl;
  }
  function snapshot() {
    const s=state(),now=new Date().toISOString();
    const age=s.age_hours,release=new Date(Date.parse(OBSERVATION_TIME)-age*3600000).toISOString();
    const origin=estimateOrigin(age),ranking=ranked(age);
    const positions=vessels.filter(v=>!v.offset_hours).map(v=>({
      mmsi:v.mmsi,vessel_name:v.vessel_name,longitude:round(estimateAtHours(v,4)[0]),latitude:round(estimateAtHours(v,4)[1]),
      timestamp_utc:OBSERVATION_TIME,motion_state:v.motion_state||'underway',sog:v.sog,cog:v.cog,source:'SYNTHETIC EXERCISE'
    })).filter(v=>v.longitude>=REGION.bbox[0]&&v.longitude<=REGION.bbox[2]&&v.latitude>=REGION.bbox[1]&&v.latitude<=REGION.bbox[3]);
    const tracks=Object.fromEntries(vessels.filter(v=>!v.offset_hours).map(v=>[v.mmsi,[0,1,2,3,4].map(hour=>estimateAtHours(v,hour).map(round))]));
    const source=(label,extra={})=>({status:'PASS',provider:`SYNTHETIC EXERCISE · ${label}`,message:'Generated case input; not a live provider response.',last_attempt_utc:now,last_success_utc:now,latest_observation_utc:OBSERVATION_TIME,...extra});
    const scene={id:SCENE_ID,platform:'Exercise SAR-like image',acquisition_time_utc:OBSERVATION_TIME,polarizations:['VV-like'],orbit_state:'EXERCISE',aoi_overlap_fraction:.88};
    const approved=s.stage>=2&&!s.rejected,complete=s.stage>=3;
    const analysis=s.stage>=1?{
      status:'REVIEW_REQUIRED',scene_id:SCENE_ID,acquisition_time_utc:OBSERVATION_TIME,
      input_url:sarImage('input'),overview_url:sarImage('overview'),mask_url:sarImage('mask'),
      detected_components:1,detected_pixel_fraction:.008,probability_summary:{maximum:.93},
      provenance_verified:false,input_provenance:{polarization:'VV-like',measurement:'generated exercise pixels'},
      model_provenance:{model_generation:'EXERCISE',threshold:.5},
      physics_screen:{status:'PLAUSIBLE_DARK_SIGNATURE',contrast_gate_passed:true,wind_gate_passed:true,
        weighted_local_contrast_db:-3.1,wind_speed_ms:Math.hypot(WIND.east,WIND.north),wind_time_utc:OBSERVATION_TIME,
        method:'Synthetic contrast and wind check',interpretation:'The generated dark feature passes the exercise plausibility gates.'},
      message:'Generated SAR-like image retained for an analyst exercise decision.'
    }:{status:'NOT_RUN'};
    const review=s.rejected?{status:'REJECTED',message:'Exercise candidate rejected. Reset or reanalyse to try again.'}:approved?{
      status:'APPROVED',message:'Synthetic candidate approved for exercise reconstruction.',assumed_age_hours:age,
      reviewed_at_utc:now,estimated_release_time_utc:release,handoff_status:'SEALED',handoff_verified:true,
      approved_slick_url:'example:slick',slick_geometry_sha256:'exercise-only-no-cryptographic-seal'
    }:{status:'NOT_REVIEWED',message:'Analyst exercise approval is required.'};
    const effective=ranking.filter(v=>v.forward_error_km<=6),excluded=vessels.filter(v=>v.offset_hours||!effective.some(c=>c.mmsi===v.mmsi));
    const filterRecord=(v,retained)=>{
      const position=estimateAtHours(v,4-age),distance=km(position,origin);
      return {mmsi:v.mmsi,vessel_name:v.vessel_name,disposition:retained?'retained':'excluded',
        reason:v.offset_hours?'outside_release_window':retained?'space_time_gate_passed':'outside_origin_search_area',
        closest_report_time_utc:v.offset_hours?when(3):release,closest_release_distance_km:distance,
        closest_time_offset_hours:v.offset_hours||0,motion_state:v.motion_state||'underway',
        course_evidence:'context_only',track_continuity:v.offset_hours?'sparse':'continuous',positions:v.offset_hours?1:5};
    };
    const scores=effective,top=scores[0],margin=(top?.total_score||0)-(scores[1]?.total_score||0);
    const gates=[['candidate_population',scores.length>=2,'Candidate population','At least two incident-relevant tracks'],
      ['top_score',(top?.total_score||0)>=.4,'Top score','Comparative score at least 40%'],
      ['score_margin',margin>=.05,'Score margin','Lead at least 5 points'],
      ['track_quality',(top?.data_quality||0)>=.4,'Track quality','At least 40%'],
      ['forward_error',(top?.forward_error_km??999)<=8,'Forward check','Within 8 km']]
      .map(([id,passed,label,requirement])=>({id,passed,label,requirement}));
    const decision=gates.every(g=>g.passed)?'LIMITED_SHORTLIST':'ABSTAIN_INSUFFICIENT_EVIDENCE';
    const particles=Array.from({length:160},(_,i)=>{
      const a=2*Math.PI*i/160,r=1.7*Math.sqrt((i+.5)/160);
      return [round(origin[0]+r*Math.cos(a)/111.3),round(origin[1]+r*Math.sin(a)/111.3)];
    });
    const forward=particles.map(p=>add(p,hoursToDegrees(DRIFT,age)).map(round));
    const attribution=complete?{
      status:'COMPLETE',drift_status:'COMPLETE',observation_time_utc:OBSERVATION_TIME,release_time_utc:release,
      assumed_age_hours:age,forcing_source:'Synthetic fixed current + wind exercise values',
      observed_centroid:OBSERVED,estimated_origin:origin,origin_particles:particles,forward_replay_particles:forward,
      credible_radius_90_km:1.7,forward_closure:{centroid_error_km:0,cloud_shape_error_km:1.7,particles_retained:160},
      origin_zone_url:'example:origin',candidate_tracks_url:'example:tracks',candidates:scores,candidate_count:scores.length,
      candidates_compared:vessels.length,top_candidate:top,score_margin:margin,decision,
      nomination_assessment:{gates,failed_gate_ids:gates.filter(g=>!g.passed).map(g=>g.id),rationale:'Exercise ranking only. A real case requires independent corroboration.'},
      ais_filter:{status:'PASS',raw_vessels:vessels.length,release_window_vessels:vessels.length-1,
        origin_zone_vessels:effective.length,retained_vessels:effective.length,excluded_vessels:excluded.length,
        release_window_hours:1.5,search_radius_km:6,retained:effective.map(v=>filterRecord(vessels.find(item=>item.mmsi===v.mmsi),true)),
        excluded:excluded.map(v=>filterRecord(v,false))}
    }:{status:'NOT_RUN',candidates:[]};
    const response=s.stage>=4?{
      status:'READY',operational_decision:decision,permitted_action:'EXERCISE_REVIEW_ONLY',
      rationale:'This is a generated case. The ranked vessel is the configured exercise source; no real-world finding is made.',
      verified_files:0,required_files:0,missing_required_files:0,generated_at_utc:now,
      message:'Synthetic exercise dossier available. No real evidence files were sealed or hash-verified.',
      dossier_url:dossier(s,ranking),recommended_actions:['Compare the ranking with the known exercise source.','Repeat with a different assumed slick age.','Seek independent evidence in a real incident.'],
      blocked_actions:['Treating exercise scores as guilt probabilities','Naming a real vessel from synthetic inputs']
    }:{status:'NOT_BUILT'};
    const stage=s.stage>=4?'EVIDENCE_PACKAGE_READY':s.stage>=3?'EVIDENCE_SHORTLIST_READY':s.stage>=2?'ANALYST_APPROVED':s.stage>=1?'REVIEW_REQUIRED':'NOT_RUN';
    const record=s.stage>=1?{
      scene_id:SCENE_ID,platform:'Synthetic exercise',polarization:'VV-like',acquisition_time_utc:OBSERVATION_TIME,
      stage,operational_decision:complete?decision:null,artifact_count:0,verified_files:0,
      integrity_status:'EXERCISE_ONLY',provenance_warnings:[],
      milestones:{observed:true,detected:true,reviewed:approved,reconstructed:complete,attributed:complete,packaged:s.stage>=4},
      urls:{dossier:s.stage>=4?dossier(s,ranking):null,sar_diagnostic:sarImage('overview')}
    }:null;
    return {
      status:'EXERCISE',exercise:true,generated_at_utc:now,updated_at_utc:now,region:REGION,
      sources:{
        ais:source('GENERATED AIS POSITIONS'),
        sentinel:source('GENERATED SAR-LIKE SCENE',{latest_scene:scene,scenes:[scene],scenes_returned:1,footprints_url:'example:footprint'}),
        environment:source('FIXED DRIFT INPUTS',{temporal_resolution:'fixed exercise field',current:{
          current_east_ms:CURRENT.east,current_north_ms:CURRENT.north,current_speed_ms:Math.hypot(CURRENT.east,CURRENT.north),
          wind_east_ms:WIND.east,wind_north_ms:WIND.north,wind_speed_ms:Math.hypot(WIND.east,WIND.north)
        }})
      },
      ais:{mode:'exercise',positions,tracks,vessel_count:positions.length,
        underway_count:positions.filter(v=>v.motion_state==='underway').length,
        stationary_count:positions.filter(v=>v.motion_state==='stationary').length,window_minutes:10},
      coastline_url:'/operator/live_command/example_coast.geojson',
      map_context:{name:'East Singapore exercise overview',bbox:[103.85,1.10,104.35,1.45],
        coastline_url:'/operator/live_command/example_context_coast.geojson'},
      analysis,review,attribution,response,
      retasking:{status:'NOT_READY',tasks:[]},evidence_intake:{status:'NOT_READY',receipts:[]},
      reanalysis:{status:'NOT_READY'},closure:{status:'NOT_READY',versions:[]},
      case_register:{cases:record?[record]:[],case_count:record?1:0,sealed_count:0,
        review_required_count:record&&s.stage===1?1:0,provenance_warning_count:0},
      pipeline:{stage},truth_policy:{synthetic_fallback:false,exercise_data:true}
    };
  }
  window.espadaExample={enabled,setMode,reset,analyze,review,reconstruct,packageCase,snapshot,geometry:geo,sceneId:SCENE_ID};
})();
