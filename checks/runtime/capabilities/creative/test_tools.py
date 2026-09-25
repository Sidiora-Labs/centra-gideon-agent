import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.loader import load_factory
from gideon.extensions.providers.registry import ProviderRegistry
from gideon.integrations.tool_providers import registry
from gideon.integrations.tool_providers.base import RiskLevel
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.creative.moodboards import BoardStore
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider, SCHEMAS, create_provider


def invoke(provider, operation, **arguments):
    result = asyncio.run(provider.invoke('creative_' + operation, arguments))
    assert result.success, result.error
    return json.loads(result.output)


def create(provider, entity='ingredient', **fields):
    payload = {'request_id': str(uuid4()), 'title': 'Aster', **fields}
    if entity == 'ingredient':
        payload.setdefault('type', 'character')
    return invoke(provider, entity + '_create', payload=payload)


def test_manifest_factory_and_actual_tool_registry(tmp_path):
    root = Path(__file__).resolve().parents[4]
    manifest = AppManifest.from_json_file(root / 'runtime/gideon/extensions/apps/native/gideon-creative/app.json')
    extensions = ProviderRegistry()
    extensions.register(manifest)
    ext = extensions._extensions['gideon-creative']
    factory = load_factory(ext)
    assert factory is create_provider
    assert manifest.native
    assert manifest.provider.type == 'tool'
    assert manifest.provider.capabilities == ['creative']
    provider = CreativeToolProvider(tmp_path)
    registry.register_provider(provider)
    try:
        registered = registry.get_provider('gideon-creative')
        assert registered is provider
        assert registered.connected
        assert registered.display_name
        record = create(registered)
        assert IngredientStore(tmp_path).get(record['id']) == record
    finally:
        registry.unregister_provider(provider.name)
    assert registry.get_provider(provider.name) is None


def test_tool_definitions_complete_strict_and_approval_bearing(tmp_path):
    provider = CreativeToolProvider(tmp_path)
    definitions = asyncio.run(provider.list_tools())
    assert len(definitions) == 76
    assert {definition.name for definition in definitions} == set(SCHEMAS)
    for definition in definitions:
        assert definition.provider == 'gideon-creative'
        assert definition.parameters['additionalProperties'] is False
        mutates = definition.name.split('_', 2)[2] in {'create', 'update', 'restore', 'merge', 'draft', 'polish_propose', 'polish_promote', 'suggest', 'adopt', 'create_work', 'prepare', 'review', 'continuity_propose', 'continuity_accept', 'voice_configure'}
        assert definition.requires_approval is mutates
        assert definition.risk_level == (RiskLevel.CAUTION if mutates else RiskLevel.SAFE)
        assert definition.description
        assert not {'home', 'session_id', 'account', 'provider'} & definition.parameters['properties'].keys()
    definitions[0].parameters['properties'].clear()
    assert asyncio.run(provider.list_tools())[0].parameters['properties']


def test_ingredient_lifecycle_shared_store_and_revision_pages(tmp_path):
    provider = CreativeToolProvider(tmp_path)
    record = create(provider, tags=['Space'])
    id = record['id']
    detail = invoke(provider, 'ingredient_get', id=id)
    assert detail['title'] == 'Aster'
    assert detail['source_status'] == []
    edited = invoke(provider, 'ingredient_update', id=id, payload={'revision': 1, 'title': 'Aster II'})
    assert edited['revision'] == 2
    assert IngredientStore(tmp_path).get(id)['title'] == 'Aster II'
    listing = invoke(provider, 'ingredient_list', q='aster', type='character', tag='space', offset=0, limit=1)
    assert listing['total'] == 1
    assert listing['items'] == [edited]
    page = invoke(provider, 'ingredient_revisions', id=id, offset=1, limit=1)
    assert page == {'items': [record], 'total': 2, 'offset': 1, 'limit': 1}
    restored = invoke(provider, 'ingredient_restore', id=id, payload={'revision': 2, 'target_revision': 1})
    assert restored['title'] == 'Aster'
    assert restored['revision'] == 3
    reopened = CreativeToolProvider(tmp_path)
    assert invoke(reopened, 'ingredient_get', id=id)['revision'] == 3
    assert invoke(reopened, 'ingredient_list', q='absent')['total'] == 0


