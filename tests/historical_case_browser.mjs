// Browser smoke test without npm dependencies or external network access.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync, mkdtempSync, readFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const project = resolve(import.meta.dirname, '..');
const site = join(project, 'operator', 'historical_case');
const chromePath = process.env.ESPADA_TEST_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const profile = mkdtempSync(join(resolve(project, '..', '..', 'work'), 'case-browser-'));
const firstPage = pathToFileURL(join(site, 'index.html')).href;
const chrome = spawn(chromePath, [
  '--headless', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', '--remote-allow-origins=*',
  `--user-data-dir=${profile}`, firstPage
], {stdio:'ignore', windowsHide:true});

const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
const portFile = join(profile,'DevToolsActivePort');
let websocket;
let nextId = 0;
const pending = new Map();
try {
  for (let attempt=0; attempt<70 && !existsSync(portFile); attempt++) await pause(100);
  assert.ok(existsSync(portFile), 'Chrome did not open a debugging port');
  const port = Number(readFileSync(portFile,'utf8').split(/\r?\n/)[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(item => item.type==='page' && item.url.includes('historical_case'));
  assert.ok(target, 'Historical case tab did not open');
  websocket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{
    websocket.addEventListener('open',resolve,{once:true});
    websocket.addEventListener('error',reject,{once:true});
  });
  websocket.addEventListener('message',event=>{
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const {resolve,reject}=pending.get(message.id); pending.delete(message.id);
    message.error?reject(new Error(message.error.message)):resolve(message.result);
  });
  function command(method,params={}) {
    const id=++nextId;
    return new Promise((resolve,reject)=>{
      pending.set(id,{resolve,reject});
      websocket.send(JSON.stringify({id,method,params}));
    });
  }
  async function evaluate(expression) {
    const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  async function navigate(filename) {
    await command('Page.navigate',{url:pathToFileURL(join(site,filename)).href});
    const expectedPage = filename === 'index.html' ? 'watch' : filename.replace('.html','');
    for (let attempt=0;attempt<30;attempt++) {
      await pause(100);
      if (await evaluate(`document.readyState === "complete" && document.body?.dataset.casePage === ${JSON.stringify(expectedPage)} && !!document.querySelector(".workflow")`)) return;
    }
    throw new Error(`${filename} did not render`);
  }

  await navigate('index.html');
  assert.equal(await evaluate('document.querySelectorAll(".workflow-step").length'),5);
  assert.match(await evaluate('document.querySelector("#observationState").textContent'),/MAPPED SLICK/);
  await evaluate('document.querySelector("#replayStart").click()');
  await pause(100);
  assert.match(await evaluate('document.querySelector("#observationState").textContent'),/NO SLICK OBSERVATION/);
  await evaluate('document.querySelector("#replayPause").click()');
  const before = await evaluate('Number(document.querySelector("#timeSlider").value)');
  await evaluate('document.querySelector("#stepForward").click()');
  assert.equal(await evaluate('Number(document.querySelector("#timeSlider").value)'),before+1);
  await evaluate(`document.querySelector('.case-vessel-button[data-mmsi="376955000"]').click()`);
  assert.match(await evaluate('document.querySelector("#vesselDetail h3").textContent'),/STANFORD HAWK/);

  await navigate('detection.html');
  assert.ok(await evaluate('document.querySelectorAll(".case-slick").length')>0);
  await evaluate('document.querySelector("#slickToggle").click()');
  assert.equal(await evaluate('document.querySelectorAll(".case-slick").length'),0);
  await evaluate('document.querySelector("#slickToggle").click()');
  assert.ok(await evaluate('document.querySelectorAll(".case-slick").length')>0);
  assert.equal(await evaluate('document.querySelector(".case-figure img").complete'),true);

  await navigate('investigation.html');
  assert.equal(await evaluate('document.querySelectorAll(".case-rank-row").length'),5);
  await evaluate(`document.querySelector('.case-rank-row[data-mmsi="215337000"]').click()`);
  assert.match(await evaluate('document.querySelector("#vesselDetail h3").textContent'),/BOKA EXPEDITION/);
  await navigate('cases.html');
  assert.ok(await evaluate('document.querySelectorAll(".case-gate-list>div").length')>0);
  await navigate('case-file.html');
  assert.match(await evaluate('document.querySelector(".case-verdict").textContent'),/PRIORITY ANALYST REVIEW/);
  assert.equal(await evaluate('document.querySelectorAll(".case-source-list a").length'),4);
  console.log('PASS: five pages, timeline, observation gate, vessel selection, slick toggle, ranking, decision');
  await command('Browser.close');
} finally {
  websocket?.close();
  chrome.kill();
}
