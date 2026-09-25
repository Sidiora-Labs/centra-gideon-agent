import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_ideas import register
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.action_providers.registry import register_action_provider
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.idea_format import parse, render, preview
from gideon.workspace.capabilities.knowledge.ideas import IdeaLists
from gideon.workspace.capabilities.knowledge.idea_schedule import IdeaSchedules, IdeaSyncActionProvider
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools

DOCUMENT = {'id': '902ee3d2-8e7e-481b-a23b-5b9741c6ca51', 'title': 'Observatory ideas', 'category': 'Exploration', 'status': 'draft',
            'created': '2025-09-25T12:00:00Z', 'modified': '2025-09-26T12:00:00Z', 'tags': ['idea-loom', 'astronomy'],
            'prompt': 'What could we observe?', 'help': 'Consider accessible equipment.\n1. This numbered line is help.', 'ideas': ['Observe Saturn', 'Photograph the Moon'], 'extra': {'owner': 'Personal notebook'}}


@pytest.fixture
def ideas(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    (tmp_path / 'config.json').write_text(json.dumps({'knowledge': {'vault_mode': 'two_way', 'vault_path': 'knowledge-vault'}}))
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    service = IdeaLists(store, tmp_path)
    yield service
    store.close()


def imported(service, document=None, **changes):
    content = render(document or DOCUMENT)
    return service.import_list({'request_id': 'idea-import-original', 'content': content, 'preview_id': preview(content)['preview_id'], 'expected_hash': '', **changes})


def sync(service, identity, request):
    return service.sync(identity, {'request_id': request, 'expected_hash': service.get(identity)['hash']})


def vault_file(service, source_id):
    return service.vault_root / service.store.vault_projection(source_id)['relpath']


def test_supported_markdown_roundtrip_preserves_order_fields_and_extra_metadata():
    content = render(DOCUMENT)
    assert parse(content) == DOCUMENT
    assert parse(content.replace('\n', '\r\n')) == DOCUMENT
    reviewed = preview(content)
    assert reviewed['document']['ideas'] == ['Observe Saturn', 'Photograph the Moon']
    assert reviewed['unsupported_fields'] == ['owner']
    assert reviewed['preview_id'] == preview(render(parse(content)))['preview_id']
    block_tags = content.replace('tags: ["idea-loom", "astronomy"]', 'tags:\n  - "idea-loom"\n  - "astronomy"')
    assert parse(block_tags)['tags'] == DOCUMENT['tags']
    aliases = content.replace('id:', 'uuid:', 1).replace('created:', 'createdAt:', 1).replace('modified:', 'updatedAt:', 1)
    assert parse(aliases) == DOCUMENT
    assert '1. This numbered line is help.' in parse(content)['help']


def test_import_uses_real_collection_and_fleeting_records(ideas):
    result = imported(ideas)
    assert result['id'] == DOCUMENT['id']
    assert result['revision'] == 1
    assert result['document'] == DOCUMENT
    assert [item['content'] for item in result['items']] == DOCUMENT['ideas']
    assert all(ideas.store.get_item(item['id'])['item_type'] == 'fleeting' for item in result['items'])
    source = ideas.store.get_item(result['source_id'])
    assert parse(source['content']) == DOCUMENT
    assert source['file_metadata']['original_at'] == DOCUMENT['created']
    assert ideas.store.get_collection(result['collection_id'])['kind'] == 'manual'
    actual = ideas.store.resolve_collection(result['collection_id'], limit=100)
    assert {item['id'] for item in actual} == {item['id'] for item in result['items']}
    assert result['collection_link'] == '#/knowledge?collection=' + result['collection_id']
    assert all(item['source_link'] == '#/knowledge/item/' + item['id'] for item in result['items'])
    assert imported(ideas) == result
    assert parse(ideas.export(result['id'])['content']) == DOCUMENT
    assert ideas.list()['vault']['available']
    assert len(ideas.list()['items']) == 1


def test_reviewed_reimport_preserves_identity_and_removes_membership_only(ideas):
    first = imported(ideas)
    removed_id = first['items'][1]['id']
    changed = {**DOCUMENT, 'ideas': ['Study Jupiter'], 'status': 'completed', 'modified': '2025-10-01T12:00:00Z'}
    second = imported(ideas, changed, request_id='idea-reimport-reviewed', expected_hash=first['hash'])
    assert second['source_id'] == first['source_id']
    assert second['collection_id'] == first['collection_id']
    assert second['items'][0]['id'] == first['items'][0]['id']
    assert second['revision'] == 2
    assert second['document'] == changed
    assert ideas.store.get_item(removed_id)['content'] == 'Photograph the Moon'
    assert len(ideas.store.resolve_collection(second['collection_id'])) == 1
    with pytest.raises(CaptureError) as failure:
        imported(ideas, changed, request_id='idea-stale-import', expected_hash=first['hash'])
    assert failure.value.status == 409
    assert imported(ideas) == first
    assert ideas.get(first['id'])['document'] == changed


def test_off_mode_import_export_does_not_enable_or_project(ideas):
    config = ideas.home / 'config.json'
    config.write_text(json.dumps({'knowledge': {'vault_mode': 'off'}}))
    result = imported(ideas)
    assert not ideas.availability()['available']
    assert parse(ideas.export(result['id'])['content']) == DOCUMENT
    assert not ideas.vault_root.exists()
    with pytest.raises(CaptureError) as failure:
        sync(ideas, result['id'], 'off-mode-sync')
    assert failure.value.status == 409
    assert json.loads(config.read_text())['knowledge']['vault_mode'] == 'off'
    assert not ideas.vault_root.exists()


def test_actual_vault_edit_updates_ordered_canonical_ideas_and_settles(ideas):
    first = imported(ideas)
    initial = sync(ideas, first['id'], 'first-vault-sync')
    assert initial['outcome'] == 'unchanged'
    path = vault_file(ideas, first['source_id'])
    assert path.is_file()
    original = path.read_text()
    assert 'Observe Saturn' in original
    path.write_text(original.replace('1. Observe Saturn', '1. Observe Jupiter'))
    absorbed = sync(ideas, first['id'], 'changed-vault-sync')
    assert absorbed['outcome'] == 'imported'
    assert absorbed['items'][0]['content'] == 'Observe Jupiter'
    assert ideas.store.get_item(first['items'][0]['id'])['content'] == 'Observe Jupiter'
    assert ideas.export(first['id'])['content'].count('1. Observe Jupiter') == 1
    sync(ideas, first['id'], 'settle-vault-sync')
    quiet = sync(ideas, first['id'], 'quiet-vault-sync')
    assert quiet['outcome'] == 'unchanged'
    assert quiet['vault_result']['units'] == 0
    assert len(ideas.store.resolve_collection(first['collection_id'])) == 2


def test_canonical_idea_edit_exports_and_both_side_conflict_retains_text(ideas):
    first = imported(ideas)
    sync(ideas, first['id'], 'local-first-sync')
    path = vault_file(ideas, first['source_id'])
    ideas.store.update_item(first['items'][0]['id'], content='Observe Mars')
    result = sync(ideas, first['id'], 'local-edit-sync')
    assert result['outcome'] == 'exported'
    assert 'Observe Mars' in path.read_text()
    sync(ideas, first['id'], 'local-settle-sync')
    path.write_text(path.read_text().replace('1. Observe Mars', '1. Remote Mercury'))
    ideas.store.update_item(first['items'][0]['id'], content='Local Venus')
    conflict = sync(ideas, first['id'], 'both-edited-sync')
    assert conflict['outcome'] == 'conflict'
    assert conflict['status'] == 'conflict'
    assert 'Remote Mercury' in path.read_text()
    assert ideas.store.get_item(first['items'][0]['id'])['content'] == 'Local Venus'
    assert ideas.store.vault_projection(first['source_id'])['conflict']
    assert 'sync_conflict' in path.read_text()


def test_owner_deleted_file_and_deleted_collection_never_resurrect(ideas):
    first = imported(ideas)
    sync(ideas, first['id'], 'delete-initial-sync')
    path = vault_file(ideas, first['source_id'])
    path.unlink()
    deleted = sync(ideas, first['id'], 'owner-delete-sync')
    assert deleted['outcome'] == 'owner_deleted'
    assert not path.exists()
    assert ideas.store.get_item(first['source_id'])
    assert ideas.store.get_collection(first['collection_id'])
    again = sync(ideas, first['id'], 'owner-delete-again')
    assert again['status'] == 'owner_deleted'
    assert not path.exists()
    ideas.store.delete_collection(first['collection_id'])
    assert ideas.get(first['id'])['status'] == 'collection_deleted'
    assert sync(ideas, first['id'], 'collection-delete-sync')['outcome'] == 'collection_deleted'
    assert not path.exists()
    assert all(ideas.store.get_item(item['id']) for item in first['items'])


def test_same_sync_request_replays_actual_receipt(ideas):
    first = imported(ideas)
    body = {'request_id': 'same-sync-request', 'expected_hash': first['hash']}
    receipt = ideas.sync(first['id'], body)
    path = vault_file(ideas, first['source_id'])
    stamp = path.stat().st_mtime_ns
    assert ideas.sync(first['id'], body) == receipt
    assert path.stat().st_mtime_ns == stamp
    with pytest.raises(CaptureError):
        ideas.sync(first['id'], {**body, 'expected_hash': 'different'})


def test_bound_home_config_and_projection_paths_refuse_foreign_mutation(ideas, monkeypatch):
    first = imported(ideas)
    foreign = ideas.home / 'foreign-runtime'
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    assert imported(ideas) == first
    assert ideas.availability()['available']
    assert ideas.export(first['id'])['content']
    with pytest.raises(CaptureError):
        sync(ideas, first['id'], 'foreign-sync-request')
    assert not foreign.exists()
    monkeypatch.setenv('GIDEON_HOME', str(ideas.home))
    (ideas.home / 'config.json').write_text(json.dumps({'knowledge': {'vault_mode': 'two_way', 'vault_path': str(foreign)}}))
    assert not ideas.availability()['available']
    with pytest.raises(CaptureError):
        sync(ideas, first['id'], 'changed-root-sync')
    assert not foreign.exists()


def test_real_concurrent_import_retries_create_one_collection(ideas):
    stores = [KnowledgeStore(str(ideas.home / 'knowledge.db')) for _ in range(4)]
    services = [IdeaLists(store, ideas.home) for store in stores]
    barrier = Barrier(4)
    def writer(service):
        barrier.wait()
        return imported(service)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(writer, services))
        assert all(row == results[0] for row in results)
        assert len(ideas.store.list_collections()) == 1
        assert ideas.db.execute("SELECT count(*) FROM items WHERE item_type='fleeting'").fetchone()[0] == 2
        assert ideas.db.execute('SELECT count(*) FROM capability_knowledge_idea_requests').fetchone()[0] == 1
    finally:
        for store in stores:
            store.close()


