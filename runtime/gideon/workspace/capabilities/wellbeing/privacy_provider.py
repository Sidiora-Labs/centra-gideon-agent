"""Agent access contains subject/consent and masked metadata, never fact values."""
import asyncio
import json
from .privacy import PrivacyStore
from .store import MeasurementError


async def invoke_privacy(home, arguments):
    store = PrivacyStore(home)
    operation = arguments.get('operation', '').removeprefix('privacy_')
    payload, identity = arguments.get('payload', {}), arguments.get('id')
    methods = {'subject_create': store.create_subject, 'subjects': store.list_subjects, 'subject': store.get_subject, 'consent': store.consent, 'consents': store.consents, 'facts': store.list_facts, 'fact': store.get_fact, 'fact_history': store.history_fact, 'audit': store.audit}
    if operation not in methods:
        raise MeasurementError('Privacy tools expose masked metadata only')
    if operation in ('subject_create', 'consent'):
        result = await asyncio.to_thread(methods[operation], identity, payload) if operation == 'consent' else await asyncio.to_thread(methods[operation], payload)
    else:
        if payload:
            raise MeasurementError('Privacy metadata read accepts no payload')
        result = await asyncio.to_thread(methods[operation], identity) if operation != 'subjects' else await asyncio.to_thread(methods[operation])
    return json.dumps(result, allow_nan=False)
