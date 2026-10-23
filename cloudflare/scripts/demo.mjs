import {readFile} from 'node:fs/promises';
import {Core} from '../src/core.mjs';
export async function recording(name,label){
  const records=(await readFile(new URL(`../../tests/fixtures/${name}.jsonl`,import.meta.url),'utf8')).trim().split('\n').map(JSON.parse);
  const core=new Core(records[0]),frames=[];
  for(const record of records.slice(1)){
    core.apply(record);
    frames.push(core.snapshot({lamin:37,lamax:39,lomin:-123,lomax:-121},record.timestamp));
  }
  return {label,frames};
}
export async function demoData(){return {recordings:[
  await recording('opensky-live','Recorded OpenSky aircraft'),
  await recording('scenario','Synthetic alert walkthrough')
]};}
