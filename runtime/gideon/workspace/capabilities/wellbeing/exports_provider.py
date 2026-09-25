"""Native agent access to immutable, versioned wellbeing exports."""
import asyncio
import base64
import json
from .exports import ExportStore
from .store import MeasurementError


async def invoke_exports(home, arguments):
    store = ExportStore(home)
    operation = arguments.get('operation', '').removeprefix('exports_')
    payload = arguments.get('payload', {})
    if operation != 'create' and payload:
        raise MeasurementError('Export read operations accept no payload')
    if operation == 'create':
        result = await asyncio.to_thread(store.create, payload)
    elif operation in ('preview', 'list'):
        result = await asyncio.to_thread(store.preview if operation == 'preview' else store.list_exports)
    elif operation in ('get', 'download'):
        result = await asyncio.to_thread(store.get if operation == 'get' else store.download, arguments.get('id'))
        if isinstance(result, bytes):
            result = dict(content_base64=base64.b64encode(result).decode(), media_type='application/json')
    else:
        raise MeasurementError('Unknown wellbeing export operation')
    return json.dumps(result, allow_nan=False)
