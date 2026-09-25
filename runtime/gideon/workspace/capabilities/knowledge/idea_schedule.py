"""Opt-in idea exchange uses the existing interval trigger and action dispatcher."""

import json
import time
from dataclasses import replace
from gideon.automation.triggers.arm import arm
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.integrations.action_providers.base import ActionProvider, ActionResult
from .capture import CaptureError, request_key
from .ideas import serialized
from .reviews import packed


class IdeaSchedules:
    def __init__(self, ideas):
        self.ideas, self.home, self.db = ideas, ideas.home, ideas.db
        self.triggers = TriggerStore(self.home)

    @serialized
    def save(self, identity, body):
        if not isinstance(body, dict) or set(body) != {'request_id', 'revision', 'enabled', 'minutes'}:
            raise CaptureError('Idea schedule requires request_id, revision, enabled and minutes')
        request = request_key(body['request_id'])
        if type(body['minutes']) is not int or not 5 <= body['minutes'] <= 1440 or type(body['enabled']) is not bool or type(body['revision']) is not int:
            raise CaptureError('Schedule minutes must be5..1440 with boolean enabled and integer revision')
        payload = packed({'operation': 'schedule', 'id': identity, **body})
        prior = self.db.execute('SELECT * FROM capability_knowledge_idea_requests WHERE request_id=?', (request,)).fetchone()
        if prior:
            if prior['payload'] != payload:
                raise CaptureError('Idea request belongs to different input', 409)
            if prior['receipt']:
                return json.loads(prior['receipt'])
        self.ideas.guard.assert_scope()
        if body['enabled'] and not self.ideas.availability()['available']:
            raise CaptureError(self.ideas.availability()['reason'], 409)
        state = self.ideas.state(identity)
        if not prior:
            if state['revision'] != body['revision']:
                raise CaptureError('Idea list changed; reload schedule revision', 409)
            if self.ideas.get(identity)['status'] in ('owner_deleted', 'collection_deleted') and body['enabled']:
                raise CaptureError('Deleted idea binding cannot be scheduled', 409)
            if self.db.execute('SELECT 1 FROM capability_knowledge_idea_requests WHERE list_id=? AND receipt IS NULL', (identity,)).fetchone():
                raise CaptureError('Previous idea operation must be retried first', 409)
            state.update(revision=state['revision'] + 1, sync_enabled=body['enabled'], minutes=body['minutes'], trigger_id='knowledge-ideas:' + state['action_id'])
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('UPDATE capability_knowledge_ideas SET body=? WHERE id=?', (packed(state), identity))
                self.db.execute('INSERT INTO capability_knowledge_idea_requests VALUES (?,?,?,NULL)', (request, payload, identity))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        trigger = Trigger(id=state['trigger_id'], name='Idea-list vault exchange', kind='clock', enabled=state['sync_enabled'], created_by='knowledge-ideas',
            spec={'kind': 'interval', 'interval_secs': state['minutes'] * 60}, workflow={'provider': 'knowledge-ideas-sync', 'config': {'id': state['action_id']}},
            capabilities={'providers': ['knowledge-ideas-sync']}, delivery='none', failure_delivery='none', session='fresh', overlap='skip')
        existing = self.triggers.get(trigger.id)
        if existing and existing.trigger.spec == trigger.spec and existing.trigger.enabled == trigger.enabled:
            trigger = existing.trigger
        else:
            if existing:
                trigger = replace(existing.trigger, spec=trigger.spec, enabled=trigger.enabled, workflow=trigger.workflow)
            trigger.next_fire_at = arm(trigger, now=time.time()) if trigger.enabled else ''
            self.triggers.upsert(trigger)
        result = self.ideas.get(identity)
        self.db.execute('UPDATE capability_knowledge_idea_requests SET receipt=? WHERE request_id=? AND receipt IS NULL', (packed(result), request))
        self.db.commit()
        return result


class IdeaSyncActionProvider(ActionProvider):
    def __init__(self, schedules):
        self.schedules = schedules

    @property
    def name(self):
        return 'knowledge-ideas-sync'

    @property
    def display_name(self):
        return 'Synchronize an opted-in canonical idea list'

    async def execute(self, action_config, ctx, timeout=30):
        try:
            if not isinstance(action_config, dict) or set(action_config) != {'id'}:
                raise CaptureError('Idea sync action requires only its bound list id')
            row = self.schedules.db.execute("SELECT id FROM capability_knowledge_ideas WHERE json_extract(body,'$.action_id')=?", (action_config['id'],)).fetchone()
            if row is None:
                raise CaptureError('Idea action binding belongs to a different allocation', 404)
            identity = row[0]
            state = self.schedules.ideas.get(identity)
            if not state['sync_enabled']:
                raise CaptureError('Idea sync schedule is disabled', 409)
            stamp = int(float(ctx.payload.get('scheduled_for', time.time())))
            request = f'idea-clock-{identity}-{stamp}'
            prior = self.schedules.db.execute('SELECT payload FROM capability_knowledge_idea_requests WHERE request_id=?', (request,)).fetchone()
            expected_hash = json.loads(prior[0])['expected_hash'] if prior else state['hash']
            result = self.schedules.ideas.sync(identity, {'request_id': request, 'expected_hash': expected_hash})
            if result['outcome'] in ('conflict', 'owner_deleted', 'collection_deleted'):
                return ActionResult(success=False, error='Idea exchange requires review: ' + result['outcome'], exit_code=1)
            return ActionResult(success=True, stdout=packed(result), outcome='completed')
        except (ValueError, TypeError, OverflowError) as exc:
            return ActionResult(success=False, error=str(exc), exit_code=1)
