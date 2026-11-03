"""One local adapter, one worker, standard broker subscriptions."""
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mlaas.common import CONTEXT, digest, prediction_id, prop, rel, value

BROKER = os.getenv('BROKER_URL', 'http://localhost:1026') + '/ngsi-ld/v1/'
PUBLIC = os.getenv('ADAPTER_URL', 'http://host.docker.internal:8000')
DB = os.getenv('STATE_DB', 'adapter.db')
stop = threading.Event()
runtime_lock = threading.RLock()


@contextmanager
def database():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS configs (id TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT,
          status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, next REAL DEFAULT 0,
          source_hash TEXT);
        CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
          time TEXT, kind TEXT, payload TEXT);
        ''')
        from mlaas.demo import initialize_demo
        initialize_demo(conn)


def event(kind, payload):
    with database() as conn:
        conn.execute('INSERT INTO events(time,kind,payload) VALUES (?,?,?)',
                     (datetime.now(timezone.utc).isoformat(), kind, json.dumps(payload)))
        conn.execute('DELETE FROM events WHERE seq < (SELECT MAX(seq)-2000 FROM events)')


def broker(method, path, body=None, allowed=(200, 201, 204)):
    headers = {'Accept': 'application/ld+json', 'Content-Type': 'application/ld+json'}
    if body is None or '@context' not in body:
        headers['Link'] = '<' + PUBLIC + '/context.jsonld>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    response = httpx.request(method, BROKER + path, json=body, headers=headers, timeout=15)
    if response.status_code not in allowed:
        raise RuntimeError(f'broker {method} {path}: {response.status_code} {response.text}')
    return response


def as_list(item):
    return item if isinstance(item, list) else [item]


def normalize(item):
    for key in ['inputAttributes', 'acceptableDataTypes', 'outputAttributes', 'outputDataTypes', 'missingInputFeatures']:
        if key in item:
            item[key]['value'] = as_list(value(item, key))
    if 'subscriptionData' in item:
        config = value(item, 'subscriptionData')
        for key in ['entities', 'watchedAttributes']:
            if key in config:
                config[key] = as_list(config[key])
    return item


def entity(entity_id):
    return normalize(broker('GET', 'entities/' + quote(entity_id, safe='')).json())


def frozen(processing, model):
    p = {k: v for k, v in processing.items() if k not in ('processingStatus', 'refSubscription', '@context')}
    m = {k: v for k, v in model.items() if k != '@context'}
    return {'processing': p, 'model': m}


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    return actual == expected


def reconcile(processing_id):
    p = entity(processing_id)
    m = entity(p['refMLModel']['object'])
    if p['type'] != 'MLProcessing' or m['type'] != 'MLModel':
        raise ValueError('expected MLProcessing and MLModel')
    for key in ['name', 'description', 'algorithm', 'mlFramework', 'version', 'inputAttributes',
                'acceptableDataTypes', 'outputAttributes', 'outputDataTypes', 'servingEndpoint', 'artifactURI']:
        value(m, key)
    config = value(p, 'subscriptionData')
    if set(config) - {'entities', 'watchedAttributes', 'q'}:
        raise ValueError('unsupported subscription configuration')
    if value(p, 'outputEntityType') != 'MLPrediction':
        raise ValueError('output type must be MLPrediction')
    selectors = config['entities']
    if not selectors or any(s['type'] in ('MLModel', 'MLProcessing', 'MLPrediction') or
                            s['type'] not in value(m, 'acceptableDataTypes') for s in selectors):
        raise ValueError('subscription must select accepted input types only')
    features = value(m, 'inputAttributes')
    mapping = value(p, 'inputMapping')
    if not features or set(features) != set(mapping) or not all(isinstance(v, str) for v in mapping.values()):
        raise ValueError('input mapping must cover model features exactly')
    if not config.get('watchedAttributes'):
        raise ValueError('watchedAttributes is required')
    status = value(p, 'processingStatus')
    if status not in ('active', 'paused', 'error'):
        raise ValueError('unknown processing status')
    payload = frozen(p, m)
    with database() as conn:
        for saved in conn.execute('SELECT payload FROM configs'):
            pinned = json.loads(saved['payload'])['model']
            if pinned['id'] == m['id'] and pinned != payload['model']:
                raise ValueError('immutable model ID was changed')
        existing = conn.execute('SELECT payload FROM configs WHERE id=?', (p['id'],)).fetchone()
        if existing and json.loads(existing['payload']) != payload:
            raise ValueError('pinned configuration changed; register a new model/processing ID')
        conn.execute('INSERT OR IGNORE INTO configs VALUES (?,?)', (p['id'], json.dumps(payload)))
    sid = 'urn:ngsi-ld:Subscription:' + digest(p['id'])
    sub = {'id': sid, 'type': 'Subscription', **config,
           'notification': {'format': 'normalized', 'endpoint': {
               'uri': PUBLIC + '/notifications/' + digest(p['id']), 'accept': 'application/ld+json'}},
           '@context': CONTEXT}
    old = broker('GET', 'subscriptions/' + sid, allowed=(200, 404))
    if status != 'active':
        broker('DELETE', 'subscriptions/' + sid, allowed=(204, 404))
    elif old.status_code == 404:
        broker('POST', 'subscriptions', sub)
    else:
        current = old.json()
        if not contains(current, {k: sub[k] for k in ['entities', 'watchedAttributes', 'q', 'notification'] if k in sub}) or ('q' not in sub and current.get('q')):
            broker('DELETE', 'subscriptions/' + sid)
            broker('POST', 'subscriptions', sub)
    broker('PATCH', 'entities/' + p['id'] + '/attrs', {'refSubscription': rel(sid), '@context': CONTEXT})
    event('subscription', {'processing': p['id'], 'status': status, 'subscription': sub})
    return {'id': sid, 'status': status}


def execute(job):
    payload = json.loads(job['payload'])
    p, m = payload['processing'], payload['model']
    # Refuse a changed pin even when registration bypassed the adapter.
    if frozen(entity(p['id']), entity(m['id'])) != {'processing': p, 'model': m}:
        raise ValueError('pinned model or processing was changed')
    source = entity(payload['source'])
    if source['type'] not in value(m, 'acceptableDataTypes'):
        raise ValueError('unexpected source type')
    fingerprint = digest(source)
    if job['source_hash'] and job['source_hash'] != fingerprint:
        raise ValueError('immutable source changed during retry')
    with database() as conn:
        conn.execute('UPDATE jobs SET source_hash=? WHERE id=?', (fingerprint, job['id']))
    previous = broker('GET', 'entities/' + job['id'], allowed=(200, 404))
    if previous.status_code == 200:
        expected = {'refInputEntity': source['id'], 'refMLModel': m['id'], 'refMLProcessing': p['id']}
        if any(previous.json().get(k, {}).get('object') != v for k, v in expected.items()):
            raise ValueError('prediction ID conflict with different lineage')
        return
    missing = m.get('missingInputFeatures', {}).get('value', [])
    features = value(m, 'inputAttributes')
    row = []
    for feature in features:
        attr = value(p, 'inputMapping')[feature]
        if attr not in source and feature in missing:
            row.append(None)
            continue
        item = source.get(attr, {})
        number = item.get('value')
        if item.get('type') != 'Property' or isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError('missing or nonnumeric feature: ' + feature)
        row.append(number)
    request = {'dataframe_split': {'columns': features, 'data': [row]}}
    event('inference_started', {'executionId': job['id'], 'endpoint': value(m, 'servingEndpoint'), 'input': request})
    result = httpx.post(value(m, 'servingEndpoint'), json=request, timeout=30)
    result.raise_for_status()
    predictions = result.json()['predictions']
    if not isinstance(predictions, list) or len(predictions) != 1 or isinstance(predictions[0], bool) or not isinstance(predictions[0], (int, float, str)):
        raise ValueError('expected one scalar prediction')
    if isinstance(predictions[0], float) and not math.isfinite(predictions[0]):
        raise ValueError('nonfinite output')
    event('inference_completed', {'executionId': job['id'], 'output': result.json()})
    if digest(entity(payload['source'])) != fingerprint:
        raise ValueError('immutable source changed during inference')
    prediction = {'id': job['id'], 'type': 'MLPrediction',
                  'refInputEntity': rel(source['id']), 'refMLModel': rel(m['id']),
                  'refMLProcessing': rel(p['id']), 'predictedValue': prop(predictions[0]),
                  'predictionType': prop('classification'),
                  'predictedAt': prop(datetime.now(timezone.utc).isoformat()),
                  'executionId': prop(job['id']), '@context': CONTEXT}
    from mlaas.demo import owns_source
    if owns_source(source['id']):
        prediction['demoOwner'] = source['demoOwner']
    response = broker('POST', 'entities', prediction, allowed=(201, 409))
    if response.status_code == 409:
        existing = entity(job['id'])
        for key in ['refInputEntity', 'refMLModel', 'refMLProcessing']:
            if existing[key] != prediction[key]:
                raise ValueError('prediction ID conflict with different lineage')
    event('prediction', prediction)


def work_once():
    from mlaas.demo import source_blocked
    with runtime_lock:
        with database() as conn:
            jobs = conn.execute("SELECT * FROM jobs WHERE status='pending' AND next<=? ORDER BY rowid", (time.time(),)).fetchall()
        job = next((j for j in jobs if not source_blocked(json.loads(j['payload'])['source'])), None)
        if not job:
            return
        try:
            execute(job)
            with database() as conn:
                conn.execute("UPDATE jobs SET status='done' WHERE id=?", (job['id'],))
        except Exception as error:
            # Invalid immutable data needs correction under a new source ID.
            permanent = isinstance(error, (ValueError, KeyError, TypeError)) or (
                isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500)
            with database() as conn:
                conn.execute('UPDATE jobs SET status=?, attempts=attempts+1, next=? WHERE id=?',
                             ('failed' if permanent else 'pending', time.time() + min(60, 2 ** min(job['attempts'], 6)), job['id']))
            event('error', {'executionId': job['id'], 'error': str(error), 'retry': not permanent})


def worker():
    while not stop.wait(.2):
        work_once()


@asynccontextmanager
async def lifespan(app):
    initialize()
    stop.clear()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=35)


from mlaas.demo import router

app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.mount('/static', StaticFiles(directory='web'), name='static')


@app.get('/')
def dashboard():
    return FileResponse('web/index.html')


@app.get('/context.jsonld')
def context():
    return {'@context': CONTEXT}


@app.get('/health')
def health():
    broker('GET', 'entities?type=MLModel')
    return {'status': 'ok'}


@app.post('/processings/reconcile')
def reconcile_api(body: dict):
    try:
        return reconcile(body['id'])
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, str(error))
    except (RuntimeError, httpx.HTTPError) as error:
        raise HTTPException(502, str(error))


@app.post('/notifications/{key}', status_code=202)
def notifications(key: str, body: dict):
    with runtime_lock:
        return accept_notification(key, body)


def accept_notification(key, body):
    from mlaas.demo import source_blocked
    with database() as conn:
        configs = conn.execute('SELECT id,payload FROM configs').fetchall()
    config = next((json.loads(c['payload']) for c in configs if digest(c['id']) == key), None)
    if config is None:
        raise HTTPException(404, 'unknown processing')
    p, m = config['processing'], config['model']
    if body.get('subscriptionId') != 'urn:ngsi-ld:Subscription:' + key:
        raise HTTPException(422, 'unexpected subscription ID')
    data = body.get('data')
    if not isinstance(data, list) or not data or any(not isinstance(x, dict) or not isinstance(x.get('id'), str) for x in data):
        raise HTTPException(422, 'notification needs source IDs')
    if value(entity(p['id']), 'processingStatus') != 'active':
        return {'queued': 0, 'status': 'paused'}
    data = [source for source in data if not source_blocked(source['id'])]
    if not data:
        return {'accepted': 0, 'status': 'demo reset'}
    event('notification', {**body, 'data': data})
    with database() as conn:
        for source in data:
            job_id = prediction_id(p, m, source['id'])
            conn.execute('INSERT OR IGNORE INTO jobs(id,payload) VALUES (?,?)',
                         (job_id, json.dumps({**config, 'source': source['id']})))
    return {'accepted': len(data)}


@app.get('/inspect/{resource}')
def inspect_resource(resource: str, id: str):
    if resource not in ('entities', 'subscriptions'):
        raise HTTPException(404, 'unknown resource')
    try:
        response = broker('GET', resource + '/' + quote(id, safe=''), allowed=(200, 404))
        if response.status_code == 404:
            raise HTTPException(404, 'broker resource not found: ' + id)
        return response.json()
    except (RuntimeError, httpx.HTTPError) as error:
        raise HTTPException(502, str(error))


@app.get('/events')
def events(after: int = 0):
    with database() as conn:
        rows = conn.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 2000', (after,)).fetchall()
    return [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]


@app.get('/state')
def state():
    result = {t: [normalize(e) for e in broker('GET', 'entities?type=' + t + '&limit=1000').json()]
              for t in ['MLModel', 'MLProcessing', 'WaterQualityObserved', 'MLPrediction']}
    with database() as conn:
        result['jobs'] = [dict(r) for r in conn.execute('SELECT id,status,attempts FROM jobs')]
    result['subscriptions'] = broker('GET', 'subscriptions?limit=1000').json()
    return result
