import {mkdir, copyFile, readFile, writeFile} from 'node:fs/promises';
import {demoData} from './demo.mjs';
const config=JSON.parse(await readFile(new URL('../wrangler.jsonc',import.meta.url),'utf8'));
await mkdir(new URL('../public/static/', import.meta.url), {recursive:true});
for (const file of ['index.html','app.js','style.css']) {
  await copyFile(new URL(`../../src/sentinel/static/${file}`, import.meta.url),
    new URL(`../public/${file === 'index.html' ? file : `static/${file}`}`, import.meta.url));
}
if(config.vars.DEMO_MODE==='true'){
  const path=new URL('../public/index.html',import.meta.url);
  let html=await readFile(path,'utf8');
  html=html.replace('<body>','<body data-demo="true">').replace('id="connection" role="status">Connecting…','id="connection" role="status">Recorded demo — not live')
    .replace('SOURCE / OPENSKY','SOURCE / RECORDED DEMO').replace('OBSERVED + ESTIMATED','RECORDED DEMO — NOT LIVE').replace('aria-label="Aircraft and vessel map"','aria-label="Recorded air and sea demo map"')
    .replace('<div class="stats">','<section class="demo-controls"><strong>Recorded demo — not live</strong><p>Archived aircraft positions or a separate synthetic alert scenario. Playback loops; timestamps are preserved.</p><label for="demo-recording">Recording</label><select id="demo-recording"><option value="0">Recorded OpenSky aircraft</option><option value="1">Synthetic alert walkthrough</option><option value="2">Recorded tracking + Kalman estimates</option><option value="3" selected>Synthetic air &amp; sea / loitering</option></select><div><button id="demo-pause">Pause</button> <button id="demo-restart">Restart</button></div><small id="demo-progress"></small></section><div class="stats">');
  await writeFile(path,html);
  await writeFile(new URL('../public/static/demo.json',import.meta.url),JSON.stringify(await demoData()));
}
