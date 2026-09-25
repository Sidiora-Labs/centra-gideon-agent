"""Agent access to privacy holding metadata without plaintext or attestations."""
import asyncio
import json

from .privacy_holdings import OrgHoldingsStore
from .store import MeasurementError


async def invoke_holdings(home, arguments):
    store = OrgHoldingsStore(home)
    operation = arguments.get('operation', '').removeprefix('privacy_')
    identity = arguments.get('id')
    payload = arguments.get('payload', {})
    methods = {
        'organizations': store.list_orgs,
        'organization': store.get_org,
        'organization_history': store.history_org,
        'holdings': store.list_holdings,
        'holding_history': store.history_holding,
        'changes': store.list_changes,
        'change': store.get_change,
    }
    if operation not in methods:
        raise MeasurementError('Privacy holding tools expose metadata reads only')
    if payload:
        raise MeasurementError('Privacy holding metadata reads accept no payload')
    if not identity:
        raise MeasurementError('Privacy holding metadata reads require id')
    return json.dumps(await asyncio.to_thread(methods[operation], identity), allow_nan=False)
