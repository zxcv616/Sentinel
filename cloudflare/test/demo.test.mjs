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
