"""Real subscription-driven inference. Requires explicit model registration."""
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
from mlaas.common import CONTEXT, digest, prediction_id, prop

pytestmark = pytest.mark.skipif(os.getenv('RUN_E2E_TESTS') != '1', reason='set RUN_E2E_TESTS=1 with running stack')
BROKER = 'http://localhost:1026/ngsi-ld/v1/'
ADAPTER = 'http://localhost:8000'
HEADERS = {'Content-Type': 'application/ld+json', 'Accept': 'application/ld+json',
           'Link': '<http://ml-adapter:8000/context.jsonld>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'}


def api(method, path, body=None):
    headers = dict(HEADERS)
    if body is not None:
        headers.pop('Link')
    response = httpx.request(method, BROKER + path, json=body, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json() if response.content else None


def wait_for(check, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(.3)
    pytest.fail('timed out waiting for real inference')


def test_full_flow():
    artifact = Path('artifacts/water-potability/v1')
    metadata = json.loads((artifact / 'metadata.json').read_text())
    frame = pd.read_csv(artifact / 'demo.csv')
    token = uuid.uuid4().hex
    processing = json.loads(Path('examples/mlprocessing.jsonld').read_text())
    processing['id'] += ':' + token
    processing['subscriptionData']['value']['entities'][0]['idPattern'] = 'urn:ngsi-ld:WaterQualityObserved:Sample:' + token + ':.*'
    sources = [observation(row, metadata, token) for _, row in frame[frame.Turbidity > 4].head(10).iterrows()]
    nonmatch = observation(frame[frame.Turbidity < 4].iloc[0], metadata, token)
    model = api('GET', 'entities/' + processing['refMLModel']['object'])
    sid = None
    created = []
    try:
        api('POST', 'entities', processing)
        response = httpx.post(ADAPTER + '/processings/reconcile', json={'id': processing['id']}, timeout=30)
        assert response.status_code == 200, response.text
        sid = response.json()['id']
        assert api('GET', 'entities/' + processing['id'])['refSubscription']['object'] == sid
        again = httpx.post(ADAPTER + '/processings/reconcile', json={'id': processing['id']}, timeout=30)
        assert again.status_code == 200, again.text
        assert again.json()['id'] == sid
        for source in sources + [nonmatch]:
            api('POST', 'entities', source)
            created.append(source['id'])
        before = {s['id']: api('GET', 'entities/' + s['id']) for s in sources}
        # coraine keeps doubles internally and serializes up to nine decimal places.
        for source in sources:
            for attr in metadata['mapping'].values():
                if attr in source:
                    assert abs(before[source['id']][attr]['value'] - source[attr]['value']) <= 5.1e-10
        ids = {prediction_id(processing, model, s['id']) for s in sources}
        def results():
            state = httpx.get(ADAPTER + '/state', timeout=20).json()
            return [p for p in state['MLPrediction'] if p['id'] in ids]
        predictions = wait_for(lambda: results() if len(results()) == 10 else None)
        for prediction in predictions:
            assert prediction['refMLModel']['object'] == model['id']
            assert prediction['refMLProcessing']['object'] == processing['id']
            source_id = prediction['refInputEntity']['object']
            assert api('GET', 'entities/' + source_id) == before[source_id]
            assert not set(metadata['mapping'].values()) & set(prediction)
        events = httpx.get(ADAPTER + '/events').json()
        notification = next(e['payload'] for e in events if e['kind'] == 'notification' and e['payload']['subscriptionId'] == sid)
        endpoint = ADAPTER + '/notifications/' + digest(processing['id'])
        for _ in range(3):
            assert httpx.post(endpoint, json=notification).status_code == 202
        time.sleep(2)
        assert len(results()) == 10
        events = httpx.get(ADAPTER + '/events').json()
        assert len([e for e in events if e['kind'] == 'inference_started' and e['payload']['executionId'] in ids]) == 10
        all_predictions = httpx.get(ADAPTER + '/state').json()['MLPrediction']
        assert not any(p['refInputEntity']['object'] == nonmatch['id'] for p in all_predictions)
        # Output writes must not appear as inputs to the broker's notification.
        for event in events:
            if event['kind'] == 'notification' and event['payload']['subscriptionId'] == sid:
                assert all(x['type'] == 'WaterQualityObserved' for x in event['payload']['data'])
        api('PATCH', 'entities/' + processing['id'] + '/attrs', {'processingStatus': prop('paused'), '@context': CONTEXT})
        response = httpx.post(ADAPTER + '/processings/reconcile', json={'id': processing['id']})
        assert response.status_code == 200, response.text
        assert httpx.get(BROKER + 'subscriptions/' + sid, headers=HEADERS).status_code == 404
        assert httpx.get(ADAPTER + '/').status_code == 200
    finally:
        if sid:
            httpx.delete(BROKER + 'subscriptions/' + sid, headers=HEADERS)
        time.sleep(1)
        for prediction in api('GET', 'entities?type=MLPrediction&limit=1000'):
            if prediction['refInputEntity']['object'] in created:
                httpx.delete(BROKER + 'entities/' + prediction['id'], headers=HEADERS)
        for entity_id in created:
            httpx.delete(BROKER + 'entities/' + entity_id, headers=HEADERS)
        for source in sources:
            httpx.delete(BROKER + 'entities/' + prediction_id(processing, model, source['id']), headers=HEADERS)
        httpx.delete(BROKER + 'entities/' + processing['id'], headers=HEADERS)
