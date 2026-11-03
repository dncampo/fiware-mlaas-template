'use strict';
let sequence = 0;
let trace = [];
let state = null;
let viewVersion = 0;
let inspectionVersion = 0;
let history = [];
let current = null;
let replaying = false;
let demo = null;
let completedGeneration = -1;
let dismissedReset = '';
let disposed = false;
let refreshTimer;
const byId = id => document.getElementById(id);
const val = (obj, key) => obj[key]?.value;
const list = item => Array.isArray(item) ? item : [item];
function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}
async function request(url, body) {
  const response = await fetch(url, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}
function link(text, id, resource = 'entities') {
  const item = node('button', text, 'link');
  item.type = 'button';
  item.dataset.resource = resource;
  item.dataset.id = id;
  item.onclick = () => inspect({id, resource});
  return item;
}
function closeInspector() {
  inspectionVersion++;
  current = null;
  history = [];
  byId('entity-panel').hidden = true;
  byId('inspector').textContent = 'No payload selected.';
  byId('inspector-fields').replaceChildren();
  byId('inspector-error').textContent = '';
}
async function inspect(selection, push = true) {
  if (push && current) history.push(current);
  current = selection;
  const version = ++inspectionVersion;
  byId('entity-panel').hidden = false;
  byId('inspector-title').textContent = selection.id || 'Recorded event';
  byId('inspector').textContent = 'Loading broker resource...';
  byId('inspector-fields').replaceChildren();
  byId('inspector-error').textContent = '';
  byId('inspector-back').disabled = history.length === 0;
  byId('inspector-close').focus();
  try {
    const payload = selection.payload || await request(`/inspect/${selection.resource}?id=${encodeURIComponent(selection.id)}`);
    if (version !== inspectionVersion) return;
    byId('inspector-title').textContent = `${payload.type || 'Event'} / ${payload.id || payload.kind}`;
    const fields = selection.payload ? payload.payload : payload;
    for (const [key, attr] of Object.entries(fields)) {
      if (key === '@context') continue;
      const row = node('div', undefined, 'field');
      row.append(node('strong', key));
      if (attr?.type === 'Relationship') {
        row.append(link(attr.object, attr.object, key === 'refSubscription' ? 'subscriptions' : 'entities'));
      } else {
        const value = attr?.type === 'Property' ? attr.value : attr;
        row.append(node('code', typeof value === 'string' ? value : JSON.stringify(value)));
      }
      byId('inspector-fields').append(row);
    }
    if (payload.type === 'MLPrediction') {
      let versionText;
      try {
        const model = await request(`/inspect/entities?id=${encodeURIComponent(payload.refMLModel.object)}`);
        versionText = val(model, 'version');
      } catch (error) { versionText = `Unavailable: ${error.message}`; }
      if (version !== inspectionVersion) return;
      const row = node('div', undefined, 'field');
      row.append(node('strong', 'Model version'), node('code', versionText));
      byId('inspector-fields').append(row);
    }
    byId('inspector').textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    if (version === inspectionVersion) {
      byId('inspector-error').textContent = error.message;
      byId('inspector').textContent = 'Resource could not be loaded. Use Back to return.';
    }
  }
}
function render() {
  if (!state) return;
  byId('models').replaceChildren();
  byId('model-count').textContent = state.MLModel.length;
  for (const model of state.MLModel) {
    const card = node('div', undefined, 'card');
    card.append(link(val(model, 'name'), model.id), node('p', `${val(model, 'algorithm')} / ${val(model, 'mlFramework')} / version ${val(model, 'version')}`), node('code', val(model, 'servingEndpoint')), node('p', `Artifact: ${val(model, 'artifactURI')}`));
    byId('models').append(card);
  }
  byId('processings').replaceChildren();
  for (const processing of state.MLProcessing) {
    const card = node('div', undefined, 'card');
    const config = val(processing, 'subscriptionData');
    card.append(link(val(processing, 'name'), processing.id), node('p', `${val(processing, 'processingStatus')} / watches: ${list(config.watchedAttributes).join(', ')} / q: ${config.q || '(none)'}`), node('p', `Source types: ${list(config.entities).map(e => e.type).join(', ')}`));
    const sid = processing.refSubscription?.object;
    card.append(sid ? link(`Subscription: ${sid}`, sid, 'subscriptions') : node('p', 'No subscription reference'));
    byId('processings').append(card);
  }
  const notifications = new Set(trace.filter(e => e.kind === 'notification').flatMap(e => e.payload.data.map(o => o.id)));
  const filter = byId('filter').value.toLowerCase();
  byId('observations').replaceChildren();
  for (const source of [...state.WaterQualityObserved].reverse().filter(s => JSON.stringify(s).toLowerCase().includes(filter)).slice(0, 100)) {
    const row = node('tr');
    const name = node('td');
    name.append(link(`Sample ${val(source, 'sampleId')}`, source.id), node('small', source.id));
    row.append(name, node('td', val(source, 'turbidity') ?? 'Missing'), node('td', notifications.has(source.id) ? 'Received' : 'Unobserved', notifications.has(source.id) ? '' : 'muted'));
    const output = node('td');
    const predictions = state.MLPrediction.filter(p => p.refInputEntity.object === source.id);
    for (const prediction of predictions) {
      output.append(link(`Class ${val(prediction, 'predictedValue')} / ${val(prediction, 'predictedAt')}`, prediction.id));
      const provenance = node('div', undefined, 'provenance');
      provenance.append(link(`Source: ${prediction.refInputEntity.object}`, prediction.refInputEntity.object), link(`Model: ${prediction.refMLModel.object}`, prediction.refMLModel.object), link(`Processing: ${prediction.refMLProcessing.object}`, prediction.refMLProcessing.object));
      output.append(provenance);
    }
    if (!predictions.length) output.append(node('span', 'No prediction', 'muted'));
    row.append(output);
    byId('observations').append(row);
  }
  byId('predictions').replaceChildren();
  for (const prediction of [...state.MLPrediction].reverse().slice(0, 100)) {
    const row = node('div', undefined, 'card');
    row.append(link(prediction.id, prediction.id), node('p', `Class ${val(prediction, 'predictedValue')}`));
    byId('predictions').append(row);
  }
  byId('totals').textContent = `${state.WaterQualityObserved.length} observations / ${state.MLPrediction.length} predictions`;
  byId('events').replaceChildren();
  for (const event of [...trace].reverse().slice(0, 100)) {
    const row = node('div', undefined, 'trace');
    const action = node('button', JSON.stringify(event.payload), 'link');
    action.type = 'button';
    action.onclick = () => inspect({payload: event});
    row.append(node('time', event.time.slice(11, 23)), node('span', event.kind, event.kind.includes('error') ? 'error' : ''), action);
    byId('events').append(row);
  }
}
function renderDemo() {
  if (!demo || disposed) return;
  const reset = demo.reset;
  const blocked = ['resetting', 'failed'].includes(reset.status);
  document.querySelectorAll('#replay button').forEach(b => b.disabled = replaying || blocked);
  byId('reset-demo').disabled = reset.status === 'resetting';
  byId('reset-demo').textContent = reset.status === 'failed' ? 'Retry Reset Demo' : 'Reset Demo';
  byId('demo-count').textContent = `${demo.created} demo observations created / next row ${demo.next_row}${demo.replaying ? ' / replay active' : ''}`;
  byId('reset-status').textContent = reset.status === 'idle' || JSON.stringify(reset) === dismissedReset ? '' :
    `Reset ${reset.status}: ${reset.phase} (${reset.completed || 0}/${reset.total || 0} samples removed). ${(reset.errors || []).join(' / ')}`;
  byId('reset-status').className = reset.status === 'failed' ? 'error' : '';
}
async function refresh() {
  const version = viewVersion;
  const results = await Promise.allSettled([request(`/events?after=${sequence}`), request('/state'), request('/demo')]);
  const next = results[2].status === 'fulfilled' ? results[2].value : null;
  const resetCompleted = next?.reset.status === 'succeeded' && completedGeneration !== next.generation;
  if (resetCompleted) {
    const fresh = await Promise.allSettled([request('/events'), request('/state')]);
    results[0] = fresh[0];
    results[1] = fresh[1];
  }
  if (version === viewVersion) {
    if (next && demo === null) byId('start').value = next.next_row;
    if (resetCompleted) {
      completedGeneration = next.generation;
      closeInspector();
      trace = [];
      byId('start').value = next.next_row;
      byId('message').textContent = '';
    }
    if (results[0].status === 'fulfilled') {
      const events = results[0].value;
      if (events.length) sequence = events[events.length - 1].seq;
      trace = trace.concat(events).slice(-2000);
    }
    if (results[1].status === 'fulfilled') state = results[1].value;
    if (next) { demo = next; renderDemo(); }
    render();
    const failure = results.find(r => r.status === 'rejected');
    byId('connection').textContent = failure ? 'Connection error' : 'Live / updates every second';
    if (failure) byId('message').textContent = failure.reason.message;
  }
  if (!disposed) refreshTimer = setTimeout(refresh, 1000);
}
async function inject(count) {
  replaying = true;
  renderDemo();
  const version = viewVersion;
  try {
    const result = await request('/inject', {start: Number(byId('start').value), count});
    if (version !== viewVersion) return;
    byId('message').textContent = `${result.stopped ? 'Stopped after creating' : 'Created'} ${result.ids.length} observations in the broker.`;
    byId('start').value = result.next_row;
  } catch (error) { if (version === viewVersion) byId('message').textContent = error.message; }
  finally { replaying = false; renderDemo(); }
}
async function clearView() {
  const version = ++viewVersion;
  closeInspector();
  trace = [];
  byId('filter').value = '';
  byId('message').textContent = '';
  byId('reset-status').textContent = '';
  dismissedReset = JSON.stringify(demo?.reset);
  render();
  // Advance only the local cursor; the server journal stays intact.
  try {
    const events = await request('/events');
    if (version === viewVersion && events.length) sequence = events[events.length - 1].seq;
  } catch (error) { /* Connection status is updated by the regular poll. */ }
}
byId('one').onclick = () => inject(1);
byId('replay').onsubmit = event => { event.preventDefault(); inject(Number(byId('count').value)); };
byId('filter').oninput = render;
byId('clear-view').onclick = clearView;
byId('reset-demo').onclick = async () => {
  if (!window.confirm('Stop replay and delete this demo\'s observations, predictions and execution trace? Model, processing, subscription and training artifacts will be kept.')) return;
  const version = ++viewVersion;
  byId('message').textContent = '';
  try {
    const result = await request('/demo/reset', {confirm: true});
    if (version === viewVersion) { demo = result; renderDemo(); }
  } catch (error) {
    if (version === viewVersion) byId('reset-status').textContent = `Reset request failed: ${error.message}. Retry Reset Demo.`;
  }
};
byId('inspector-close').onclick = closeInspector;
byId('inspector-back').onclick = () => { if (history.length) inspect(history.pop(), false); };
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeInspector(); });
window.addEventListener('pagehide', () => {
  disposed = true;
  viewVersion++;
  inspectionVersion++;
  clearTimeout(refreshTimer);
});
window.addEventListener('pageshow', () => {
  if (disposed) { disposed = false; refresh(); }
});
document.querySelectorAll('[data-section]').forEach(button => {
  button.onclick = () => {
    const section = byId(button.dataset.section);
    section.tabIndex = -1;
    section.scrollIntoView?.({behavior: 'smooth'});
    section.focus();
  };
});
refresh();
