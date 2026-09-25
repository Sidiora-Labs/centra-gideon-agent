import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.tasks.models import TaskStatus
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_reviews import register
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.action_providers.registry import register_action_provider
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.communications.store import PeopleStore
from gideon.workspace.capabilities.knowledge.capture import CaptureError, CaptureInbox
from gideon.workspace.capabilities.knowledge.reviews import ReviewService, window
from gideon.workspace.capabilities.knowledge.review_schedule import ReviewSchedules, ReviewActionProvider
from gideon.workspace.capabilities.knowledge.typed import BoundTasks
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools


@pytest.fixture
def reviews(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    service = ReviewService(store, home=tmp_path)
    yield service
    store.close()


def preview(service, **changes):
    return service.preview(**{'period': 'weekly', 'date': '2026-09-25', 'timezone': 'UTC', **changes})


def body(service, **changes):
    return {'request_id': 'review-save-original', 'period': 'weekly', 'date': '2026-09-25', 'timezone': 'UTC', 'preview_id': preview(service)['preview_id'], 'reflection': 'Follow up on the telescope booking.', **changes}


def schedule(service, **changes):
    return service.save({'request_id': 'review-schedule-original', 'period': 'daily', 'timezone': 'Europe/Berlin', 'time': '09:00', 'weekday': 0, 'enabled': True, **changes})


def note(service, title, stamp):
    identity = service.store.create_typed_item(item_type='note', title=title, content='Original source content')
    service.db.execute('UPDATE items SET created_at=? WHERE id=?', (stamp, identity))
    service.db.commit()
    return identity


def test_actual_obligations_and_activity_have_exact_citations(reviews):
    tasks = BoundTasks(reviews.home)
    overdue = asyncio.run(tasks.create_task(title='Book telescope', due='2026-09-20'))
    upcoming = asyncio.run(tasks.create_task(title='Visit observatory', due='2026-09-27'))
    undated = asyncio.run(tasks.create_task(title='Review equipment'))
    done = asyncio.run(tasks.create_task(title='Inspect mount'))
    done.status, done.updated_at = TaskStatus.DONE, '2026-09-23T15:00:00Z'
    tasks._write_task(done)
    cancelled = asyncio.run(tasks.create_task(title='Cancelled visit', due='2026-09-01'))
    cancelled.status = TaskStatus.CANCELLED
    tasks._write_task(cancelled)
    people = PeopleStore(reviews.home / 'capabilities/communications')
    missing = people.save({'name': 'No recorded history', 'ring': 'core', 'cadence_days': 3})
    late = people.save({'name': 'Overdue contact', 'ring': 'core', 'cadence_days': 3})
    people.record(late['id'], {'source': 'manual', 'external_id': 'contact-review-original', 'occurred_at': '2026-09-01T12:00:00Z', 'direction': 'outbound', 'summary': 'Last actual conversation'})
    people.save({'name': 'External contact', 'ring': 'external'})
    recent = note(reviews, 'Observatory plan', '2026-09-21T10:00:00Z')
    note(reviews, 'Outside window', '2026-09-18T23:59:59Z')
    result = preview(reviews)
    sections = result['sections']
    assert [row['source_id'] for row in sections['overdue_tasks']] == [overdue.id]
    assert [row['source_id'] for row in sections['upcoming_tasks']] == [upcoming.id]
    assert [row['source_id'] for row in sections['undated_tasks']] == [undated.id]
    assert [row['source_id'] for row in sections['completed_activity']] == [done.id]
    assert [row['source_id'] for row in sections['contacts_missing']] == [missing['id']]
    assert [row['source_id'] for row in sections['contacts_overdue']] == [late['id']]
    assert [row['source_id'] for row in sections['recent_notes']] == [recent]
    assert sections['overdue_tasks'][0]['source_link'] == '#/tasks?open=' + overdue.id
    assert sections['recent_notes'][0]['source_link'] == '#/knowledge/item/' + recent
    assert 'not an immutable completion event' in result['limitations'][0]
    assert sections['contacts_missing'][0]['detail'] == 'No recorded contact history'
    assert result['scanned'] == {'tasks': 5, 'contacts': 3, 'notes': 2, 'captures': 0}
    assert result['truncated'] == []


def test_timezone_window_is_half_open_and_dst_aware(reviews):
    included = note(reviews, 'Local midnight', '2026-03-28T23:00:00Z')
    excluded = note(reviews, 'Next midnight', '2026-03-29T22:00:00Z')
    result = preview(reviews, period='daily', date='2026-03-29', timezone='Europe/Berlin')
    assert result['window_start'] == '2026-03-29T00:00:00+01:00'
    assert result['window_end'] == '2026-03-30T00:00:00+02:00'
    assert [row['source_id'] for row in result['sections']['recent_notes']] == [included]
    assert excluded not in json.dumps(result['sections'])
    start, end = (datetime.fromisoformat(result[key]).timestamp() for key in ('window_start', 'window_end'))
    assert end - start == 23 * 3600
    weekly = preview(reviews, timezone='Pacific/Auckland')
    assert weekly['window_start'].startswith('2026-09-19T00:00:00')
    assert weekly['window_end'].startswith('2026-09-26T00:00:00')


def test_reflection_snapshot_replay_and_source_changes(reviews):
    identity = note(reviews, 'Original title', '2026-09-21T12:00:00Z')
    original = body(reviews)
    receipt = reviews.save(original)
    assert not receipt['scheduled']
    assert receipt['source_link'].endswith(receipt['destination_id'])
    destination = reviews.store.get_item(receipt['destination_id'])
    assert destination['item_type'] == 'note'
    assert original['reflection'] in destination['content']
    assert '#/knowledge/item/' + identity in destination['content']
    metadata = destination['file_metadata']
    assert metadata['review_snapshot']['preview_id'] == original['preview_id']
    assert preview(reviews)['preview_id'] == original['preview_id']
    reviews.store.update_item(identity, title='Corrected source title')
    assert reviews.save(original) == receipt
    assert 'Original title' in reviews.store.get_item(receipt['destination_id'])['content']
    with pytest.raises(CaptureError) as failure:
        reviews.save({**original, 'request_id': 'review-changed-source'})
    assert failure.value.status == 409
    with pytest.raises(CaptureError):
        reviews.save({**original, 'reflection': 'Conflicting retry'})
    assert reviews.list()['total'] == 1
    assert reviews.list()['items'] == [receipt]


def test_bound_reads_and_replay_refuse_new_foreign_writes(reviews, monkeypatch):
    original = body(reviews)
    receipt = reviews.save(original)
    schedules = ReviewSchedules(reviews)
    saved = schedule(schedules)
    foreign = reviews.home / 'foreign-allocation'
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    assert reviews.save(original) == receipt
    assert schedule(schedules) == saved
    assert reviews.list()['total'] == 1
    assert preview(reviews)['preview_id'] == original['preview_id']
    with pytest.raises(CaptureError) as failure:
        reviews.save({**original, 'request_id': 'review-foreign-save'})
    assert failure.value.status == 409
    with pytest.raises(CaptureError):
        schedule(schedules, request_id='review-foreign-schedule')
    assert not foreign.exists()
    monkeypatch.setenv('GIDEON_HOME', str(reviews.home))
    assert reviews.save({**original, 'request_id': 'review-foreign-save'})['destination_id']


def test_schedules_are_real_revisioned_clock_triggers(reviews):
    schedules = ReviewSchedules(reviews)
    first = schedule(schedules)
    trigger = schedules.triggers.get(first['trigger_id']).trigger
    assert trigger.kind == 'clock'
    assert trigger.spec == {'kind': 'cron', 'expr': '0 9 * * *', 'timezone': 'Europe/Berlin'}
    assert trigger.workflow == {'provider': 'knowledge-review', 'config': {'schedule_id': first['id']}}
    assert trigger.delivery == 'none'
    assert trigger.failure_delivery == 'none'
    assert first['next_fire_at']
    assert schedule(schedules) == first
    edited = schedule(schedules, request_id='review-weekly-edit', id=first['id'], revision=1, period='weekly', weekday=4, time='18:30')
    assert edited['revision'] == 2
    assert schedules.triggers.get(first['trigger_id']).trigger.spec['expr'] == '30 18 * * 5'
    assert schedule(schedules) == first
    assert schedules.get(first['id']) == edited
    with pytest.raises(CaptureError) as failure:
        schedule(schedules, request_id='review-stale-edit', id=first['id'], revision=1)
    assert failure.value.status == 409
    disabled = schedule(schedules, request_id='review-disable-edit', id=first['id'], revision=2, enabled=False)
    assert not disabled['enabled']
    assert disabled['next_fire_at'] == ''
    with pytest.raises(CaptureError):
        schedules.materialize(first['id'])
    assert reviews.list()['total'] == 0


def test_scheduled_materialization_previous_local_day_and_replay(reviews):
    schedules = ReviewSchedules(reviews)
    saved = schedule(schedules, period='weekly', timezone='Pacific/Auckland')
    stamp = datetime(2026, 9, 25, 14, tzinfo=timezone.utc).timestamp()
    receipt = schedules.materialize(saved['id'], stamp)
    assert receipt['date'] == '2026-09-25'
    assert receipt['period'] == 'weekly'
    assert receipt['scheduled']
    assert schedules.materialize(saved['id'], stamp) == receipt
    source = reviews.store.get_item(receipt['destination_id'])
    assert '(No reflection recorded)' in source['content']
    assert source['file_metadata']['scheduled']
    assert reviews.list()['total'] == 1


def test_actual_tick_dispatch_writes_canonical_note_and_execution_journal(reviews):
    from gideon.automation.triggers.service import tick
    from gideon.automation.schedule_history import ExecutionJournal
    from gideon.engine.gateway import RuntimeCoordinator
    schedules = ReviewSchedules(reviews)
    saved = schedule(schedules, timezone='UTC')
    register_action_provider(ReviewActionProvider(schedules))
    stamp = datetime.fromisoformat(saved['next_fire_at'].replace('Z', '+00:00')).timestamp()
    result = asyncio.run(tick(schedules.triggers, now=stamp + 1, persist=True, base_dir=reviews.home))
    assert len(result.fires) == 1
    fire = result.fires[0]
    assert fire.trigger.id == saved['trigger_id']
    assert reviews.list()['total'] == 0
    async def dispatch():
        runtime = RuntimeCoordinator(AppConfig(), no_dashboard=True, no_crons=True, no_open=True)
        await runtime._fire_store_trigger(fire.trigger, {'scheduled_for': fire.scheduled_for})
        return await ExecutionJournal(reviews.home).list_for_job(saved['trigger_id'])
    records, total = asyncio.run(dispatch())
    assert total == 1
    assert records[0]['status'] == 'success'
    assert reviews.list()['total'] == 1
    receipt = reviews.list()['items'][0]
    assert reviews.store.get_item(receipt['destination_id'])['item_type'] == 'note'
    assert records[0]['job_id'] == saved['trigger_id']
    assert records[0]['run_id']
    assert receipt['request_id'].startswith('scheduled-' + saved['id'])
    assert schedules.triggers.get(saved['trigger_id']).trigger.run_count == 1
    assert asyncio.run(tick(schedules.triggers, now=stamp + 2, persist=True, base_dir=reviews.home)).fires == []


def test_concurrent_review_writers_share_one_canonical_receipt(reviews):
    original = body(reviews)
    connections = [KnowledgeStore(str(reviews.home / 'knowledge.db')) for _ in range(4)]
    services = [ReviewService(store, reviews.home) for store in connections]
    barrier = Barrier(4)
    def save_one(service):
        barrier.wait()
        return service.save(original)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(save_one, services))
        assert all(receipt == receipts[0] for receipt in receipts)
        assert reviews.list()['total'] == 1
        assert reviews.db.execute("SELECT count(*) FROM items WHERE guid=?", ('review:' + original['request_id'],)).fetchone()[0] == 1
    finally:
        for store in connections:
            store.close()


