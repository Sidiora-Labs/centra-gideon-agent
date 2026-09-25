"""Historical usage grouped only by attribution captured when each turn was written."""
import math
from datetime import datetime, timedelta, timezone
from gideon.operations import usage_ledger


def view(*, days=30, now=None):
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('Usage window must be 1..365 days')
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    groups = {}
    invalid = 0
    for row in usage_ledger._iter_rows():
        try:
            timestamp = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if not start <= timestamp <= now:
                continue
            keys = tuple(row.get(key) or None for key in ('instance_id', 'provider_instance', 'credential_ref', 'subscription_source'))
            if any(value is not None and (not isinstance(value, str) or len(value) > 200) for value in keys):
                raise ValueError('Invalid attribution')
            tokens = [int(row.get(key, 0) or 0) for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens')]
            cost = float(row.get('cost_usd', 0) or 0)
            if any(value < 0 for value in tokens) or not math.isfinite(cost) or cost < 0:
                raise ValueError('Invalid usage')
            group = groups.setdefault(keys, {'instance_id': keys[0], 'provider_instance': keys[1], 'credential_ref': keys[2], 'subscription_source': keys[3], 'turns': 0, 'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_creation_tokens': 0, 'recorded_cost_usd': 0.0, 'unpriced_turns': 0, 'models': set(), 'sources': set()})
            group['turns'] += 1
            for key, count in zip(('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'), tokens):
                group[key] += count
            group['recorded_cost_usd'] += cost
            group['unpriced_turns'] += not bool(row.get('priced', False))
            group['models'].add(str(row.get('model') or 'unknown'))
            group['sources'].add(str(row.get('source') or 'unknown'))
        except (ValueError, TypeError, KeyError, AttributeError):
            invalid += 1
    rows = sorted(groups.values(), key=lambda row: (-row['turns'], str(row['instance_id']), str(row['provider_instance'])))
    for row in rows:
        row.update(models=sorted(row['models']), sources=sorted(row['sources']), recorded_cost_usd=round(row['recorded_cost_usd'], 6), billed_cost_usd=None, quota_remaining=None)
    return {'version': 1, 'days': days, 'since': start.isoformat(), 'until': now.isoformat(), 'rows': rows, 'turns': sum(row['turns'] for row in rows), 'invalid_rows': invalid, 'unattributed_turns': sum(row['turns'] for row in rows if row['provider_instance'] is None), 'coverage': 'Retained canonical per-turn ledger only; guarded model-call audit records remain available on the existing usage page. Legacy attribution stays unknown. Credential and subscription-source values are emission-time binding references; vendor plan, billing and live quotas are unavailable.'}
