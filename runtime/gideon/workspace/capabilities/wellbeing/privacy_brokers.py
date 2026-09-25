"""Subject-scoped privacy broker cases with explicit evidence provenance."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from .privacy import PrivacyStore, fields, now
from .store import MeasurementError, text


STATES = (
    'unscanned', 'found', 'not_found', 'indirect_exposure', 'blocked',
    'optout_in_progress', 'submitted', 'verification_pending',
    'awaiting_processing', 'confirmed_removed', 'human_task_queued', 'reappeared',
)
OBSERVATIONS = ('found', 'not_found', 'indirect_exposure', 'blocked')
MANUAL_TRANSITIONS = {
    'found': ('optout_in_progress', 'human_task_queued'),
    'indirect_exposure': ('optout_in_progress', 'human_task_queued'),
    'blocked': ('human_task_queued',),
    'optout_in_progress': ('submitted', 'human_task_queued'),
    'submitted': ('verification_pending', 'human_task_queued'),
    'verification_pending': ('awaiting_processing', 'human_task_queued'),
    'awaiting_processing': ('human_task_queued',),
    'human_task_queued': ('optout_in_progress', 'submitted', 'verification_pending', 'awaiting_processing'),
    'reappeared': ('optout_in_progress', 'human_task_queued'),
}
BACKOFF_DAYS = {
    'unscanned': 0, 'found': 1, 'indirect_exposure': 1, 'blocked': 14,
    'optout_in_progress': 1, 'submitted': 3, 'verification_pending': 3,
    'awaiting_processing': 7, 'confirmed_removed': 30,
    'not_found': 60, 'human_task_queued': 14, 'reappeared': 1,
}


@dataclass(frozen=True)
class VerifiedBrokerObservation:
    """Result accepted only from a scanner adapter, never from owner HTTP input."""

    outcome: str
    verifier: str
    checked_at: str
    evidence_ref: str


@dataclass(frozen=True)
class BrokerAdapterResult:
    provider: str
    outcome: str
    checked_at: str
    evidence_ref: str


class PrivacyBrokerStore(PrivacyStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS privacy_brokers(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS privacy_broker_cases(id TEXT NOT NULL,revision INTEGER NOT NULL,subject_id TEXT NOT NULL,broker_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE UNIQUE INDEX IF NOT EXISTS privacy_broker_subject ON privacy_broker_cases(subject_id,broker_id,revision);
                CREATE TABLE IF NOT EXISTS privacy_broker_events(case_id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(case_id,revision));''')

    def _broker(self, db, identity):
        row = db.execute('SELECT data FROM privacy_brokers WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise MeasurementError('Privacy broker not found', 404, 'not_found')
        return json.loads(row[0])

    def _case(self, db, identity):
        row = db.execute('SELECT data FROM privacy_broker_cases WHERE id=? ORDER BY revision DESC LIMIT 1', (identity,)).fetchone()
        if row is None:
            raise MeasurementError('Privacy broker case not found', 404, 'not_found')
        return self._decorate(json.loads(row[0]))

    def _decorate(self, row):
        return dict(row, allowed_transitions=list(MANUAL_TRANSITIONS.get(row['state'], ())))

    def _append(self, db, row, operation, **details):
        row = dict(row, revision=row['revision'] + 1, updated_at=now())
        db.execute('INSERT INTO privacy_broker_cases VALUES(?,?,?,?,?)', (row['id'], row['revision'], row['subject_id'], row['broker_id'], json.dumps(row)))
        event = dict(case_id=row['id'], revision=row['revision'], operation=operation, state=row['state'], at=row['updated_at'], **details)
        db.execute('INSERT INTO privacy_broker_events VALUES(?,?,?)', (row['id'], row['revision'], json.dumps(event)))
        self._audit(db, row['subject_id'], 'broker_case_' + operation, case_id=row['id'], broker_id=row['broker_id'], state=row['state'], evidence_basis=row.get('evidence_basis'))
        return self._decorate(row)

    def _mutate_case(self, kind, identity, payload, execute):
        text(payload.get('request_id'), 'request_id', 128)
        fingerprint = dict(identity=identity, payload=payload)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._prior(db, payload['request_id'], kind, fingerprint)
            if prior:
                return prior
            result = execute(db)
            return self._receipt(db, payload['request_id'], kind, fingerprint, result)

    def create_broker(self, payload):
        fields(payload, ('request_id', 'name', 'website', 'source'), ('optout_url', 'enabled'))
        def execute(db):
            for key, limit in (('name', 200), ('source', 256), ('website', 1000), ('optout_url', 1000)):
                text(payload.get(key, ''), key, limit, key in ('website', 'optout_url'))
            for key in ('website', 'optout_url'):
                if payload.get(key):
                    address = urlsplit(payload[key])
                    if address.scheme not in ('http', 'https') or not address.hostname or address.username or address.password:
                        raise MeasurementError(key + ' must be an HTTP(S) address without credentials')
            if 'enabled' in payload and type(payload['enabled']) is not bool:
                raise MeasurementError('Enabled must be a boolean')
            row = dict(id=str(uuid4()), name=payload['name'], website=payload['website'], optout_url=payload.get('optout_url', ''), source=payload['source'], enabled=payload.get('enabled', True), created_at=now(), updated_at=now())
            db.execute('INSERT INTO privacy_brokers VALUES(?,?)', (row['id'], json.dumps(row)))
            return row
        return self._mutate_case('privacy-broker', 'catalog', payload, execute)

    def list_brokers(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM privacy_brokers ORDER BY json_extract(data,"$.name")')]

    def create_case(self, subject, payload):
        fields(payload, ('request_id', 'broker_id'))
        def execute(db):
            self._require(db, subject, 'broker_scan')
            broker = self._broker(db, payload['broker_id'])
            existing = db.execute('SELECT id FROM privacy_broker_cases WHERE subject_id=? AND broker_id=? LIMIT 1', (subject, broker['id'])).fetchone()
            if existing:
                raise MeasurementError('Broker case already exists for subject', 409, 'conflict')
            stamp = now()
            row = dict(id=str(uuid4()), subject_id=subject, broker_id=broker['id'], revision=0, state='unscanned', evidence_basis='none', evidence='', reason='', next_recheck_at=stamp, created_at=stamp, updated_at=stamp)
            return self._append(db, row, 'created')
        return self._mutate_case('privacy-broker-case', subject, payload, execute)

    def list_cases(self, subject):
        with self.connection() as db:
            self._subject(db, subject)
            rows = db.execute('SELECT c.data,b.data FROM privacy_broker_cases c JOIN privacy_brokers b ON b.id=c.broker_id WHERE c.subject_id=? AND c.revision=(SELECT MAX(s.revision) FROM privacy_broker_cases s WHERE s.id=c.id) ORDER BY json_extract(b.data,"$.name")', (subject,)).fetchall()
            return [dict(self._decorate(json.loads(case)), broker=json.loads(broker)) for case, broker in rows]

    def get_case(self, identity):
        with self.connection() as db:
            return self._case(db, identity)

    def history(self, identity):
        with self.connection() as db:
            self._case(db, identity)
            return [self._decorate(json.loads(row[0])) for row in db.execute('SELECT data FROM privacy_broker_cases WHERE id=? ORDER BY revision', (identity,))]

    def events(self, identity):
        with self.connection() as db:
            self._case(db, identity)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM privacy_broker_events WHERE case_id=? ORDER BY revision', (identity,))]

    def record_user_observation(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'outcome', 'evidence'))
        def execute(db):
            row = self._case(db, identity)
            self._require(db, row['subject_id'], 'broker_scan')
            self._revision(row, payload)
            if payload['outcome'] not in OBSERVATIONS:
                raise MeasurementError('Invalid broker observation')
            text(payload['evidence'], 'evidence', 2000)
            if row['state'] == 'confirmed_removed' and payload['outcome'] == 'found':
                state = 'reappeared'
            elif row['state'] in ('unscanned', 'found', 'not_found', 'indirect_exposure', 'blocked', 'reappeared'):
                state = payload['outcome']
            else:
                raise MeasurementError('Case must complete or request verification before a new owner observation', 409, 'conflict')
            row.update(state=state, evidence_basis='user_attested', evidence=payload['evidence'], next_recheck_at=self._next(state))
            return self._append(db, row, 'owner_observation', outcome=payload['outcome'])
        return self._mutate_case('privacy-broker-observation', identity, payload, execute)

    def transition(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'state', 'reason'))
        def execute(db):
            row = self._case(db, identity)
            self._revision(row, payload)
            if payload['state'] not in MANUAL_TRANSITIONS.get(row['state'], ()):
                raise MeasurementError('Invalid manual broker case transition', 409, 'conflict')
            if payload['state'] in ('optout_in_progress', 'submitted'):
                self._require(db, row['subject_id'], 'broker_submit')
            text(payload['reason'], 'reason', 2000)
            row.update(state=payload['state'], reason=payload['reason'], evidence_basis='user_attested', next_recheck_at=self._next(payload['state']))
            return self._append(db, row, 'manual_transition')
        return self._mutate_case('privacy-broker-transition', identity, payload, execute)

    def request_recheck(self, identity, payload):
        fields(payload, ('request_id', 'revision'))
        def execute(db):
            row = self._case(db, identity)
            self._require(db, row['subject_id'], 'broker_scan')
            self._revision(row, payload)
            row['next_recheck_at'] = now()
            return self._append(db, row, 'recheck_requested')
        return self._mutate_case('privacy-broker-recheck', identity, payload, execute)

    def apply_verified_observation(self, identity, observation):
        if not isinstance(observation, VerifiedBrokerObservation):
            raise MeasurementError('Verified observations require the scanner adapter boundary', 403, 'verification_required')
        if observation.outcome not in ('found', 'not_found'):
            raise MeasurementError('Verifier outcome must be found or not_found')
        text(observation.verifier, 'verifier', 200)
        text(observation.evidence_ref, 'evidence_ref', 1000)
        try:
            datetime.fromisoformat(observation.checked_at.replace('Z', '+00:00'))
        except (ValueError, AttributeError) as exc:
            raise MeasurementError('Verifier checked_at must be an offset timestamp') from exc
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._case(db, identity)
            self._require(db, row['subject_id'], 'broker_scan')
            if observation.outcome == 'not_found' and row['state'] in ('verification_pending', 'awaiting_processing', 'human_task_queued'):
                state = 'confirmed_removed'
            elif observation.outcome == 'found' and row['state'] == 'confirmed_removed':
                state = 'reappeared'
            else:
                raise MeasurementError('Verified observation does not match the case lifecycle', 409, 'conflict')
            row.update(state=state, evidence_basis='verified_rescan', evidence=observation.evidence_ref, verifier=observation.verifier, verified_at=observation.checked_at, next_recheck_at=self._next(state))
            return self._append(db, row, 'verified_observation', outcome=observation.outcome)

    def apply_adapter_scan(self, identity, payload, result):
        fields(payload, ('request_id', 'revision'))
        if not isinstance(result, BrokerAdapterResult) or result.outcome not in OBSERVATIONS:
            raise MeasurementError('Broker scan requires a typed adapter result', 403, 'verification_required')
        return self._adapter_mutation('privacy-broker-adapter-scan', identity, payload, result, 'broker_scan')

    def apply_adapter_prepared(self, identity, payload, result):
        fields(payload, ('request_id', 'revision'))
        if not isinstance(result, BrokerAdapterResult) or result.outcome != 'prepared':
            raise MeasurementError('Opt-out preparation requires a typed adapter result', 403, 'verification_required')
        return self._adapter_mutation('privacy-broker-adapter-prepare', identity, payload, result, 'broker_submit')

    def apply_adapter_submission(self, identity, payload, result):
        fields(payload, ('request_id', 'revision'))
        if not isinstance(result, BrokerAdapterResult) or result.outcome != 'submitted':
            raise MeasurementError('Opt-out submission requires a typed adapter result', 403, 'verification_required')
        return self._adapter_mutation('privacy-broker-adapter-submit', identity, payload, result, 'broker_submit')

    def apply_adapter_verification(self, identity, payload, result):
        fields(payload, ('request_id', 'revision'))
        if not isinstance(result, BrokerAdapterResult) or result.outcome not in ('found', 'not_found'):
            raise MeasurementError('Verification requires a typed adapter result', 403, 'verification_required')
        return self._adapter_mutation('privacy-broker-adapter-verify', identity, payload, result, 'broker_scan')

    def _adapter_mutation(self, kind, identity, payload, result, scope):
        text(result.provider, 'provider', 100)
        text(result.evidence_ref, 'evidence_ref', 1000)
        try:
            datetime.fromisoformat(result.checked_at.replace('Z', '+00:00'))
        except (ValueError, AttributeError) as exc:
            raise MeasurementError('Adapter checked_at must be an offset timestamp') from exc
        def execute(db):
            row = self._case(db, identity)
            self._require(db, row['subject_id'], scope)
            self._revision(row, payload)
            if kind.endswith('-scan'):
                if row['state'] not in ('unscanned', 'found', 'not_found', 'indirect_exposure', 'blocked', 'confirmed_removed', 'reappeared'):
                    raise MeasurementError('Case is owned by the opt-out workflow', 409, 'conflict')
                state, operation = ('reappeared' if row['state'] == 'confirmed_removed' and result.outcome == 'found' else result.outcome), 'provider_scan'
            elif kind.endswith('-prepare'):
                if row['state'] not in ('found', 'indirect_exposure', 'reappeared'):
                    raise MeasurementError('Case is not ready for opt-out preparation', 409, 'conflict')
                state, operation = 'optout_in_progress', 'provider_optout_prepared'
            elif kind.endswith('-submit'):
                if row['state'] != 'optout_in_progress':
                    raise MeasurementError('Case is not ready for opt-out submission', 409, 'conflict')
                state, operation = 'submitted', 'provider_optout_submitted'
            else:
                if row['state'] not in ('submitted', 'verification_pending', 'awaiting_processing'):
                    raise MeasurementError('Case is not ready for provider verification', 409, 'conflict')
                state = 'confirmed_removed' if result.outcome == 'not_found' else 'awaiting_processing'
                operation = 'provider_verified'
            row.update(state=state, evidence_basis='provider_protocol', evidence=result.evidence_ref,
                       verifier=result.provider, verified_at=result.checked_at,
                       next_recheck_at=self._next(state))
            return self._append(db, row, operation, provider=result.provider, outcome=result.outcome)
        return self._mutate_case(kind, identity, payload, execute)

    @staticmethod
    def _revision(row, payload):
        if type(payload['revision']) is not int or payload['revision'] != row['revision']:
            raise MeasurementError('Broker case changed; reload', 409, 'conflict')

    @staticmethod
    def _next(state):
        return (datetime.now(timezone.utc) + timedelta(days=BACKOFF_DAYS[state])).isoformat()
