import {readFile} from 'node:fs/promises';
import {Core} from '../src/core.mjs';
export async function recording(name,label){
  const records=(await readFile(new URL(`../../tests/fixtures/${name}.jsonl`,import.meta.url),'utf8')).trim().split('\n').map(JSON.parse);
  const core=new Core(records[0]),frames=[];
  for(const record of records.slice(1)){
    core.apply(record);
    frames.push(core.snapshot({lamin:37,lamax:39,lomin:-123,lomax:-121},record.timestamp));
  }
  if(name==='maritime-synthetic') {
    const combined=[...new Map(frames.map(frame=>[frame.timestamp,frame])).values()];
    for(const frame of combined)frame.bounds={lamin:37.7,lamax:38.05,lomin:-122.7,lomax:-122.2};
    return {label,frames:combined};
  }
  return {label,frames};
}
export async function demoData(){return {recordings:[
  await recording('opensky-live','Recorded OpenSky aircraft'),
  await recording('scenario','Synthetic alert walkthrough'),
  await trackingRecording(),
  await recording('maritime-synthetic','Synthetic air & sea / loitering')
]};}

async function trackingRecording(){
  const records=(await readFile(new URL('../../tests/fixtures/opensky-tracking.jsonl',import.meta.url),'utf8')).trim().split('\n').map(JSON.parse);
  const core=new Core(records[0]),frames=[];
  const responses=records.slice(1);
  let ids;
  for(let i=0;i<responses.length;i++){
    core.apply(responses[i]);
    // Limit visual density and download size while retaining full histories in the core.
    if(!ids)ids=new Set(Object.values(core.tracks).filter(t=>t.history.at(-1).status==='airborne').slice(0,12).map(t=>t.object_id));
    const end=responses[i+1]?.timestamp??responses[i].timestamp+65;
    for(let t=responses[i].timestamp;t<end;t+=5){
      core.apply({kind:"tick",timestamp:t});
      const frame=core.snapshot({lamin:37,lamax:39,lomin:-123,lomax:-121},t);
      frame.tracks=frame.tracks.filter(t=>ids.has(t.object_id));
      frame.counts=Object.fromEntries(['active','gap','lost'].map(s=>[s,frame.tracks.filter(t=>t.state===s).length]));
      frame.alerts=frame.alerts.filter(a=>ids.has(a.object_id));
      frames.push(frame);
    }
  }
  return {label:'Recorded tracking + Kalman estimates',frames};
}