def test_concurrent_schedule_edits_admit_one_revision(reviews):
    schedules = ReviewSchedules(reviews)
    first = schedule(schedules)
    connections = [KnowledgeStore(str(reviews.home / 'knowledge.db')) for _ in range(2)]
    services = [ReviewSchedules(ReviewService(store, reviews.home)) for store in connections]
    barrier = Barrier(2)
    def save_one(pair):
        index, service = pair
        barrier.wait()
        try:
            return schedule(service, request_id=f'concurrent-schedule-{index}', id=first['id'], revision=1, time=f'0{index + 1}:00')
        except CaptureError as error:
            return error.status
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(save_one, enumerate(services)))
        assert sum(isinstance(value, dict) for value in receipts) == 1
        assert 409 in receipts
        current = schedules.get(first['id'])
        assert current['revision'] == 2
        assert current['time'] in ('01:00', '02:00')
        assert schedules.triggers.get(first['trigger_id']).trigger.spec['expr'].startswith('0 ' + str(int(current['time'][:2])))
    finally:
        for store in connections:
            store.close()


@pytest.mark.parametrize('changes', [{'period': 'monthly'}, {'date': 'invalid'}, {'timezone': 'not/a-zone'}, {'date': '0001-01-01'}, {'date': '9999-12-31'}])
def test_invalid_calendar_is_rejected_without_writes(reviews, changes):
    with pytest.raises(CaptureError):
        preview(reviews, **changes)
    assert reviews.list()['total'] == 0


