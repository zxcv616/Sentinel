import {DurableObject} from 'cloudflare:workers';
import airports from '../../src/sentinel/reference/airports.json';
import {Core,trackDefaults,alertDefaults,validPayload} from './core.mjs';
import {settings,retryAfter,encodeBody,readBounded,pack,unpack} from './policy.mjs';

const BOUNDS={lamin:37,lamax:39,lomin:-123,lomax:-121};
const STATES='https://opensky-network.org/api/states/all';
const TOKEN='https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token';
const now=()=>Date.now()/1000;
const json=data=>Response.json(data,{headers:{'Cache-Control':'no-store'}});
const routes=new Set(['/api/tracks','/api/alerts','/api/health','/api/admin/recording','/api/admin/resume','/ws/tracks']);

export default {
  async fetch(request,env){
    const path=new URL(request.url).pathname;
    if(env.DEMO_MODE==='true'&&(path.startsWith('/api/')||path.startsWith('/ws/'))){
      const info={state:'demo',message:'Recorded demo — not live',live_ingestion:false};
      return path==='/api/health'?json(info):Response.json(info,{status:503,headers:{'Cache-Control':'no-store'}});
    }
    if(path.startsWith('/api/')||path.startsWith('/ws/')){
      if(!routes.has(path))return new Response('Not found',{status:404});
      return env.AIRSPACE.get(env.AIRSPACE.idFromName('bay-area-v1')).fetch(request);
    }
    return env.ASSETS.fetch(request);
  }
};

