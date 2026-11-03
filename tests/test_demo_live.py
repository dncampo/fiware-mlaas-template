"""Owned replay/reset and inspector against coraine and MLflow."""
import copy
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pandas as pd
import pytest

from mlaas.cli import observation
from mlaas.common import prediction_id, prop
from tests.test_e2e import ADAPTER, BROKER, HEADERS, api, wait_for
from tests.test_recovery import compose, ready

pytestmark = pytest.mark.skipif(os.getenv('RUN_DEMO_TESTS') != '1', reason='set RUN_DEMO_TESTS=1 with running stack')


def demo():
    response = httpx.get(ADAPTER + '/demo', timeout=10)
    response.raise_for_status()
    return response.json()


def reset():
    response = httpx.post(ADAPTER + '/demo/reset', json={'confirm': True}, timeout=10)
    assert response.status_code == 202, response.text
    return wait_for(lambda: demo() if demo()['reset']['status'] != 'resetting' else None, timeout=90)


def matching_row():
    frame = pd.read_csv('artifacts/water-potability/v1/demo.csv')
    return int(frame.index[frame.Turbidity > 4][0])


def inject():
    response = httpx.post(ADAPTER + '/inject', json={'count': 1, 'start': matching_row()}, timeout=30)
    assert response.status_code == 200, response.text
    return response.json()['ids'][0]


def outputs(source):
    return [p for p in api('GET', 'entities?type=MLPrediction&limit=1000') if p['refInputEntity']['object'] == source]


def test_inspection_reset_and_id_reuse():
    assert reset()['reset']['status'] == 'succeeded'
    models = api('GET', 'entities?type=MLModel&limit=1000')
    processings = api('GET', 'entities?type=MLProcessing&limit=1000')
    subs = api('GET', 'subscriptions?limit=1000')
    serving = httpx.get('http://localhost:5001/ping').status_code
    artifacts = {str(p): p.stat().st_mtime_ns for p in Path('artifacts').rglob('*') if p.is_file()}
    metadata = json.loads(Path('artifacts/water-potability/v1/metadata.json').read_text())
    frame = pd.read_csv('artifacts/water-potability/v1/demo.csv')
    foreign = observation(frame.iloc[matching_row()], metadata, 'unrelated-' + uuid.uuid4().hex)
    # Even an ID which looks like a demo ID grants no ownership to Reset Demo.
    foreign['id'] = foreign['id'].replace('Sample:unrelated-', 'Sample:demo-unrelated-')
    foreign_prediction = None
    try:
        api('POST', 'entities', foreign)
        foreign_outputs = wait_for(lambda: outputs(foreign['id']))
        foreign_snapshot = api('GET', 'entities/' + foreign['id'])
        foreign_jobs = {p['id'] for p in foreign_outputs}
        source = inject()
        prediction = wait_for(lambda: outputs(source))[0]
        foreign_prediction = copy.deepcopy(prediction)
        foreign_prediction['id'] += ':external'
        foreign_prediction['demoOwner'] = prop('unrelated writer')
        api('POST', 'entities', foreign_prediction)
        for resource, ids in [('entities', [source, prediction['id'], prediction['refMLModel']['object'], prediction['refMLProcessing']['object']]),
                              ('subscriptions', [subs[0]['id']])]:
            for entity_id in ids:
                response = httpx.get(ADAPTER + '/inspect/' + resource, params={'id': entity_id})
                assert response.status_code == 200, response.text
                assert response.json() == api('GET', resource + '/' + entity_id)
        assert httpx.get(ADAPTER + '/inspect/entities', params={'id': 'urn:ngsi-ld:missing'}).status_code == 404
        assert httpx.get(ADAPTER + '/inspect/invalid', params={'id': source}).status_code == 404
        assert httpx.post(ADAPTER + '/demo/reset', json={'confirm': False}).status_code == 422
        assert api('GET', 'entities/' + source)['id'] == source
        result = reset()
        assert result['reset']['status'] == 'succeeded', result
        assert result['next_row'] == result['created'] == 0
        for entity_id in (source, prediction['id']):
            assert httpx.get(BROKER + 'entities/' + entity_id, headers=HEADERS).status_code == 404
        assert api('GET', 'entities?type=MLModel&limit=1000') == models
        assert api('GET', 'entities?type=MLProcessing&limit=1000') == processings
        assert {s['id'] for s in api('GET', 'subscriptions?limit=1000')} == {s['id'] for s in subs}
        for sub in subs:
            actual = api('GET', 'subscriptions/' + sub['id'])
            for key in ('entities', 'q', 'watchedAttributes'):
                assert actual.get(key) == sub.get(key)
            for key in ('endpoint', 'format', 'attributes'):
                assert actual['notification'].get(key) == sub['notification'].get(key)
        assert httpx.get('http://localhost:5001/ping').status_code == serving == 200
        assert {str(p): p.stat().st_mtime_ns for p in Path('artifacts').rglob('*') if p.is_file()} == artifacts
        assert api('GET', 'entities/' + foreign['id']) == foreign_snapshot
        assert api('GET', 'entities/' + foreign_prediction['id'])['demoOwner']['value'] == 'unrelated writer'
        state = httpx.get(ADAPTER + '/state').json()
        assert not any(j['id'] == prediction['id'] for j in state['jobs'])
        assert foreign_jobs <= {j['id'] for j in state['jobs']}
        events = httpx.get(ADAPTER + '/events').json()
        assert any(e['payload'].get('executionId') in foreign_jobs for e in events if isinstance(e['payload'].get('executionId'), str))
        assert not any(e['payload'].get('id') == prediction['id'] or e['payload'].get('executionId') == prediction['id'] for e in events)
        assert inject() == source
        repeated = wait_for(lambda: [p for p in outputs(source) if p['id'] == prediction['id']])[0]
        assert repeated['id'] == prediction['id']
        assert repeated['predictedAt'] != prediction['predictedAt']
    finally:
        reset()
        for p in outputs(foreign['id']):
            httpx.delete(BROKER + 'entities/' + p['id'], headers=HEADERS)
        if foreign_prediction:
            httpx.delete(BROKER + 'entities/' + foreign_prediction['id'], headers=HEADERS)
        httpx.delete(BROKER + 'entities/' + foreign['id'], headers=HEADERS)


