"""Native health document operations use the captured home only."""
import asyncio
import base64
import json
from .shared_health import SharedHealthStore
from .store import MeasurementError


async def invoke_shared(home, arguments):
    store = SharedHealthStore(home)
    operation = arguments.get('operation', '').removeprefix('shared_')
    payload = arguments.get('payload', {})
    if operation in ('status', 'preview_file', 'download'):
        if payload:
            raise MeasurementError('Shared file read accepts no overrides')
        result = await asyncio.to_thread(getattr(store, operation))
        if isinstance(result, bytes):
            result = dict(content_base64=base64.b64encode(result).decode(), media_type='application/json')
    elif operation in ('preview', 'commit', 'commit_file', 'publish'):
        result = await asyncio.to_thread(getattr(store, operation), payload)
    else:
        raise MeasurementError('Unknown shared health operation')
    return json.dumps(result, allow_nan=False)
