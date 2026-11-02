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
    constructor({url, onState, onStatus, doc=document, makeSocket=url=>new WebSocket(url), now=Date.now, timers=globalThis, random=Math.random, storage}) {
      Object.assign(this,{url,onState,onStatus,doc,makeSocket,now,timers,random});
      this.lastActivity=now(); this.lastMessage=now(); this.lastActivitySent=0;
      this.socket=null; this.state=null; this.reason=null; this.retry=2000; this.reconnect=null; this.retryAttempts=0; this.userActive=false; this.opened=false; this.resumeToken=null;
      try {
        this.storage=storage===undefined ? globalThis.sessionStorage : storage;
        const saved=JSON.parse(this.storage?.getItem('sentinel.connection') || 'null');
        if(saved && Number.isFinite(saved.lastActivity)) {
          this.lastActivity=saved.lastActivity; this.retryAttempts=Number.isInteger(saved.retryAttempts) ? Math.max(0,Math.min(3,saved.retryAttempts)) : 0;
          this.retry=Math.min(2000*2**this.retryAttempts,30000);
          this.userActive=!!saved.userActive; this.opened=true; this.resumeToken=saved.resumeToken || null;
          this.reason=saved.reason || null;
        }
      } catch { this.storage=null; }
    }
    save() {
      try {this.storage?.setItem('sentinel.connection',JSON.stringify({lastActivity:this.lastActivity,
        retryAttempts:this.retryAttempts,userActive:this.userActive,resumeToken:this.resumeToken,reason:this.reason}));} catch {}
    }
    start() {
      this.doc.addEventListener('visibilitychange', () => this.visibility());
      for (const name of ['pointerdown','keydown','wheel','touchstart']) this.doc.addEventListener(name, event => {if(event.isTrusted) this.activity();}, {passive:true});
      this.timer=this.timers.setInterval(() => this.tick(),1000);
      this.visibility();
    }
    pause(reason) {
      this.reason=reason; this.save();
      if (this.reconnect !== null) this.timers.clearTimeout(this.reconnect);
      this.reconnect=null;
      const socket=this.socket; this.socket=null;
      if(socket) socket.close(reason==='hidden' ? 4005 : reason==='idle' ? 4001 : 1000);
      this.onStatus(reason==='budget' ? 'Live feed paused for today to stay within hosting limits.' : reason==='connection' ? 'Connection interrupted, click to resume' : reason==='capacity' ? 'Live feed busy, click to retry' : reason==='idle' ? 'Paused to save bandwidth, click to resume' : 'Paused while this tab is hidden',false,reason!=='hidden');
    }
    budgetBlocked() {
      return this.reason==='budget' && this.budgetDay===new Date(this.now()).toISOString().slice(0,10);
    }
    visibility() {
      if(this.budgetBlocked()) return;
      if(this.reason==='budget') this.reason=null;
      if(['capacity','connection'].includes(this.reason)) {this.pause(this.reason);return;}
      if(this.doc.hidden) { this.pause(this.reason==='idle' ? 'idle' : 'hidden'); return; }
      if(this.reason==='idle' || this.now()-this.lastActivity>=600000) {this.pause('idle'); return;}
      if(this.opened && !this.userActive) {this.pause('connection');return;}
      this.reason=null; this.connect();
    }
    activity() {
      if(this.doc.hidden || ['idle','budget','capacity'].includes(this.reason)) return;
      this.lastActivity=this.now(); this.userActive=true; this.retryAttempts=0; this.retry=2000;
      if(this.reason==='connection') {this.resume();return;}
      this.save();
      if(this.socket?.readyState===1 && this.now()-this.lastActivitySent>=15000) {
        this.socket.send('activity'); this.lastActivitySent=this.now();
      }
    }
    resume() {
      if(this.doc.hidden || this.budgetBlocked()) return;
      this.lastActivity=this.now(); this.userActive=true; this.retryAttempts=0; this.retry=2000; this.reason=null;
      if(this.reconnect!==null) this.timers.clearTimeout(this.reconnect);
      this.reconnect=null; this.resumeToken=null; this.save(); this.connect();
    }
    connect() {
      if(this.doc.hidden || this.reason || this.socket || this.reconnect !== null) return;
      if(this.now()-this.lastActivity>=600000) {this.pause('idle');return;}
      const separator=this.url.includes('?')?'&':'?';
      const socket=this.makeSocket(this.url+(this.resumeToken ? separator+'resume='+encodeURIComponent(this.resumeToken) : '')); this.opened=true; this.socket=socket; this.state=null; this.lastMessage=this.now();
      this.onStatus('Connecting…',false,false);
      socket.onmessage=event=>{
        if(this.socket!==socket) return;
        this.lastMessage=this.now();
        if(event.data==='pong') return;
        try { const message=JSON.parse(event.data); if(message.resume_token) {this.resumeToken=message.resume_token;this.save();} this.state=apply(this.state,message); }
        catch { socket.close(4007); return; }
        this.onStatus('Map connected',true,false); this.onState(this.state);
      };
      socket.onclose=event=>{
        if(this.socket!==socket) return;
        this.socket=null;
        if(event.code===4003) {this.budgetDay=new Date(this.now()).toISOString().slice(0,10);this.pause('budget'); return;}
        if(event.code===4004) {this.pause('capacity'); return;}
        if(event.code===4001 || event.code===4002) {this.resumeToken=null;this.pause('idle'); return;}
        if(this.reason || this.doc.hidden) return;
        if(this.now()-this.lastActivity>=600000) {this.pause('idle');return;}
        if(!this.userActive || this.retryAttempts>=3) {this.pause('connection');return;}
        this.retryAttempts++; this.save();
        this.onStatus('Disconnected · reconnecting',false,false);
        this.reconnect=this.timers.setTimeout(()=>{this.reconnect=null;this.visibility();},this.retry*(1+this.random()*.25));
        this.retry=Math.min(this.retry*2,30000);
      };
      socket.onerror=()=>socket.close();
    }
    tick() {
      if(['budget','capacity','connection'].includes(this.reason)) return;
      if(this.doc.hidden) {if(this.socket) this.pause('hidden'); return;}
      if(this.reason==='idle') return;
      if(this.now()-this.lastActivity>=600000) {this.pause('idle'); return;}
      if(this.socket?.readyState===1) {
        if(this.now()-this.lastMessage>45000) {this.socket.close(4006);return;}
        if(this.now()-(this.lastPing||0)>=20000) {this.socket.send('ping');this.lastPing=this.now();}
      }
    }
  }
  const exported={apply,Session};
  if(typeof module!=='undefined' && module.exports) module.exports=exported;
  else root.SentinelStream=exported;
})(globalThis);