def test_interval_schedule_and_actual_clock_dispatch_journal(ideas):
    from gideon.automation.triggers.service import tick
    from gideon.automation.schedule_history import ExecutionJournal
    from gideon.engine.gateway import RuntimeCoordinator
    first = imported(ideas)
    schedules = IdeaSchedules(ideas)
    arguments = {'request_id': 'idea-schedule-first', 'revision': first['revision'], 'enabled': True, 'minutes': 5}
    configured = schedules.save(first['id'], arguments)
    assert configured['minutes'] == 5
    assert configured['sync_enabled']
    assert schedules.save(first['id'], arguments) == configured
    trigger = schedules.triggers.get(configured['trigger_id']).trigger
    assert trigger.spec['interval_secs'] == 300
    assert trigger.workflow['provider'] == 'knowledge-ideas-sync'
    assert trigger.delivery == 'none'
    provider = IdeaSyncActionProvider(schedules)
    register_action_provider(provider)
    stamp = datetime.fromisoformat(configured['next_fire_at'].replace('Z', '+00:00')).timestamp()
    async def journey():
        clock = await tick(schedules.triggers, now=stamp + 1, persist=True, base_dir=ideas.home)
        assert len(clock.fires) == 1
        fire = clock.fires[0]
        runtime = RuntimeCoordinator(AppConfig(), no_dashboard=True, no_crons=True, no_open=True)
        await runtime._fire_store_trigger(fire.trigger, {'scheduled_for': fire.scheduled_for})
        records, total = await ExecutionJournal(ideas.home).list_for_job(trigger.id)
        assert total == 1
        assert records[0]['status'] == 'success'
        repeated = await provider.execute(trigger.workflow['config'], ActionContext(event='clock', payload={'scheduled_for': fire.scheduled_for}))
        assert repeated.success
        return records
    records = asyncio.run(journey())
    assert records[0]['job_id'] == trigger.id
    assert vault_file(ideas, first['source_id']).is_file()
    current = ideas.get(first['id'])
    disabled = schedules.save(first['id'], {'request_id': 'idea-schedule-disable', 'revision': current['revision'], 'enabled': False, 'minutes': 5})
    assert not disabled['sync_enabled']
    assert disabled['next_fire_at'] == ''
    assert not asyncio.run(provider.execute(trigger.workflow['config'], ActionContext(event='clock'))).success


