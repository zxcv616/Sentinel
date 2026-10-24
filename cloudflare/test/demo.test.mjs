import test from 'node:test';
import assert from 'node:assert/strict';
import {demoData} from '../scripts/demo.mjs';
test('demo separates observed aircraft from synthetic alerts and preserves replay results',async()=>{
  const {recordings:[live,scenario]}=await demoData();
  assert.ok(live.frames.some(f=>f.tracks.length>100));
  const moving=new Map();
  for(const frame of live.frames)for(const t of frame.tracks){const points=moving.get(t.object_id)||new Set();points.add(`${t.position.lat},${t.position.lon}`);moving.set(t.object_id,points);}
  assert.ok([...moving.values()].some(p=>p.size>1));
  assert.equal(scenario.frames.at(-1).alerts.length,4);
  assert.ok(scenario.frames.some(f=>f.alerts.some(a=>a.type==='signal_gap')));
  assert.ok(scenario.frames.some(f=>f.alerts.some(a=>a.type==='emergency_squawk')));
  assert.ok(live.frames.every(f=>f.timestamp>1000000000));
  assert.ok(scenario.frames.every(f=>f.timestamp<1000));
});
test('tracking demo includes finite predictions, an expiry, and preserved raw positions',async()=>{
  const recording=(await demoData()).recordings[2];
  assert.ok(recording.frames.length>100);
  assert.ok(recording.frames.some(f=>f.tracks.some(t=>t.estimate?.prediction)));
  assert.ok(recording.frames.at(-1).tracks.every(t=>t.estimate?.prediction===null));
  for(const f of recording.frames)for(const t of f.tracks){
    const p=t.estimate?.prediction;
    if(p){assert.ok(Number.isFinite(p.lat));assert.ok(p.horizon_seconds<=60);assert.ok(p.uncertainty_radius_m>0);}
    assert.equal(t.position.timestamp,t.last_seen);
  }
});
test('mixed-domain demo groups timestamps and shows one vessel loitering episode',async()=>{
  const recording=(await demoData()).recordings[3];
  assert.equal(recording.frames.length,29);
  assert.equal(recording.frames[0].tracks.length,4);
  assert.deepEqual(recording.frames.at(-1).alerts.map(a=>[a.type,a.object_id]),[['loitering','999000001']]);
  assert.equal(recording.frames.at(-1).tracks.filter(t=>t.source==='aisstream').length,3);
});
