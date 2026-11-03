import json
from pathlib import Path

import mlflow.sklearn
import numpy as np
import pandas as pd

from mlaas.train import split, MAPPING, CHECKSUM


def test_split_and_imputer():
    import hashlib
    csv = Path('data/water_potability.csv')
    assert hashlib.sha256(csv.read_bytes()).hexdigest() == CHECKSUM
    frame = pd.read_csv(csv)
    artifact = Path('artifacts/water-potability/v1')
    partitions = json.loads((artifact / 'splits.json').read_text())
    assert partitions == split(frame) == split(frame)
    ids = [i for part in partitions.values() for i in part]
    assert len(ids) == len(set(ids)) == len(frame)
    pipeline = mlflow.sklearn.load_model(str(artifact / 'model'))
    np.testing.assert_allclose(pipeline.steps[0][1].statistics_, frame.loc[partitions['train'], list(MAPPING)].median())
    assert len(pd.read_csv(artifact / 'demo.csv')) == len(partitions['demo'])
    assert 'Potability' not in pd.read_csv(artifact / 'demo.csv')


def test_deployed_artifact_cannot_be_overwritten():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'mlaas.train', '--version', 'v1'], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'version already exists' in result.stderr


def test_serving_rejects_corrupt_artifact(tmp_path):
    import subprocess
    import sys
    artifact = tmp_path / 'v1'
    (artifact / 'model').mkdir(parents=True)
    (artifact / 'metadata.json').write_text(json.dumps({'version': 'v1', 'artifactSHA256': 'invalid'}))
    (artifact / 'model/model.pkl').write_bytes(b'corrupt')
    result = subprocess.run([sys.executable, '-m', 'mlaas.serve', '--artifact', str(artifact)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'checksum mismatch' in result.stderr
