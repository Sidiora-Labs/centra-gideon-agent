import hashlib
import io
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_music_catalog import register
from gideon.workspace.artifacts.models import kind_for_mime, mime_for_ext
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.store import DomainError


def catalog_at(home):
    return MusicCatalog(home / 'music', NativeArtifactProvider(root=home / 'artifacts'))


def wav_bytes(frames=8000, rate=8000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b'\x00\x00' * frames)
    return output.getvalue()


def artifact(store, frames=8000):
    return store.artifacts.create_binary(name='Recorded practice fixture', kind='audio', mime='audio/wav', data=wav_bytes(frames), source='import')


def attach_body(track, art):
    return {'revision': track['revision'], 'artifact_ref': {'slug': art.slug, 'version': art.version},
            'source': {'kind': 'imported', 'label': 'Locally authored test recording', 'license': 'Test fixture'}}


def test_artist_album_track_relationships_and_order_survive_reopen(tmp_path):
    store = catalog_at(tmp_path)
    artist = store.create('artists', {'name': 'Ensemble', 'bio': 'Acoustic trio'})
    first = store.create('tracks', {'title': 'First', 'artist_id': artist['id'], 'notes': 'Take notes'})
    second = store.create('tracks', {'title': 'Second', 'artist_id': artist['id']})
    album = store.create('albums', {'title': 'Sessions', 'artist_id': artist['id'], 'track_ids': [first['id'], second['id']]})
    changed = store.update('albums', album['id'], {'revision': 1, 'track_ids': [second['id'], first['id']]})
    assert changed['track_ids'] == [second['id'], first['id']]
    assert changed['revision'] == 2
    reopened = catalog_at(tmp_path)
    assert reopened.get('artists', artist['id']) == artist
    assert reopened.get('tracks', first['id']) == first
    assert reopened.get('albums', album['id']) == changed
    assert first['selected_render_id'] is None
    assert first['renders'] == []
    assert artist['archived'] is False


