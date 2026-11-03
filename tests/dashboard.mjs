import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';

const origin = 'http://localhost:8000';
const api = async (path, body) => {
  const response = await fetch(origin + path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};
async function until(check, message = 'condition', timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  throw new Error('dashboard timeout: ' + message);
}
// Only records created through the ownership-aware demo API are reset.
await api('/demo/reset', {confirm: true});
await until(async () => (await api('/demo')).reset.status === 'succeeded', 'initial reset');
const html = await (await fetch(origin)).text();
const dom = new JSDOM(html, {url: origin, runScripts: 'outside-only'});
const window = dom.window;
const writes = [];
window.fetch = (url, options) => {
  if (options?.method === 'POST') writes.push(url);
  return fetch(new URL(url, origin), options);
};
let confirmations = 0;
let confirm = false;
window.confirm = () => { confirmations++; return confirm; };
window.eval(await readFile('web/app.js', 'utf8'));
const doc = window.document;
const el = id => doc.getElementById(id);
const selected = () => { try { return JSON.parse(el('inspector').textContent); } catch { return {}; } };
async function open(button) {
  const id = button.dataset.id;
  button.click();
  await until(() => selected().id === id && !el('entity-panel').hidden, `inspect ${id}`);
  assert.equal(el('inspector-error').textContent, '');
  const actual = await api(`/inspect/${button.dataset.resource}?id=${encodeURIComponent(id)}`);
  assert.deepEqual(selected(), actual);
}
try {
  await until(() => el('connection').textContent.startsWith('Live'), 'live connection');
  assert.match(el('models').textContent, /RandomForestClassifier/);
  assert.match(el('processings').textContent, /turbidity>4/);
  for (const button of doc.querySelectorAll('[data-section]')) {
    button.click();
    assert.equal(doc.activeElement.id, button.dataset.section);
  }
  const cancelled = writes.length;
  el('reset-demo').click();
  assert.equal(confirmations, 1);
  assert.equal(writes.length, cancelled, 'cancelled reset must not reach the API');
  el('one').click();
  await until(() => el('message').textContent.startsWith('Created 1'), 'one injection');
  el('count').value = 3;
  el('replay').dispatchEvent(new window.Event('submit', {cancelable: true}));
  await until(() => el('message').textContent.startsWith('Created 3'), 'batch replay');
  await until(async () => {
    const state = await api('/state');
    return state.MLPrediction.some(p => p.demoOwner) && el('observations').textContent.includes('Class');
  }, 'real predictions');
  const links = [...doc.querySelectorAll('#models [data-resource], #processings [data-resource], #observations [data-resource], #predictions [data-resource]')];
  assert.ok(links.length > 10);
  console.log(`Checking ${links.length} broker navigation buttons.`);
  for (const button of links) await open(button);
  const events = [...doc.querySelectorAll('#events button')];
  assert.ok(events.length > 0);
  for (const button of events) {
    button.click();
    assert.ok(selected().kind);
    assert.ok(selected().seq);
  }
  console.log('Broker and trace links passed; checking provenance and view controls.');
  const state = await api('/state');
  const prediction = state.MLPrediction.find(p => p.demoOwner);
  const predictionButton = () => [...doc.querySelectorAll('#predictions [data-id]')].find(b => b.dataset.id === prediction.id);
  await until(() => predictionButton(), 'prediction link');
  for (const relation of ['refInputEntity', 'refMLModel', 'refMLProcessing']) {
    await open(predictionButton());
    assert.match(el('inspector-fields').textContent, /Model version/);
    const target = prediction[relation].object;
    const button = [...doc.querySelectorAll('#inspector-fields [data-id]')].find(b => b.dataset.id === target);
    assert.ok(button, relation);
    await open(button);
    el('inspector-back').click();
    await until(() => selected().id === prediction.id, `Back from ${relation}`);
  }
  await open([...doc.querySelectorAll('#processings [data-resource="entities"]')][0]);
  const subscription = doc.querySelector('#inspector-fields [data-resource="subscriptions"]');
  assert.ok(subscription);
  await open(subscription);
  el('inspector-close').click();
  assert.ok(el('entity-panel').hidden);
  await open(doc.querySelector('#models [data-resource]'));
  doc.dispatchEvent(new window.KeyboardEvent('keydown', {key: 'Escape'}));
  assert.ok(el('entity-panel').hidden);
  // A missing resource is a visible error with working Back navigation.
  await open(doc.querySelector('#models [data-resource]'));
  const fetchBeforeMissing = window.fetch;
  window.fetch = (url, options) => fetchBeforeMissing(url.startsWith('/inspect/entities') ? '/inspect/entities?id=urn:ngsi-ld:missing' : url, options);
  doc.querySelector('#models [data-resource]').click();
  await until(() => el('inspector-error').textContent.includes('404'), 'missing resource error');
  window.fetch = fetchBeforeMissing;
  el('inspector-back').click();
  await until(() => selected().type === 'MLModel', 'Back after failed lookup');
  el('filter').value = 'does-not-exist';
  el('filter').dispatchEvent(new window.Event('input'));
  assert.equal(el('observations').children.length, 0);
  const beforeClear = await api('/state');
  const journal = await api('/events');
  const beforeDemo = await api('/demo');
  const writeCount = writes.length;
  el('clear-view').click();
  assert.ok(el('entity-panel').hidden);
  assert.equal(el('filter').value, '');
  assert.equal(el('message').textContent, '');
  assert.equal(el('events').children.length, 0);
  await new Promise(resolve => setTimeout(resolve, 1300));
  assert.equal(writes.length, writeCount, 'Clear View sends no mutations');
  const afterClear = await api('/state');
  for (const key of ['MLModel', 'MLProcessing', 'WaterQualityObserved', 'MLPrediction', 'subscriptions']) {
    assert.deepEqual(afterClear[key], beforeClear[key], `Clear View preserves ${key}`);
  }
  const retained = await api('/events');
  for (const event of journal) assert.deepEqual(retained.find(e => e.seq === event.seq), event);
  assert.equal((await api('/demo')).created, beforeDemo.created);
  assert.equal((await api('/demo')).next_row, beforeDemo.next_row);
  // Hold a real poll response across Clear View to expose stale-response races.
  const normalFetch = window.fetch;
  let releasePoll;
  let held = false;
  window.fetch = async (url, options) => {
    if (url.startsWith('/events?') && !held) {
      held = true;
      const response = await normalFetch(url, options);
      await new Promise(resolve => { releasePoll = resolve; });
      return response;
    }
    return normalFetch(url, options);
  };
  await until(() => releasePoll, 'held poll response');
  el('clear-view').click();
  await new Promise(resolve => setTimeout(resolve, 100));
  releasePoll();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(el('events').children.length, 0, 'late poll must not undo Clear View');
  window.fetch = normalFetch;
  // Surface a reset request failure without changing any server state.
  confirm = true;
  window.fetch = (url, options) => url === '/demo/reset' ? Promise.reject(new Error('test transport unavailable')) : normalFetch(url, options);
  el('reset-demo').click();
  await until(() => el('reset-status').textContent.includes('Reset request failed'), 'reset request error');
  assert.match(el('reset-status').textContent, /Retry Reset Demo/);
  window.fetch = normalFetch;
  // Reset is confirmed, polled and followed by the same sample IDs being reused.
  confirm = true;
  el('reset-demo').click();
  await until(() => el('reset-status').textContent.startsWith('Reset succeeded'), 'reset success');
  assert.equal(confirmations, 3);
  assert.equal(el('start').value, '0');
  assert.match(el('demo-count').textContent, /^0 demo observations/);
  assert.deepEqual((await api('/state')).MLModel, beforeClear.MLModel);
  assert.deepEqual((await api('/state')).MLProcessing, beforeClear.MLProcessing);
  el('one').click();
  await until(() => el('message').textContent.startsWith('Created 1'), 'reuse after reset');
  const afterReplay = await api('/state');
  const firstId = beforeClear.WaterQualityObserved.find(s => s.demoOwner && s.sampleId.value === afterReplay.WaterQualityObserved.find(s => s.demoOwner).sampleId.value).id;
  assert.ok(afterReplay.WaterQualityObserved.some(s => s.id === firstId));
  const resumed = new JSDOM(html, {url: origin, runScripts: 'outside-only'});
  try {
    resumed.window.fetch = (url, options) => fetch(new URL(url, origin), options);
    resumed.window.eval(await readFile('web/app.js', 'utf8'));
    await until(() => resumed.window.document.getElementById('connection').textContent.startsWith('Live'), 'reloaded dashboard');
    assert.equal(Number(resumed.window.document.getElementById('start').value), (await api('/demo')).next_row);
    resumed.window.dispatchEvent(new resumed.window.Event('pagehide'));
    resumed.window.document.getElementById('connection').textContent = 'Paused';
    resumed.window.dispatchEvent(new resumed.window.Event('pageshow'));
    await until(() => resumed.window.document.getElementById('connection').textContent.startsWith('Live'), 'page return polling');
  } finally {
    resumed.window.dispatchEvent(new resumed.window.Event('pagehide'));
    resumed.window.close();
  }
  console.log(`Dashboard passed: ${links.length} broker links, ${events.length} trace links, 5 flow actions, all provenance paths, Back/Close/Escape/errors, Clear View isolation, confirmation, reset and ID reuse.`);
} finally {
  window.dispatchEvent(new window.Event('pagehide'));
  window.close();
  await api('/demo/reset', {confirm: true});
  await until(async () => (await api('/demo')).reset.status === 'succeeded', 'cleanup reset');
}
