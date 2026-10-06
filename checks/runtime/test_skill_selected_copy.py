import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.skills import loader, marketplace, overlays, shipped
from gideon.interfaces.dashboard.handlers import skills, prompts


def text(value):
    return f'---\nname: sample\ndescription: A sample skill\n---\n{value}\n'


@pytest.fixture
async def copies(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('GIDEON_HOME', str(home))
    project = tmp_path / 'project'
    monkeypatch.setenv('GIDEON_PROJECT_DIR', str(project))
    roots = [project / 'skills', home / 'skills']
    for root, value in zip(roots, ['Project source', 'Home source']):
        marketplace.install_skill_files([{'path':'SKILL.md','contents':text(value)}], 'sample', root / 'namespace')
    monkeypatch.setattr('gideon.engine.agent._all_skill_paths', lambda: [str(r) for r in roots])
    app = web.Application()
    app.router.add_get('/api/skills', skills.api_skills_list)
    app.router.add_post('/api/skills/overlay/revert', skills.api_skill_overlay_revert)
    app.router.add_post('/api/skills/{name:.+}/bundled/{choice:update|keep}', skills.api_skill_bundled_choice)
    app.router.add_get('/api/skills/{name:.+}/files', skills.api_skill_files)
    app.router.add_post('/api/skills/{name:.+}/verify', skills.api_skill_verify)
    app.router.add_delete('/api/skills/{name:.+}', skills.api_skills_delete)
    app.router.add_get('/api/skills/{name:.+}', prompts.api_skill_detail)
    app.router.add_put('/api/skills/{name:.+}', prompts.api_skill_detail)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, roots
    await client.close()


async def listed(client):
    response = await client.get('/api/skills')
    assert response.status == 200
    return await response.json()


@pytest.mark.asyncio
async def test_selected_project_and_home_namespace_copy_are_distinct(copies):
    client, roots = copies
    rows = await listed(client)
    assert len(rows) == 2
    assert rows[0]['copy'] != rows[1]['copy']
    selected = rows[1]['copy']
    url = f'/api/skills/namespace/sample?copy={selected}'
    response = await client.get(url)
    document = await response.json()
    assert document['content'] == text('Home source')
    saved = await client.put(url, json={'content':text('Owner save'), 'revision':document['revision']})
    assert saved.status == 200
    assert (roots[0] / 'namespace/sample/SKILL.md').read_text() == text('Project source')
    stale = await client.put(url, json={'content':text('Stale save'), 'revision':document['revision']})
    assert stale.status == 409
    wrong = await client.get('/api/skills/namespace/sample?copy=unknown')
    assert wrong.status == 404
    delete = await client.delete(url)
    assert delete.status == 200
    assert (roots[0] / 'namespace/sample/SKILL.md').is_file()
    assert not (roots[1] / 'namespace/sample').exists()


@pytest.mark.asyncio
async def test_refinement_revert_repairs_exact_copies_and_preserves_author_edits(copies):
    client, roots = copies
    row = (await listed(client))[1]
    name = 'namespace/sample'
    identity = loader.overlay_identity(roots[1], name)
    overlays.apply_overlay(identity, procedure_md='First addition', created_at='2026-10-06')
    overlays.apply_overlay(identity, procedure_md='Second addition', created_at='2026-10-07')
    active = overlays.applied(identity)
    path = roots[1] / name / 'SKILL.md'
    path.write_text(text('Owner') + '\n\n' + active[0].block + '\n\n' + active[1].block + '\n')
    url = f'/api/skills/{name}?copy={row["copy"]}'
    document = await (await client.get(url)).json()
    assert document['recognized_copies']
    assert 'First addition' not in document['content']
    assert document['loaded_content'].count('First addition') == 1
    reverted = await client.post('/api/skills/overlay/revert',json={'name':name,'copy':row['copy'],'refinement':active[0].id})
    assert reverted.status == 200
    assert 'First addition' not in path.read_text()
    remaining = overlays.applied(identity)
    assert remaining[0].id == active[1].id
    document = await (await client.get(url)).json()
    assert 'First addition' not in document['loaded_content']
    assert 'Second addition' in document['loaded_content']
    assert 'Owner' in document['content']
    overlays.apply_overlay(identity, procedure_md='Concurrent accepted addition')
    stale = await client.put(url,json={'content':text('Old editor'), 'revision':document['revision']})
    assert stale.status == 409


@pytest.mark.asyncio
async def test_offered_update_is_bound_to_copy_and_digest(copies, monkeypatch, tmp_path):
    client, roots = copies
    origin = tmp_path / 'release'
    marketplace.install_skill_files([{'path':'SKILL.md','contents':text('Shipped')}], 'sample', origin / 'namespace')
    monkeypatch.setattr(shipped, '_roots', lambda: [origin])
    rows = await listed(client)
    row = rows[1]
    assert row['bundled_update']
    url = f'/api/skills/namespace/sample/bundled/update?copy={row["copy"]}'
    wrong = await client.post(url,json={'digest':'obsolete'})
    assert wrong.status == 409
    updated = await client.post(url,json={'digest':row['bundled_update']})
    assert updated.status == 200
    assert (roots[1] / 'namespace/sample/SKILL.md').read_text() == text('Shipped')
    assert (roots[0] / 'namespace/sample/SKILL.md').read_text() == text('Project source')
