"""Owned demo replay and retryable reset. No type-wide deletion."""
import json
import math
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mlaas.cli import observation
from mlaas.common import prop

router = APIRouter()
replay_lock = threading.Lock()
reset_lock = threading.Lock()


def initialize_demo(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS demo_sources (id TEXT PRIMARY KEY, payload TEXT,
      active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS demo_control (id INTEGER PRIMARY KEY CHECK(id=1),
      owner TEXT, next_row INTEGER DEFAULT 0, created INTEGER DEFAULT 0,
      generation INTEGER DEFAULT 0, reset TEXT);
    ''')
    conn.execute('INSERT OR IGNORE INTO demo_control(id,owner,reset) VALUES (1,?,?)',
                 (uuid.uuid4().hex, json.dumps({'status': 'idle'})))
    row = conn.execute('SELECT reset FROM demo_control WHERE id=1').fetchone()
    if json.loads(row['reset'])['status'] == 'resetting':
        conn.execute('UPDATE demo_control SET reset=? WHERE id=1', (json.dumps({
            'status': 'failed', 'phase': 'interrupted',
            'errors': ['Adapter restarted during reset. Retry Reset Demo.']}),))


def control():
    from mlaas.adapter import database
    with database() as conn:
        row = dict(conn.execute('SELECT * FROM demo_control WHERE id=1').fetchone())
    row['reset'] = json.loads(row['reset'])
    return row


def set_reset(progress):
    from mlaas.adapter import database
    with database() as conn:
        conn.execute('UPDATE demo_control SET reset=? WHERE id=1', (json.dumps(progress),))


def owns_source(source_id):
    from mlaas.adapter import database
    with database() as conn:
        row = conn.execute('SELECT active FROM demo_sources WHERE id=?', (source_id,)).fetchone()
    return bool(row and row['active'])


def source_blocked(source_id):
    from mlaas.adapter import database
    with database() as conn:
        row = conn.execute('SELECT active FROM demo_sources WHERE id=?', (source_id,)).fetchone()
    return row is not None and (not row['active'] or control()['reset']['status'] in ('resetting', 'failed'))


@router.get('/demo')
def demo_status():
    state = control()
    return {k: state[k] for k in ('next_row', 'created', 'generation', 'reset')} | {'replaying': replay_lock.locked()}


class Replay(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    start: int | None = Field(default=None, ge=0)


def same_payload(actual, expected):
    # coraine serializes doubles to at most nine decimal places.
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k == '@context' or
            (k in actual and same_payload(actual[k], v)) for k, v in expected.items())
    if isinstance(expected, float):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=0, abs_tol=5.1e-10)
    return actual == expected


@router.post('/inject')
def replay(body: Replay):
    from mlaas.adapter import broker, database, event, runtime_lock
    if not replay_lock.acquire(blocking=False):
        raise HTTPException(409, 'replay already active')
    ids = []
    try:
        state = control()
        generation = state['generation']
        if state['reset']['status'] in ('resetting', 'failed'):
            raise HTTPException(409, 'finish or retry Reset Demo before replay')
        start = state['next_row'] if body.start is None else body.start
        artifact = Path(os.getenv('DEMO_DIR', 'artifacts/water-potability/v1'))
        metadata = json.loads((artifact / 'metadata.json').read_text())
        frame = pd.read_csv(artifact / 'demo.csv')
        if start + body.count > len(frame):
            raise HTTPException(422, 'replay exceeds available demo rows')
        for offset, (_, row) in enumerate(frame.iloc[start:start+body.count].iterrows()):
            with runtime_lock:
                current = control()
                if current['generation'] != generation or current['reset']['status'] in ('resetting', 'failed'):
                    break
                source = observation(row, metadata, 'demo-' + state['owner'])
                source['demoOwner'] = prop(state['owner'])
                path = 'entities/' + quote(source['id'], safe='')
                previous = broker('GET', path, allowed=(200, 404))
                if previous.status_code == 200:
                    raise HTTPException(409, 'sample already exists; Reset Demo to reuse its ID')
                # Reserve before POST so an ambiguous network failure remains recoverable.
                with database() as conn:
                    conn.execute('INSERT OR REPLACE INTO demo_sources VALUES (?,?,1)',
                                 (source['id'], json.dumps(source)))
                response = broker('POST', 'entities', source, allowed=(201, 409))
                if response.status_code == 409:
                    raise HTTPException(409, 'sample ID conflict; inspect before resetting')
                ids.append(source['id'])
                with database() as conn:
                    conn.execute('UPDATE demo_control SET next_row=?, created=created+1 WHERE id=1',
                                 (start + offset + 1,))
                event('injection', {'ids': [source['id']], 'demoStart': start + offset})
        return {'ids': ids, 'stopped': len(ids) < body.count, 'next_row': control()['next_row']}
    except HTTPException:
        raise
    except Exception as error:
        # The reserved ownership record is retained for Reset Demo after a lost reply.
        event('injection_error', {'ids': [source['id']] if 'source' in locals() else [], 'error': str(error)})
        raise HTTPException(502, str(error))
    finally:
        replay_lock.release()


def purge_trace(conn, source_id, job_ids):
    for row in conn.execute('SELECT seq,kind,payload FROM events').fetchall():
        payload = json.loads(row['payload'])
        execution = payload.get('executionId')
        if isinstance(execution, dict):
            execution = execution.get('value')
        remove = execution in job_ids or payload.get('id') in job_ids
        if row['kind'] == 'notification':
            payload['data'] = [x for x in payload['data'] if x['id'] != source_id]
            remove = not payload['data']
        if row['kind'] in ('injection', 'injection_error'):
            payload['ids'] = [x for x in payload.get('ids', []) if x != source_id]
            remove = not payload['ids']
        if remove:
            conn.execute('DELETE FROM events WHERE seq=?', (row['seq'],))
        else:
            conn.execute('UPDATE events SET payload=? WHERE seq=?', (json.dumps(payload), row['seq']))


def delete_owned(entity_id, expected):
    from mlaas.adapter import broker
    path = 'entities/' + quote(entity_id, safe='')
    response = broker('GET', path, allowed=(200, 404))
    if response.status_code == 200:
        if not same_payload(response.json(), expected):
            raise ValueError('ownership or payload changed; refusing deletion: ' + entity_id)
        broker('DELETE', path, allowed=(204, 404))
    if broker('GET', path, allowed=(200, 404)).status_code != 404:
        raise RuntimeError('entity still exists: ' + entity_id)


def run_reset():
    from mlaas.adapter import database, runtime_lock
    progress = {'status': 'resetting', 'phase': 'waiting for active inference/replay',
                'completed': 0, 'total': 0, 'errors': []}
    try:
        set_reset(progress)
        # Finish the current HTTP invocation before removing any of its sources.
        with runtime_lock:
            with database() as conn:
                sources = conn.execute('SELECT * FROM demo_sources WHERE active=1').fetchall()
            progress.update(phase='deleting demo entities', total=len(sources))
            set_reset(progress)
            for source in sources:
                try:
                    with database() as conn:
                        jobs = [dict(j) for j in conn.execute('SELECT * FROM jobs')
                                if json.loads(j['payload'])['source'] == source['id']]
                    for job in jobs:
                        payload = json.loads(job['payload'])
                        expected = {'id': job['id'], 'type': 'MLPrediction',
                                    'refInputEntity': {'object': source['id']},
                                    'refMLModel': {'object': payload['model']['id']},
                                    'refMLProcessing': {'object': payload['processing']['id']},
                                    'demoOwner': json.loads(source['payload'])['demoOwner']}
                        delete_owned(job['id'], expected)
                    delete_owned(source['id'], json.loads(source['payload']))
                    # Commit dedup cleanup only after all associated broker deletions.
                    with database() as conn:
                        job_ids = {j['id'] for j in jobs}
                        for job_id in job_ids:
                            conn.execute('DELETE FROM jobs WHERE id=?', (job_id,))
                        purge_trace(conn, source['id'], job_ids)
                        conn.execute('UPDATE demo_sources SET active=0 WHERE id=?', (source['id'],))
                    progress['completed'] += 1
                except Exception as error:
                    progress['errors'].append(str(error))
                set_reset(progress)
            progress['status'] = 'failed' if progress['errors'] else 'succeeded'
            progress['phase'] = 'retry Reset Demo after resolving errors' if progress['errors'] else 'complete'
            with database() as conn:
                if not progress['errors']:
                    conn.execute('UPDATE demo_control SET next_row=0,created=0 WHERE id=1')
                conn.execute('UPDATE demo_control SET reset=? WHERE id=1', (json.dumps(progress),))
    except Exception as error:
        progress.update(status='failed', phase='reset interrupted')
        progress['errors'].append(str(error))
        set_reset(progress)


class Reset(BaseModel):
    confirm: bool = False


@router.post('/demo/reset', status_code=202)
def reset_demo(body: Reset):
    from mlaas.adapter import database
    if not body.confirm:
        raise HTTPException(422, 'explicit confirmation is required')
    with reset_lock:
        if control()['reset']['status'] == 'resetting':
            return demo_status()
        with database() as conn:
            conn.execute('UPDATE demo_control SET generation=generation+1,reset=? WHERE id=1',
                         (json.dumps({'status': 'resetting', 'phase': 'stopping replay and waiting for inference',
                                      'completed': 0, 'total': 0, 'errors': []}),))
        threading.Thread(target=run_reset, daemon=True).start()
    return demo_status()
