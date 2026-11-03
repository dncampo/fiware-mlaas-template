"""Failure and restart checks against the actual local services."""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pandas as pd
import pytest

from mlaas.cli import observation
from mlaas.common import CONTEXT, digest, prediction_id, prop
from tests.test_e2e import ADAPTER, BROKER, HEADERS, api, wait_for

pytestmark = pytest.mark.skipif(os.getenv('RUN_RECOVERY_TESTS') != '1', reason='set RUN_RECOVERY_TESTS=1; restarts local Compose services')


def compose(*args):
    subprocess.run(['docker', 'compose', *args], check=True, capture_output=True)


def ready():
    try:
        return httpx.get(ADAPTER + '/health', timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def test_failure_and_restart():
    artifact = Path('artifacts/water-potability/v1')
    metadata = json.loads((artifact / 'metadata.json').read_text())
    frame = pd.read_csv(artifact / 'demo.csv')
    row = frame[frame.Turbidity > 4].iloc[0]
    token = uuid.uuid4().hex
    processing = json.loads(Path('examples/mlprocessing.jsonld').read_text())
    processing['id'] += ':recovery:' + token
    processing['subscriptionData']['value']['entities'][0]['idPattern'] = 'urn:ngsi-ld:WaterQualityObserved:Sample:' + token + ':.*'
    model = api('GET', 'entities/' + processing['refMLModel']['object'])
    source = observation(row, metadata, token)
    invalid = observation(row, metadata, token + '-bad')
    invalid['id'] = source['id'] + '-bad'
    invalid.pop('hardness')
    prediction = prediction_id(processing, model, source['id'])
    invalid_prediction = prediction_id(processing, model, invalid['id'])
    sid = None
    try:
        api('POST', 'entities', processing)
        response = httpx.post(ADAPTER + '/processings/reconcile', json={'id': processing['id']}, timeout=30)
        assert response.status_code == 200, response.text
        sid = response.json()['id']
        endpoint = ADAPTER + '/notifications/' + digest(processing['id'])
        assert httpx.post(endpoint, json={'subscriptionId': sid, 'data': [{}]}).status_code == 422
        api('POST', 'entities', invalid)
        def failed():
            jobs = httpx.get(ADAPTER + '/state').json()['jobs']
            return any(j['id'] == invalid_prediction and j['status'] == 'failed' for j in jobs)
        wait_for(failed)
        assert httpx.get(BROKER + 'entities/' + invalid_prediction, headers=HEADERS).status_code == 404
        compose('stop', 'ml-serving')
        api('POST', 'entities', source)
        def attempted():
            jobs = httpx.get(ADAPTER + '/state').json()['jobs']
            return any(j['id'] == prediction and j['status'] == 'pending' and j['attempts'] > 0 for j in jobs)
        wait_for(attempted)
        # Accepted work survives a process restart while serving is unavailable.
        compose('restart', 'ml-adapter')
        wait_for(ready)
        compose('up', '-d', '--wait', 'ml-serving')
        def completed():
            result = httpx.get(BROKER + 'entities/' + prediction, headers=HEADERS)
            return result.json() if result.status_code == 200 else None
        result = wait_for(completed)
        assert result['refInputEntity']['object'] == source['id']
        compose('restart', 'broker')
        wait_for(ready)
        assert api('GET', 'subscriptions/' + sid)['id'] == sid
        assert api('GET', 'entities/' + prediction)['id'] == prediction
        notification = {'subscriptionId': sid, 'data': [{'id': source['id'], 'type': 'WaterQualityObserved'}]}
        assert httpx.post(endpoint, json=notification).status_code == 202
        time.sleep(1)
        state = httpx.get(ADAPTER + '/state').json()
        assert len([p for p in state['MLPrediction'] if p['id'] == prediction]) == 1
        # An out-of-band change to an immutable model is rejected on reconciliation.
        original = model['version']
        api('PATCH', 'entities/' + model['id'] + '/attrs', {'version': prop('changed'), '@context': CONTEXT})
        response = httpx.post(ADAPTER + '/processings/reconcile', json={'id': processing['id']})
        assert response.status_code == 422, response.text
        api('PATCH', 'entities/' + model['id'] + '/attrs', {'version': original, '@context': CONTEXT})
    finally:
        compose('up', '-d', '--wait', 'ml-serving')
        api('PATCH', 'entities/' + model['id'] + '/attrs', {'version': model['version'], '@context': CONTEXT})
        if sid:
            httpx.delete(BROKER + 'subscriptions/' + sid, headers=HEADERS)
        time.sleep(1)
        for prediction_entity in api('GET', 'entities?type=MLPrediction&limit=1000'):
            if prediction_entity['refInputEntity']['object'] in (source['id'], invalid['id']):
                httpx.delete(BROKER + 'entities/' + prediction_entity['id'], headers=HEADERS)
        for entity_id in [source['id'], invalid['id'], processing['id']]:
            httpx.delete(BROKER + 'entities/' + entity_id, headers=HEADERS)
