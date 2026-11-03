"""Explicit offline preparation, training and evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, f1_score, precision_score, recall_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline

URL = 'https://raw.githubusercontent.com/MainakRepositor/Datasets/master/water_potability.csv'
CHECKSUM = '904004bde729bfe3d2e195f46343bceead09e32a0eb95bb8184e7e20e029b2bf'
MAPPING = dict(zip(['ph', 'Hardness', 'Solids', 'Chloramines', 'Sulfate', 'Conductivity',
                    'Organic_carbon', 'Trihalomethanes', 'Turbidity'],
                   ['ph', 'hardness', 'solids', 'chloramines', 'sulfate', 'conductivity',
                    'organicCarbon', 'trihalomethanes', 'turbidity']))


def split(frame):
    remaining, demo = train_test_split(frame.index, test_size=.1, random_state=42, stratify=frame.Potability)
    train, holdout = train_test_split(remaining, test_size=1/3, random_state=42, stratify=frame.loc[remaining].Potability)
    validation, test = train_test_split(holdout, test_size=.5, random_state=42, stratify=frame.loc[holdout].Potability)
    return {name: sorted(map(int, ids)) for name, ids in
            [('train', train), ('validation', validation), ('test', test), ('demo', demo)]}


def evaluate(pipeline, frame):
    truth = frame.Potability
    prediction = pipeline.predict(frame[list(MAPPING)])
    return {**{name: float(fn(truth, prediction)) for name, fn in
               [('accuracy', accuracy_score), ('balanced_accuracy', balanced_accuracy_score),
                ('precision', precision_score), ('recall', recall_score), ('f1', f1_score)]},
            'confusion_matrix': confusion_matrix(truth, prediction, labels=[0, 1]).tolist()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='v1')
    args = parser.parse_args()
    if not args.version.isalnum():
        parser.error('version must be alphanumeric')
    output = Path('artifacts/water-potability') / args.version
    if output.exists():
        parser.error('version already exists; choose a new version')
    data = Path('data'); data.mkdir(exist_ok=True)
    csv = data / 'water_potability.csv'
    if not csv.exists():
        with urlopen(URL, timeout=60) as response:
            csv.write_bytes(response.read())
    if hashlib.sha256(csv.read_bytes()).hexdigest() != CHECKSUM:
        raise ValueError('dataset checksum mismatch')
    frame = pd.read_csv(csv)
    assert list(frame.columns) == list(MAPPING) + ['Potability']
    partitions = split(frame)
    output.mkdir(parents=True)
    (output / 'splits.json').write_text(json.dumps(partitions, indent=2))
    train = frame.loc[partitions['train']]
    pipeline = make_pipeline(SimpleImputer(strategy='median'),
                             RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=1,
                                                    class_weight='balanced'))
    pipeline.fit(train[list(MAPPING)], train.Potability)
    np.testing.assert_allclose(pipeline.steps[0][1].statistics_, train[list(MAPPING)].median())
    metrics = {part: evaluate(pipeline, frame.loc[partitions[part]]) for part in ['validation', 'test']}
    metadata = {'version': args.version, 'features': list(MAPPING), 'mapping': MAPPING,
                'missingInputFeatures': [c for c in MAPPING if frame[c].isna().any()],
                'target': 'Potability', 'output': 'class label: 0=not potable, 1=potable',
                'dataset': {'origin': 'https://www.kaggle.com/datasets/adityakadiwal/water-potability',
                            'download': URL, 'sha256': CHECKSUM,
                            'license': 'CC0-1.0 (upstream Kaggle listing)'},
                'seed': 42, 'counts': {k: len(v) for k, v in partitions.items()},
                'classBalance': {k: frame.loc[v].Potability.value_counts().to_dict() for k, v in partitions.items()}}
    mlflow.set_tracking_uri(Path('mlruns').resolve().as_uri())
    mlflow.set_experiment('water-potability')
    with mlflow.start_run(run_name=args.version) as run:
        mlflow.log_params({'seed': 42, 'n_estimators': 200, 'dataset_sha256': CHECKSUM})
        mlflow.log_metrics({part + '_' + k: v for part, vals in metrics.items() for k, v in vals.items() if k != 'confusion_matrix'})
        example = train[list(MAPPING)].head(2)
        mlflow.sklearn.save_model(pipeline, str(output / 'model'), input_example=example,
                                  signature=mlflow.models.infer_signature(example, pipeline.predict(example)))
        metadata['trainingRunId'] = run.info.run_id
        metadata['artifactSHA256'] = hashlib.sha256((output / 'model/model.pkl').read_bytes()).hexdigest()
        (output / 'metadata.json').write_text(json.dumps(metadata, indent=2))
        (output / 'metrics.json').write_text(json.dumps(metrics, indent=2))
        demo = frame.loc[partitions['demo']].drop(columns='Potability').copy()
        demo.insert(0, 'sampleId', demo.index)
        demo.to_csv(output / 'demo.csv', index=False)
        mlflow.log_artifacts(str(output), artifact_path='training')
    print(json.dumps({'artifact': str(output), 'metrics': metrics}, indent=2))


if __name__ == '__main__':
    main()
