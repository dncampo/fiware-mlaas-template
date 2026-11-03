"""Explicit registration and immutable demo injection."""
import argparse
import json
import uuid
from pathlib import Path

import httpx
import pandas as pd

from mlaas.common import CONTEXT, prop, rel


def observation(row, metadata, batch):
    sample = str(int(row['sampleId']))
    result = {'id': f'urn:ngsi-ld:WaterQualityObserved:Sample:{batch}:{sample}',
              'type': 'WaterQualityObserved', 'sampleId': prop(sample),
              'sourceDataset': prop(metadata['dataset']['origin'] + '#sha256=' + metadata['dataset']['sha256']),
              '@context': CONTEXT}
    for feature, attr in metadata['mapping'].items():
        number = row[feature]
        if pd.notna(number):
            result[attr] = prop(float(number))
    return result


def post_entity(broker_url, body):
    response = httpx.post(broker_url + '/ngsi-ld/v1/entities', json=body,
                          headers={'Content-Type': 'application/ld+json'}, timeout=20)
    response.raise_for_status()
    return body['id']


def inject(artifact, count=1, start=0, broker_url='http://localhost:1026'):
    metadata = json.loads((artifact / 'metadata.json').read_text())
    frame = pd.read_csv(artifact / 'demo.csv')
    if count < 1 or count > 100 or start < 0 or start + count > len(frame):
        raise ValueError('replay requires 1-100 available demo rows')
    batch = uuid.uuid4().hex
    return [post_entity(broker_url, observation(row, metadata, batch))
            for _, row in frame.iloc[start:start+count].iterrows()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['register-model', 'register-processing', 'inject'])
    parser.add_argument('--artifact', type=Path, default=Path('artifacts/water-potability/v1'))
    parser.add_argument('--broker', default='http://localhost:1026')
    parser.add_argument('--adapter', default='http://localhost:8000')
    parser.add_argument('--endpoint', default='http://ml-serving:5000/invocations')
    parser.add_argument('--check-endpoint', default='http://localhost:5001/invocations')
    parser.add_argument('--processing-id', default='urn:ngsi-ld:MLProcessing:water-demo-01')
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--start', type=int, default=0)
    args = parser.parse_args()
    metadata = json.loads((args.artifact / 'metadata.json').read_text())
    model_id = 'urn:ngsi-ld:MLModel:WaterPotability:' + metadata['version']
    if args.command == 'register-model':
        row = pd.read_csv(args.artifact / 'demo.csv').iloc[0]
        values = [None if pd.isna(row[c]) else float(row[c]) for c in metadata['features']]
        response = httpx.post(args.check_endpoint, json={'dataframe_split': {'columns': metadata['features'], 'data': [values]}}, timeout=30)
        response.raise_for_status()
        if len(response.json()['predictions']) != 1:
            raise ValueError('serving smoke test failed')
        model = json.loads(Path('examples/mlmodel.jsonld').read_text())
        model['id'] = model_id
        for key, val in {'version': metadata['version'], 'inputAttributes': metadata['features'],
                         'servingEndpoint': args.endpoint, 'artifactURI': '/artifacts/water-potability/' + metadata['version'] + '/model',
                         'trainingRunId': metadata['trainingRunId'], 'artifactSHA256': metadata['artifactSHA256'],
                         'missingInputFeatures': metadata['missingInputFeatures'],
                         'evaluationMetrics': json.loads((args.artifact / 'metrics.json').read_text())['test'],
                         'outputAttributes': ['predictedValue'], 'outputDataTypes': ['integer']}.items():
            model[key] = prop(val)
        print(post_entity(args.broker, model))
    elif args.command == 'register-processing':
        processing = json.loads(Path('examples/mlprocessing.jsonld').read_text())
        processing['id'] = args.processing_id
        processing['refMLModel'] = rel(model_id)
        processing['inputMapping'] = prop(metadata['mapping'])
        print(post_entity(args.broker, processing))
        response = httpx.post(args.adapter + '/processings/reconcile', json={'id': processing['id']}, timeout=30)
        response.raise_for_status()
        print(response.text)
    else:
        print(json.dumps(inject(args.artifact, args.count, args.start, args.broker), indent=2))


if __name__ == '__main__':
    main()
