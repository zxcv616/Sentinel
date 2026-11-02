import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {Session,apply}=createRequire(import.meta.url)('../../src/sentinel/static/stream.js');

function harness(hidden=false,storage=null){
  let clock=1000000;
  const delays=[],listeners={},sockets=[],timeouts=new Map(),statuses=[],states=[];
  const doc={hidden,addEventListener:(name,fn)=>listeners[name]=fn};
  const timers={setInterval:()=>1,setTimeout:(fn,delay)=>{delays.push(delay);const id=Symbol();timeouts.set(id,fn);return id;},clearTimeout:id=>timeouts.delete(id)};
  const session=new Session({url:'ws://test',doc,timers,storage,random:()=>0,now:()=>clock,
    onState:s=>states.push(s),onStatus:(...s)=>statuses.push(s),
    makeSocket:()=>{const s={readyState:1,sent:[],send(v){this.sent.push(v);},close(code=1000){this.closeCode=code;this.readyState=3;this.onclose?.({code});}};sockets.push(s);return s;}});
  session.start();
  return {session,doc,sockets,timeouts,statuses,states,listeners,delays,advance:ms=>clock+=ms,
    visibility:hidden=>{doc.hidden=hidden;listeners.visibilitychange();}};
}
const snapshot={type:'snapshot',sequence:0,timestamp:0,tracks:[],alerts:[]};

test('hidden tabs never connect; showing gets a new snapshot',()=>{
  const h=harness(true);assert.equal(h.sockets.length,0);
  h.visibility(false);assert.equal(h.sockets.length,1);
  h.sockets[0].onmessage({data:JSON.stringify(snapshot)});h.listeners.pointerdown({isTrusted:true});
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
  const h=harness();h.listeners.pointerdown({isTrusted:true});h.sockets[0].close();assert.equal(h.timeouts.size,1);
  h.visibility(true);assert.equal(h.timeouts.size,0);
  h.visibility(false);h.sockets[1].onclose({code:4001});
  assert.equal(h.session.reason,'idle');assert.equal(h.timeouts.size,0);
});

test('missed sequence closes connection and requests fresh state on retry',()=>{
  const h=harness();h.listeners.pointerdown({isTrusted:true});h.sockets[0].onmessage({data:JSON.stringify(snapshot)});
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


test('hidden and idle pauses send distinct close codes for session analytics',()=>{
  const hidden=harness();hidden.visibility(true);assert.equal(hidden.sockets[0].closeCode,4005);
  const idle=harness();idle.advance(600000);idle.session.tick();assert.equal(idle.sockets[0].closeCode,4001);
});

test('67/67/187 second failures cannot reset retry budget with snapshots or pongs',()=>{
  const h=harness();h.listeners.pointerdown({isTrusted:true});const activity=h.session.lastActivity;
  for(const seconds of [67,67,187,67]) {
    const socket=h.sockets.at(-1);
    socket.onmessage({data:JSON.stringify(snapshot)});socket.onmessage({data:'pong'});
    h.advance(seconds*1000);socket.onclose({code:1006});
    if(h.session.reconnect!==null) {const id=h.session.reconnect;const fn=h.timeouts.get(id);h.timeouts.delete(id);fn();}
  }
  assert.deepEqual(h.delays,[2000,4000,8000]);
  assert.equal(h.sockets.length,4);assert.equal(h.session.reason,'connection');
  assert.equal(h.session.lastActivity,activity);
  h.visibility(true);h.visibility(false);h.session.tick();assert.equal(h.sockets.length,4);
  h.listeners.pointerdown({isTrusted:true});assert.equal(h.sockets.length,5);
});

test('unattended connection never auto retries and reload preserves the pause',()=>{
  const data=new Map();const storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)};
  const first=harness(false,storage);first.sockets[0].onmessage({data:JSON.stringify({...snapshot,resume_token:'opaque'})});
  first.advance(67000);first.sockets[0].onclose({code:1006});
  assert.equal(first.timeouts.size,0);assert.equal(first.session.reason,'connection');
  const reloaded=harness(false,storage);assert.equal(reloaded.sockets.length,0);
  reloaded.listeners.pointerdown({isTrusted:false});assert.equal(reloaded.sockets.length,0);
  reloaded.listeners.pointerdown({isTrusted:true});assert.equal(reloaded.sockets.length,1);
});

test('watchdog and decode failures have distinct diagnostic close codes',()=>{
  const h=harness();h.advance(46000);h.session.tick();assert.equal(h.sockets[0].closeCode,4006);
  const malformed=harness();malformed.sockets[0].onmessage({data:'bad json'});
  assert.equal(malformed.sockets[0].closeCode,4007);
});
