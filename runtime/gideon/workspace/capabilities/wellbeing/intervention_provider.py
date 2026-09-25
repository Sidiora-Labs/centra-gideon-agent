"""Agent access to intervention plans and explicit adherence records."""
import asyncio
import json
from .intervention import InterventionStore
from .store import MeasurementError


async def invoke_intervention(home, arguments):
    store = InterventionStore(home)
    name = arguments.get('operation', '').removeprefix('intervention_')
    payload, identity = arguments.get('payload', {}), arguments.get('id')
    methods = {key: getattr(store, key) for key in ('create_plan', 'update_plan', 'get_plan', 'list_plans', 'history_plan', 'record', 'correct_record', 'get_record', 'list_records', 'history_record', 'summary')}
    if name not in methods:
        raise MeasurementError('Unknown intervention operation')
    if name == 'create_plan':
        result = await asyncio.to_thread(methods[name], payload)
    elif name in ('update_plan', 'record', 'correct_record'):
        result = await asyncio.to_thread(methods[name], identity, payload)
    elif name in ('list_plans', 'summary'):
        result = await asyncio.to_thread(methods[name], **payload) if name == 'list_plans' else await asyncio.to_thread(methods[name], identity, **payload)
    else:
        result = await asyncio.to_thread(methods[name], identity)
    return json.dumps(result, allow_nan=False)
