"""Recurring creative commissions over the shared trigger and direction stores."""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.automation.schedule import validate_cron_expr
from gideon.automation.triggers.arm import arm
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import config_dir
from gideon.core.sqlite_compat import sqlite3
from gideon.core.timezones import UnknownTimeZone, resolve_zone_name
from gideon.integrations.action_providers.base import ActionContext, ActionProvider, ActionResult

from .store import CatalogError, identifier, integer, keys, text
from .commission_dispatch import CommissionDispatcher, normalize_dispatch


ABILITIES = {'video', 'image', 'music', 'music-video', 'series'}
OPERATIONS = {'source.verify', 'treatment.snapshot'}
RATINGS = {'liked', 'disliked'}
TRIGGER_PREFIX = 'creative-commission:'
ACTION_PROVIDER = 'creative-commission'
MAX_FEEDBACK_CONTEXT = 20


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def _recurrence_rule(value, anchor):
    allowed = {'FREQ', 'UNTIL', 'COUNT', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'BYYEARDAY',
               'BYWEEKNO', 'BYMONTH', 'BYSETPOS', 'BYHOUR', 'BYMINUTE', 'BYSECOND', 'WKST'}
    components = {}
    for item in value.split(';'):
        if '=' not in item:
            raise CatalogError('Invalid recurrence rule')
        key, raw = item.split('=', 1)
        if key not in allowed or key in components:
            raise CatalogError('Unsupported or duplicate recurrence component: ' + key)
        components[key] = raw
    if components.get('FREQ') not in {'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'}:
        raise CatalogError('Recurrence frequency must be daily, weekly, monthly or yearly')
    try:
        interval = int(components.get('INTERVAL', '1'))
        count = int(components['COUNT']) if 'COUNT' in components else 1
    except (TypeError, ValueError):
        raise CatalogError('Recurrence interval and count must be positive integers') from None
    if interval < 1 or count < 1:
        raise CatalogError('Recurrence interval or count is outside supported bounds')
    try:
        from dateutil.rrule import rrulestr
        return rrulestr(value, dtstart=anchor, forceset=False)
    except ImportError as exc:
        raise CatalogError('Calendar recurrence support is unavailable', 503) from exc
    except (TypeError, ValueError, OverflowError):
        raise CatalogError('Invalid recurrence rule') from None


def _recurrence_next(cadence, after):
    from gideon.core.timezones import resolve_zone
    zone = resolve_zone(cadence['timezone'])
    anchor = datetime.fromisoformat(cadence['dtstart'])
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=zone)
    else:
        anchor = anchor.astimezone(zone)
    cursor = datetime.fromtimestamp(after, tz=zone)
    rule = _recurrence_rule(cadence['rrule'], anchor)
    excluded = set(cadence['exdates'])
    for _ in range(101):
        candidate = rule.after(cursor, inc=False)
        if candidate is None:
            return 0.0
        if candidate.isoformat() not in excluded and candidate.date().isoformat() not in excluded:
            return candidate.timestamp()
        cursor = candidate
    raise CatalogError('Recurrence exclusions exceed the bounded search window')


def _cadence_hash(cadence):
    return digest({key: value for key, value in cadence.items() if key != 'spec'}) if cadence['kind'] == 'recurrence' else digest(cadence['spec'])


