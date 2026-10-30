/* Shared browser transport and pure reducer; testable without a map or network. */
(function(root) {
  'use strict';
  const key = t => `${t.source}:${t.object_id}`;
  function apply(state, message) {
    if (message.type === 'snapshot') return message;
    if (!state || message.type !== 'delta' || message.sequence !== state.sequence + 1) throw new Error('Snapshot required');
    const tracks = new Map(state.tracks.map(t => [key(t), t]));
    for (const id of message.tracks.remove) tracks.delete(id);
    for (const track of message.tracks.upsert) tracks.set(key(track), track);
    for (const patch of message.tracks.update) {
      if (!tracks.has(key(patch))) throw new Error('Unknown track; snapshot required');
      const {history_delta, ...fields}=patch;
      const track={...tracks.get(key(patch)), ...fields};
      if(history_delta) track.history=track.history.slice(history_delta.drop).concat(history_delta.append);
      tracks.set(key(patch),track);
    }
    const alerts=new Map(state.alerts.map(a=>[a.id,a]));
    for(const id of message.alerts.remove) alerts.delete(id);
    for(const alert of message.alerts.upsert) alerts.set(alert.id,alert);
    const order=message.alerts.order || state.alerts.map(a=>a.id);
    return {...state, alerts:order.map(id=>alerts.get(id)), ...message.meta, type:'snapshot', sequence:message.sequence,
      timestamp:message.timestamp, tracks:[...tracks.values()].sort((a,b) => key(a).localeCompare(key(b)))};
  }
  class Session {
    constructor({url, onState, onStatus, doc=document, makeSocket=url=>new WebSocket(url), now=Date.now, timers=globalThis}) {
      Object.assign(this,{url,onState,onStatus,doc,makeSocket,now,timers});
      this.lastActivity=now(); this.lastMessage=now(); this.lastActivitySent=0;
      this.socket=null; this.state=null; this.reason=null; this.retry=1000; this.reconnect=null;
    }
    start() {
      this.doc.addEventListener('visibilitychange', () => this.visibility());
      for (const name of ['pointerdown','keydown','wheel','touchstart']) this.doc.addEventListener(name, event => {if(event.isTrusted) this.activity();}, {passive:true});
      this.timer=this.timers.setInterval(() => this.tick(),1000);
      this.visibility();
    }
    pause(reason) {
      this.reason=reason;
      if (this.reconnect !== null) this.timers.clearTimeout(this.reconnect);
      this.reconnect=null;
      const socket=this.socket; this.socket=null;
      if(socket) socket.close();
      this.onStatus(reason==='budget' ? 'Live feed paused for today to stay within hosting limits.' : reason==='capacity' ? 'Live feed busy, click to retry' : reason==='idle' ? 'Paused to save bandwidth, click to resume' : 'Paused while this tab is hidden',false,reason!=='hidden');
    }
    budgetBlocked() {
      return this.reason==='budget' && this.budgetDay===new Date(this.now()).toISOString().slice(0,10);
    }
    visibility() {
      if(this.budgetBlocked()) return;
      if(this.reason==='budget') this.reason=null;
      if(this.reason==='capacity') return;
      if(this.doc.hidden) { this.pause(this.reason==='idle' ? 'idle' : 'hidden'); return; }
      if(this.reason==='idle' || this.now()-this.lastActivity>=600000) {this.pause('idle'); return;}
      this.reason=null; this.connect();
    }
    activity() {
      if(this.doc.hidden || ['idle','budget','capacity'].includes(this.reason)) return;
      this.lastActivity=this.now();
      if(this.socket?.readyState===1 && this.now()-this.lastActivitySent>=15000) {
        this.socket.send('activity'); this.lastActivitySent=this.now();
      }
    }
    resume() {
      if(this.doc.hidden || this.budgetBlocked()) return;
      this.lastActivity=this.now(); this.reason=null; this.connect();
    }
    connect() {
      if(this.doc.hidden || this.reason || this.socket || this.reconnect !== null) return;
      const socket=this.makeSocket(this.url); this.socket=socket; this.state=null; this.lastMessage=this.now();
      this.onStatus('Connecting…',false,false);
      socket.onmessage=event=>{
        if(this.socket!==socket) return;
        this.lastMessage=this.now(); this.retry=1000;
        if(event.data==='pong') return;
        try { this.state=apply(this.state,JSON.parse(event.data)); }
        catch { socket.close(); return; }
        this.onStatus('Map connected',true,false); this.onState(this.state);
      };
      socket.onclose=event=>{
        if(this.socket!==socket) return;
        this.socket=null;
        if(event.code===4003) {this.budgetDay=new Date(this.now()).toISOString().slice(0,10);this.pause('budget'); return;}
        if(event.code===4004) {this.pause('capacity'); return;}
        if(event.code===4001 || event.code===4002) {this.pause('idle'); return;}
        if(this.reason || this.doc.hidden) return;
        this.onStatus('Disconnected · reconnecting',false,false);
        this.reconnect=this.timers.setTimeout(()=>{this.reconnect=null;this.visibility();},this.retry);
        this.retry=Math.min(this.retry*2,30000);
      };
      socket.onerror=()=>socket.close();
    }
    tick() {
      if(['budget','capacity'].includes(this.reason)) return;
      if(this.doc.hidden) {if(this.socket) this.pause('hidden'); return;}
      if(this.reason==='idle') return;
      if(this.now()-this.lastActivity>=600000) {this.pause('idle'); return;}
      if(this.socket?.readyState===1) {
        if(this.now()-this.lastMessage>45000) {this.socket.close();return;}
        if(this.now()-(this.lastPing||0)>=20000) {this.socket.send('ping');this.lastPing=this.now();}
      }
    }
  }
  const exported={apply,Session};
  if(typeof module!=='undefined' && module.exports) module.exports=exported;
  else root.SentinelStream=exported;
})(globalThis);
