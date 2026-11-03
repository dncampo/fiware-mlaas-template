'use strict';
let sequence = 0;
let trace = [];
const byId = id => document.getElementById(id);
const val = (obj, key) => obj[key]?.value;
function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}
function inspect(payload) { byId('inspector').textContent = JSON.stringify(payload, null, 2); }
function link(text, payload) {
  const item = node('button', text, 'link');
  item.onclick = () => inspect(payload);
  return item;
}
async function request(url, body) {
  const response = await fetch(url, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}
function render(state) {
  byId('models').replaceChildren();
  byId('model-count').textContent = state.MLModel.length;
  for (const model of state.MLModel) {
    const card = node('div', undefined, 'card');
    card.append(link(val(model, 'name'), model), node('p', `${val(model, 'algorithm')} / ${val(model, 'mlFramework')} / version ${val(model, 'version')}`), node('code', val(model, 'servingEndpoint')), node('p', `Artifact: ${val(model, 'artifactURI')}`));
    byId('models').append(card);
  }
  byId('processings').replaceChildren();
  for (const processing of state.MLProcessing) {
    const card = node('div', undefined, 'card');
    const config = val(processing, 'subscriptionData');
    const subscription = state.subscriptions.find(s => s.id === processing.refSubscription?.object);
    card.append(link(val(processing, 'name'), processing), node('p', `${val(processing, 'processingStatus')} / watches: ${config.watchedAttributes.join(', ')} / q: ${config.q || '(none)'}`), node('p', `Source types: ${config.entities.map(e => e.type).join(', ')}`));
    card.append(subscription ? link(`Subscription: ${subscription.id}`, subscription) : node('p', 'No live subscription'));
    byId('processings').append(card);
  }
  const notifications = new Set(trace.filter(e => e.kind === 'notification').flatMap(e => e.payload.data.map(o => o.id)));
  byId('observations').replaceChildren();
  for (const source of [...state.WaterQualityObserved].reverse().slice(0, 100)) {
    const row = node('tr');
    const name = node('td');
    name.append(link(`Sample ${val(source, 'sampleId')}`, source), node('small', source.id));
    row.append(name, node('td', val(source, 'turbidity') ?? 'Missing'), node('td', notifications.has(source.id) ? 'Received' : 'Unobserved', notifications.has(source.id) ? '' : 'muted'));
    const output = node('td');
    const predictions = state.MLPrediction.filter(p => p.refInputEntity.object === source.id);
    for (const prediction of predictions) {
      output.append(link(`Class ${val(prediction, 'predictedValue')} / ${val(prediction, 'predictedAt')}`, prediction));
      const model = state.MLModel.find(m => m.id === prediction.refMLModel.object);
      output.append(node('small', `Model: ${prediction.refMLModel.object}`), node('small', `Processing: ${prediction.refMLProcessing.object}`));
      if (model) output.append(link('Inspect model', model));
    }
    if (!predictions.length) output.append(node('span', 'No prediction', 'muted'));
    row.append(output);
    byId('observations').append(row);
  }
  byId('totals').textContent = `${state.WaterQualityObserved.length} observations / ${state.MLPrediction.length} predictions`;
  byId('events').replaceChildren();
  for (const event of [...trace].reverse().slice(0, 100)) {
    const row = node('div', undefined, 'trace');
    row.append(node('time', event.time.slice(11, 23)), node('span', event.kind, event.kind.includes('error') ? 'error' : ''), link(JSON.stringify(event.payload), event));
    byId('events').append(row);
  }
}
async function refresh() {
  try {
    const events = await request(`/events?after=${sequence}`);
    if (events.length) sequence = events[events.length - 1].seq;
    trace = trace.concat(events).slice(-2000);
    render(await request('/state'));
    byId('connection').textContent = 'Live / updates every second';
  } catch (error) { byId('connection').textContent = 'Connection error'; byId('message').textContent = error.message; }
  setTimeout(refresh, 1000);
}
async function inject(count) {
  const buttons = document.querySelectorAll('#replay button');
  buttons.forEach(b => b.disabled = true);
  try {
    const start = Number(byId('start').value);
    const result = await request('/inject', {start, count});
    byId('message').textContent = `Created ${result.ids.length} observations in the broker. Waiting for subscription notifications.`;
    byId('start').value = start + count;
  } catch (error) { byId('message').textContent = error.message; }
  finally { buttons.forEach(b => b.disabled = false); }
}
byId('one').onclick = () => inject(1);
byId('replay').onsubmit = event => { event.preventDefault(); inject(Number(byId('count').value)); };
refresh();
