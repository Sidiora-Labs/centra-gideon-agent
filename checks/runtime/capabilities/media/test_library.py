import hashlib
import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.media.library_http import register_library
from gideon.workspace.capabilities.media.sketches import SketchError


@pytest.fixture
def library(tmp_path):
    return MediaLibrary(NativeArtifactProvider(tmp_path / 'artifacts'))


def picture(color='blue', fmt='PNG', size=(16, 12)):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, fmt)
    return output.getvalue()


def upload(library, request='first', **kwargs):
    values = dict(filename='blue.png', request_id=request, mime='image/png')
    values.update(kwargs)
    return library.import_image(picture(), **values)


def test_import_preserves_bytes_and_records_original_provenance(library):
    item = upload(library, name='Blue square')
    assert item['name'] == 'Blue square'
    assert item['kind'] == 'image'
    assert item['source'] == 'import'
    assert item['version'] == 1
    assert item['mime'] == 'image/png'
    assert item['provenance'] == {'original_filename': 'blue.png', 'sha256': hashlib.sha256(picture()).hexdigest()}
    assert library.artifacts.raw_bytes(item['id'])[0] == picture()
    reopened = MediaLibrary(NativeArtifactProvider(library.artifacts.root))
    assert reopened.get(item['id']) == item
    assert reopened.list({})['total'] == 1
    assert 'source_path' not in item
    assert 'content' not in item
    assert 'import_fingerprint' not in item['provenance']


def test_import_replay_and_conflicting_filename_and_bytes(library):
    first = upload(library)
    assert upload(library) == first
    with pytest.raises(SketchError) as conflict:
        upload(library, filename='different.png')
    assert conflict.value.status == 409
    with pytest.raises(SketchError) as conflict:
        library.import_image(picture('red'), filename='blue.png', request_id='first', mime='image/png')
    assert conflict.value.status == 409
    assert library.list({})['total'] == 1
    assert library.artifacts.raw_bytes(first['id'])[0] == picture()


def test_concurrent_imports_across_provider_instances_reuse_one_artifact(library):
    other = MediaLibrary(NativeArtifactProvider(library.artifacts.root))
    assert other.artifacts.mutation_lock is library.artifacts.mutation_lock
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(upload, [library, other]))
    assert results[0] == results[1]
    assert library.list({})['total'] == 1
    assert len(library.artifacts.list()) == 1


def test_metadata_tags_and_collection_are_canonical_and_keep_bytes(library):
    item = upload(library)
    updated = library.update(item['id'], {'expected_updated_at': item['updated_at'], 'name': 'Renamed', 'tags': ['blue', 'favorite', 'blue'], 'collection': 'Studies'})
    artifact = library.artifacts.get(item['id'])
    assert artifact.name == 'Renamed'
    assert artifact.tags == ['blue', 'favorite']
    assert artifact.collection == 'Studies'
    assert artifact.version == 1
    assert artifact.updated_at != item['updated_at']
    assert library.artifacts.raw_bytes(item['id'])[0] == picture()
    assert library.list({'collection': 'Studies'})['items'] == [updated]
    assert library.list({'tag': 'favorite'})['total'] == 1
    assert library.list({'q': 'renamed'})['total'] == 1
    cleared = library.update(item['id'], {'expected_updated_at': updated['updated_at'], 'tags': [], 'collection': ''})
    assert cleared['tags'] == []
    assert cleared['collection'] == ''
    assert library.list({'collection': 'Studies'})['total'] == 0


def test_existing_canonical_writer_invalidates_library_edit(library):
    item = upload(library)
    other = NativeArtifactProvider(library.artifacts.root)
    other.update(item['id'], tags=['externally edited'])
    with pytest.raises(SketchError) as conflict:
        library.update(item['id'], {'expected_updated_at': item['updated_at'], 'tags': ['stale']})
    assert conflict.value.status == 409
    assert library.get(item['id'])['tags'] == ['externally edited']


def test_two_concurrent_metadata_writers_have_one_winner(library):
    item = upload(library)
    other = MediaLibrary(NativeArtifactProvider(library.artifacts.root))
    def change(pair):
        service, name = pair
        try:
            return service.update(item['id'], {'expected_updated_at': item['updated_at'], 'name': name})
        except SketchError as exc:
            return exc.status
    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(change, [(library, 'First'), (other, 'Second')]))
    assert sum(isinstance(value, dict) for value in values) == 1
    assert 409 in values
    assert library.get(item['id'])['name'] in ('First', 'Second')


def test_facets_are_query_wide_not_page_local(library):
    for index in range(5):
        item = upload(library, request=f'item-{index}', name=f'Image {index}')
        library.update(item['id'], {'expected_updated_at': item['updated_at'], 'tags': ['all', f'tag-{index}'], 'collection': 'Group'})
    library.artifacts.create(name='Non-media', content='body', kind='text')
    video = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.1', '-an', '-c:v', 'libx264', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'], check=True, capture_output=True).stdout
    library.artifacts.create_binary(name='Video', data=video, mime='video/mp4', kind='video', tags=['all'])
    first = library.list({'limit': '2'})
    second = library.list({'limit': '2', 'offset': '2'})
    assert first['total'] == 6
    assert len(first['items']) == 2
    assert len(second['items']) == 2
    assert {item['id'] for item in first['items']}.isdisjoint(item['id'] for item in second['items'])
    assert first['facets']['kinds'] == {'video': 1, 'image': 5}
    assert first['facets']['tags']['all'] == 6
    assert first['facets']['collections'] == {'Group': 5}
    assert library.list({'kind': 'image', 'tag': 'tag-4'})['total'] == 1
    assert library.list({'q': 'no matches'})['facets'] == {'kinds': {}, 'tags': {}, 'collections': {}}
    assert library.list({'offset': '10'})['items'] == []
    assert library.list({'kind': 'video'})['items'][0]['name'] == 'Video'


