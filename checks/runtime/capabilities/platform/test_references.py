import json
import subprocess
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.config.loader import config_dir
from gideon.interfaces.dashboard.handlers.capabilities_references import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/references'


def git(path, *args):
    result = subprocess.run(['git', '-c', 'user.name=Reference Test', '-c', 'user.email=reference@example.invalid', '-C', str(path), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(path, text):
    (path / 'changes.txt').write_text(text)
    git(path, 'add', '--', 'changes.txt')
    git(path, 'commit', '-m', text)
    return git(path, 'rev-parse', 'HEAD')


@pytest.fixture
def repository(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    upstream = tmp_path / 'upstream'
    workspace.mkdir()
    upstream.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(home))
    monkeypatch.setenv('GIDEON_WORKSPACE', str(workspace))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    git(upstream, 'init', '-b', 'main')
    first = commit(upstream, 'first upstream revision')
    git(workspace, 'clone', str(upstream), 'reference')
    return workspace, upstream, first


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    return app


async def authenticate(client):
    response = await client.get(PREFIX, params={'token': generate_token('reference-owner')})
    assert response.status == 200
    return await response.json()


async def track(client):
    response = await client.post(PREFIX, json={'name': 'Upstream', 'path': 'reference', 'branch': 'main'})
    assert response.status == 200, await response.text()
    return (await response.json())['references'][0]


@pytest.mark.asyncio
async def test_real_git_fetch_check_review_and_restart(repository):
    workspace, upstream, first = repository
    second = commit(upstream, 'second upstream revision')
    async with TestClient(TestServer(application())) as client:
        assert (await authenticate(client))['references'] == []
        row = await track(client)
        assert row['reviewed'] is None
        assert row['snapshot'] is None
        identifier = row['id']
        checked = await client.post(f'{PREFIX}/{identifier}/check', json={})
        assert checked.status == 200
        row = (await checked.json())['references'][0]
        assert row['reviewed'] is None
        assert row['snapshot']['head'] == second
        assert [item['sha'] for item in row['snapshot']['commits']] == [second, first]
        assert row['snapshot']['commits'][0]['subject'] == 'second upstream revision'
        assert row['checked_at']
        assert row['stale'] is False
        assert git(workspace / 'reference', 'rev-parse', 'HEAD') == first
        stale_review = await client.post(f'{PREFIX}/{identifier}/review', json={'head': first})
        assert stale_review.status == 409
        reviewed = await client.post(f'{PREFIX}/{identifier}/review', json={'head': second})
        assert reviewed.status == 200
        row = (await reviewed.json())['references'][0]
        assert row['reviewed'] == second
        assert row['snapshot']['commits'] == []
        third = commit(upstream, 'third upstream revision')
        checked = await client.post(f'{PREFIX}/{identifier}/check', json={})
        row = (await checked.json())['references'][0]
        assert row['reviewed'] == second
        assert [item['sha'] for item in row['snapshot']['commits']] == [third]
    async with TestClient(TestServer(application())) as restarted:
        row = (await authenticate(restarted))['references'][0]
        assert row['reviewed'] == second
        assert row['snapshot']['head'] == third
        assert row['path'] == 'reference'
        assert 'identity' not in row
        assert str(upstream) not in json.dumps(row)
        provider = create_provider()
        definition = next(tool for tool in await provider.list_tools() if tool.name == 'platform_reference_repositories')
        assert not definition.requires_approval
        result = await provider.invoke(definition.name, {})
        assert result.success
        assert json.loads(result.output)['references'][0] == row
        bad = await provider.invoke(definition.name, {'fetch': True})
        assert not bad.success
        manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
        assert definition.name in manifest['provider']['capabilities']
        removed = await restarted.delete(f'{PREFIX}/{identifier}')
        assert removed.status == 200
        assert (await removed.json())['references'] == []
        assert (workspace / 'reference/.git').is_dir()
        assert git(workspace / 'reference', 'rev-parse', 'HEAD') == first


@pytest.mark.asyncio
async def test_failed_fetch_preserves_timestamped_snapshot_and_prevents_review(repository):
    workspace, upstream, first = repository
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        row = await track(client)
        identifier = row['id']
        good = await client.post(f'{PREFIX}/{identifier}/check', json={})
        previous = (await good.json())['references'][0]
        upstream.rename(upstream.with_name('unavailable-upstream'))
        failed = await client.post(f'{PREFIX}/{identifier}/check', json={})
        assert failed.status == 200
        stale = (await failed.json())['references'][0]
        assert stale['snapshot'] == previous['snapshot']
        assert stale['reviewed'] is None
        assert stale['stale'] is True
        assert stale['checked_at'] >= previous['checked_at']
        assert 'Git operation failed' in stale['error']
        assert str(upstream) not in stale['error']
        refusal = await client.post(f'{PREFIX}/{identifier}/review', json={'head': first})
        assert refusal.status == 409
        assert (await authenticate(client))['references'][0]['reviewed'] is None
        upstream.with_name('unavailable-upstream').rename(upstream)
        recovered = await client.post(f'{PREFIX}/{identifier}/check', json={})
        assert (await recovered.json())['references'][0]['stale'] is False


@pytest.mark.asyncio
async def test_origin_change_and_nonancestor_history_cannot_reuse_cursor(repository):
    workspace, upstream, first = repository
    repo = workspace / 'reference'
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        row = await track(client)
        identifier = row['id']
        await client.post(f'{PREFIX}/{identifier}/check', json={})
        await client.post(f'{PREFIX}/{identifier}/review', json={'head': first})
        git(repo, 'remote', 'set-url', 'origin', str(upstream) + '-different')
        changed = await client.post(f'{PREFIX}/{identifier}/check', json={})
        assert changed.status == 409
        assert 'register a new reference' in (await changed.json())['error']
        assert (await authenticate(client))['references'][0]['reviewed'] == first
        git(repo, 'remote', 'set-url', 'origin', str(upstream))
        git(upstream, 'checkout', '--orphan', 'replacement')
        replacement = commit(upstream, 'replacement history')
        git(upstream, 'branch', '-M', 'main')
        force_rewritten = await client.post(f'{PREFIX}/{identifier}/check', json={})
        result = (await force_rewritten.json())['references'][0]
        assert result['stale'] is True
        assert result['reviewed'] == first
        assert result['snapshot']['head'] != replacement


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'path': '../upstream'}, {'path': '/tmp'}, {'branch': '--upload-pack=evil'}, {'branch': '../main'},
    {'branch': 'main..other'}, {'name': ''}, {'name': 'x' * 101}, {'path': 'missing'},
])
async def test_invalid_paths_and_refs_do_not_create_records(repository, changes):
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        response = await client.post(PREFIX, json={'name': 'Upstream', 'path': 'reference', 'branch': 'main', **changes})
        assert response.status == 400
        assert 'error' in await response.json()
        assert (await authenticate(client))['references'] == []
        assert not (config_dir() / 'reference-repositories.json').exists()