def test_measured_duration_checksum_and_source_are_retained(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Practice recording'})
    art = artifact(store, 12000)
    attached = store.attach(track['id'], attach_body(track, art))
    render = attached['renders'][0]
    assert render['duration_seconds'] == 1.5
    assert render['sha256'] == hashlib.sha256(wav_bytes(12000)).hexdigest()
    assert render['artifact_ref'] == {'slug': art.slug, 'version': 1}
    assert render['source']['kind'] == 'imported'
    assert render['source']['model'] is None
    assert render['source']['job_id'] is None
    assert render['source']['attestation'] == 'user_supplied'
    assert attached['selected_render_id'] == render['id']
    assert catalog_at(tmp_path).get('tracks', track['id']) == attached
    assert store.artifacts.raw_bytes(art.slug, version=1) == (wav_bytes(12000), 'audio/wav')


def test_additional_renders_selection_and_metadata_edits_do_not_rewrite_audio(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Two takes'})
    one, two = artifact(store, 4000), artifact(store, 16000)
    first = store.attach(track['id'], attach_body(track, one))
    second = store.attach(track['id'], attach_body(first, two))
    assert second['selected_render_id'] == first['selected_render_id']
    selected = store.select(track['id'], {'revision': second['revision'], 'render_id': second['renders'][1]['id']})
    assert selected['selected_render_id'] == second['renders'][1]['id']
    edited = store.update('tracks', track['id'], {'revision': selected['revision'], 'title': 'Final take', 'notes': 'Use second'})
    assert edited['renders'] == selected['renders']
    assert edited['selected_render_id'] == selected['selected_render_id']
    assert store.artifacts.raw_bytes(one.slug, version=1)[0] == wav_bytes(4000)
    assert store.artifacts.raw_bytes(two.slug, version=1)[0] == wav_bytes(16000)
    assert catalog_at(tmp_path).get('tracks', track['id']) == edited


def test_attachment_retry_is_idempotent_but_provenance_cannot_change(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Retry'})
    body = attach_body(track, artifact(store))
    result = store.attach(track['id'], body)
    assert store.attach(track['id'], body) == result
    assert catalog_at(tmp_path).attach(track['id'], body) == result
    changed = {**body, 'source': {**body['source'], 'license': 'Different claim'}}
    with pytest.raises(DomainError) as err:
        store.attach(track['id'], changed)
    assert err.value.code == 'render_conflict'
    assert store.get('tracks', track['id']) == result


def test_simultaneous_attachment_retry_uses_one_render(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Concurrent'})
    body = attach_body(track, artifact(store))
    repositories = [catalog_at(tmp_path), catalog_at(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda current: current.attach(track['id'], body), repositories))
    assert results[0] == results[1]
    assert len(results[0]['renders']) == 1
    assert results[0]['revision'] == 2


def test_archive_filter_and_restore_preserve_album_links(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Blue room'})
    album = store.create('albums', {'title': 'Archive collection', 'track_ids': [track['id']]})
    archived = store.update('tracks', track['id'], {'revision': 1, 'archived': True})
    assert store.list('tracks') == []
    assert store.list('tracks', archived=True, q='BLUE') == [archived]
    assert store.list('tracks', archived=True, q='other') == []
    assert store.get('albums', album['id'])['track_ids'] == [track['id']]
    restored = store.update('tracks', track['id'], {'revision': 2, 'archived': False})
    assert store.list('tracks', q='room') == [restored]
    assert store.list('tracks', archived=True) == []


def test_metadata_revision_and_dangling_relationships_fail_atomically(tmp_path):
    store = catalog_at(tmp_path)
    artist = store.create('artists', {'name': 'One'})
    updated = store.update('artists', artist['id'], {'revision': 1, 'bio': 'Changed'})
    with pytest.raises(DomainError) as err:
        store.update('artists', artist['id'], {'revision': 1, 'name': 'Stale'})
    assert err.value.code == 'revision_conflict'
    assert store.get('artists', artist['id']) == updated
    with pytest.raises(DomainError):
        store.create('tracks', {'title': 'Dangling', 'artist_id': 'missing'})
    with pytest.raises(DomainError):
        store.create('albums', {'title': 'Dangling', 'track_ids': ['missing']})
    assert store.list('tracks') == []
    assert store.list('albums') == []


def test_duplicate_album_tracks_and_cross_collection_ids_rejected(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Track'})
    with pytest.raises(DomainError):
        store.create('albums', {'title': 'Repeated', 'track_ids': [track['id'], track['id']]})
    with pytest.raises(DomainError):
        store.create('tracks', {'title': 'Wrong kind', 'artist_id': track['id']})
    assert store.list('albums') == []
    assert store.list('tracks') == [track]


@pytest.mark.parametrize('kind,data', [
    ('unknown', {'title': 'x'}), ('artists', {'name': ''}), ('tracks', {}),
    ('albums', {'title': 'x', 'track_ids': 'not a list'}),
    ('albums', {'title': 'x', 'track_ids': [False]}),
    ('tracks', {'title': 'x', 'renders': []}), ('tracks', {'title': 'x', 'selected_render_id': 'fake'}),
    ('artists', {'name': 'x', 'archived': 'false'}), ('artists', {'name': 'x', 'revision': 1}),
    ('tracks', {'title': 'x', 'duration_seconds': 90}), ('tracks', {'title': 'x', 'path': '/tmp/elsewhere'}),
])
def test_invalid_metadata_cannot_create_partial_rows(tmp_path, kind, data):
    store = catalog_at(tmp_path)
    with pytest.raises(DomainError):
        store.create(kind, data)
    assert sum(len(store.list(collection)) for collection in ('artists', 'albums', 'tracks')) == 0


def test_generated_claim_is_explicitly_unavailable_without_real_job(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'No invented generation'})
    body = attach_body(track, artifact(store))
    body['source'] = {'kind': 'generated', 'model': 'unverified', 'job_id': 'missing'}
    with pytest.raises(DomainError) as err:
        store.attach(track['id'], body)
    assert err.value.status == 503
    assert err.value.code == 'generation_provenance_unavailable'
    assert store.get('tracks', track['id']) == track


def test_imported_source_cannot_smuggle_model_duration_or_job_claims(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Source boundary'})
    body = attach_body(track, artifact(store))
    with pytest.raises(DomainError):
        store.attach(track['id'], {**body, 'duration_seconds': 99})
    for key in ('model', 'job_id', 'duration_seconds'):
        with pytest.raises(DomainError):
            store.attach(track['id'], {**body, 'source': {**body['source'], key: 'claimed'}})
    assert store.get('tracks', track['id'])['renders'] == []


@pytest.mark.parametrize('data', [b'not audio', wav_bytes()[:-20], wav_bytes(0)])
def test_invalid_truncated_and_empty_audio_rejected(tmp_path, data):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Invalid input'})
    art = store.artifacts.create_binary(name='Invalid fixture', data=data, kind='audio', mime='audio/wav')
    with pytest.raises(DomainError) as err:
        store.attach(track['id'], attach_body(track, art))
    assert err.value.code == 'invalid_audio'
    assert store.get('tracks', track['id']) == track


def test_non_audio_and_missing_versions_cannot_be_attached(tmp_path):
    store = catalog_at(tmp_path)
    track = store.create('tracks', {'title': 'Type safety'})
    art = store.artifacts.create(name='Notes', kind='document', content='Text is not audio')
    with pytest.raises(DomainError):
        store.attach(track['id'], attach_body(track, art))
    audio = artifact(store)
    body = attach_body(track, audio)
    body['artifact_ref']['version'] = 99
    with pytest.raises(DomainError) as err:
        store.attach(track['id'], body)
    assert err.value.code == 'artifact_not_found'
    assert store.get('tracks', track['id'])['renders'] == []


def test_two_homes_do_not_share_catalog_or_canonical_audio(tmp_path):
    one, two = catalog_at(tmp_path / 'one'), catalog_at(tmp_path / 'two')
    track = one.create('tracks', {'title': 'Private track'})
    audio = artifact(one)
    other = two.create('tracks', {'title': 'Other'})
    with pytest.raises(DomainError):
        two.get('tracks', track['id'])
    with pytest.raises(DomainError) as err:
        two.attach(other['id'], attach_body(other, audio))
    assert err.value.code == 'artifact_not_found'
    assert one.get('tracks', track['id']) == track
    assert two.get('tracks', other['id']) == other


def test_pagination_filters_before_slicing(tmp_path):
    store = catalog_at(tmp_path)
    expected = [store.create('artists', {'name': 'Match ' + str(index)}) for index in range(5)]
    store.create('artists', {'name': 'Other'})
    first = store.list('artists', q='Match', limit=2)
    second = store.list('artists', q='Match', offset=2, limit=2)
    last = store.list('artists', q='Match', offset=4, limit=2)
    assert len(first) == len(second) == 2
    assert len(last) == 1
    assert {row['id'] for row in first + second + last} == {row['id'] for row in expected}
    assert store.list('artists', q='Match', offset=5) == []


@pytest.mark.parametrize('mime,extension', [('audio/wav', 'wav'), ('audio/mpeg', 'mp3'), ('audio/ogg', 'ogg'), ('audio/flac', 'flac'), ('audio/mp4', 'm4a')])
def test_audio_mime_is_canonical_binary_kind(mime, extension):
    assert kind_for_mime(mime) == 'audio'
    assert mime_for_ext(extension) == mime


def test_canonical_audio_bytes_version_roundtrip(tmp_path):
    provider = NativeArtifactProvider(root=tmp_path / 'artifacts')
    initial = provider.create_binary(name='Versioned audio', kind='audio', mime='audio/wav', data=wav_bytes(4000))
    provider.update_binary(initial.slug, data=wav_bytes(16000), mime='audio/wav')
    reopened = NativeArtifactProvider(root=tmp_path / 'artifacts')
    assert reopened.get(initial.slug).kind == 'audio'
    assert reopened.raw_bytes(initial.slug, version=1)[0] == wav_bytes(4000)
    assert reopened.raw_bytes(initial.slug, version=2)[0] == wav_bytes(16000)
    assert reopened.raw_bytes(initial.slug)[1] == 'audio/wav'


@pytest.mark.asyncio
async def test_actual_http_catalog_journey(tmp_path):
    store = catalog_at(tmp_path)
    app = web.Application()
    register(app, store)
    prefix = '/api/capabilities/music/catalog'
    async with TestClient(TestServer(app)) as client:
        response = await client.post(prefix + '/artists', json={'name': 'HTTP artist'})
        assert response.status == 201
        artist_row = (await response.json())['item']
        response = await client.post(prefix + '/tracks', json={'title': 'HTTP track', 'artist_id': artist_row['id']})
        track = (await response.json())['item']
        response = await client.post(prefix + f'/tracks/{track["id"]}/renders', json=attach_body(track, artifact(store)))
        assert response.status == 200
        attached = (await response.json())['item']
        assert attached['renders'][0]['duration_seconds'] == 1
        response = await client.patch(prefix + f'/tracks/{track["id"]}', json={'revision': 2, 'archived': True})
        assert response.status == 200
        filtered = await client.get(prefix + '/tracks?archived=true&q=HTTP')
        assert len((await filtered.json())['items']) == 1
        stale = await client.patch(prefix + f'/tracks/{track["id"]}', json={'revision': 1, 'title': 'Stale'})
        assert stale.status == 409
        malformed = await client.get(prefix + '/tracks?archived=maybe')
        assert malformed.status == 400
        missing = await client.get(prefix + '/tracks/missing')
        assert missing.status == 404
    assert catalog_at(tmp_path).get('tracks', track['id'])['renders'] == attached['renders']
