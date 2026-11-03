import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';

const origin = 'http://localhost:8000';
const html = await (await fetch(origin)).text();
const dom = new JSDOM(html, {url: origin, runScripts: 'outside-only'});
const window = dom.window;
window.fetch = (url, options) => fetch(new URL(url, origin), options);
window.eval(await readFile('web/app.js', 'utf8'));
async function wait(check) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('dashboard timeout: ' + window.document.getElementById('message').textContent);
}
try {
  const doc = window.document;
  await wait(() => doc.getElementById('connection').textContent.startsWith('Live'));
  assert.match(doc.getElementById('models').textContent, /RandomForestClassifier/);
  assert.match(doc.getElementById('processings').textContent, /turbidity>4/);
  doc.querySelector('#models button').click();
  assert.match(doc.getElementById('inspector').textContent, /servingEndpoint/);
  doc.getElementById('one').click();
  await wait(() => doc.getElementById('message').textContent.startsWith('Created 1'));
  doc.getElementById('count').value = 3;
  doc.getElementById('replay').dispatchEvent(new window.Event('submit', {cancelable: true}));
  await wait(() => doc.getElementById('message').textContent.startsWith('Created 3'));
  await wait(() => doc.getElementById('events').textContent.includes('prediction'));
  assert.match(doc.getElementById('observations').textContent, /Class/);
  assert.match(doc.getElementById('observations').textContent, /Unobserved/);
  console.log('Dashboard DOM test passed: live data, payload inspector, one injection, batch replay, predictions and unobserved notifications.');
} finally {
  window.close();
}
