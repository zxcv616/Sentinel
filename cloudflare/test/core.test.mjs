import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Core} from '../src/core.mjs';
const fixture=name=>new URL(`../../tests/fixtures/${name}`,import.meta.url);
function run(name,restart=false){const records=readFileSync(fixture(name),'utf8').trim().split('\n').map(JSON.parse);let core=new Core(records[0]);const frames=[];for(const r of records){core.apply(r);if(restart)core=Core.restore(core.dump());frames.push(core.result());}return {core,frames};}
test('real OpenSky capture matches every Python track, observation, source status and alert',()=>{
  const expected=JSON.parse(readFileSync(fixture('opensky-live.expected.json'),'utf8'));
  const {core}=run('opensky-live.jsonl');const {session,records,timestamp,...state}=expected;assert.deepEqual(core.result(),state);
});
test('controlled recording produces Python alert episodes and bounded history',()=>{
  const {core,frames}=run('scenario.jsonl');
  const {session,records,timestamp,...expected}=JSON.parse(readFileSync(fixture('scenario.expected.json'),'utf8'));
  assert.deepEqual(core.result(),expected);
  assert.deepEqual(core.alerts.map(a=>[a.type,a.timestamp]),[['signal_gap',111],['emergency_squawk',112],['signal_gap',146],['emergency_squawk',148]]);
  assert.deepEqual(core.alerts.map(a=>a.id),[1,2,3,4].map(i=>`fixture-scenario:${i}`));
  assert.deepEqual(core.result().tracks,[]);
  assert.ok(frames.some(f=>JSON.stringify(f.tracks[0]?.history.map(o=>o.timestamp))==='[113,147,148]'));
});
test('restoring persistent state after every event preserves exact outcomes',()=>{
  for(const name of ['scenario.jsonl','opensky-live.jsonl'])assert.deepEqual(run(name,true).frames,run(name).frames);
});
