import hashlib
import io
import json
import struct

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.sdk.image import ImageGenModel
from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.images import ImageService
from gideon.workspace.capabilities.media.images_http import register_images
from gideon.workspace.capabilities.media.jobs import MediaJobs
from gideon.workspace.capabilities.media.jobs_http import register_jobs
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.loras import LoraCatalog
from gideon.workspace.capabilities.media.sketches import SketchError, SketchStore
from gideon.workspace.capabilities.media.tools import MediaToolProvider


def tensors(root, name='adapter.safetensors', base='base-model'):
    root.mkdir(parents=True, exist_ok=True)
    header = {'__metadata__': {'base_model': base, 'trigger_words': 'red bird'},
              'layer.lora_A.weight': {'dtype': 'F32', 'shape': [1, 2], 'data_offsets': [0, 8]},
              'layer.lora_B.weight': {'dtype': 'F32', 'shape': [2, 1], 'data_offsets': [8, 16]}}
    encoded = json.dumps(header).encode()
    encoded += b' ' * (-len(encoded) % 8)
    data = struct.pack('<Q', len(encoded)) + encoded + struct.pack('<ffff', 1, 2, 3, 4)
    path = root / name
    path.write_bytes(data)
    return path, data, header


def selection(item, scale=1):
    return {'id': item['id'], 'sha256': item['sha256'], 'scale': scale}


@pytest.fixture
def catalog(tmp_path):
    return LoraCatalog(tmp_path / 'models/loras')


def test_actual_tensor_container_discovery_hash_metadata_and_bounds(catalog):
    path, data, _ = tensors(catalog.root)
    item = catalog.get(path.name, 'base-model')
    assert item['id'] == path.name
    assert item['sha256'] == hashlib.sha256(data).hexdigest()
    assert item['bytes'] == len(data)
    assert item['tensor_count'] == 2
    assert item['base_model'] == 'base-model'
    assert item['trigger_words'] == 'red bird'
    assert item['compatibility'] == 'metadata_match'
    assert item['effect_verified'] is False
    assert 'path' not in item
    assert str(catalog.root) not in json.dumps(item)
    result = catalog.list('base-model')
    assert result['items'] == [item]
    assert result['invalid'] == []
    assert result['truncated'] is False
    assert result['effect_verified'] is False
    assert path.read_bytes() == data


def test_unknown_and_mismatched_metadata_never_claim_compatibility(catalog):
    path, _, _ = tensors(catalog.root)
    assert catalog.get(path.name)['compatibility'] == 'unknown'
    assert catalog.get(path.name, 'different')['compatibility'] == 'metadata_mismatch'
    unknown, _, _ = tensors(catalog.root, 'unknown.safetensors', base='')
    assert catalog.get(unknown.name, 'base-model')['compatibility'] == 'unknown'
    item = catalog.get(path.name)
    with pytest.raises(SketchError, match='metadata'):
        catalog.resolve(selection(item), 'different')
    with pytest.raises(SketchError, match='metadata'):
        catalog.resolve(selection(item), '')
    assert catalog.get(path.name)['effect_verified'] is False


def test_actual_staged_adapter_is_digest_pinned_and_original_unchanged(catalog, tmp_path):
    path, data, _ = tensors(catalog.root)
    item = catalog.get(path.name, 'base-model')
    destination = tmp_path / 'staged.safetensors'
    staged = catalog.stage(selection(item, -.5), 'base-model', destination)
    assert staged['path'] == str(destination)
    assert staged['sha256'] == item['sha256']
    assert staged['scale'] == -.5
    assert destination.read_bytes() == data
    assert path.read_bytes() == data
    path.write_bytes(data[:-4] + struct.pack('<f', 9))
    assert destination.read_bytes() == data
    assert catalog.get(path.name)['sha256'] != item['sha256']
    with pytest.raises(SketchError) as changed:
        catalog.stage(selection(item), 'base-model', tmp_path / 'changed.safetensors')
    assert changed.value.status == 409
    assert not (tmp_path / 'changed.safetensors').exists()


@pytest.mark.parametrize('adapter_id', ['../outside.safetensors', '/outside.safetensors', 'a/b.safetensors', '.hidden.safetensors', 'file.pt', '', None])
def test_public_ids_cannot_select_arbitrary_paths(catalog, adapter_id):
    with pytest.raises(SketchError):
        catalog.get(adapter_id)


def test_symlink_file_root_and_ancestor_escape_are_rejected(catalog, tmp_path):
    outside, _, _ = tensors(tmp_path / 'outside')
    catalog.root.mkdir(parents=True)
    (catalog.root / 'link.safetensors').symlink_to(outside)
    with pytest.raises(SketchError):
        catalog.get('link.safetensors')
    listing = catalog.list()
    assert listing['items'] == []
    assert listing['invalid'][0]['id'] == 'link.safetensors'
    root_link = tmp_path / 'root-link'
    root_link.symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(SketchError):
        LoraCatalog(root_link).list()
    home_link = tmp_path / 'home-link'
    home_link.symlink_to(catalog.root.parent, target_is_directory=True)
    with pytest.raises(SketchError):
        LoraCatalog(home_link / 'loras').get('link.safetensors')