def _cadence(value):
    if not isinstance(value, dict):
        raise CatalogError('Cadence must be an object')
    kind = value.get('kind')
    if kind == 'daily':
        keys(value, {'kind', 'at', 'timezone', 'weekdays_only'})
        at = value.get('at', '')
        try:
            hour, minute = (int(part) for part in at.split(':'))
        except (AttributeError, TypeError, ValueError):
            raise CatalogError('Daily cadence time must be HH:MM') from None
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise CatalogError('Daily cadence time must be HH:MM')
        spec = {'kind': 'cron', 'expr': f'{minute} {hour} * * ' + ('1-5' if value.get('weekdays_only') else '*')}
    elif kind == 'weekly':
        keys(value, {'kind', 'at', 'weekday', 'timezone'})
        at = value.get('at', '')
        try:
            hour, minute = (int(part) for part in at.split(':'))
        except (AttributeError, TypeError, ValueError):
            raise CatalogError('Weekly cadence time must be HH:MM') from None
        weekday = integer(value.get('weekday'), 0, 6)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise CatalogError('Weekly cadence time must be HH:MM')
        spec = {'kind': 'cron', 'expr': f'{minute} {hour} * * {weekday}'}
    elif kind == 'custom':
        keys(value, {'kind', 'cron', 'timezone'})
        expression = text(value.get('cron'), 120, True)
        if not validate_cron_expr(expression) or len(expression.split()) != 5:
            raise CatalogError('Custom cadence requires a valid five-field cron')
        spec = {'kind': 'cron', 'expr': expression}
    elif kind == 'interval':
        keys(value, {'kind', 'seconds', 'timezone'})
        spec = {'kind': 'interval', 'interval_secs': integer(value.get('seconds'), 900, 31536000)}
    elif kind == 'recurrence':
        keys(value, {'kind', 'dtstart', 'rrule', 'timezone', 'exdates'})
        zone_name = text(value.get('timezone'), 64, True)
        try:
            zone_name = resolve_zone_name(zone_name)[0]
            anchor = datetime.fromisoformat(text(value.get('dtstart'), 64, True))
        except (UnknownTimeZone, ValueError) as exc:
            raise CatalogError('Recurrence start or timezone is invalid') from exc
        if anchor.tzinfo is not None:
            from gideon.core.timezones import resolve_zone
            anchor = anchor.astimezone(resolve_zone(zone_name)).replace(tzinfo=None)
        rule = text(value.get('rrule'), 500, True).upper()
        _recurrence_rule(rule, anchor.replace(tzinfo=timezone.utc))
        exclusions = value.get('exdates', [])
        if not isinstance(exclusions, list) or len(exclusions) > 100:
            raise CatalogError('Recurrence exclusions are invalid')
        exclusions = [text(item, 64, True) for item in exclusions]
        return {'kind': kind, 'dtstart': anchor.isoformat(), 'rrule': rule, 'timezone': zone_name,
                'exdates': list(dict.fromkeys(exclusions)), 'spec': {'kind': 'recurrence'}}
    else:
        raise CatalogError('Choose daily, weekly, custom, interval or recurrence cadence')
    zone = value.get('timezone')
    if zone:
        try:
            spec['timezone'] = resolve_zone_name(text(zone, 64, True))[0]
        except UnknownTimeZone as exc:
            raise CatalogError('Cadence timezone is invalid') from exc
    return {**value, 'spec': spec}


def _brief(value):
    if not isinstance(value, dict):
        raise CatalogError('Brief must be an object')
    keys(value, {'intent', 'genre', 'category', 'style', 'constraints', 'seed_refs'})
    constraints = value.get('constraints', {})
    if not isinstance(constraints, dict) or len(constraints) > 20:
        raise CatalogError('Brief constraints must be a bounded object')
    seeds = value.get('seed_refs', [])
    if not isinstance(seeds, list) or len(seeds) > 50:
        raise CatalogError('Brief seed references are invalid')
    return {'intent': text(value.get('intent'), 4000, True), 'genre': text(value.get('genre', ''), 100),
            'category': text(value.get('category', ''), 100), 'style': text(value.get('style', ''), 8000),
            'constraints': {text(str(k), 80, True): text(str(v), 500) for k, v in constraints.items()},
            'seed_refs': [identifier(item) for item in seeds]}


