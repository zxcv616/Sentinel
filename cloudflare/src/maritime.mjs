export const loiterDefaults={loiter_radius_m:500,loiter_seconds:300,loiter_min_speed_mps:1,loiter_min_distance_m:100,loiter_max_gap_seconds:90};
const valid=(x,a,b)=>typeof x==='number'&&Number.isFinite(x)&&x>=a&&x<=b;
export function normalizeAIS(data,received){
  try{
    const kind=data.MessageType;if(!['PositionReport','StandardClassBPositionReport','ExtendedClassBPositionReport'].includes(kind))return null;
    const r=data.Message[kind],m=data.MetaData||{};
    if(r.Valid!==true||!Number.isInteger(r.UserID)||!valid(r.UserID,100000000,999999999)||(m.MMSI!==undefined&&m.MMSI!==r.UserID)||!valid(r.Latitude,-90,90)||!valid(r.Longitude,-180,180))return null;
    let stamp=m.time_utc;
    if(stamp){
      stamp=stamp.trim();
      if(stamp.endsWith(' UTC')){stamp=stamp.slice(0,-4);if(!/[+-]\d{2}:?\d{2}$/.test(stamp))stamp+='Z';}
      stamp=stamp.replace(/\s+([+-]\d{2})(\d{2})$/,'$1:$2').replace(' ','T');
      if(!/(Z|[+-]\d{2}:?\d{2})$/.test(stamp))return null;
    }
    const timestamp=stamp?Date.parse(stamp)/1000:received;
    if(!valid(received-timestamp,0,120))return null;
    let speed=r.Sog??null,heading=r.TrueHeading;
    if(speed===102.3)speed=null;
    if(speed!==null&&!valid(speed,0,102.2))return null;
    if(heading===undefined||heading===null||heading===511)heading=r.Cog;
    if(heading===undefined||heading===null||heading===360)heading=null;
    if(heading!==null&&!valid(heading,0,359.9999))return null;
    return {source:'aisstream',object_id:String(r.UserID),timestamp,received_at:received,lat:r.Latitude,lon:r.Longitude,altitude:null,speed:speed===null?null:speed*.514444,heading,squawk:null,status:({1:'anchored',5:'moored'})[r.NavigationalStatus]||'underway'};
  }catch{return null;}
}
export function observeLoiter(core,o,distance){
  const cfg={...loiterDefaults,...core.alertConfig};core.loiterEpisodes??={};
  const key=o.object_id,old=core.loiterEpisodes[key];
  if(old&&o.timestamp<=old.last.timestamp)return;
  if(['anchored','moored'].includes(o.status)||o.speed===null||o.speed<cfg.loiter_min_speed_mps){delete core.loiterEpisodes[key];return;}
  if(!old||o.timestamp-old.last.timestamp>cfg.loiter_max_gap_seconds||distance(old.anchor.lat,old.anchor.lon,o.lat,o.lon)*1000>cfg.loiter_radius_m){core.loiterEpisodes[key]={anchor:o,last:o,distance:0,emitted:false};return;}
  old.distance+=distance(old.last.lat,old.last.lon,o.lat,o.lon)*1000;old.last=o;
  const duration=o.timestamp-old.anchor.timestamp;
  if(!old.emitted&&duration>=cfg.loiter_seconds&&old.distance>=cfg.loiter_min_distance_m){old.emitted=true;core.emit({type:'loitering',source:o.source,object_id:o.object_id,timestamp:o.timestamp,lat:o.lat,lon:o.lon,details:{duration_seconds:duration,radius_m:cfg.loiter_radius_m,message:'Sustained vessel movement within configured radius; intent unknown'}});}
}
export function ageLoiter(core,t){const max=core.alertConfig.loiter_max_gap_seconds??90;core.loiterEpisodes=Object.fromEntries(Object.entries(core.loiterEpisodes||{}).filter(([,v])=>t-v.last.timestamp<=max));}
