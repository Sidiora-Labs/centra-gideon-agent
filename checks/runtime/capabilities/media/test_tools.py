import io
import json
from pathlib import Path

import pytest
from PIL import Image
from jsonschema import Draft202012Validator

from gideon.sdk.tool import RiskLevel
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.sketches import SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


@pytest.fixture
def provider(tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / 'artifacts')
    return MediaToolProvider(SketchStore(tmp_path / 'sketches.sqlite3', artifacts), MediaLibrary(artifacts))


async def call(provider, name, args):
    result = await provider.invoke(name, args)
    assert result.success, result.error
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_manifest_and_discovery_are_executable(provider):
    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / 'runtime/gideon/extensions/apps/native/gideon-media/app.json').read_text())
    assert manifest['name'] == provider.name
    assert manifest['provider']['implementation'] == 'gideon.workspace.capabilities.media.tools:create_provider'
    assert manifest['native'] is True
    tools = await provider.list_tools()
    assert len(tools) == 31
    assert len({tool.name for tool in tools}) == 31
    assert provider.display_name == 'Gideon Media'
    for tool in tools:
        Draft202012Validator.check_schema(tool.parameters)
        assert tool.provider == 'gideon-media'
        assert tool.parameters['additionalProperties'] is False
        assert tool.description
        assert not tool.requires_approval
        if tool.name.endswith(('_get', '_list', '_history', '_refresh', '_capabilities', '_readiness', '_checkpoints')):
            assert tool.risk_level == RiskLevel.SAFE
        else:
            assert tool.risk_level == RiskLevel.CAUTION


@pytest.mark.asyncio
async def test_tool_sketch_draw_erase_undo_export_and_persistence(provider):
    sketch = await call(provider, 'media_sketch_create', {'width': 20, 'height': 20, 'request_id': 'tool'})
    assert sketch['revision'] == 1
    stroke = {'tool': 'draw', 'color': '#ff0000', 'width': 4, 'points': [[3, 10], [16, 10]]}
    saved = await call(provider, 'media_sketch_update', {'sketch_id': sketch['id'], 'revision': 1, 'strokes': [stroke]})
    assert saved['revision'] == 2
    assert saved['strokes'] == [stroke]
    assert await call(provider, 'media_sketch_get', {'sketch_id': sketch['id']}) == saved
    assert (await call(provider, 'media_sketch_list', {}))['items'] == [saved]
    exported = await call(provider, 'media_sketch_export', {'sketch_id': sketch['id'], 'revision': 2})
    raw, mime = provider.library.artifacts.raw_bytes(exported['artifact_id'])
    assert mime == 'image/png'
    assert Image.open(io.BytesIO(raw)).getpixel((10, 10)) == (255, 0, 0, 255)
    undone = await call(provider, 'media_sketch_update', {'sketch_id': sketch['id'], 'revision': 2, 'strokes': []})
    assert undone['revision'] == 3
    assert provider.sketches.get(sketch['id'])['strokes'] == []
    assert provider.library.artifacts.raw_bytes(exported['artifact_id'])[0] == raw


@pytest.mark.asyncio
async def test_tool_metadata_changes_existing_canonical_artifact(provider):
    sketch = await call(provider, 'media_sketch_create', {'width': 4, 'height': 4, 'request_id': 'library'})
    exported = await call(provider, 'media_sketch_export', {'sketch_id': sketch['id'], 'revision': 1})
    item = await call(provider, 'media_library_get', {'artifact_id': exported['artifact_id']})
    updated = await call(provider, 'media_library_update', {'artifact_id': item['id'], 'expected_updated_at': item['updated_at'], 'tags': ['tool-edited'], 'collection': 'Generated'})
    assert updated['tags'] == ['tool-edited']
    assert updated['collection'] == 'Generated'
    listed = await call(provider, 'media_library_list', {'tag': 'tool-edited', 'limit': 1})
    assert listed['items'] == [updated]
    assert listed['total'] == 1
    assert provider.library.artifacts.get(item['id']).collection == 'Generated'
    stale = await provider.invoke('media_library_update', {'artifact_id': item['id'], 'expected_updated_at': item['updated_at'], 'tags': []})
    assert not stale.success
    assert stale.metadata['status'] == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args', [
    ('media_sketch_list', {'home': '/tmp/other'}),
    ('media_library_list', {'provider': 'elsewhere'}),
    ('media_sketch_create', {'width': True, 'height': 10, 'request_id': 'bad'}),
    ('media_sketch_create', {'width': 10, 'height': 10}),
    ('media_sketch_update', {'sketch_id': 'none', 'revision': 1, 'strokes': [{'tool': 'bad'}]}),
    ('media_library_get', {'artifact_id': '../escape'}),
])
async def test_tools_reject_invalid_or_scope_selecting_arguments(provider, name, args):
    result = await provider.invoke(name, args)
    assert not result.success
    assert result.error
    assert result.recovery_hints
    assert provider.sketches.list() == []
    assert provider.library.list({})['items'] == []


@pytest.mark.asyncio
async def test_tools_unknown_missing_and_isolated_homes(provider, tmp_path):
    result = await provider.invoke('not_a_tool', {})
    assert not result.success
    assert result.error == 'Unknown media tool'
    sketch = await call(provider, 'media_sketch_create', {'width': 5, 'height': 5, 'request_id': 'one'})
    other_artifacts = NativeArtifactProvider(tmp_path / 'other/artifacts')
    other = MediaToolProvider(SketchStore(tmp_path / 'other/sketches.sqlite3', other_artifacts), MediaLibrary(other_artifacts))
    missing = await other.invoke('media_sketch_get', {'sketch_id': sketch['id']})
    assert not missing.success
    assert missing.metadata['status'] == 404
    assert (await call(other, 'media_sketch_list', {}))['items'] == []
