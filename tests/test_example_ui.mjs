import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(path.join(os.homedir(),'.cache','codex-runtimes','codex-primary-runtime','dependencies','node','node_modules','playwright')); }
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const publicRoot = path.join(root,'operator','live_command');
const virtualPages = new Set(['detection.html','investigation.html','cases.html','case-file.html']);
const apiRequests=[];
const screenDir=process.argv.includes('--screenshots') ? path.join(root,'out','example_incident_review') : null;
if (screenDir) await fs.mkdir(screenDir,{recursive:true});
const server=http.createServer(async (request,response)=>{
  const pathname=new URL(request.url,'http://localhost').pathname;
  if (pathname.startsWith('/api/')) {
    apiRequests.push(pathname);
    response.writeHead(503,{'Content-Type':'application/json'});
    response.end('{"status":"OFFLINE"}');
    return;
  }
  if (!pathname.startsWith('/operator/live_command/')) { response.writeHead(404); response.end(); return; }
  const name=path.posix.basename(pathname)||'index.html';
  const file=path.join(publicRoot,virtualPages.has(name)?'index.html':name);
  try {
    const body=await fs.readFile(file);
    const mime=name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html';
    response.writeHead(200,{'Content-Type':mime});
    response.end(body);
  } catch { response.writeHead(404); response.end(); }
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}/operator/live_command/`;
const executablePath=process.env.ESPADA_BROWSER || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
let browser;
try {
  browser=await playwright.chromium.launch({headless:true,executablePath});
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'index.html');
  const liveRequestCount=apiRequests.length;
  await page.locator('#exampleModeButton').click();
  await page.waitForFunction(()=>document.body.classList.contains('example-mode'));
  assert.match(await page.locator('#exerciseBanner').innerText(),/SYNTHETIC/);
  assert.equal(apiRequests.length,liveRequestCount,'Exercise must not call the live API');
  assert.equal((await page.request.get(base+'example_coast.geojson')).status(),200);
  assert.equal((await page.request.get(base+'example_context_coast.geojson')).status(),200);
  await page.waitForFunction(()=>document.querySelector('#evidenceMap .land-shape')?.getAttribute('d')?.length>0);
  if (screenDir) await page.screenshot({path:path.join(screenDir,'watch.png'),fullPage:true});

  await page.locator('[data-page-link="detection"]').click();
  assert.match(await page.locator('#sarSceneSelect').inputValue(),/EXERCISE_SAR/);
  await page.locator('#analyzeSarButton').click();
  await page.waitForFunction(()=>document.querySelector('#detectionStatus b')?.textContent==='ANALYST REVIEW REQUIRED');
  assert.equal(await page.locator('#sarEvidenceImage').evaluate(image=>image.complete&&image.naturalWidth>0),true);
  if (screenDir) await page.screenshot({path:path.join(screenDir,'detection.png'),fullPage:true});
  await page.locator('#approveCandidateButton').click();
  await page.waitForURL('**/investigation.html#reverse-drift');
  await page.waitForFunction(()=>document.querySelector('#driftStatus b')?.textContent==='DRIFT CLOSURE COMPLETE');

  await page.locator('[data-panel-link="candidate-attribution"]').click();
  await page.waitForFunction(()=>document.querySelector('#selectedCandidateName')?.textContent?.includes('ASTER VALE'));
  assert.match(await page.locator('#candidateSafeOutput').innerText(),/SHORTLIST/);
  if (screenDir) await page.screenshot({path:path.join(screenDir,'ranking.png'),fullPage:true});

  await page.locator('[data-page-link="case-file"]').click();
  await page.locator('#buildResponseButton').click();
  await page.waitForFunction(()=>document.querySelector('#responseStatus b')?.textContent==='EXERCISE DOSSIER READY');
  assert.match(await page.locator('#dossierLink').getAttribute('href'),/^blob:/);
  const [dossierPage]=await Promise.all([page.waitForEvent('popup'),page.locator('#dossierLink').click()]);
  await dossierPage.waitForLoadState('domcontentloaded');
  assert.match(await dossierPage.locator('body').innerText(),/SYNTHETIC EXERCISE ONLY/);
  assert.match(await dossierPage.locator('body').innerText(),/MV ASTER VALE/);
  await dossierPage.close();
  if (screenDir) await page.screenshot({path:path.join(screenDir,'dossier.png'),fullPage:true});
  assert.equal(apiRequests.length,liveRequestCount,'Exercise must remain independent of all live case APIs');
  assert.deepEqual(errors,[],'Pages must not throw JavaScript errors');

  await page.locator('#exampleModeButton').click();
  await page.waitForFunction(()=>!document.body.classList.contains('example-mode'));
  assert.equal(await page.locator('#exerciseBanner').isHidden(),true);
  console.log('Example incident UI: Watch → Detection → Investigation → Case File → Live passed');
} finally {
  if (browser) await browser.close();
  await new Promise(resolve=>server.close(resolve));
}