@pytest.mark.parametrize('query', [{'limit': '0'}, {'limit': '101'}, {'offset': '-1'}, {'offset': '1.5'}, {'limit': 'nan'}, {'kind': 'text'}, {'home': '/tmp/other'}, {'q': 'x'*201}])
def test_invalid_filters_are_rejected(library, query):
    with pytest.raises(SketchError):
        library.list(query)
    assert library.list({})['total'] == 0


@pytest.mark.parametrize('changes', [{'name': ''}, {'name': 'x'*201}, {'tags': 'bad'}, {'tags': ['x']*17}, {'tags': ['']}, {'tags': ['x'*65]}, {'collection': '\nsecret'}, {'version': 99}, {'source_path': '/tmp/file'}, {'tags': [False]}])
def test_invalid_metadata_does_not_mutate_artifact(library, changes):
    item = upload(library)
    with pytest.raises(SketchError):
        library.update(item['id'], {'expected_updated_at': item['updated_at'], **changes})
    assert library.get(item['id']) == item


@pytest.mark.parametrize('filename', ['../escape.png', 'folder/file.png', 'C:\\file.png', '..', '', 'line\nname.png'])
def test_import_rejects_filesystem_paths(library, filename):
    with pytest.raises(SketchError):
        upload(library, filename=filename)
    assert library.artifacts.list() == []


def test_import_supported_encodings_and_mime_mismatch(library):
    for fmt, mime in [('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')]:
        data = picture(fmt=fmt)
        item = library.import_image(data, filename=f'image.{fmt.lower()}', request_id=fmt, mime=mime)
        assert library.artifacts.raw_bytes(item['id'])[0] == data
        assert item['mime'] == mime
    with pytest.raises(SketchError, match='content type'):
        upload(library, mime='image/jpeg')
    with pytest.raises(SketchError):
        library.import_image(b'not-image', filename='bad.png', request_id='bad', mime='image/png')
    with pytest.raises(SketchError) as oversized:
        library.import_image(b'x'*(16*1024*1024+1), filename='large.png', request_id='large', mime='image/png')
    assert oversized.value.status == 413
    assert library.list({})['total'] == 2


def test_detail_missing_non_media_and_cross_home_isolation(library, tmp_path):
    item = upload(library)
    other = MediaLibrary(NativeArtifactProvider(tmp_path / 'other'))
    assert other.list({})['items'] == []
    with pytest.raises(SketchError) as missing:
        other.get(item['id'])
    assert missing.value.status == 404
    document = library.artifacts.create(name='Text', content='body', kind='text')
    with pytest.raises(SketchError):
        library.get(document.slug)
    with pytest.raises(SketchError):
        library.get('../escape')


def test_binary_update_invalidates_metadata_cas(library):
    item = upload(library)
    library.artifacts.update_binary(item['id'], data=picture('red'), mime='image/png')
    with pytest.raises(SketchError) as conflict:
        library.update(item['id'], {'expected_updated_at': item['updated_at'], 'collection': 'stale'})
    assert conflict.value.status == 409
    latest = library.get(item['id'])
    assert latest['version'] == 2
    assert latest['raw_url'].endswith('?version=2')
    assert latest['provenance']['sha256'] == hashlib.sha256(picture()).hexdigest()


@pytest.mark.asyncio
async def test_http_import_list_filter_metadata_and_source_bytes(library):
    app = web.Application()
    register_library(app, library.artifacts)
    async with TestClient(TestServer(app)) as client:
        base = '/api/capabilities/media/library'
        response = await client.post(base+'/import', data=picture(), headers={'Content-Type': 'image/png', 'X-File-Name': quote('चित्र.png'), 'X-Request-ID': 'http'})
        assert response.status == 201
        item = await response.json()
        assert item['provenance']['original_filename'] == 'चित्र.png'
        assert library.artifacts.raw_bytes(item['id'])[0] == picture()
        response = await client.patch(base+'/'+item['id'], json={'expected_updated_at': item['updated_at'], 'collection': 'Album', 'tags': ['blue']})
        assert response.status == 200
        changed = await response.json()
        response = await client.get(base+'?collection=Album&tag=blue&limit=1')
        result = await response.json()
        assert result['items'] == [changed]
        assert result['facets']['collections'] == {'Album': 1}
        response = await client.get(base+'/'+item['id'])
        assert await response.json() == changed
        response = await client.patch(base+'/'+item['id'], json={'expected_updated_at': item['updated_at'], 'name': 'stale'})
        assert response.status == 409


@pytest.mark.asyncio
async def test_http_refuses_scope_injection_and_malformed_mutations(library):
    app = web.Application()
    register_library(app, library.artifacts)
    async with TestClient(TestServer(app)) as client:
        base = '/api/capabilities/media/library'
        item = upload(library)
        response = await client.get(base+'?account=someone-else')
        assert response.status == 400
        response = await client.get(base+'/'+item['id']+'?home=elsewhere')
        assert response.status == 400
        response = await client.patch(base+'/'+item['id'], data='bad')
        assert response.status == 400
        response = await client.patch(base+'/'+item['id'], json={'expected_updated_at': item['updated_at'], 'provider': 'other'})
        assert response.status == 400
        response = await client.post(base+'/import?runtime=other', data=picture())
        assert response.status == 400
        response = await client.post(base+'/import', data=picture(), headers={'Content-Type': 'image/png'})
        assert response.status == 400
        response = await client.get(base+'/absent')
        assert response.status == 404
        assert library.get(item['id']) == item