@pytest.mark.parametrize('mutation', [lambda s: s.replace('1. Observe Saturn', '3. Observe Saturn'), lambda s: s.replace('"draft"', '"invalid"'), lambda s: s.replace('"idea-loom"', '"other"'), lambda s: s.replace(DOCUMENT['id'], '../other'), lambda s: s + '\n## Ideas\n9. Invalid', lambda s: s.replace('owner: "Personal notebook"', 'owner: &anchor value\ncopy: *anchor')])
def test_invalid_portable_inputs_never_create_canonical_records(ideas, mutation):
    with pytest.raises(CaptureError):
        preview(mutation(render(DOCUMENT)))
    assert ideas.store.list_collections() == []
    assert ideas.list()['items'] == []


def test_native_idea_consumers_and_session_policy(ideas):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = ideas.store
    provider = KnowledgeCapabilityTools(ActionServices(state=state, spawn_background=asyncio.create_task))
    token = set_current_session_key('dashboard:ideas-native')
    try:
        state._sessions['ideas-native'] = _ChatSession('ideas-native', memory_mode='persistent')
        content = render(DOCUMENT)
        reviewed = asyncio.run(provider.invoke('knowledge_idea_preview', {'content': content}))
        assert reviewed.success
        args = {'request_id': 'native-idea-import', 'content': content, 'preview_id': json.loads(reviewed.output)['preview_id'], 'expected_hash': ''}
        state._sessions['ideas-native'].memory_mode = 'incognito'
        assert not asyncio.run(provider.invoke('knowledge_idea_import', args)).success
        state._sessions['ideas-native'].memory_mode = 'persistent'
        saved = asyncio.run(provider.invoke('knowledge_idea_import', args))
        assert saved.success
        receipt = json.loads(saved.output)
        exported = asyncio.run(provider.invoke('knowledge_idea_export', {'id': receipt['id']}))
        assert parse(json.loads(exported.output)['content']) == DOCUMENT
        assert len(json.loads(asyncio.run(provider.invoke('knowledge_idea_list', {})).output)['items']) == 1
        state._sessions['ideas-native'].memory_mode = 'temporary'
        assert not asyncio.run(provider.invoke('knowledge_idea_list', {})).success
        definitions = {row.name: row for row in asyncio.run(provider.list_tools())}
        assert definitions['knowledge_idea_import'].requires_approval
        assert definitions['knowledge_idea_sync'].requires_approval
        assert definitions['knowledge_idea_schedule'].requires_approval
    finally:
        reset_current_session_key(token)


