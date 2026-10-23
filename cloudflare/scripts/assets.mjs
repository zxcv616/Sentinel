import {mkdir, copyFile} from 'node:fs/promises';
await mkdir(new URL('../public/static/', import.meta.url), {recursive:true});
for (const file of ['index.html','app.js','style.css']) {
  await copyFile(new URL(`../../src/sentinel/static/${file}`, import.meta.url),
    new URL(`../public/${file === 'index.html' ? file : `static/${file}`}`, import.meta.url));
}
