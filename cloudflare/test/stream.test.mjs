import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {Session,apply}=createRequire(import.meta.url)('../../src/sentinel/static/stream.js');

function harness(hidden=false){
  let clock=1000000;
  const listeners={},sockets=[],timeouts=new Map(),statuses=[],states=[];
  const doc={hidden,addEventListener:(name,fn)=>listeners[name]=fn};
  const timers={setInterval:()=>1,setTimeout:fn=>{const id=Symbol();timeouts.set(id,fn);return id;},clearTimeout:id=>timeouts.delete(id)};
  const session=new Session({url:'ws://test',doc,timers,now:()=>clock,
    onState:s=>states.push(s),onStatus:(...s)=>statuses.push(s),
    makeSocket:()=>{const s={readyState:1,sent:[],send(v){this.sent.push(v);},close(){this.readyState=3;this.onclose?.({code:1000});}};sockets.push(s);return s;}});
  session.start();
  return {session,doc,sockets,timeouts,statuses,states,listeners,advance:ms=>clock+=ms,
    visibility:hidden=>{doc.hidden=hidden;listeners.visibilitychange();}};
}
const snapshot={type:'snapshot',sequence:0,timestamp:0,tracks:[],alerts:[]};

test('hidden tabs never connect; showing gets a new snapshot',()=>{
  const h=harness(true);assert.equal(h.sockets.length,0);
  h.visibility(false);assert.equal(h.sockets.length,1);
  h.sockets[0].onmessage({data:JSON.stringify(snapshot)});
  h.visibility(true);assert.equal(h.sockets[0].readyState,3);assert.equal(h.timeouts.size,0);
  h.advance(60000);h.session.tick();assert.equal(h.sockets.length,1);
  h.visibility(false);assert.equal(h.sockets.length,2);assert.equal(h.session.state,null);
  h.sockets[1].onmessage({data:JSON.stringify({...snapshot,timestamp:60})});
  assert.equal(h.states.at(-1).timestamp,60);
});

test('ten minute inactivity pauses despite incoming data, click resumes',()=>{
  const h=harness();h.advance(600000);
  h.sockets[0].onmessage({data:JSON.stringify(snapshot)});h.session.tick();
  assert.equal(h.session.reason,'idle');assert.equal(h.timeouts.size,0);
  assert.equal(h.statuses.at(-1)[2],true);
  h.listeners.pointerdown({isTrusted:true});h.visibility(true);h.visibility(false);
  assert.equal(h.sockets.length,1);
  h.session.resume();assert.equal(h.sockets.length,2);assert.equal(h.session.reason,null);
});

test('real interaction extends idle time and signals activity with throttling',()=>{
  const h=harness();h.advance(590000);h.listeners.keydown({isTrusted:true});
  assert.deepEqual(h.sockets[0].sent,['activity']);
  h.advance(5000);h.listeners.wheel({isTrusted:true});assert.equal(h.sockets[0].sent.length,1);
  h.sockets[0].onmessage({data:'pong'});h.advance(10000);h.session.tick();
  assert.equal(h.session.reason,null);
  h.advance(600000);h.session.tick();assert.equal(h.session.reason,'idle');
});

test('hiding cancels scheduled reconnect; server idle close never retries',()=>{
  const h=harness();h.sockets[0].close();assert.equal(h.timeouts.size,1);
  h.visibility(true);assert.equal(h.timeouts.size,0);
  h.visibility(false);h.sockets[1].onclose({code:4001});
  assert.equal(h.session.reason,'idle');assert.equal(h.timeouts.size,0);
});

test('missed sequence closes connection and requests fresh state on retry',()=>{
  const h=harness();h.sockets[0].onmessage({data:JSON.stringify(snapshot)});
  h.sockets[0].onmessage({data:JSON.stringify({type:'delta',sequence:2})});
  assert.equal(h.sockets[0].readyState,3);assert.equal(h.timeouts.size,1);
  const retry=[...h.timeouts.values()][0];retry();assert.equal(h.session.state,null);
  assert.throws(()=>apply(null,{type:'delta',sequence:1}));
});

test('budget pause survives visibility and clicks until next UTC day',()=>{
  const h=harness();h.sockets[0].onclose({code:4003});
  assert.equal(h.session.reason,'budget');
  assert.equal(h.statuses.at(-1)[0],'Live feed paused for today to stay within hosting limits.');
  h.visibility(true);h.visibility(false);h.session.resume();h.session.tick();
  assert.equal(h.sockets.length,1);assert.equal(h.timeouts.size,0);
  h.advance(86400000);h.session.resume();assert.equal(h.sockets.length,2);
});

test('session cap and capacity rejection require explicit resume',()=>{
  for(const code of [4002,4004]) {
    const h=harness();h.sockets[0].onclose({code});
    assert.equal(h.timeouts.size,0);h.session.tick();assert.equal(h.sockets.length,1);
    h.session.resume();assert.equal(h.sockets.length,2);
  }
});


test('synthetic DOM events do not renew activity',()=>{
  const h=harness();h.advance(590000);h.listeners.pointerdown({isTrusted:false});
  assert.equal(h.sockets[0].sent.length,0);
  h.advance(10000);h.session.tick();assert.equal(h.session.reason,'idle');
});