def test_actual_http_preview_import_export_sync_and_invalid_selectors(ideas):
    async def journey():
        state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        state._knowledge_store = ideas.store
        app = web.Application()
        app['state'] = state
        register(app)
        root = '/api/capabilities/knowledge/ideas'
        async with TestClient(TestServer(app)) as client:
            content = render(DOCUMENT)
            response = await client.post(root + '/preview', json={'content': content})
            assert response.status == 200
            reviewed = await response.json()
            response = await client.post(root + '/import', json={'request_id': 'http-ideas-import', 'content': content, 'preview_id': reviewed['preview_id'], 'expected_hash': ''})
            assert response.status == 200
            receipt = await response.json()
            response = await client.get(root + '/' + receipt['id'] + '/export')
            assert parse((await response.json())['content']) == DOCUMENT
            response = await client.post(root + '/' + receipt['id'] + '/sync', json={'request_id': 'http-ideas-sync', 'expected_hash': receipt['hash']})
            assert response.status == 200
            assert (await response.json())['outcome'] == 'unchanged'
            assert (await client.get(root + '?home=/tmp/other')).status == 400
            assert (await client.post(root + '/preview', json={'content': content, 'model': 'other'})).status == 400
            assert (await client.get(root + '/missing')).status == 404
    asyncio.run(journey())


