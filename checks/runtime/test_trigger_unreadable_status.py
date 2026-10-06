"""Real trigger reads disclose corrupt stores without replacing their contents."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers import triggers
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.mark.asyncio
@pytest.mark.parametrize('broken', [b'{broken', b'\xff', b'{"triggers": {}}', b'{"triggers": [null]}'])
async def test_list_and_week_disclose_preserved_store_then_clear_after_recovery(tmp_path, monkeypatch, broken):
    import gideon.core.config.loader as loader
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.setattr(loader, 'config_dir', lambda: tmp_path)
    monkeypatch.setattr(triggers, 'config_dir', lambda: tmp_path)
    path = tmp_path / 'triggers.json'
    path.write_bytes(broken)
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    app = web.Application()
    app['state'] = state
    app.router.add_get('/api/triggers', triggers.api_triggers)
    app.router.add_get('/api/triggers/week', triggers.api_triggers_week)
    async with TestClient(TestServer(app)) as client:
        for route in ['/api/triggers?type=schedule', '/api/triggers/week']:
            response = await client.get(route)
            assert response.status == 200
            data = await response.json()
            notice = data['unreadable'][0]
            assert notice['file'] == str(path)
            assert 'cannot be listed or changed' in notice['said']
            assert 'copy is kept' in notice['said']
            assert 'Repair' in notice['remedy']
            assert path.read_bytes() == broken
        copies = list(tmp_path.glob('triggers.json.broken-*'))
        assert len(copies) == 1
        assert copies[0].read_bytes() == broken
        assert copies[0].stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match='unreadable'):
            with TriggerStore(tmp_path)._mutation() as mutation:
                mutation.changed = True
        assert path.read_bytes() == broken
        path.write_text(json.dumps({'version': 1, 'triggers': []}))
        for route in ['/api/triggers?type=schedule', '/api/triggers/week']:
            data = await (await client.get(route)).json()
            assert data['unreadable'] == []
        assert len(list(tmp_path.glob('triggers.json.broken-*'))) == 1


def test_missing_and_readable_empty_store_are_healthy(tmp_path):
    store = TriggerStore(tmp_path)
    assert store.unreadable_status() == []
    store.path.write_text('{"triggers": []}')
    assert store.unreadable_status() == []
