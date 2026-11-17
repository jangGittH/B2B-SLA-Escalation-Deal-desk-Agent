"""Run-scoped structured events; never persist provider secrets or hidden thoughts."""
from contextvars import ContextVar
import logging
import time
import hashlib
import json

current_run = ContextVar('current_run', default=None)
log = logging.getLogger('work_assistant.runs')


def event(kind, payload):
    context = current_run.get()
    if context:
        database, run = context
        database.event(run['id'], kind, payload)
        log.info('run_event', extra={'run_id': run['id'], 'event_kind': kind})


def model_event(response, model, started):
    usage = response.usage_metadata
    from .runtime.pricing import estimate
    data=usage.model_dump(mode='json', exclude_none=True) if usage else None
    event('model_call', {
        'model': model, 'duration_ms': round((time.monotonic() - started) * 1000),
        'usage': data,
        **estimate(model,data),
    })


def source_reference(document):
    payload = document.model_dump(mode='json')
    return {'id': document.id, 'language': document.language, 'version': document.version,
            'checksum': hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