def _plan(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise CatalogError('Commission plan requires 1 to 10 bounded steps')
    result, seen = [], set()
    for row in value:
        keys(row, {'id', 'title', 'operation', 'depends_on'})
        identity = identifier(row.get('id'))
        dependencies = row.get('depends_on', [])
        if identity in seen or row.get('operation') not in OPERATIONS or not isinstance(dependencies, list):
            raise CatalogError('Commission plan contains an invalid step')
        dependencies = [identifier(item) for item in dependencies]
        if any(item not in seen for item in dependencies):
            raise CatalogError('Commission steps may depend only on earlier steps')
        seen.add(identity)
        result.append({'id': identity, 'title': text(row.get('title'), 200, True),
                       'operation': row['operation'], 'depends_on': list(dict.fromkeys(dependencies))})
    return result


class CommissionStore:
    def __init__(self, home=None, direction=None, triggers=None, dispatcher=None):
        self.home = Path(home) if home is not None else config_dir()
        self.path = self.home / 'capabilities' / 'creative' / 'commissions.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.direction = direction
        self.triggers = triggers or TriggerStore(base_dir=self.home)
        self.dispatcher = dispatcher or CommissionDispatcher(self.home)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS creative_commissions(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_commission_requests(id TEXT PRIMARY KEY, request_hash TEXT, commission_id TEXT);
                CREATE TABLE IF NOT EXISTS creative_commission_runs(id TEXT PRIMARY KEY, commission_id TEXT, occurrence TEXT, record TEXT,
                    UNIQUE(commission_id,occurrence));
                CREATE TABLE IF NOT EXISTS creative_commission_feedback(id TEXT PRIMARY KEY, commission_id TEXT, run_id TEXT, record TEXT);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _load(self, db, identity):
        row = db.execute('SELECT record FROM creative_commissions WHERE id=?', (identifier(identity),)).fetchone()
        if not row:
            raise CatalogError('Creative commission not found', 404)
        return json.loads(row[0])

    def _save(self, db, record):
        db.execute('INSERT OR REPLACE INTO creative_commissions VALUES(?,?)', (record['id'], json.dumps(record, sort_keys=True)))
        return record

    def _direction(self):
        if self.direction is None:
            try:
                from .direction import DirectionStore
            except ImportError as exc:
                raise CatalogError('Creative direction capability is unavailable', 503) from exc
            self.direction = DirectionStore(self.home)
        return self.direction

    def create(self, payload):
        keys(payload, {'request_id', 'name', 'target_ability', 'brief', 'cadence', 'sources', 'steps', 'enabled', 'max_attempts', 'dispatch'})
        request = identifier(payload.get('request_id'))
        ability = payload.get('target_ability')
        if ability not in ABILITIES:
            raise CatalogError('Unknown creative commission ability')
        sources = payload.get('sources')
        if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
            raise CatalogError('Commission requires canonical direction sources')
        for source in sources:
            keys(source, {'kind', 'id', 'revision'})
            if source.get('kind') not in ('work', 'series'):
                raise CatalogError('Commission sources must be work or series')
            identifier(source.get('id')); integer(source.get('revision'))
        direction = self._direction()
        for source in sources:
            direction.pin(source)
        normalized = {'name': text(payload.get('name'), 200, True), 'target_ability': ability,
                      'brief': _brief(payload.get('brief')), 'cadence': _cadence(payload.get('cadence')),
                      'sources': sources, 'steps': _plan(payload.get('steps')), 'enabled': payload.get('enabled', True),
                      'max_attempts': integer(payload.get('max_attempts', 2), 1, 3)}
        if 'dispatch' in payload:
            normalized['dispatch'] = normalize_dispatch(ability, payload.get('dispatch'), sources)
        if type(normalized['enabled']) is not bool:
            raise CatalogError('Commission enabled must be boolean')
        fingerprint = digest(normalized)
        with self.db() as db:
            prior = db.execute('SELECT request_hash,commission_id FROM creative_commission_requests WHERE id=?', (request,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise CatalogError('Commission request already used with different values', 409)
                return self._load(db, prior[1])
            stamp = now_iso()
            record = {**normalized, 'id': str(uuid4()), 'revision': 1, 'schedule_revision': 1,
                      'schedule_error': '', 'created_at': stamp, 'updated_at': stamp}
            self._save(db, record)
            db.execute('INSERT INTO creative_commission_requests VALUES(?,?,?)', (request, fingerprint, record['id']))
        return self._sync(record)

    def get(self, identity):
        with self.db() as db:
            return self._load(db, identity)

    def list(self):
        with self.db() as db:
            return {'items': [json.loads(row[0]) for row in db.execute('SELECT record FROM creative_commissions ORDER BY rowid DESC')]}

    def update(self, identity, payload):
        keys(payload, {'revision', 'name', 'brief', 'cadence', 'enabled', 'max_attempts', 'dispatch'})
        with self.db() as db:
            record = self._load(db, identity)
            if integer(payload.get('revision')) != record['revision']:
                raise CatalogError('Commission changed; reload', 409)
            changed_schedule = 'cadence' in payload or 'enabled' in payload
            if 'name' in payload: record['name'] = text(payload['name'], 200, True)
            if 'brief' in payload: record['brief'] = _brief(payload['brief'])
            if 'dispatch' in payload: record['dispatch'] = normalize_dispatch(record['target_ability'], payload['dispatch'], record['sources'])
            if 'cadence' in payload: record['cadence'] = _cadence(payload['cadence'])
            if 'enabled' in payload:
                if type(payload['enabled']) is not bool: raise CatalogError('Commission enabled must be boolean')
                record['enabled'] = payload['enabled']
            if 'max_attempts' in payload: record['max_attempts'] = integer(payload['max_attempts'], 1, 3)
            record.update(revision=record['revision'] + 1, updated_at=now_iso())
            if changed_schedule: record['schedule_revision'] += 1
            self._save(db, record)
        return self._sync(record)

    def _trigger(self, record, at=0.0):
        if not record['enabled']:
            return None
        cadence_hash = _cadence_hash(record['cadence'])
        specification = dict(record['cadence']['spec'])
        if record['cadence']['kind'] == 'recurrence':
            occurrence = _recurrence_next(record['cadence'], at or time.time())
            if occurrence <= 0:
                return None
            specification = {'kind': 'at', 'at': occurrence, 'timezone': record['cadence']['timezone'], 'strict': True}
        trigger = Trigger(id=TRIGGER_PREFIX + record['id'], name='Creative commission: ' + record['name'], kind='clock',
            enabled=True, created_by='creative-commission', spec=specification,
            workflow={'inline': {'provider': ACTION_PROVIDER, 'config': {'commission_id': record['id'],
                'schedule_revision': record['schedule_revision'], 'cadence_hash': cadence_hash}}},
            overlap='skip', session='fresh', model_tier='background', delivery='none', failure_delivery='inbox',
            retry={'max_attempts': record['max_attempts']})
        from gideon.automation.triggers.screen import capabilities_for_action
        trigger.capabilities = capabilities_for_action(trigger)
        trigger.next_fire_at = arm(trigger, now=at or time.time())
        return trigger

    def _sync(self, record, after=0.0):
        error = ''
        state = 'active'
        next_fire = ''
        try:
            trigger = self._trigger(record, at=after)
            if trigger is None:
                self.triggers.delete(TRIGGER_PREFIX + record['id'])
                state = 'disabled' if not record['enabled'] else 'exhausted'
            else:
                self.triggers.upsert(trigger)
                next_fire = trigger.next_fire_at
        except Exception as exc:
            error = 'schedule-write:' + type(exc).__name__
            state = 'error'
        with self.db() as db:
            latest = self._load(db, record['id'])
            latest.update(schedule_error=error, schedule_state=state, next_fire_at=next_fire)
            return self._save(db, latest)

    def rearm_recurrence(self, commission_id, occurrence, schedule_revision, cadence_hash):
        with self.db() as db:
            record = self._load(db, commission_id)
        if record['cadence']['kind'] != 'recurrence' or not record['enabled']:
            return record
        if record['schedule_revision'] != schedule_revision or _cadence_hash(record['cadence']) != cadence_hash:
            return record
        try:
            after = float(occurrence)
        except (TypeError, ValueError):
            raise CatalogError('Scheduled recurrence occurrence is invalid') from None
        return self._sync(record, after=after)

    def runs(self, commission_id):
        self.get(commission_id)
        with self.db() as db:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT record FROM creative_commission_runs WHERE commission_id=? ORDER BY rowid DESC', (commission_id,))]
        return {'items': rows}

    def _feedback_context(self, db, commission_id):
        rows = [json.loads(row[0]) for row in db.execute(
            'SELECT record FROM creative_commission_feedback WHERE commission_id=? ORDER BY rowid DESC LIMIT ?',
            (commission_id, MAX_FEEDBACK_CONTEXT * 2))]
        live = [row for row in rows if not row['deleted']][:MAX_FEEDBACK_CONTEXT]
        return [{'id': row['id'], 'revision': row['revision'], 'rating': row['rating'], 'note': row['note'],
                 'tags': row['tags'], 'author': row['author'], 'output': row['output']} for row in reversed(live)]

    async def execute(self, commission_id, occurrence, schedule_revision=None, cadence_hash=None, trigger='schedule'):
        occurrence = text(str(occurrence), 200, True)
        with self.db() as db:
            commission = self._load(db, commission_id)
            if trigger == 'schedule' and not commission['enabled']:
                return {'status': 'skipped', 'reason': 'disabled', 'commission_id': commission_id}
            if schedule_revision is not None and schedule_revision != commission['schedule_revision']:
                return {'status': 'skipped', 'reason': 'stale_schedule', 'commission_id': commission_id}
            if cadence_hash is not None and cadence_hash != _cadence_hash(commission['cadence']):
                return {'status': 'skipped', 'reason': 'stale_schedule', 'commission_id': commission_id}
            row = db.execute('SELECT record FROM creative_commission_runs WHERE commission_id=? AND occurrence=?',
                             (commission_id, occurrence)).fetchone()
            run = json.loads(row[0]) if row else None
            if run and run['status'] in ('completed', 'submitted'): return run
            attempts = run['attempts'] if run else []
            if len(attempts) >= commission['max_attempts']:
                return {**run, 'status': 'exhausted'}
            feedback = self._feedback_context(db, commission_id)
            run_id = run['id'] if run else digest(commission_id + ':' + occurrence)
            if run is None:
                run = {'id': run_id, 'commission_id': commission_id, 'occurrence': occurrence, 'trigger': trigger,
                       'status': 'running', 'project_id': None, 'outputs': [], 'dispatch_receipts': [],
                       'feedback_refs': [], 'attempts': [],
                       'created_at': now_iso(), 'updated_at': now_iso()}
                db.execute('INSERT INTO creative_commission_runs VALUES(?,?,?,?)',
                           (run_id, commission_id, occurrence, json.dumps(run, sort_keys=True)))
            attempt = len(attempts) + 1
        project_id = run.get('project_id')
        started = now_iso()
        receipt = None
        try:
            direction = self._direction()
            treatment = json.dumps({'ability': commission['target_ability'], 'brief': commission['brief'],
                                    'feedback': feedback}, ensure_ascii=False, sort_keys=True)
            if project_id:
                project = direction.get(project_id)
            else:
                project = direction.create({'request_id': 'commission-' + run['id'][:48], 'name': commission['name'],
                    'treatment': treatment, 'sources': commission['sources'], 'steps': commission['steps']})
                project_id = project['id']
                with self.db() as db:
                    current = json.loads(db.execute('SELECT record FROM creative_commission_runs WHERE id=?', (run['id'],)).fetchone()[0])
                    current['project_id'] = project_id; current['updated_at'] = now_iso()
                    db.execute('UPDATE creative_commission_runs SET record=? WHERE id=?', (json.dumps(current, sort_keys=True), run['id']))
            if project['status'] == 'draft':
                project = direction.control(project_id, {'revision': project['revision'], 'action': 'start'})
            for _ in range(len(commission['steps'])):
                if project['status'] == 'completed': break
                project = direction.advance(project_id, {'revision': project['revision']})
            if project['status'] != 'completed': raise CatalogError('Direction project did not complete its bounded plan', 409)
            outputs = [step['result'] for step in project['steps'] if step.get('result', {}).get('artifact_id')]
            receipt = None
            if commission.get('dispatch'):
                receipt = await self.dispatcher.submit(commission['target_ability'], commission['dispatch'], run['id'], attempt)
                if receipt['status'] in ('failed', 'external_unavailable'):
                    raise CatalogError('Ability dispatch ' + receipt['status'], 503 if receipt['status'] == 'external_unavailable' else 409)
                outputs.extend(row for row in receipt['artifact_refs'] if row.get('content_hash'))
            status = receipt['status'] if receipt else 'completed'
            entry = {'number': attempt, 'status': status, 'started_at': started, 'finished_at': now_iso(), 'error': ''}
            error = ''
        except Exception as exc:
            outputs = run.get('outputs', [])
            error = (receipt or {}).get('error_code') or 'execution-failed:' + type(exc).__name__
            status = 'exhausted' if attempt >= commission['max_attempts'] else 'failed'
            entry = {'number': attempt, 'status': status, 'started_at': started, 'finished_at': now_iso(), 'error': error}
        with self.db() as db:
            current = json.loads(db.execute('SELECT record FROM creative_commission_runs WHERE id=?', (run['id'],)).fetchone()[0])
            current.update(status=status, project_id=project_id, outputs=outputs,
                           feedback_refs=[{'id': item['id'], 'revision': item['revision']} for item in feedback], updated_at=now_iso())
            if receipt and not any(row['request_id'] == receipt['request_id'] for row in current.get('dispatch_receipts', [])):
                current.setdefault('dispatch_receipts', []).append(receipt)
            current['attempts'].append(entry)
            db.execute('UPDATE creative_commission_runs SET record=? WHERE id=?', (json.dumps(current, sort_keys=True), run['id']))
            return current

    async def retry(self, commission_id, run_id):
        with self.db() as db:
            row = db.execute('SELECT record FROM creative_commission_runs WHERE id=? AND commission_id=?',
                             (identifier(run_id), identifier(commission_id))).fetchone()
            if not row: raise CatalogError('Commission run not found', 404)
            run = json.loads(row[0])
            if run['status'] != 'failed': raise CatalogError('Only a retryable failed run can retry', 409)
        return await self.execute(commission_id, run['occurrence'], trigger=run['trigger'])

    def feedback(self, commission_id):
        self.get(commission_id)
        with self.db() as db:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT record FROM creative_commission_feedback WHERE commission_id=? ORDER BY rowid DESC', (commission_id,))]
        return {'items': rows}

    def react(self, commission_id, payload):
        keys(payload, {'run_id', 'author', 'output', 'rating', 'note', 'tags', 'revision'})
        author = identifier(payload.get('author')); run_id = identifier(payload.get('run_id'))
        output = payload.get('output')
        if not isinstance(output, dict): raise CatalogError('Feedback output is required')
        keys(output, {'artifact_id', 'artifact_version', 'content_hash'})
        normalized_output = {'artifact_id': identifier(output.get('artifact_id')),
            'artifact_version': integer(output.get('artifact_version')), 'content_hash': text(output.get('content_hash'), 64, True)}
        rating = payload.get('rating')
        if rating not in RATINGS: raise CatalogError('Feedback rating must be liked or disliked')
        tags = payload.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 20: raise CatalogError('Feedback tags are invalid')
        tags = list(dict.fromkeys(text(item, 40, True) for item in tags))
        identity = 'reaction-' + digest(':'.join((commission_id, run_id, normalized_output['artifact_id'], author)))[:48]
        with self.db() as db:
            run_row = db.execute('SELECT record FROM creative_commission_runs WHERE id=? AND commission_id=?', (run_id, commission_id)).fetchone()
            if not run_row: raise CatalogError('Commission run not found', 404)
            run = json.loads(run_row[0])
            linked = any(all(item.get(key) == value for key, value in normalized_output.items()) for item in run['outputs'])
            if not linked: raise CatalogError('Feedback output is not linked to this run', 409)
            prior = db.execute('SELECT record FROM creative_commission_feedback WHERE id=?', (identity,)).fetchone()
            old = json.loads(prior[0]) if prior else None
            if old and integer(payload.get('revision')) != old['revision']: raise CatalogError('Feedback changed; reload', 409)
            if not old and 'revision' in payload: raise CatalogError('New feedback does not accept a revision')
            stamp = now_iso()
            record = {'id': identity, 'commission_id': commission_id, 'run_id': run_id, 'author': author,
                'output': normalized_output, 'rating': rating, 'note': text(payload.get('note', ''), 2000), 'tags': tags,
                'revision': old['revision'] + 1 if old else 1, 'deleted': False, 'deleted_at': None,
                'created_at': old['created_at'] if old else stamp, 'updated_at': stamp}
            db.execute('INSERT OR REPLACE INTO creative_commission_feedback VALUES(?,?,?,?)',
                       (identity, commission_id, run_id, json.dumps(record, sort_keys=True)))
            return record

    def remove_reaction(self, commission_id, reaction_id, payload):
        keys(payload, {'revision', 'author'})
        with self.db() as db:
            row = db.execute('SELECT record FROM creative_commission_feedback WHERE id=? AND commission_id=?',
                             (identifier(reaction_id), identifier(commission_id))).fetchone()
            if not row: raise CatalogError('Commission feedback not found', 404)
            record = json.loads(row[0])
            if record['author'] != identifier(payload.get('author')): raise CatalogError('Feedback author does not match', 403)
            if integer(payload.get('revision')) != record['revision']: raise CatalogError('Feedback changed; reload', 409)
            record.update(revision=record['revision'] + 1, deleted=True, deleted_at=now_iso(), updated_at=now_iso())
            db.execute('UPDATE creative_commission_feedback SET record=? WHERE id=?', (json.dumps(record, sort_keys=True), record['id']))
            return record


class CommissionActionProvider(ActionProvider):
    def __init__(self, store=None):
        self.store = store or CommissionStore()

    @property
    def name(self): return ACTION_PROVIDER

    @property
    def display_name(self): return 'Run creative commission'

    async def execute(self, action_config, ctx: ActionContext, timeout=30):
        try:
            keys(action_config, {'commission_id', 'schedule_revision', 'cadence_hash'})
            occurrence = str(ctx.payload.get('scheduled_for') or ctx.payload.get('occurrence') or '')
            result = await self.store.execute(identifier(action_config.get('commission_id')), occurrence,
                schedule_revision=integer(action_config.get('schedule_revision')),
                cadence_hash=text(action_config.get('cadence_hash'), 64, True), trigger='schedule')
            if result.get('reason') not in ('disabled', 'stale_schedule'):
                self.store.rearm_recurrence(action_config['commission_id'], occurrence,
                    action_config['schedule_revision'], action_config['cadence_hash'])
            success = result.get('status') in ('completed', 'submitted', 'skipped')
            return ActionResult(success=success, stdout=json.dumps(result), error='' if success else result['attempts'][-1]['error'],
                                outcome='ran' if result.get('status') == 'completed' else 'skipped_noop' if success else 'failed')
        except CatalogError as exc:
            return ActionResult(False, error=str(exc), outcome='failed')


def create_provider(config=None):
    if config not in (None, {}): raise CatalogError('Creative commission provider accepts no configuration')
    return CommissionActionProvider()
