export function boundedInteger(value,fallback,min,max){const n=value===undefined?fallback:Number(value);if(!Number.isInteger(n)||n<min||n>max)throw new Error(`Configuration must be an integer between ${min} and ${max}`);return n;}
export function settings(env){
  const authenticated=Boolean(env.OPENSKY_CLIENT_ID&&env.OPENSKY_CLIENT_SECRET);
  if(Boolean(env.OPENSKY_CLIENT_ID)!==Boolean(env.OPENSKY_CLIENT_SECRET))throw new Error('Set both OpenSky credentials or neither');
  const requested=boundedInteger(env.POLL_SECONDS,240,30,600);
  return {pollSeconds:authenticated?requested:Math.max(240,requested),rawLimit:boundedInteger(env.RAW_RECORD_LIMIT,64,1,128),maxClients:boundedInteger(env.MAX_CLIENTS,20,1,50),authenticated};
}
export function retryAfter(headers,now){
  return Math.max(0,...['retry-after','x-rate-limit-retry-after-seconds'].map(name=>{
    const v=headers.get(name);if(v===null)return 0;
    const numeric=Number(v),n=Number.isFinite(numeric)?numeric:(Date.parse(v)/1000-now);
    return Number.isFinite(n)?Math.max(0,n):0;
  }));
}
export const encodeBody=bytes=>{let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(s);};
export async function readBounded(response,maxBytes=2*1024*1024){
  if(!response.body)return new Uint8Array();
  const reader=response.body.getReader(),chunks=[];let size=0;
  try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>maxBytes){await reader.cancel();throw new Error('Source response exceeds recording size limit');}chunks.push(value);}}finally{reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}return bytes;
}
export async function pack(value){
  const stream=new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream('gzip'));
  const packed='gz:'+encodeBody(new Uint8Array(await new Response(stream).arrayBuffer()));
  if(packed.length>1900000)throw new Error('Stored state exceeds the SQLite row safety limit');
  return packed;
}
export async function unpack(value){
  if(!value.startsWith('gz:'))return JSON.parse(value); // local development migration
  const bytes=Uint8Array.from(atob(value.slice(3)),c=>c.charCodeAt(0));
  return JSON.parse(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text());
}
