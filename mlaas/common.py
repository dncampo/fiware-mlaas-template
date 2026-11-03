import hashlib
import json
from pathlib import Path

CONTEXT = json.loads(Path('contexts/context.jsonld').read_text())['@context']


def prop(value):
    return {'type': 'Property', 'value': value}


def rel(entity_id):
    return {'type': 'Relationship', 'object': entity_id}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def value(entity, key):
    return entity[key]['value']


def prediction_id(processing, model, source_id):
    return 'urn:ngsi-ld:MLPrediction:' + digest([processing['id'], model['id'], value(model, 'version'), source_id])
