// Deterministic port of the Python M0–M4 domain logic. No network, storage, or wall clock.
export const trackDefaults={gap_after:60,lost_after:300,expire_after:1800,history_size:120,tick_interval:1};
export const alertDefaults={gap_seconds:60,source_stale_seconds:45,airport_radius_km:8,dedup_retention_seconds:1800,recent_limit:200,airport_file:''};
const emergencies={'7500':'Unlawful interference code','7600':'Radio communication failure code','7700':'General emergency code'};
const keyOf=o=>`${o.source}:${o.object_id}`;
const numeric=(v,lo=-Infinity,hi=Infinity)=>typeof v==='number'&&Number.isFinite(v)&&v>=lo&&v<=hi;
const validRow=r=>Array.isArray(r)&&r.length>=17&&typeof r[0]==='string'&&/^[a-f\d]{6}$/i.test(r[0]);
const validCode=c=>typeof c==='string'&&/^[0-7]{4}$/.test(c);
export function validPayload(data){return data&&typeof data==='object'&&!Array.isArray(data)&&Number.isInteger(data.time)&&('states' in data)&&(data.states===null||Array.isArray(data.states));}
export function normalize(data,received){
  const observations=[]; let rejected=0;
  for(const r of data.states||[]){
    if(!validRow(r)||!numeric(r[3],0,data.time)||!numeric(r[6],-90,90)||!numeric(r[5],-180,180)||
      !(r[7]===null||numeric(r[7]))||!(r[9]===null||numeric(r[9],0))||!(r[10]===null||numeric(r[10],0,360))||
      !(r[14]===null||validCode(r[14]))||typeof r[8]!=='boolean'){rejected++;continue;}
    observations.push({source:'opensky',object_id:r[0].toLowerCase(),timestamp:r[3],received_at:received,
      lat:r[6],lon:r[5],altitude:r[7],speed:r[9],heading:r[10],squawk:r[14],status:r[8]?'ground':'airborne'});
  }
  return {observations,rejected};
}
export function distanceKm(a,b,c,d){
  const rad=x=>x*Math.PI/180, x=rad(a), y=rad(c);
  const h=Math.sin((y-x)/2)**2+Math.cos(x)*Math.cos(y)*Math.sin(rad(d-b)/2)**2;
  return 6371.0088*2*Math.asin(Math.min(1,Math.sqrt(h)));
}
export class Core{
  constructor(session){
    this.session=session.session; this.trackConfig={...trackDefaults,...session.tracks};this.alertConfig={...alertDefaults,...session.alerts};
    this.reference=session.airport_reference;this.tracks={};this.now=0;this.engineNow=0;this.eligible=[];this.gaps=[];this.signals={};
    this.healthySince=null;this.lastSuccess=null;this.sequence=0;this.alerts=[];
    this.source={state:'starting',message:'Waiting for OpenSky',last_success:null};
  }
  static restore(saved){return Object.assign(Object.create(Core.prototype),structuredClone(saved));}
  dump(){return structuredClone({...this});}
  emit(alert){this.sequence++;const event={version:1,session:this.session,id:`${this.session}:${this.sequence}`,...alert};this.alerts.push(event);return event;}
  age(t){
    this.now=Math.max(this.now,t);
    for(const [key,track] of Object.entries(this.tracks)){
      const age=this.now-track.last_seen;
      if(age>this.trackConfig.expire_after) delete this.tracks[key];
      else track.state=age>this.trackConfig.lost_after?'lost':age>this.trackConfig.gap_after?'gap':'active';
    }
  }
  update(o){
    const key=keyOf(o),track=this.tracks[key];
    if((track&&o.timestamp<=track.last_seen)||this.now-o.timestamp>this.trackConfig.expire_after)return false;
    const current=track||{source:o.source,object_id:o.object_id,first_seen:o.timestamp,last_seen:o.timestamp,history:[],state:'active'};
    current.last_seen=o.timestamp;current.history.push(o);current.history=current.history.slice(-this.trackConfig.history_size);
    this.tracks[key]=current;this.age(this.now);return true;
  }
  failed(reason){this.healthySince=null;this.source={...this.source,state:'retrying',message:reason};}
  stopped(){this.healthySince=null;this.source={...this.source,state:'stopped',message:'Ingestion stopped; check server logs'};}
  healthy(t){
    if(this.lastSuccess===null||t-this.lastSuccess>this.alertConfig.source_stale_seconds)this.healthySince=null;
    if(this.healthySince===null)this.healthySince=t;
    this.lastSuccess=t;
  }
  squawks(data,now){
    for(const r of data.states||[]){
      if(!validRow(r)||!numeric(r[4],0,data.time)||!validCode(r[14]))continue;
      const key=`opensky:${r[0].toLowerCase()}`,stamp=r[4],code=r[14],previous=this.signals[key];
      if((previous&&stamp<=previous[0])||now-stamp<0||now-stamp>this.alertConfig.source_stale_seconds)continue;
      this.signals[key]=[stamp,code];
      if(!emergencies[code]||(previous&&previous[1]===code))continue;
      let lat=null,lon=null,positionTime=null;
      if(numeric(r[6],-90,90)&&numeric(r[5],-180,180)&&numeric(r[3],0,stamp)){lat=r[6];lon=r[5];positionTime=r[3];}
      else if(this.tracks[key]){const p=this.tracks[key].history.at(-1);lat=p.lat;lon=p.lon;positionTime=p.timestamp;}
      this.emit({type:'emergency_squawk',source:'opensky',object_id:r[0].toLowerCase(),timestamp:now,lat,lon,
        details:{squawk:code,meaning:emergencies[code],observed_at:stamp,position_timestamp:positionTime,message:`Observed squawk ${code}: ${emergencies[code]}`}});
    }
  }
  advance(t){
    this.engineNow=Math.max(this.engineNow,t);
    this.eligible=this.eligible.filter(k=>this.tracks[k]);this.gaps=this.gaps.filter(k=>this.tracks[k]);
    this.signals=Object.fromEntries(Object.entries(this.signals).filter(([,v])=>this.engineNow-v[0]<=this.alertConfig.dedup_retention_seconds));
    if(this.lastSuccess===null||this.engineNow-this.lastSuccess>this.alertConfig.source_stale_seconds)this.healthySince=null;
    if(this.healthySince===null)return;
    const b=this.reference.coverage;
    for(const key of this.eligible.filter(k=>!this.gaps.includes(k)).sort()){
      const track=this.tracks[key],p=track.history.at(-1);
      if(this.engineNow-Math.max(track.last_seen,this.healthySince)<=this.alertConfig.gap_seconds||p.status==='ground'||
        p.lat<b.lamin||p.lat>b.lamax||p.lon<b.lomin||p.lon>b.lomax)continue;
      const nearest=this.reference.airports.map(a=>[distanceKm(p.lat,p.lon,a.lat,a.lon),a.id]).sort((a,b)=>a[0]-b[0]||a[1].localeCompare(b[1]))[0];
      if(nearest[0]<=this.alertConfig.airport_radius_km)continue;
      this.emit({type:'signal_gap',source:p.source,object_id:p.object_id,timestamp:this.engineNow,lat:p.lat,lon:p.lon,
        details:{last_position_at:track.last_seen,silence_seconds:this.engineNow-track.last_seen,threshold_seconds:this.alertConfig.gap_seconds,
          nearest_airport:nearest[1],airport_distance_km:Math.round(nearest[0]*100)/100,message:'Position updates missing while source is healthy; cause unknown'}});
      this.gaps.push(key);
    }
  }
  accept(data,t){
    this.source={state:'live',message:'Receiving OpenSky positions',last_success:t};
    const {observations,rejected}=normalize(data,t);this.age(t);
    if((rejected&&!observations.length)||t-data.time<0||t-data.time>this.alertConfig.source_stale_seconds)this.failed('Source response has unusable or stale positions');
    else this.healthy(t);
    for(const o of observations){if(this.update(o)&&t-o.timestamp>=0&&t-o.timestamp<=this.alertConfig.gap_seconds){const key=keyOf(o);if(!this.eligible.includes(key))this.eligible.push(key);this.gaps=this.gaps.filter(k=>k!==key);}}
    this.squawks(data,t);this.advance(t);
  }
  apply(record){
    const t=record.timestamp;
    if(record.kind==='tick'){this.age(t);this.advance(t);}
    else if(record.kind==='source_failure')this.failed(record.reason);
    else if(record.kind==='source_stopped')this.stopped();
    else if(record.kind==='response'){
      if(record.source!=='opensky')throw new Error('Unsupported source');
      if(record.status>=200&&record.status<300){
        let data;try{data=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(record.body_base64),c=>c.charCodeAt(0))));}catch{return;}
        if(validPayload(data))this.accept(data,t);
      }
    }else if(record.kind!=='session')throw new Error('Unknown replay event');
  }
  result(){const tracks=Object.keys(this.tracks).sort().map(k=>this.tracks[k]);return {counts:Object.fromEntries(['active','gap','lost'].map(s=>[s,tracks.filter(t=>t.state===s).length])),tracks:structuredClone(tracks),source:{...this.source},alerts:structuredClone(this.alerts)};}
  snapshot(bounds,t){const result=this.result();return {type:'snapshot',timestamp:t,bounds,...result,
    tracks:result.tracks.map(track=>({...track,position:track.history.at(-1),history:track.history.map(p=>({lat:p.lat,lon:p.lon,timestamp:p.timestamp}))})),
    alerts:result.alerts.slice(-this.alertConfig.recent_limit).reverse(),heartbeat_interval_seconds:20};}
}
