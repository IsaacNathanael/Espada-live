// Verify both cases render through the original five-page recorded console.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync, mkdtempSync, readFileSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';

const root = resolve(import.meta.dirname, '..');
const python = process.env.ESPADA_TEST_PYTHON ||
  'C:\\Users\\glori\\Documents\\Codex\\2026-09-02\\do-x20\\work\\envs\\espada-py\\Scripts\\python.exe';
const chromePath = process.env.ESPADA_TEST_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const profile = mkdtempSync(join(resolve(root, '..'), 'recorded-cases-browser-'));
const base = 'http://127.0.0.1:4191/recorded/';
const server = spawn(python, ['-m', 'http.server', '4191', '--bind', '127.0.0.1', '--directory',
  join(root, 'docs', 'prototype')], {stdio:'ignore', windowsHide:true});
const chrome = spawn(chromePath, [
  '--headless', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', '--remote-allow-origins=*', '--window-size=1500,1100',
  `--user-data-dir=${profile}`, base + 'index.html'
], {stdio:'ignore', windowsHide:true});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const portFile = join(profile, 'DevToolsActivePort');
let websocket;
let nextId = 0;
const pending = new Map();
const exceptions = [];

try {
  for (let i=0; i<80 && !existsSync(portFile); i+=1) await pause(100);
  assert.ok(existsSync(portFile), 'Chrome did not start');
  const port = Number(readFileSync(portFile,'utf8').split(/\r?\n/)[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(item => item.type === 'page' && item.url.includes('/recorded/index.html'));
  assert.ok(target, 'Recorded console tab did not open');
  websocket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((done,fail) => {
    websocket.addEventListener('open',done,{once:true});
    websocket.addEventListener('error',fail,{once:true});
  });
  websocket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
    if (!message.id || !pending.has(message.id)) return;
    const {done,fail} = pending.get(message.id); pending.delete(message.id);
    message.error ? fail(new Error(message.error.message)) : done(message.result);
  });
  function command(method,params={}) {
    const id=++nextId;
    return new Promise((done,fail) => {
      pending.set(id,{done,fail});
      websocket.send(JSON.stringify({id,method,params}));
    });
  }
  async function evaluate(expression) {
    const result = await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  async function until(expression, label) {
    for (let i=0; i<70; i+=1) {
      if (await evaluate(expression)) return;
      await pause(150);
    }
    throw new Error(`Timeout waiting for ${label}`);
  }
  async function navigate(path) {
    await command('Page.navigate',{url:base + path});
    await until('document.readyState === "complete" && document.body?.dataset.recorded === "true" && !!document.querySelector(".workflow-step")',path);
  }
  await command('Runtime.enable');
  await command('Page.enable');
  await until('document.querySelector("#sourceCount")?.textContent === "RECORDED CASE"','original case');
  assert.equal(await evaluate('document.querySelectorAll(".workflow-step").length'),5);
  assert.match(await evaluate('document.querySelector("#recordedNotice").textContent'),/15 September 2026/);
  await navigate('cases.html');
  await until('document.querySelectorAll("#caseRegisterRows tr[data-case-id]").length === 2','two case rows');
  assert.match(await evaluate('document.querySelector("#caseRegisterRows").textContent'),/Mauritius/);
  await evaluate('document.querySelector("#caseRegisterRows tr[data-case-id=EXERCISE_S1_WAKASHIO_20200811]").click()');
  await until('location.search.includes("case=known_source") && document.querySelector("#selectedCaseId")?.textContent.includes("WAKASHIO")','exercise selected');
  assert.equal(await evaluate('document.querySelectorAll(".workflow-step").length'),5);
  assert.match(await evaluate('document.querySelector("#recordedNotice").textContent'),/constructed/);
  await navigate('detection.html?mode=recorded&case=known_source');
  await until('document.querySelector("#sarEvidenceImage")?.naturalWidth === 944','real SAR image');
  assert.match(await evaluate('document.querySelector("#viewerLabel").textContent'),/NO MODEL INFERENCE/);
  if (process.env.ESPADA_CAPTURE_RECORDED) {
    const screenshot = await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    writeFileSync(process.env.ESPADA_CAPTURE_RECORDED + '-detection.png',Buffer.from(screenshot.data,'base64'));
  }
  await navigate('investigation.html?mode=recorded&case=known_source#candidate-attribution');
  await until('document.querySelectorAll("#candidateRows tr[data-candidate-mmsi]").length === 2','two ranked vessels');
  await until('document.querySelectorAll("#candidateMap .candidate-track-line").length === 2','two mapped candidate routes');
  assert.match(await evaluate('document.querySelector("#candidateRows tr").textContent'),/MV Wakashio/);
  assert.match(await evaluate('document.querySelector("#attributionDecision").textContent'),/LIMITED SHORTLIST/);
  if (process.env.ESPADA_CAPTURE_RECORDED) {
    const screenshot = await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    writeFileSync(process.env.ESPADA_CAPTURE_RECORDED + '-investigation.png',Buffer.from(screenshot.data,'base64'));
  }
  await navigate('case-file.html?mode=recorded&case=known_source');
  await until('document.querySelector("#responseStatus")?.textContent.includes("ANALYST SHORTLIST READY")','case file');
  const dossier = await evaluate('document.querySelector("#dossierLink").href');
  assert.equal((await fetch(dossier)).status,200);
  await navigate('cases.html?mode=recorded&case=known_source');
  await until('document.querySelectorAll("#caseRegisterRows tr[data-case-id]").length === 2','case switcher');
  await evaluate('document.querySelector("#caseRegisterRows tr:not([data-case-id=EXERCISE_S1_WAKASHIO_20200811])").click()');
  await until('!location.search.includes("case=known_source") && document.querySelector("#sourceCount")?.textContent === "RECORDED CASE"','original restored');
  assert.deepEqual(exceptions,[]);
  console.log('PASS: original console, two case rows, case switching, real SAR image, ranking, dossier, all five pages');
  await command('Browser.close');
} finally {
  websocket?.close();
  chrome.kill();
  server.kill();
}