def test_empty_absent_and_directory_files_are_distinct(catalog):
    assert catalog.list()['items'] == []
    assert not catalog.root.exists()
    catalog.root.mkdir(parents=True)
    (catalog.root / 'directory.safetensors').mkdir()
    (catalog.root / 'empty.safetensors').write_bytes(b'')
    result = catalog.list()
    assert result['items'] == []
    assert {row['id'] for row in result['invalid']} == {'directory.safetensors', 'empty.safetensors'}
    assert result['truncated'] is False


@pytest.mark.parametrize('change', ['dtype', 'offset', 'shape', 'overlap', 'layout', 'metadata', 'truncated', 'trailing', 'header'])
def test_structurally_invalid_tensor_files_are_reported_without_execution(catalog, change):
    path, data, header = tensors(catalog.root)
    if change == 'dtype':
        header['layer.lora_A.weight']['dtype'] = []
    elif change == 'offset':
        header['layer.lora_A.weight']['data_offsets'] = [0, 7]
    elif change == 'shape':
        header['layer.lora_A.weight']['shape'] = [True, 2]
    elif change == 'overlap':
        header['layer.lora_B.weight']['data_offsets'] = [0, 8]
    elif change == 'layout':
        header['ordinary.weight'] = header.pop('layer.lora_A.weight')
    elif change == 'metadata':
        header['__metadata__']['base_model'] = []
    else:
        path.write_bytes(data[:-1] if change == 'truncated' else data + b'X' if change == 'trailing' else struct.pack('<Q', 2**60) + b'{}')
    if change in ('dtype', 'offset', 'shape', 'overlap', 'layout', 'metadata'):
        encoded = json.dumps(header).encode()
        path.write_bytes(struct.pack('<Q', len(encoded)) + encoded + struct.pack('<ffff', 1, 2, 3, 4))
    result = catalog.list()
    assert result['items'] == []
    assert len(result['invalid']) == 1
    assert result['invalid'][0]['id'] == path.name
    assert result['effect_verified'] is False


def test_duplicate_header_keys_are_not_silently_accepted(catalog):
    catalog.root.mkdir(parents=True)
    encoded = b'{"__metadata__":{},"__metadata__":{}}'
    (catalog.root / 'duplicates.safetensors').write_bytes(struct.pack('<Q', len(encoded)) + encoded)
    with pytest.raises(SketchError):
        catalog.get('duplicates.safetensors')


def test_model_lora_support_is_opt_in_and_request_values_are_bounded(catalog):
    path, _, _ = tensors(catalog.root)
    item = catalog.get(path.name, 'base-model')
    provider = OpenAIImageProvider(provider_name='image', api_key='')
    service = ImageService(NativeArtifactProvider(catalog.root.parents[1] / 'artifacts'), selector=lambda: (provider, 'model'))
    prepared = service.prepare({'prompt': 'A bird', 'loras': [selection(item)]})
    assert prepared['loras'][0]['sha256'] == item['sha256']
    assert ImageGenModel('model').supports_lora is False
    assert ImageGenModel('model').lora_base_model == ''
    with pytest.raises(SketchError, match='LoRA'):
        service.validate_model(prepared, ImageGenModel('model'))
    model = ImageGenModel('model', supports_lora=True, lora_base_model='base-model')
    service.validate_model(prepared, model)
    for scale in (True, float('nan'), float('inf'), 3, -3, '1'):
        with pytest.raises(SketchError):
            service.prepare({'prompt': 'A bird', 'loras': [selection(item, scale)]})
    with pytest.raises(SketchError):
        service.prepare({'prompt': 'A bird', 'loras': [dict(selection(item), sha256='wrong')]})
    with pytest.raises(SketchError):
        service.prepare({'prompt': 'A bird', 'loras': [selection(item), selection(item)]})


@pytest.mark.asyncio
async def test_actual_http_tool_catalog_shares_scoped_files_and_rejects_scope(catalog):
    path, _, _ = tensors(catalog.root)
    artifacts = NativeArtifactProvider(catalog.root.parents[1] / 'artifacts')
    sketches = SketchStore(catalog.root.parents[1] / 'sketches.sqlite3', artifacts)
    jobs = MediaJobs(catalog.root.parents[1] / 'jobs.sqlite3', sketches)
    app = web.Application()
    register_jobs(app, jobs)
    register_images(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/api/capabilities/media/loras')
        assert response.status == 200
        inventory = await response.json()
        assert inventory['items'][0]['id'] == path.name
        assert inventory['items'][0]['compatibility'] == 'unknown'
        assert inventory['supports_lora'] is False
        assert inventory['effect_verified'] is False
        response = await client.get('/api/capabilities/media/loras/'+path.name)
        item = await response.json()
        assert item['sha256'] == inventory['items'][0]['sha256']
        assert str(catalog.root) not in json.dumps(item)
        response = await client.get('/api/capabilities/media/loras?home=other')
        assert response.status == 400
        provider = MediaToolProvider(sketches, MediaLibrary(artifacts))
        result = await provider.invoke('media_loras_get', {'adapter_id': path.name})
        assert result.success
        assert json.loads(result.output) == item
        result = await provider.invoke('media_loras_list', {'base_model': 'caller-selected'})
        assert not result.success
