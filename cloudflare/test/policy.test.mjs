import {test} from 'node:test';
import assert from 'node:assert/strict';
import {settings,retryAfter,encodeBody,readBounded,pack,unpack} from '../src/policy.mjs';
import {replayCheckpointBundle} from '../scripts/replay.mjs';
import {Core} from '../src/core.mjs';
import {readFileSync} from 'node:fs';
test('anonymous quota floor and authenticated cadence',()=>{
  assert.equal(settings({POLL_SECONDS:'30'}).pollSeconds,240);
  assert.equal(settings({POLL_SECONDS:'60',OPENSKY_CLIENT_ID:'id',OPENSKY_CLIENT_SECRET:'secret'}).pollSeconds,60);
  assert.throws(()=>settings({OPENSKY_CLIENT_ID:'id'}));
  for(const n of ['NaN','0','15','1000'])assert.throws(()=>settings({POLL_SECONDS:n}));
  assert.throws(()=>settings({RAW_RECORD_LIMIT:'100000'}));
});
test('rate-limit delays honor both headers and HTTP dates',()=>{
  assert.equal(retryAfter(new Headers({'Retry-After':'3600','X-Rate-Limit-Retry-After-Seconds':'80000'}),100),80000);
  assert.equal(retryAfter(new Headers({'Retry-After':new Date(200000).toUTCString()}),100),100);
  assert.equal(retryAfter(new Headers({'Retry-After':'garbage'}),100),0);
});
test('raw body preserves non-UTF8 bytes and rejects oversized responses',async()=>{
  const bytes=new Uint8Array([0,255,128,12]);assert.equal(encodeBody(bytes),'AP+ADA==');
  assert.deepEqual(await readBounded(new Response(bytes)),bytes);
  await assert.rejects(readBounded(new Response('too large'),3));
});
test('retained checkpoint batch replays independently of discarded history',()=>{
  const rows=readFileSync(new URL('../../tests/fixtures/scenario.jsonl',import.meta.url),'utf8').trim().split('\n').map(JSON.parse);
  const core=new Core(rows[0]);for(const event of rows.slice(1,8))core.apply(event);
  const checkpoint=core.dump(),events=rows.slice(8);for(const event of events)core.apply(event);
  const result=replayCheckpointBundle({format:'sentinel-cloudflare-checkpoints-v1',batches:[{version:1,checkpoint,events}]});
  assert.deepEqual(result[0],core.result());
});

test('compressed persistence handles a full multi-megabyte history losslessly',async()=>{
  const data={history:Array.from({length:30000},(_,i)=>({timestamp:i,lat:37.7,lon:-122.4,source:'opensky',object_id:'abc123',altitude:1000,speed:100}))};
  assert.ok(JSON.stringify(data).length>2000000);
  const packed=await pack(data);assert.ok(packed.length<1900000);assert.deepEqual(await unpack(packed),data);
});
