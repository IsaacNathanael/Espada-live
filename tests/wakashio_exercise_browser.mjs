import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync, mkdtempSync, readFileSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';

const project = resolve(import.meta.dirname, '..');
const chromePath = process.env.ESPADA_TEST_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const profile = mkdtempSync(join(resolve(project, '..', '..', 'work'), 'wakashio-browser-'));
const url = process.env.ESPADA_WAKASHIO_URL || 'http://127.0.0.1:4190/operator/live_command/wakashio.html';
const chrome = spawn(chromePath, [
  '--headless', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', '--remote-allow-origins=*', '--window-size=1500,1100',
  `--user-data-dir=${profile}`, url
], {stdio:'ignore', windowsHide:true});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const portFile = join(profile, 'DevToolsActivePort');
let websocket;
let nextId = 0;
const pending = new Map();
const exceptions = [];

try {
  for (let attempt = 0; attempt < 80 && !existsSync(portFile); attempt += 1) await pause(100);
  assert.ok(existsSync(portFile), 'Chrome did not open its debugging port');
  const port = Number(readFileSync(portFile, 'utf8').split(/\r?\n/)[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(item => item.type === 'page' && item.url.includes('wakashio.html'));
  assert.ok(target, 'The exercise page did not open');
  websocket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolveOpen,rejectOpen) => {
    websocket.addEventListener('open',resolveOpen,{once:true});
    websocket.addEventListener('error',rejectOpen,{once:true});
  });
  websocket.addEventListener('message',event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
    if (!message.id || !pending.has(message.id)) return;
    const {resolve:done,reject} = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(message.error.message)) : done(message.result);
  });
  function command(method,params={}) {
    const id = ++nextId;
    return new Promise((done,reject) => {
      pending.set(id,{resolve:done,reject});
      websocket.send(JSON.stringify({id,method,params}));
    });
  }
  async function evaluate(expression) {
    const answer = await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (answer.exceptionDetails) throw new Error(answer.exceptionDetails.text);
    return answer.result.value;
  }
  await command('Runtime.enable');
  await command('Page.enable');
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (await evaluate('document.readyState === "complete" && !!document.querySelector(".wakashio-fact")')) break;
    await pause(100);
  }
  assert.equal(await evaluate('document.querySelectorAll(".wakashio-fact").length'),4);
  assert.equal(await evaluate('document.querySelector("#caseImageView img").naturalWidth'),3508);
  assert.match(await evaluate('document.querySelector("#caseStageTitle").textContent'),/documented source/);

  await evaluate('document.querySelector("#caseNext").click()');
  assert.match(await evaluate('document.querySelector("#caseStageTitle").textContent'),/real oil-spill image/);
  await evaluate('document.querySelector("#caseNext").click()');
  assert.equal(await evaluate('document.querySelector("#caseChartView").hidden'),false);
  assert.equal(await evaluate('document.querySelectorAll("#caseChart .case-route").length'),2);
  assert.equal(await evaluate('document.querySelectorAll("#caseChart .case-origin").length'),1);
  const firstOrigin = await evaluate('document.querySelector("#caseStageFacts").textContent');
  await evaluate(`(() => { const slider=document.querySelector('#caseAge'); slider.value='3'; slider.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  assert.notEqual(await evaluate('document.querySelector("#caseStageFacts").textContent'),firstOrigin);
  await evaluate(`(() => { const slider=document.querySelector('#caseAge'); slider.value='1.5'; slider.dispatchEvent(new Event('input',{bubbles:true})); })()`);

  await evaluate('document.querySelector("#caseNext").click()');
  assert.equal(await evaluate('document.querySelectorAll(".wakashio-rank").length'),3);
  assert.match(await evaluate('document.querySelector(".wakashio-rank b").textContent'),/MV Wakashio/);
  await evaluate('document.querySelector("#casePlay").click()');
  await pause(700);
  assert.ok(await evaluate('Number(document.querySelector("#caseClock").value)') > 8);
  await evaluate('document.querySelector("#casePlay").click()');
  await evaluate('document.querySelector("[data-view=image]").click()');
  assert.equal(await evaluate('document.querySelector("#caseImageView").hidden'),false);
  await evaluate('document.querySelector("[data-view=chart]").click()');
  assert.equal(await evaluate('document.querySelector("#caseChartView").hidden'),false);
  await evaluate('document.querySelector("#caseNext").click()');
  assert.match(await evaluate('document.querySelector("#caseDecision").textContent'),/ranks #1 of 3/);
  assert.equal(await evaluate('document.querySelector("#caseNext").disabled'),true);

  if (process.env.ESPADA_WAKASHIO_SCREENSHOT) {
    const screenshot = await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    writeFileSync(process.env.ESPADA_WAKASHIO_SCREENSHOT,Buffer.from(screenshot.data,'base64'));
  }
  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(150);
  assert.ok(await evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'), 'Mobile horizontal overflow');
  assert.deepEqual(exceptions,[]);
  console.log('PASS: image, five stages, age sensitivity, route playback, ranking, view switch, decision, mobile layout');
  await command('Browser.close');
} finally {
  websocket?.close();
  chrome.kill();
}