export class Airspace extends DurableObject {
  constructor(ctx,env){
    super(ctx,env);this.ctx=ctx;this.env=env;this.sql=ctx.storage.sql;this.token=null;this.tokenExpires=0;
    this.ctx.blockConcurrencyWhile(async()=>{
      this.config=settings(env);
      this.sql.exec('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)');
      this.sql.exec('CREATE TABLE IF NOT EXISTS recording (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, bytes INTEGER NOT NULL)');
      const saved=this.sql.exec('SELECT body FROM state WHERE id=1').toArray()[0];
      if(saved){const data=await unpack(saved.body);if(data.version!==1)throw new Error('Unsupported stored state version');this.core=Core.restore(data.core);this.nextPoll=data.nextPoll;this.backoff=data.backoff;this.halted=data.halted;}
      else{
        this.core=new Core({session:crypto.randomUUID(),tracks:trackDefaults,alerts:alertDefaults,airport_reference:airports});
        this.nextPoll=0;this.backoff=30;this.halted=false;
      }
      // Deployment settings change cadence; checkpoints retain the actual rule settings used.
      this.core.trackConfig.gap_after=this.config.pollSeconds+30;
      this.core.trackConfig.lost_after=Math.max(300,this.config.pollSeconds*3);
      this.core.alertConfig.source_stale_seconds=this.config.pollSeconds+30;
      this.core.alertConfig.gap_seconds=Math.max(60,this.config.pollSeconds*2);
      this.ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair('ping','pong'));
    });
  }
  async persist(batch){
    // A batch has a complete starting checkpoint, making each retained batch replayable.
    this.core.alerts=this.core.alerts.slice(-this.core.alertConfig.recent_limit);
    const saved=await pack({version:1,core:this.core.dump(),nextPoll:this.nextPoll,backoff:this.backoff,halted:this.halted});
    const batchBody=batch?await pack(batch):null;
    this.ctx.storage.transactionSync(()=>{
      if(batch){
        const body=batchBody,bytes=body.length;
        this.sql.exec('INSERT INTO recording(body,bytes) VALUES (?,?)',body,bytes);
        const rows=this.sql.exec('SELECT id,bytes FROM recording ORDER BY id DESC').toArray();
        let total=0,keep=0;
        for(const row of rows){total+=row.bytes;keep++;if(keep>this.config.rawLimit||total>16*1024*1024){this.sql.exec('DELETE FROM recording WHERE id<=?',row.id);break;}}
      }
      this.sql.exec('INSERT INTO state(id,body) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',saved);
    });
  }
  snapshot(){return {...this.core.snapshot(BOUNDS,now()),deployment:{mode:'cloudflare',poll_seconds:this.config.pollSeconds},source:{...this.core.source,message:this.core.source.state==='starting'?'Waiting for first scheduled OpenSky poll':this.core.source.message}};}
  async fetch(request){
    const url=new URL(request.url),path=url.pathname;
    if(path.startsWith('/api/admin/')){
      if(!this.env.ADMIN_TOKEN||request.headers.get('Authorization')!==`Bearer ${this.env.ADMIN_TOKEN}`)return new Response('Unauthorized',{status:401});
      if(path==='/api/admin/recording'&&request.method==='GET'){
        const limit=Math.min(4,Math.max(1,Number(url.searchParams.get('limit'))||1));
        const rows=this.sql.exec('SELECT body FROM recording ORDER BY id DESC LIMIT ?',Math.floor(limit)).toArray().reverse();
        const batches=[];for(const row of rows)batches.push(await unpack(row.body));
        return json({format:'sentinel-cloudflare-checkpoints-v1',batches});
      }
      if(path==='/api/admin/resume'&&request.method==='POST'){
        this.halted=false;this.nextPoll=0;this.backoff=30;await this.persist();await this.ctx.storage.setAlarm(Date.now()+1000);return json({resumed:true});
      }
      return new Response('Method not allowed',{status:405});
    }
    if(request.method!=='GET')return new Response('Method not allowed',{status:405});
    if(!await this.ctx.storage.getAlarm())await this.ctx.storage.setAlarm(Date.now()+1000);
    if(path==='/ws/tracks'){
      if(request.headers.get('Upgrade')?.toLowerCase()!=='websocket')return new Response('WebSocket upgrade required',{status:426});
      const origin=request.headers.get('Origin');if(origin&&origin!==url.origin)return new Response('Origin not allowed',{status:403});
      if(this.ctx.getWebSockets().length>=this.config.maxClients)return new Response('Viewer limit reached',{status:503});
      const pair=new WebSocketPair(),[client,server]=Object.values(pair);this.ctx.acceptWebSocket(server);server.send(JSON.stringify(this.snapshot()));
      return new Response(null,{status:101,webSocket:client});
    }
    if(path==='/api/tracks')return json(this.snapshot());
    if(path==='/api/alerts')return json({alerts:this.core.alerts.slice().reverse()});
    return json({...this.core.source,poll_seconds:this.config.pollSeconds,halted:this.halted});
  }
  webSocketMessage(socket){socket.close(1008,'Only heartbeat messages are supported');}
  webSocketClose(socket,code){socket.close(code);}
  webSocketError(socket){socket.close(1011,'Connection error');}
  broadcast(){const body=JSON.stringify(this.snapshot());for(const ws of this.ctx.getWebSockets()){try{ws.send(body);}catch{ws.close(1011,'Unable to send update');}}}
  async headers(){
    if(!this.config.authenticated)return {};
    if(!this.token||now()>=this.tokenExpires){
      const response=await fetch(TOKEN,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'client_credentials',client_id:this.env.OPENSKY_CLIENT_ID,client_secret:this.env.OPENSKY_CLIENT_SECRET}),signal:AbortSignal.timeout(15000)});
      if(!response.ok){const error=new Error(`OpenSky authentication HTTP ${response.status}`);error.retry=retryAfter(response.headers,now());error.fatal=response.status>=400&&response.status<500&&![408,425,429].includes(response.status);throw error;}
      const data=JSON.parse(new TextDecoder().decode(await readBounded(response,65536)));
      if(typeof data.access_token!=='string'||!data.access_token||!Number.isFinite(data.expires_in)||data.expires_in<=0)throw new Error('Malformed OpenSky token response');
      this.token=data.access_token;this.tokenExpires=now()+Math.max(0,data.expires_in-30);
    }
    return {Authorization:`Bearer ${this.token}`};
  }
  async alarm(){
    if(this.env.DEMO_MODE==='true'){await this.ctx.storage.deleteAlarm();return;}
    // Re-arm before I/O so interruption cannot permanently stop the feed.
    await this.ctx.storage.setAlarm(Date.now()+60000);
    const checkpoint=this.core.dump(),events=[];
    const previousSchedule={nextPoll:this.nextPoll,backoff:this.backoff,halted:this.halted};
    const apply=event=>{const record={version:1,session:this.core.session,...event};events.push(record);this.core.apply(record);};
    apply({kind:'tick',timestamp:now()});
    if(!this.halted&&now()>=this.nextPoll){
      try{
        for(let attempt=0;attempt<2;attempt++){
          const url=new URL(STATES);for(const [k,v] of Object.entries(BOUNDS))url.searchParams.set(k,v);
          const response=await fetch(url,{headers:{'User-Agent':'Sentinel/0.1','Accept':'application/json',...await this.headers()},signal:AbortSignal.timeout(20000)});
          const timestamp=now(),bytes=await readBounded(response);
          apply({kind:'response',timestamp,source:'opensky',status:response.status,body_base64:encodeBody(bytes),headers:Object.fromEntries(['content-type','retry-after','x-rate-limit-retry-after-seconds'].filter(h=>response.headers.has(h)).map(h=>[h,response.headers.get(h)]))});
          if(response.status===401&&this.config.authenticated&&attempt===0){this.token=null;continue;}
          if(!response.ok){const error=new Error(`OpenSky HTTP ${response.status}`);error.retry=retryAfter(response.headers,timestamp);error.fatal=response.status>=400&&response.status<500&&![408,425,429].includes(response.status);throw error;}
          let data;try{data=JSON.parse(new TextDecoder().decode(bytes));}catch{throw new Error('Malformed OpenSky state response');}
          if(!validPayload(data))throw new Error('Malformed OpenSky state response');
          this.backoff=30;this.nextPoll=now()+this.config.pollSeconds;break;
        }
      }catch(error){
        // Do not log upstream response bodies, tokens, or credential-bearing requests.
        const reason=error.message?.startsWith('OpenSky ')||error.message?.startsWith('Malformed OpenSky')?error.message:'OpenSky network or response error';
        const delay=Math.max(error.retry||0,Math.min(300,this.backoff*(1+Math.random()*.2)));
        if(error.fatal){this.halted=true;apply({kind:'source_stopped',timestamp:now()});}
        else{apply({kind:'source_failure',timestamp:now(),reason:`${reason}; retry in ${Math.ceil(delay)}s`});this.nextPoll=now()+delay;this.backoff=Math.min(300,this.backoff*2);}
      }
    }
    try{await this.persist({version:1,checkpoint,events});}catch(error){this.core=Core.restore(checkpoint);Object.assign(this,previousSchedule);throw error;}
    this.broadcast();
    await this.ctx.storage.setAlarm(Date.now()+Math.max(1000,Math.min(60000,this.halted?60000:(this.nextPoll-now())*1000)));
  }
}