@pytest.mark.skipif(os.getenv('RUN_RECOVERY_TESTS') != '1', reason='restarts Compose services')
def test_reset_pending_inference_and_broker_failure():
    assert reset()['reset']['status'] == 'succeeded'
    source = None
    try:
        compose('stop', 'ml-serving')
        source = inject()
        processing = api('GET', 'entities/urn:ngsi-ld:MLProcessing:water-demo-01')
        model = api('GET', 'entities/' + processing['refMLModel']['object'])
        jid = prediction_id(processing, model, source)
        def pending():
            state = httpx.get(ADAPTER + '/state').json()
            return next((j for j in state['jobs'] if j['id'] == jid and j['status'] == 'pending' and j['attempts'] > 0), None)
        wait_for(pending)
        job = next(j for j in httpx.get(ADAPTER + '/state').json()['jobs'] if j['id'] == jid)
        compose('stop', 'broker')
        result = reset()
        assert result['reset']['status'] == 'failed', result
        assert result['reset']['errors']
        assert httpx.post(ADAPTER + '/inject', json={'count': 1}).status_code == 409
        # A failed reset and its dedup records survive an adapter restart.
        compose('restart', 'ml-adapter')
        def status_ready():
            try:
                return httpx.get(ADAPTER + '/demo').status_code == 200
            except httpx.HTTPError:
                return False
        wait_for(status_ready)
        assert demo()['reset']['status'] == 'failed'
        compose('up', '-d', '--wait', 'broker')
        wait_for(ready)
        assert next(j for j in httpx.get(ADAPTER + '/state').json()['jobs'] if j['id'] == jid) == job
        assert api('GET', 'entities/' + source)['id'] == source
        assert reset()['reset']['status'] == 'succeeded'
        assert httpx.get(BROKER + 'entities/' + source, headers=HEADERS).status_code == 404
        compose('up', '-d', '--wait', 'ml-serving')
        assert inject() == source
        wait_for(lambda: outputs(source))
    finally:
        compose('up', '-d', '--wait', 'broker', 'ml-serving')
        wait_for(ready)
        reset()