def test_board_lifecycle_real_artifact_provenance_and_export(tmp_path):
    provider = CreativeToolProvider(tmp_path)
    artifact_provider = NativeArtifactProvider(tmp_path / 'artifacts')
    artifact = artifact_provider.create(name='Reference letter', content='Blue evening', kind='markdown')
    sources = invoke(provider, 'board_sources', q='Reference')
    assert sources['items'][0]['id'] == artifact.slug
    ingredient = create(provider)
    groups = [{'id': 'group-one', 'title': 'Letters', 'cards': [
        {'id': 'card-one', 'artifact_id': artifact.slug, 'artifact_version': 1,
         'caption': 'Evening', 'colors': ['#AABBCC']}]}]
    record = create(provider, 'board', groups=groups, ingredient_ids=[ingredient['id']])
    id = record['id']
    card = record['groups'][0]['cards'][0]
    assert card['provenance']['title'] == artifact.name
    assert card['colors'] == ['#aabbcc']
    detail = invoke(provider, 'board_get', id=id)
    assert detail['source_status'][0]['missing'] is False
    assert detail['ingredient_status'] == [{'id': ingredient['id'], 'missing': False}]
    changed = invoke(provider, 'board_update', id=id, payload={'revision': 1, 'title': 'Night references'})
    assert changed['revision'] == 2
    assert invoke(provider, 'board_list', q='Night')['items'] == [changed]
    assert invoke(provider, 'board_export', id=id, revision=1) == record
    assert invoke(provider, 'board_revisions', id=id)['items'] == [changed, record]
    restored = invoke(provider, 'board_restore', id=id, payload={'revision': 2, 'target_revision': 1})
    assert restored['title'] == record['title']
    assert restored['groups'] == record['groups']
    assert BoardStore(tmp_path).export(id) == restored
    artifact_provider.delete(artifact.slug)
    assert invoke(provider, 'board_get', id=id)['source_status'][0]['missing'] is True
    assert invoke(provider, 'board_export', id=id) == restored
    assert invoke(provider, 'board_sources')['items'] == []


@pytest.mark.parametrize('entity', ['ingredient', 'board'])
def test_conflict_and_unknown_record_are_structured_errors(tmp_path, entity):
    provider = CreativeToolProvider(tmp_path)
    record = create(provider, entity)
    invoke(provider, entity + '_update', id=record['id'], payload={'revision': 1, 'title': 'Changed'})
    result = asyncio.run(provider.invoke('creative_' + entity + '_update', {
        'id': record['id'], 'payload': {'revision': 1, 'title': 'Stale'}}))
    assert not result.success
    assert result.metadata['status'] == 409
    assert invoke(provider, entity + '_get', id=record['id'])['title'] == 'Changed'
    missing = asyncio.run(provider.invoke('creative_' + entity + '_get', {'id': 'missing'}))
    assert not missing.success
    assert missing.metadata['status'] == 404


@pytest.mark.parametrize('field', ['home', 'account', 'provider', 'session_id', 'model', 'credential'])
def test_unknown_scope_fields_fail_without_side_effects(tmp_path, field):
    provider = CreativeToolProvider(tmp_path)
    result = asyncio.run(provider.invoke('creative_ingredient_list', {field: 'override'}))
    assert not result.success
    assert result.metadata['status'] == 400
    bad = asyncio.run(provider.invoke('creative_ingredient_create', {'payload': {
        'request_id': 'invalid', 'type': 'character', 'title': 'Injected', field: 'override'}}))
    assert not bad.success
    assert invoke(provider, 'ingredient_list')['total'] == 0
    with pytest.raises(CatalogError):
        create_provider({field: 'override'})


def test_missing_bad_arguments_and_revision_bounds(tmp_path):
    provider = CreativeToolProvider(tmp_path)
    record = create(provider)
    for name, arguments in [('unknown', {}), ('creative_ingredient_get', {}),
                            ('creative_board_create', []),
                            ('creative_ingredient_revisions', {'id': record['id'], 'limit': 101}),
                            ('creative_ingredient_revisions', {'id': record['id'], 'offset': -1})]:
        result = asyncio.run(provider.invoke(name, arguments))
        assert not result.success
        assert result.error
    assert invoke(provider, 'ingredient_revisions', id=record['id'])['total'] == 1


def test_runtime_homes_cannot_read_each_others_records_or_sources(tmp_path):
    one = CreativeToolProvider(tmp_path / 'one')
    two = CreativeToolProvider(tmp_path / 'two')
    record = create(one)
    board = create(one, 'board')
    for entity, value in [('ingredient', record), ('board', board)]:
        result = asyncio.run(two.invoke('creative_' + entity + '_get', {'id': value['id']}))
        assert not result.success
        assert result.metadata['status'] == 404
        assert invoke(two, entity + '_list')['total'] == 0
    assert one.ingredients.path != two.ingredients.path
    assert one.boards.path == one.ingredients.path