@pytest.mark.asyncio
async def test_duplicate_scope_auth_and_corrupt_state(repository):
    workspace, upstream, first = repository
    (workspace / 'escaped').symlink_to(upstream, target_is_directory=True)
    async with TestClient(TestServer(application())) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        assert (await client.post(PREFIX, json={})).status in {401, 403}
        assert (await client.get(PREFIX, params={'token': generate_token('owner', app='other')})).status in {401, 403}
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        escaped = await client.post(PREFIX, json={'name': 'Escape', 'path': 'escaped', 'branch': 'main'})
        assert escaped.status == 400
        row = await track(client)
        duplicate = await client.post(PREFIX, json={'name': 'Again', 'path': 'reference', 'branch': 'main'})
        assert duplicate.status == 409
        assert len((await authenticate(client))['references']) == 1
        path = config_dir() / 'reference-repositories.json'
        path.write_text('broken')
        assert (await client.get(PREFIX)).status == 503
        assert (await client.delete(f"{PREFIX}/{row['id']}")).status == 503
        assert path.read_text() == 'broken'


@pytest.mark.asyncio
async def test_git_metadata_escape_is_rejected(repository):
    workspace, upstream, first = repository
    directory = workspace / 'reference/.git'
    outside = workspace.parent / 'outside-git'
    directory.rename(outside)
    directory.write_text(f'gitdir: {outside}\n')
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        response = await client.post(PREFIX, json={'name': 'Escape', 'path': 'reference', 'branch': 'main'})
        assert response.status == 400
        assert 'Git metadata' in (await response.json())['error']
        assert (await authenticate(client))['references'] == []
        assert git(workspace / 'reference', 'rev-parse', 'HEAD') == first
