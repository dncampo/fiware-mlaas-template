"""Direct API tests against a running broker. No application triggers."""
import copy
import json
import os
import queue
import subprocess
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = json.loads((ROOT / 'contexts/context.jsonld').read_text())['@context']
BROKER = os.getenv('BROKER_URL', 'http://localhost:1026')
LINK = '<' + os.getenv('TEST_CONTEXT_URL', 'http://host.docker.internal:18080/context.jsonld') + '>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/ld+json', 'Accept': 'application/ld+json'}
    if body is None or '@context' not in body:
        headers['Link'] = LINK
    req = Request(BROKER + '/ngsi-ld/v1/' + path, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, response.headers
    except HTTPError as error:
        return error.code, error.read().decode(), error.headers


class Receiver(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    received = queue.Queue()

    def do_GET(self):
        data = json.dumps({'@context': CONTEXT}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Type', 'application/ld+json')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.received.put(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(os.getenv('RUN_BROKER_TESTS') == '1', 'set RUN_BROKER_TESTS=1 for real broker tests')
class BrokerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('0.0.0.0', 18080), Receiver)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_crud_subscription(self):
        token = uuid.uuid4().hex
        entities = []
        subscription = None
        def create(entity):
            status, body, _ = api('POST', 'entities', entity)
            self.assertEqual(status, 201, body)
            entities.append(entity['id'])
            status, result, _ = api('GET', 'entities/' + entity['id'])
            self.assertEqual(status, 200, result)
            return result
        try:
            for filename in ['mlmodel', 'mlprocessing', 'water-quality-observed', 'mlprediction']:
                entity = json.loads((ROOT / ('examples/' + filename + '.jsonld')).read_text())
                entity['id'] += ':' + token
                result = create(entity)
                self.assertEqual(result['type'], entity['type'])
                if 'refMLModel' in entity:
                    self.assertEqual(result['refMLModel']['object'], entity['refMLModel']['object'])
            sub = json.loads((ROOT / 'examples/subscription.jsonld').read_text())
            sub['entities'][0]['idPattern'] = 'urn:ngsi-ld:WaterQualityObserved:Smoke:' + token + ':.*'
            sub['notification']['endpoint']['uri'] = os.getenv('TEST_CALLBACK_URL', 'http://host.docker.internal:18080/notifications')
            status, body, headers = api('POST', 'subscriptions', sub)
            self.assertEqual(status, 201, body)
            subscription = headers['Location'].split('/')[-1]
            self.assertEqual(api('GET', 'subscriptions/' + subscription)[0], 200)
            if os.getenv('RUN_RESTART_TESTS') == '1':
                subprocess.run(['docker', 'compose', 'restart', 'broker'], check=True, capture_output=True)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        if api('GET', 'subscriptions/' + subscription)[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(.2)
                self.assertEqual(api('GET', 'entities/' + entities[0])[0], 200)
                self.assertEqual(api('GET', 'subscriptions/' + subscription)[0], 200)
            template = json.loads((ROOT / 'examples/water-quality-observed.jsonld').read_text())
            while not Receiver.received.empty():
                Receiver.received.get_nowait()
            for label, value in [('match', 5), ('no-match', 2)]:
                entity = copy.deepcopy(template)
                entity['id'] = 'urn:ngsi-ld:WaterQualityObserved:Smoke:' + token + ':' + label
                entity['turbidity']['value'] = value
                create(entity)
            notification = Receiver.received.get(timeout=15)
            self.assertEqual(notification['data'][0]['id'], entities[-2])
            time.sleep(2)
            self.assertTrue(Receiver.received.empty(), 'nonmatching creation notified')
            status, body, _ = api('PATCH', 'entities/' + entities[-2] + '/attrs',
                                  {'hardness': {'type': 'Property', 'value': 180}, '@context': CONTEXT})
            self.assertIn(status, (204, 207), body)
            time.sleep(2)
            self.assertTrue(Receiver.received.empty(), 'unwatched update notified')
            status, body, _ = api('PATCH', 'entities/' + entities[-1] + '/attrs',
                                  {'turbidity': {'type': 'Property', 'value': 6}, '@context': CONTEXT})
            self.assertIn(status, (204, 207), body)
            self.assertEqual(Receiver.received.get(timeout=15)['data'][0]['id'], entities[-1])
        finally:
            if subscription:
                self.assertEqual(api('DELETE', 'subscriptions/' + subscription)[0], 204)
            for entity_id in reversed(entities):
                self.assertEqual(api('DELETE', 'entities/' + entity_id)[0], 204)


if __name__ == '__main__':
    unittest.main()
