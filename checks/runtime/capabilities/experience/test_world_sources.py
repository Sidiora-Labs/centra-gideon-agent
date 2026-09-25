import asyncio
import json
import time
from pathlib import Path

import pytest
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.integrations.action_providers.services import ActionServices, get_action_services, set_action_services
from gideon.workspace.capabilities.experience.world_sources import extended_sources
from gideon.workspace.capabilities.experience import Conflict
from gideon.workspace.capabilities.workspace.processes import get_registry, close_registry
from test_worlds import world


@pytest.fixture
def bound_memory(tmp_path):
    prior = get_action_services()
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    archive = SemanticArchive(tmp_path / 'actual-memory.sqlite3')
    archive.init()
    memory = MemoryJournal(tmp_path)
    memory.vector_store = archive
    state._standalone_memory = memory
    set_action_services(ActionServices(state, asyncio.create_task))
    yield archive
    set_action_services(prior)
    archive.close()


def test_actual_memory_metadata_is_bound_filtered_and_read_only(tmp_path, bound_memory):
    archive = bound_memory
    assert archive.write_episodic('Remember garden planning\nPrivate detailed body', source='user')
    before = archive.get_episodic_list(limit=10)
    result = extended_sources(tmp_path)
    rows = [row for row in result['sources'] if row['kind']=='memory']
    assert len(rows) == 1
    assert rows[0]['id'] == before[0]['id']
    assert rows[0]['title'] == 'Remember garden planning'
    assert rows[0]['status'] == before[0]['created_at']
    assert rows[0]['url'].endswith(before[0]['id'])
    assert 'Private detailed body' not in json.dumps(result)
    assert 'embedding' not in json.dumps(result)
    assert 'memory' not in result['unavailable']
    assert archive.get_episodic_list(limit=10) == before
    another = tmp_path / 'uncreated'
    isolated = extended_sources(another)
    assert 'memory' in isolated['unavailable']
    assert not any(row['kind']=='memory' for row in isolated['sources'])
    assert not another.exists()
    assert archive.delete_episodic(before[0]['id'])
    deleted = extended_sources(tmp_path)
    assert not any(row['kind']=='memory' for row in deleted['sources'])
    assert 'memory' not in deleted['unavailable']


def test_missing_sources_do_not_create_ownership_or_memory(tmp_path):
    prior = get_action_services()
    set_action_services(None)
    try:
        before = list(tmp_path.rglob('*'))
        result = extended_sources(tmp_path)
        assert result['sources'] == []
        assert set(result['unavailable']) == {'memory','operations','peers'}
        assert list(tmp_path.rglob('*')) == before
    finally:
        set_action_services(prior)


@pytest.mark.asyncio
async def test_real_process_projection_does_not_take_owner_or_expose_command(tmp_path):
    root = tmp_path / 'capabilities/workspace'
    workspace = tmp_path / 'project'
    workspace.mkdir()
    registry = get_registry(root, allowed_roots=[workspace])
    try:
        row = await registry.start({'project_id':'Actual operation','workspace':str(workspace),'command':'printf private_output; sleep 20','request_id':'projection_process'})
        assert row['status'] == 'running'
        handle = registry.handles[row['id']]
        before = registry.get(row['id'])
        projection = extended_sources(tmp_path)
        source = next(item for item in projection['sources'] if item['kind']=='operations')
        assert source['id'] == row['id']
        assert source['title'] == 'Actual operation'
        assert source['status'] == 'recorded:running'
        assert source['url'].endswith(row['id'])
        assert 'command' not in source
        assert 'private_output' not in json.dumps(projection)
        assert str(workspace) not in json.dumps(projection)
        assert registry.handles[row['id']] is handle
        assert handle.returncode is None
        assert registry.get(row['id']) == before
        stopped = await registry.stop(row['id'], before['revision'])
        assert stopped['status'] == 'stopped'
        source = next(item for item in extended_sources(tmp_path)['sources'] if item['kind']=='operations')
        assert source['status'] == 'recorded:stopped'
    finally:
        await close_registry(root)
    persisted = next(item for item in extended_sources(tmp_path)['sources'] if item['kind']=='operations')
    assert persisted['status'] == 'recorded:stopped'
    assert persisted['id'] == row['id']


@pytest.mark.asyncio
async def test_actual_bound_memory_projects_and_deleted_source_stays_explicit(world, bound_memory):
    assert bound_memory.write_episodic('Actual memory for world', source='user')
    snapshot = await world.open('memory_projection', {})
    receipt = await world.mutate('memory_projection','project',{'kinds':['memory'],'expected_seq':snapshot['seq'],'request_id':'memory_projection'})
    assert receipt['complete']
    actual = await world.call('memory_projection')
    entities = list(actual['state']['entities'].values())
    assert len(entities) == 1
    source = entities[0]['comp']['gideon_source']
    assert source['kind'] == 'memory'
    assert source['title'] == 'Actual memory for world'
    assert source['id'] == bound_memory.get_episodic_list(limit=10)[0]['id']
    assert bound_memory.delete_episodic(source['id'])
    removed = await world.mutate('memory_projection','project',{'kinds':['memory'],'expected_seq':actual['seq'],'request_id':'removed_source'})
    assert removed['complete']
    actual = await world.call('memory_projection')
    assert list(actual['state']['entities'].values())[0]['comp']['gideon_source']['status'] == 'not_in_current_source_preview'
    prior = get_action_services()
    set_action_services(None)
    try:
        with pytest.raises(Conflict, match='unavailable'):
            await world.mutate('memory_projection','project',{'kinds':['memory'],'expected_seq':actual['seq'],'request_id':'unavailable'})
        assert (await world.call('memory_projection'))['seq'] == actual['seq']
    finally:
        set_action_services(prior)
