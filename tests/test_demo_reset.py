"""Reset safety under controlled failures; broker integration is in test_demo_live."""
import copy
import csv
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mlaas import adapter as a, demo as d
from mlaas.cli import observation
from mlaas.common import prediction_id, prop


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(a, 'DB', str(tmp_path / 'journal.db'))
    a.initialize()
    store = {}
    requests = []
    failures = set()

    def broker(method, path, body=None, allowed=(200, 201, 204)):
        from urllib.parse import unquote
        path = unquote(path)
        requests.append((method, path))
        if (method, path) in failures:
            raise RuntimeError('service unavailable: ' + path)
        if path == 'entities' and method == 'POST':
            key = 'entities/' + body['id']
            code = 409 if key in store else 201
            if code == 201:
                store[key] = copy.deepcopy(body)
            result = None
        elif method == 'GET':
            code = 200 if path in store else 404
            result = store.get(path)
        elif method == 'DELETE':
            code = 204 if store.pop(path, None) is not None else 404
            result = None
        else:
            raise AssertionError((method, path))
        if code not in allowed:
            raise RuntimeError(f'broker {method} {path}: {code}')
        return httpx.Response(code, json=result)

    monkeypatch.setattr(a, 'broker', broker)
    model = json.loads(Path('examples/mlmodel.jsonld').read_text())
    processing = json.loads(Path('examples/mlprocessing.jsonld').read_text())
    model = a.normalize(model)
    processing = a.normalize(processing)
    metadata = {'mapping': processing['inputMapping']['value'],
                'dataset': {'origin': 'unit-test', 'sha256': 'test'}}
    (tmp_path / 'metadata.json').write_text(json.dumps(metadata))
    with (tmp_path / 'demo.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['sampleId', *metadata['mapping']])
        writer.writeheader()
        writer.writerows({'sampleId': i, **{f: 5.0 for f in metadata['mapping']}} for i in range(120))
    monkeypatch.setenv('DEMO_DIR', str(tmp_path))
    store['entities/' + model['id']] = copy.deepcopy(model)
    store['entities/' + processing['id']] = copy.deepcopy(processing)
    pinned = a.frozen(processing, model)
    with a.database() as conn:
        conn.execute('INSERT INTO configs VALUES (?,?)', (processing['id'], json.dumps(pinned)))
    owner = d.control()['owner']
    row = {'sampleId': 42, **{f: 5.0 for f in metadata['mapping']}}
    source = observation(row, metadata, 'demo-' + owner)
    source['demoOwner'] = prop(owner)
    store['entities/' + source['id']] = source
    jid = prediction_id(processing, model, source['id'])
    payload = {**pinned, 'source': source['id']}
    with a.database() as conn:
        conn.execute('INSERT INTO demo_sources VALUES (?,?,1)', (source['id'], json.dumps(source)))
        conn.execute('INSERT INTO jobs(id,payload) VALUES (?,?)', (jid, json.dumps(payload)))
    a.event('notification', {'data': [{'id': source['id']}, {'id': 'unrelated'}]})
    a.event('error', {'executionId': jid, 'error': 'old error'})
    a.event('injection', {'ids': [source['id']]})
    return TestClient(a.app), store, requests, failures, source, jid, payload


def finish(client):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        result = client.get('/demo').json()
        if result['reset']['status'] != 'resetting':
            return result
        time.sleep(.01)
    raise AssertionError('reset did not finish')


def test_confirmation_and_scoped_reset(setup):
    client, store, requests, failures, source, jid, payload = setup
    foreign = {'id': 'foreign', 'type': 'MLPrediction', 'refInputEntity': {'object': source['id']}}
    store['entities/foreign'] = foreign
    before = copy.deepcopy(store)
    assert client.post('/demo/reset', json={}).status_code == 422
    assert store == before
    client.post('/demo/reset', json={'confirm': True})
    result = finish(client)
    assert result['reset']['status'] == 'succeeded', json.dumps(result)
    assert 'entities/' + source['id'] not in store
    assert store['entities/foreign'] == foreign
    assert all(store['entities/' + payload[k]['id']] == before['entities/' + payload[k]['id']] for k in ('model', 'processing'))
    with a.database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM configs').fetchone()[0] == 1
    assert a.events()[0]['payload']['data'] == [{'id': 'unrelated'}]
    assert len(a.events()) == 1
    assert d.source_blocked(source['id'])  # Late delivery after reset is ignored.


@pytest.mark.parametrize('failing', ['source', 'prediction', 'verify'])
def test_partial_failure_retains_dedup_until_retry(setup, monkeypatch, failing):
    client, store, requests, failures, source, jid, payload = setup
    prediction = {'id': jid, 'type': 'MLPrediction', 'demoOwner': source['demoOwner'],
                  'refInputEntity': {'object': source['id']},
                  'refMLModel': {'object': payload['model']['id']},
                  'refMLProcessing': {'object': payload['processing']['id']}}
    store['entities/' + jid] = prediction
    target = source['id'] if failing != 'prediction' else jid
    if failing == 'verify':
        original = a.broker
        def broker(method, path, *args, **kwargs):
            from urllib.parse import unquote
            if method == 'GET' and unquote(path) == 'entities/' + source['id'] and 'entities/' + source['id'] not in store:
                raise RuntimeError('verification failed after deletion')
            return original(method, path, *args, **kwargs)
        monkeypatch.setattr(a, 'broker', broker)
    else:
        failures.add(('DELETE', 'entities/' + target))
    client.post('/demo/reset', json={'confirm': True})
    assert finish(client)['reset']['status'] == 'failed'
    with a.database() as conn:
        assert conn.execute('SELECT id FROM jobs WHERE id=?', (jid,)).fetchone()
    assert any(e['payload'].get('executionId') == jid for e in a.events())
    assert client.post('/inject', json={'count': 1}).status_code == 409
    failures.clear()
    if failing == 'verify':
        monkeypatch.setattr(a, 'broker', original)
    client.post('/demo/reset', json={'confirm': True})
    result = finish(client)
    assert result['reset']['status'] == 'succeeded', json.dumps(result)
    with a.database() as conn:
        assert not conn.execute('SELECT id FROM jobs WHERE id=?', (jid,)).fetchone()


def test_changed_ownership_is_not_deleted(setup):
    client, store, requests, failures, source, jid, payload = setup
    store['entities/' + source['id']] = {**source, 'demoOwner': prop('someone else')}
    client.post('/demo/reset', json={'confirm': True})
    assert finish(client)['reset']['status'] == 'failed'
    assert ('DELETE', 'entities/' + source['id']) not in requests
    with a.database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


def test_changed_prediction_lineage_is_not_deleted(setup):
    client, store, requests, failures, source, jid, payload = setup
    store['entities/' + jid] = {'id': jid, 'type': 'MLPrediction', 'demoOwner': prop('someone else')}
    client.post('/demo/reset', json={'confirm': True})
    assert finish(client)['reset']['status'] == 'failed'
    assert ('DELETE', 'entities/' + jid) not in requests
    assert 'entities/' + source['id'] in store


def test_reset_waits_for_active_inference(setup, monkeypatch):
    client, store, requests, failures, source, jid, payload = setup
    started, release = threading.Event(), threading.Event()
    def invoke(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return httpx.Response(200, json={'predictions': [1]}, request=httpx.Request('POST', 'http://model'))
    monkeypatch.setattr(a.httpx, 'post', invoke)
    worker = threading.Thread(target=a.work_once)
    worker.start()
    assert started.wait(2)
    d.reset_demo(d.Reset(confirm=True))
    assert d.control()['reset']['status'] == 'resetting'
    assert 'entities/' + source['id'] in store
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    result = finish(client)
    assert result['reset']['status'] == 'succeeded', json.dumps(result)
    assert 'entities/' + jid not in store
    assert 'entities/' + source['id'] not in store
    assert not any(e['payload'].get('executionId') == jid for e in a.events())


def test_interrupted_reset_blocks_demo_after_restart(setup):
    client, store, requests, failures, source, jid, payload = setup
    d.set_reset({'status': 'resetting'})
    a.initialize()
    assert d.control()['reset']['status'] == 'failed'
    a.work_once()
    assert 'entities/' + source['id'] in store
    assert client.post('/inject', json={'count': 1}).status_code == 409
    client.post('/demo/reset', json={'confirm': True})
    result = finish(client)
    assert result['reset']['status'] == 'succeeded', json.dumps(result)


def test_notifications_during_and_after_reset_are_ignored(setup):
    from mlaas.common import digest
    client, store, requests, failures, source, jid, payload = setup
    key = digest(payload['processing']['id'])
    body = {'subscriptionId': 'urn:ngsi-ld:Subscription:' + key, 'data': [{'id': source['id']}]}
    d.set_reset({'status': 'failed'})
    assert client.post('/notifications/' + key, json=body).json()['accepted'] == 0
    client.post('/demo/reset', json={'confirm': True})
    result = finish(client)
    assert result['reset']['status'] == 'succeeded', json.dumps(result)
    assert client.post('/notifications/' + key, json=body).json()['accepted'] == 0
    with a.database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_reset_stops_an_active_batch(setup, monkeypatch):
    client, store, requests, failures, source, jid, payload = setup
    started, release = threading.Event(), threading.Event()
    original = a.broker
    def broker(method, path, *args, **kwargs):
        if method == 'POST' and path == 'entities' and not started.is_set():
            started.set()
            assert release.wait(5)
        return original(method, path, *args, **kwargs)
    monkeypatch.setattr(a, 'broker', broker)
    result = []
    thread = threading.Thread(target=lambda: result.append(d.replay(d.Replay(start=0, count=100))))
    thread.start()
    assert started.wait(2)
    d.reset_demo(d.Reset(confirm=True))
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert result[0]['stopped']
    assert len(result[0]['ids']) < 100
    assert finish(client)['reset']['status'] == 'succeeded'
    assert not d.replay_lock.locked()
    assert d.control()['created'] == d.control()['next_row'] == 0
    assert not any('WaterQualityObserved' in key for key in store)


def test_unrelated_pending_job_can_run_after_reset_failure(setup, monkeypatch):
    client, store, requests, failures, source, jid, payload = setup
    d.set_reset({'status': 'failed'})
    with a.database() as conn:
        conn.execute('INSERT INTO jobs(id,payload) VALUES (?,?)',
                     ('foreign-job', json.dumps({**payload, 'source': 'unrelated'})))
    executed = []
    monkeypatch.setattr(a, 'execute', lambda job: executed.append(job['id']))
    a.work_once()
    assert executed == ['foreign-job']


def test_partial_reset_commits_only_successful_source_groups(setup):
    client, store, requests, failures, source, jid, payload = setup
    second = {**source, 'id': source['id'] + '-second'}
    second_job = jid + '-second'
    store['entities/' + second['id']] = second
    with a.database() as conn:
        conn.execute('INSERT INTO demo_sources VALUES (?,?,1)', (second['id'], json.dumps(second)))
        conn.execute('INSERT INTO jobs(id,payload) VALUES (?,?)',
                     (second_job, json.dumps({**payload, 'source': second['id']})))
        conn.execute('UPDATE demo_control SET next_row=2,created=2 WHERE id=1')
    failures.add(('DELETE', 'entities/' + source['id']))
    client.post('/demo/reset', json={'confirm': True})
    result = finish(client)
    assert result['reset']['status'] == 'failed'
    assert result['reset']['completed'] == 1
    assert result['created'] == result['next_row'] == 2
    assert 'entities/' + source['id'] in store
    assert 'entities/' + second['id'] not in store
    with a.database() as conn:
        assert {r['id'] for r in conn.execute('SELECT id FROM jobs')} == {jid}
    failures.clear()
    client.post('/demo/reset', json={'confirm': True})
    result = finish(client)
    assert result['reset']['status'] == 'succeeded'
    assert result['created'] == result['next_row'] == 0


def test_reset_recovers_a_lost_creation_reply(setup, monkeypatch):
    client, store, requests, failures, source, jid, payload = setup
    original = a.broker
    def broker(method, path, *args, **kwargs):
        response = original(method, path, *args, **kwargs)
        if method == 'POST' and path == 'entities':
            raise RuntimeError('creation succeeded but reply was lost')
        return response
    monkeypatch.setattr(a, 'broker', broker)
    assert client.post('/inject', json={'start': 0, 'count': 1}).status_code == 502
    with a.database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM demo_sources WHERE active=1').fetchone()[0] == 2
    assert len([e for e in store.values() if e['type'] == 'WaterQualityObserved']) == 2
    client.post('/demo/reset', json={'confirm': True})
    assert finish(client)['reset']['status'] == 'succeeded'
    assert not any(e['type'] == 'WaterQualityObserved' for e in store.values())
