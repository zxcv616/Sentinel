import {readFileSync} from 'node:fs';
import {Core} from '../src/core.mjs';
export function replayCheckpointBundle(bundle){
  if(bundle.format!=='sentinel-cloudflare-checkpoints-v1'||!Array.isArray(bundle.batches))throw new Error('Unsupported checkpoint export');
  return bundle.batches.map(batch=>{
    if(batch.version!==1||!batch.checkpoint||!Array.isArray(batch.events))throw new Error('Invalid checkpoint batch');
    const core=Core.restore(batch.checkpoint);
    for(const event of batch.events)core.apply(event);
    core.alerts=core.alerts.slice(-core.alertConfig.recent_limit);
    return core.result();
  });
}
if(process.argv[1]&&import.meta.url===new URL(`file://${process.argv[1]}`).href){
  try{if(!process.argv[2])throw new Error('Usage: node scripts/replay.mjs recording.json');
    const results=replayCheckpointBundle(JSON.parse(readFileSync(process.argv[2],'utf8')));
    console.log(JSON.stringify({batches:results.length,final:results.at(-1)||null},null,2));
  }catch(error){console.error(error.message);process.exitCode=1;}
}