@pytest.mark.parametrize('changes', [{'time': '24:00'}, {'weekday': True}, {'enabled': 'yes'}, {'timezone': 'invalid'}, {'period': 'hourly'}, {'home': '/tmp/foreign'}, {'id': 'missing'}, {'id': [], 'revision': 1}])
def test_invalid_schedules_never_publish_triggers(reviews, changes):
    schedules = ReviewSchedules(reviews)
    with pytest.raises(CaptureError):
        schedule(schedules, **changes)
    assert schedules.list()['items'] == []
    assert reviews.db.execute('SELECT count(*) FROM capability_knowledge_review_schedule_requests').fetchone()[0] == 0


def test_native_review_consumers_enforce_session_policy(reviews):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = reviews.store
    provider = KnowledgeCapabilityTools(ActionServices(state=state, spawn_background=asyncio.create_task))
    token = set_current_session_key('dashboard:review-native')
    try:
        state._sessions['review-native'] = _ChatSession('review-native', memory_mode='persistent')
        arguments = {'period': 'daily', 'date': '2026-09-25', 'timezone': 'UTC'}
        result = asyncio.run(provider.invoke('knowledge_review_preview', arguments))
        assert result.success
        snapshot = json.loads(result.output)
        draft = {**arguments, 'preview_id': snapshot['preview_id'], 'reflection': 'Native reflection', 'request_id': 'native-review-save'}
        state._sessions['review-native'].memory_mode = 'incognito'
        assert not asyncio.run(provider.invoke('knowledge_review_save', draft)).success
        state._sessions['review-native'].memory_mode = 'persistent'
        saved = asyncio.run(provider.invoke('knowledge_review_save', draft))
        assert saved.success
        identity = json.loads(saved.output)['destination_id']
        assert 'Native reflection' in reviews.store.get_item(identity)['content']
        assert json.loads(asyncio.run(provider.invoke('knowledge_review_list', {})).output)['total'] == 1
        state._sessions['review-native'].memory_mode = 'temporary'
        assert not asyncio.run(provider.invoke('knowledge_review_preview', arguments)).success
        definitions = {tool.name: tool for tool in asyncio.run(provider.list_tools())}
        assert definitions['knowledge_review_save'].requires_approval
        assert definitions['knowledge_review_schedule_save'].requires_approval
        assert not definitions['knowledge_review_preview'].requires_approval
    finally:
        reset_current_session_key(token)