def test_identical_external_uuid_has_distinct_bound_action_identity(ideas, monkeypatch):
    first = imported(ideas)
    original_schedule = IdeaSchedules(ideas)
    configured = original_schedule.save(first['id'], {'request_id': 'original-bound-clock', 'revision': first['revision'], 'enabled': True, 'minutes': 5})
    original_trigger = original_schedule.triggers.get(configured['trigger_id']).trigger
    other_home = ideas.home / 'second-runtime'
    other_home.mkdir()
    (other_home / 'config.json').write_text(json.dumps({'knowledge': {'vault_mode': 'two_way'}}))
    monkeypatch.setenv('GIDEON_HOME', str(other_home))
    store = KnowledgeStore(str(other_home / 'knowledge.db'))
    try:
        other = IdeaLists(store, other_home)
        second = imported(other)
        schedules = IdeaSchedules(other)
        scheduled = schedules.save(second['id'], {'request_id': 'second-bound-clock', 'revision': second['revision'], 'enabled': True, 'minutes': 5})
        assert second['id'] == first['id']
        assert second['source_id'] != first['source_id']
        assert second['action_id'] != first['action_id']
        assert scheduled['trigger_id'] != configured['trigger_id']
        provider = IdeaSyncActionProvider(schedules)
        foreign = asyncio.run(provider.execute(original_trigger.workflow['config'], ActionContext(event='clock')))
        assert not foreign.success
        assert 'different allocation' in foreign.error
        assert not other.vault_root.exists()
        own = schedules.triggers.get(scheduled['trigger_id']).trigger
        context = ActionContext(event='clock', payload={'scheduled_for': 1790300000})
        fired = asyncio.run(provider.execute(own.workflow['config'], context))
        assert fired.success
        assert vault_file(other, second['source_id']).exists()
        assert asyncio.run(provider.execute(own.workflow['config'], context)).stdout == fired.stdout
        assert not ideas.vault_root.exists()
    finally:
        store.close()


def test_bound_projection_symlink_refuses_foreign_file_read(ideas):
    first = imported(ideas)
    sync(ideas, first['id'], 'symlink-before-sync')
    path = vault_file(ideas, first['source_id'])
    foreign = ideas.home / 'outside-vault.md'
    foreign.write_text('Private unrelated file')
    path.unlink()
    path.symlink_to(foreign)
    with pytest.raises(CaptureError) as failure:
        sync(ideas, first['id'], 'symlink-refused-sync')
    assert failure.value.status == 409
    assert foreign.read_text() == 'Private unrelated file'
    assert ideas.store.get_item(first['source_id'])['content'] == render(DOCUMENT)
    assert ideas.store.get_item(first['items'][0]['id'])['content'] == DOCUMENT['ideas'][0]


