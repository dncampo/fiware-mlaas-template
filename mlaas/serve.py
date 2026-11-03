"""Verify a fixed local artifact, then start standard MLflow Models serving."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact', type=Path, required=True)
    args = parser.parse_args()
    metadata = json.loads((args.artifact / 'metadata.json').read_text())
    actual = hashlib.sha256((args.artifact / 'model/model.pkl').read_bytes()).hexdigest()
    if metadata['version'] != args.artifact.name or actual != metadata['artifactSHA256']:
        raise ValueError('artifact version or checksum mismatch')
    os.execvp('mlflow', ['mlflow', 'models', 'serve', '-m', str(args.artifact / 'model'),
                         '--host', '0.0.0.0', '--port', '5000', '--env-manager', 'local'])


if __name__ == '__main__':
    main()