def test_http_preview_save_schedules_and_rejected_scope_selectors(reviews):
    async def journey():
        state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        state._knowledge_store = reviews.store
        app = web.Application()
        app['state'] = state
        register(app)
        root = '/api/capabilities/knowledge/reviews'
        async with TestClient(TestServer(app)) as client:
            response = await client.get(root + '/preview?period=weekly&date=2026-09-25&timezone=UTC')
            assert response.status == 200
            snapshot = await response.json()
            draft = {**body(reviews), 'preview_id': snapshot['preview_id']}
            response = await client.post(root, json=draft)
            assert response.status == 200
            receipt = await response.json()
            assert (await (await client.post(root, json=draft)).json()) == receipt
            assert (await (await client.get(root)).json())['total'] == 1
            for query in ('home=/tmp/other', 'model=x', 'date=2026-09-25&date=2026-09-24'):
                assert (await client.get(root + '/preview?' + query)).status == 400
            invalid = await client.post(root, json={**draft, 'runtime_id': 'other'})
            assert invalid.status == 400
            response = await client.post(root + '/schedules', json={'request_id': 'http-schedule-new', 'period': 'daily', 'timezone': 'UTC', 'time': '07:30', 'weekday': 0, 'enabled': True})
            assert response.status == 200
            schedule_row = await response.json()
            assert schedule_row['next_fire_at']
            assert (await (await client.get(root + '/schedules')).json())['items'][0]['id'] == schedule_row['id']
    asyncio.run(journey())
