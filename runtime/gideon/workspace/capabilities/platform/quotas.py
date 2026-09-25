"""Configured subscription ceilings, canonical usage views and atomic reservations."""
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.operations.usage_ledger import UsageJournal


class QuotaError(ValueError):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class ProviderQuotaSnapshot:
    """Evidence supplied by a provider adapter, never by owner HTTP input."""

    provider: str
    captured_at: str
    source: str
    limits: tuple[dict, ...]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name, limit=256, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise QuotaError(f'{name} must be a nonempty string up to {limit} characters')
    return value


def _timestamp(value, name):
    _text(value, name, 64)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise QuotaError(f'{name} must be an ISO timestamp with an offset') from exc
    if parsed.tzinfo is None:
        raise QuotaError(f'{name} must be an ISO timestamp with an offset')
    return parsed.astimezone(timezone.utc)


def _amount(value, name, integer=False, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise QuotaError(f'{name} must be a positive number')
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise QuotaError(f'{name} must be a positive number') from exc
    if not math.isfinite(number) or number <= 0 or (integer and number != value):
        raise QuotaError(f'{name} must be a positive number')
    return number


class SubscriptionQuotaStore:
    def __init__(self, home):
        self.home = Path(home)
        self.path = self.home / 'capabilities' / 'platform_quotas.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS quota_plans(id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS quota_reservations(id TEXT NOT NULL,revision INTEGER NOT NULL,plan_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS quota_snapshots(plan_id TEXT NOT NULL,captured_at TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(plan_id,captured_at));
                CREATE TABLE IF NOT EXISTS quota_requests(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,result TEXT NOT NULL);''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _plan(self, db, identity):
        row = db.execute('SELECT data FROM quota_plans WHERE id=? ORDER BY revision DESC LIMIT 1', (identity,)).fetchone()
        if row is None:
            raise QuotaError('Subscription quota plan not found', 404, 'not_found')
        return json.loads(row[0])

    def _reservation(self, db, identity):
        row = db.execute('SELECT data FROM quota_reservations WHERE id=? ORDER BY revision DESC LIMIT 1', (identity,)).fetchone()
        if row is None:
            raise QuotaError('Quota reservation not found', 404, 'not_found')
        return json.loads(row[0])

    def _request(self, db, request_id, kind, payload, execute):
        _text(request_id, 'request_id', 128)
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        prior = db.execute('SELECT kind,payload,result FROM quota_requests WHERE id=?', (request_id,)).fetchone()
        if prior:
            if prior[0] != kind or prior[1] != encoded:
                raise QuotaError('Request ID already used', 409, 'conflict')
            return json.loads(prior[2])
        result = execute()
        db.execute('INSERT INTO quota_requests VALUES(?,?,?,?)', (request_id, kind, encoded, json.dumps(result)))
        return result

    def create_plan(self, payload):
        required = {'request_id', 'provider', 'name', 'source', 'cycle_start', 'cycle_end', 'token_limit', 'dollar_limit'}
        optional = {'monthly_cost_usd'}
        if not isinstance(payload, dict) or set(payload) - required - optional or required - set(payload):
            raise QuotaError('Missing or unsupported quota plan fields')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            def execute():
                start, end = _timestamp(payload['cycle_start'], 'cycle_start'), _timestamp(payload['cycle_end'], 'cycle_end')
                if end <= start:
                    raise QuotaError('cycle_end must be after cycle_start')
                tokens = _amount(payload['token_limit'], 'token_limit', integer=True, optional=True)
                dollars = _amount(payload['dollar_limit'], 'dollar_limit', optional=True)
                if tokens is None and dollars is None:
                    raise QuotaError('At least one configured quota limit is required')
                monthly = _amount(payload.get('monthly_cost_usd'), 'monthly_cost_usd', optional=True)
                stamp = _now()
                row = {'id': str(uuid4()), 'revision': 1, 'provider': _text(payload['provider'], 'provider', 200), 'name': _text(payload['name'], 'name', 200), 'source': _text(payload['source'], 'source', 500), 'cycle_start': start.isoformat(), 'cycle_end': end.isoformat(), 'token_limit': tokens, 'dollar_limit': dollars, 'monthly_cost_usd': monthly, 'archived': False, 'quota_basis': 'user_configured', 'created_at': stamp, 'updated_at': stamp}
                db.execute('INSERT INTO quota_plans VALUES(?,?,?)', (row['id'], 1, json.dumps(row)))
                return row
            return self._request(db, payload['request_id'], 'quota-plan-create', payload, execute)

    def update_plan(self, identity, payload):
        allowed = {'request_id', 'revision', 'name', 'source', 'token_limit', 'dollar_limit', 'monthly_cost_usd', 'archived'}
        if not isinstance(payload, dict) or set(payload) - allowed or not {'request_id', 'revision'} <= set(payload):
            raise QuotaError('Missing or unsupported quota plan fields')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            def execute():
                row = self._plan(db, identity)
                if type(payload['revision']) is not int or payload['revision'] != row['revision']:
                    raise QuotaError('Quota plan changed; reload', 409, 'conflict')
                for key in ('name', 'source'):
                    if key in payload:
                        row[key] = _text(payload[key], key, 500 if key == 'source' else 200)
                for key, integer in (('token_limit', True), ('dollar_limit', False), ('monthly_cost_usd', False)):
                    if key in payload:
                        row[key] = _amount(payload[key], key, integer=integer, optional=True)
                if row['token_limit'] is None and row['dollar_limit'] is None:
                    raise QuotaError('At least one configured quota limit is required')
                if 'archived' in payload:
                    if type(payload['archived']) is not bool:
                        raise QuotaError('archived must be a boolean')
                    row['archived'] = payload['archived']
                row.update(revision=row['revision'] + 1, updated_at=_now())
                db.execute('INSERT INTO quota_plans VALUES(?,?,?)', (identity, row['revision'], json.dumps(row)))
                return row
            return self._request(db, payload['request_id'], 'quota-plan-update', {'id': identity, **payload}, execute)

    def list_plans(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT p.data FROM quota_plans p WHERE p.revision=(SELECT MAX(s.revision) FROM quota_plans s WHERE s.id=p.id) ORDER BY json_extract(p.data,"$.name")')]

    def get_plan(self, identity):
        with self.connection() as db:
            return self._plan(db, identity)

    def history(self, identity):
        with self.connection() as db:
            self._plan(db, identity)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM quota_plans WHERE id=? ORDER BY revision', (identity,))]

    def _usage(self, plan):
        tokens = turns = unpriced = 0
        dollars = 0.0
        for row in UsageJournal(self.home / 'usage' / 'turns.jsonl').rows():
            provider = row.get('provider_instance') or row.get('provider')
            if provider != plan['provider'] or not plan['cycle_start'] <= str(row.get('ts', '')) < plan['cycle_end']:
                continue
            tokens += sum(max(0, int(row.get(key, 0) or 0)) for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
            dollars += max(0.0, float(row.get('cost_usd', 0) or 0))
            turns += 1
            unpriced += not bool(row.get('priced', True))
        return {'tokens': tokens, 'dollars': round(dollars, 6), 'turns': turns, 'unpriced_turns': unpriced, 'basis': 'canonical_local_usage_ledger'}

    def _latest_reservations(self, db, plan_id):
        return [json.loads(row[0]) for row in db.execute('SELECT r.data FROM quota_reservations r WHERE r.plan_id=? AND r.revision=(SELECT MAX(s.revision) FROM quota_reservations s WHERE s.id=r.id) ORDER BY r.rowid', (plan_id,))]

    def summary(self, identity, at=None):
        instant = _timestamp(at, 'at') if at else datetime.now(timezone.utc)
        with self.connection() as db:
            plan = self._plan(db, identity)
            usage = self._usage(plan)
            reservations = self._latest_reservations(db, identity)
            active = [row for row in reservations if row['status'] == 'held' and _timestamp(row['expires_at'], 'expires_at') > instant]
            reserved_tokens = sum(row['expected_tokens'] for row in active)
            reserved_dollars = round(sum(row['expected_dollars'] for row in active), 6)
            available_tokens = None if plan['token_limit'] is None else max(0, plan['token_limit'] - usage['tokens'] - reserved_tokens)
            available_dollars = None if plan['dollar_limit'] is None else round(max(0.0, plan['dollar_limit'] - usage['dollars'] - reserved_dollars), 6)
            return {'plan': plan, 'usage': usage, 'reservations': {'active': len(active), 'expired_unreleased': sum(row['status'] == 'held' and row not in active for row in reservations), 'reserved_tokens': reserved_tokens, 'reserved_dollars': reserved_dollars}, 'available': {'tokens': available_tokens, 'dollars': available_dollars}, 'provider_evidence': self._latest_snapshot(db, identity), 'coverage': 'Configured availability uses owner-supplied ceilings, canonical local usage and active planning reservations. It does not grant provider entitlement or runtime admission. Provider evidence is shown only when a provider adapter recorded it; no vendor quota is inferred.'}

    def reserve(self, identity, payload):
        required = {'request_id', 'run_id', 'purpose', 'expected_tokens', 'expected_dollars', 'expires_at'}
        if not isinstance(payload, dict) or set(payload) != required:
            raise QuotaError('Missing or unsupported quota reservation fields')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            def execute():
                plan = self._plan(db, identity)
                if plan['archived']:
                    raise QuotaError('Archived quota plans cannot reserve capacity', 409, 'conflict')
                expiry = _timestamp(payload['expires_at'], 'expires_at')
                instant = datetime.now(timezone.utc)
                if not instant < expiry <= _timestamp(plan['cycle_end'], 'cycle_end'):
                    raise QuotaError('Reservation expiry must be future and within plan cycle')
                expected_tokens = 0 if payload['expected_tokens'] == 0 else _amount(payload['expected_tokens'], 'expected_tokens', integer=True)
                expected_dollars = 0.0 if payload['expected_dollars'] == 0 else _amount(payload['expected_dollars'], 'expected_dollars')
                if expected_tokens == 0 and expected_dollars == 0:
                    raise QuotaError('Reservation must request tokens or dollars')
                current = self.summary(identity, instant.isoformat())
                if current['available']['tokens'] is not None and expected_tokens > current['available']['tokens']:
                    raise QuotaError('Configured token quota is exhausted', 409, 'quota_exhausted')
                if current['available']['dollars'] is not None and expected_dollars > current['available']['dollars']:
                    raise QuotaError('Configured dollar quota is exhausted', 409, 'quota_exhausted')
                stamp = _now()
                row = {'id': str(uuid4()), 'revision': 1, 'plan_id': identity, 'run_id': _text(payload['run_id'], 'run_id', 200), 'purpose': _text(payload['purpose'], 'purpose', 500), 'expected_tokens': expected_tokens, 'expected_dollars': expected_dollars, 'expires_at': expiry.isoformat(), 'status': 'held', 'evidence_basis': 'local_planning_only', 'created_at': stamp, 'updated_at': stamp}
                db.execute('INSERT INTO quota_reservations VALUES(?,?,?,?)', (row['id'], 1, identity, json.dumps(row)))
                return row
            return self._request(db, payload['request_id'], 'quota-reserve', {'plan_id': identity, **payload}, execute)

    def release(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id', 'revision', 'reason'}:
            raise QuotaError('Missing or unsupported reservation release fields')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            def execute():
                row = self._reservation(db, identity)
                if type(payload['revision']) is not int or payload['revision'] != row['revision'] or row['status'] != 'held':
                    raise QuotaError('Reservation changed; reload', 409, 'conflict')
                row.update(revision=row['revision'] + 1, status='released', release_reason=_text(payload['reason'], 'reason', 500), updated_at=_now())
                db.execute('INSERT INTO quota_reservations VALUES(?,?,?,?)', (identity, row['revision'], row['plan_id'], json.dumps(row)))
                return row
            return self._request(db, payload['request_id'], 'quota-release', {'id': identity, **payload}, execute)

    def list_reservations(self, plan_id):
        with self.connection() as db:
            self._plan(db, plan_id)
            return self._latest_reservations(db, plan_id)

    def record_provider_snapshot(self, plan_id, snapshot):
        if not isinstance(snapshot, ProviderQuotaSnapshot):
            raise QuotaError('Provider evidence requires the provider adapter boundary', 403, 'provider_evidence_required')
        captured = _timestamp(snapshot.captured_at, 'captured_at').isoformat()
        with self.connection() as db:
            plan = self._plan(db, plan_id)
            if snapshot.provider != plan['provider']:
                raise QuotaError('Provider evidence does not match quota plan', 409, 'conflict')
            limits = []
            for limit in snapshot.limits:
                if not isinstance(limit, dict) or set(limit) != {'key', 'label', 'percent_used', 'resets_at'}:
                    raise QuotaError('Invalid provider quota evidence')
                percent = limit['percent_used']
                if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
                    raise QuotaError('Provider percent_used must be between 0 and 100')
                limits.append({'key': _text(limit['key'], 'key', 100), 'label': _text(limit['label'], 'label', 200), 'percent_used': float(percent), 'resets_at': _timestamp(limit['resets_at'], 'resets_at').isoformat()})
            row = {'plan_id': plan_id, 'provider': snapshot.provider, 'captured_at': captured, 'source': _text(snapshot.source, 'source', 500), 'limits': limits, 'evidence_basis': 'provider_reported'}
            db.execute('INSERT INTO quota_snapshots VALUES(?,?,?)', (plan_id, captured, json.dumps(row)))
            return row

    def _latest_snapshot(self, db, plan_id):
        row = db.execute('SELECT data FROM quota_snapshots WHERE plan_id=? ORDER BY captured_at DESC LIMIT 1', (plan_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def provider_evidence(self, plan_id):
        with self.connection() as db:
            self._plan(db, plan_id)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM quota_snapshots WHERE plan_id=? ORDER BY captured_at DESC', (plan_id,))]