def test_schedule_revision_validation_and_disabled_mode_preserve_trigger(ideas):
    first = imported(ideas)
    schedules = IdeaSchedules(ideas)
    args = {'request_id': 'schedule-validation-first', 'revision': 1, 'enabled': True, 'minutes': 15}
    saved = schedules.save(first['id'], args)
    before = schedules.triggers.get(saved['trigger_id']).trigger
    for change in ({'revision': 1}, {'minutes': 4}, {'minutes': 1441}, {'minutes': True}, {'enabled': 'true'}, {'home': '/tmp/other'}):
        invalid = {**args, 'request_id': 'schedule-validation-' + str(len(str(change))), 'revision': saved['revision'], **change}
        with pytest.raises(CaptureError):
            schedules.save(first['id'], invalid)
    assert schedules.triggers.get(saved['trigger_id']).trigger.spec == before.spec
    (ideas.home / 'config.json').write_text(json.dumps({'knowledge': {'vault_mode': 'off'}}))
    with pytest.raises(CaptureError):
        schedules.save(first['id'], {**args, 'request_id': 'schedule-now-off', 'revision': saved['revision']})
    disabled = schedules.save(first['id'], {**args, 'request_id': 'schedule-disable-off', 'revision': saved['revision'], 'enabled': False})
    assert not disabled['sync_enabled']
    assert disabled['next_fire_at'] == ''
    assert schedules.save(first['id'], args) == saved
    assert not schedules.triggers.get(saved['trigger_id']).trigger.enabled


def test_format_bounds_and_unsupported_nested_metadata_are_explicit(ideas):
    excessive = {**DOCUMENT, 'ideas': ['Idea ' + str(index) for index in range(101)]}
    with pytest.raises(CaptureError):
        preview(render(excessive))
    for field, value in (('title', 'x' * 301), ('category', 'x' * 101), ('prompt', 'x' * 1001), ('help', 'x' * 10001)):
        with pytest.raises(CaptureError):
            preview(render({**DOCUMENT, field: value}))
    with pytest.raises(CaptureError):
        preview(render(DOCUMENT).replace('owner: "Personal notebook"', 'owner:\n  nested: unsupported'))
    with pytest.raises(CaptureError):
        preview('x' * 262145)
    assert ideas.store.list_collections() == []
    assert ideas.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0


def test_empty_list_is_portable_and_existing_items_are_not_deleted_on_reimport(ideas):
    first = imported(ideas)
    emptied = {**DOCUMENT, 'ideas': []}
    result = imported(ideas, emptied, request_id='empty-reviewed-import', expected_hash=first['hash'])
    assert result['items'] == []
    assert result['document']['ideas'] == []
    assert ideas.store.resolve_collection(first['collection_id']) == []
    assert all(ideas.store.get_item(item['id']) for item in first['items'])
    assert parse(ideas.export(first['id'])['content']) == emptied
    assert result['source_id'] == first['source_id']
    assert result['collection_id'] == first['collection_id']
    assert len(ideas.store.list_collections()) == 1


def test_refused_mode_sync_recovers_same_durable_request_after_explicit_enable(ideas):
    first = imported(ideas)
    config = ideas.home / 'config.json'
    config.write_text(json.dumps({'knowledge': {'vault_mode': 'off'}}))
    body = {'request_id': 'recover-off-mode-sync', 'expected_hash': first['hash']}
    with pytest.raises(CaptureError):
        ideas.sync(first['id'], body)
    assert not ideas.vault_root.exists()
    config.write_text(json.dumps({'knowledge': {'vault_mode': 'two_way'}}))
    receipt = ideas.sync(first['id'], body)
    assert receipt['outcome'] == 'unchanged'
    assert vault_file(ideas, first['source_id']).exists()
    assert ideas.sync(first['id'], body) == receipt
    assert ideas.db.execute('SELECT count(*) FROM capability_knowledge_idea_requests WHERE receipt IS NULL').fetchone()[0] == 0
