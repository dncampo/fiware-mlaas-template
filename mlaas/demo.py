"""Dataset-specific dashboard injection. The inference adapter stays generic."""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mlaas.cli import inject

router = APIRouter()


class Replay(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    start: int = Field(default=0, ge=0)


@router.post('/inject')
def replay(body: Replay):
    from mlaas.adapter import event
    try:
        ids = inject(Path(os.getenv('DEMO_DIR', 'artifacts/water-potability/v1')),
                     body.count, body.start, os.getenv('BROKER_URL', 'http://localhost:1026'))
        event('injection', {'ids': ids, 'demoStart': body.start})
        return {'ids': ids}
    except Exception as error:
        event('injection_error', {'error': str(error)})
        raise HTTPException(502, str(error))
