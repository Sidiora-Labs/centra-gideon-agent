"""Successful native registry mutations reach a real owner WebSocket."""
import asyncio
import time
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.native import NativeTaskProvider, TaskRevisionConflict
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard import token_auth
from gideon.integrations.inbox_providers.native_source import get_dashboard_state, set_dashboard_state


@pytest.mark.asyncio
async def test_real_native_task_mutations_send_refresh_only_after_success(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path))
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS',raising=False)
    original=registry._providers.copy()
    registry._providers.clear();registry.register_provider(NativeTaskProvider())
    previous=get_dashboard_state()
    state=ConsoleState(sessions=None,start_time=time.time())
    set_dashboard_state(state)
    token_auth.use_ephemeral_secret(b'companion-task-owner');token_auth.revoke_all_sessions()
    owner=token_auth.generate_token('companion-owner',kind='desktop')
    app=web.Application(middlewares=[token_auth.token_auth_middleware(port=0)])
    app['port']=0;app['allowed_origins']=set()
    async def socket(request):
        ws=web.WebSocketResponse();await ws.prepare(request);state.register_ws(ws)
        try:
            async for message in ws:
                pass
        finally:state.unregister_ws(ws)
        return ws
    app.router.add_get('/api/ws',socket)
    client=TestClient(TestServer(app));await client.start_server()
    ws=await client.ws_connect('/api/ws',headers={'Authorization':'Bearer '+owner})
    async def refresh():
        assert await ws.receive_json(timeout=2) == {'type':'refresh','data':{'kinds':['tasks']}}
    async def quiet():
        # ping/pong is a real transport barrier: any earlier refresh arrives before pong.
        await ws.ping(b'barrier')
        with pytest.raises(asyncio.TimeoutError):
            await ws.receive_json(timeout=0.05)
    try:
        task=await registry.create_task(title='Original')
        await refresh()
        edited=await registry.update_task(task.id,title='Changed',expected_revision=task.updated_at)
        assert edited.title == 'Changed';await refresh()
        with pytest.raises(TaskRevisionConflict):
            await registry.update_task(task.id,title='Stale',expected_revision=task.updated_at)
        await quiet()
        assert await registry.update_task('missing-task',title='No change') is None;await quiet()
        comment=await registry.add_comment(task.id,'Actual comment','owner')
        assert comment is not None;await refresh()
        assert await registry.delete_comment(task.id,comment.id);await refresh()
        assert not await registry.delete_comment(task.id,comment.id);await quiet()
        assert await registry.delete_task(task.id);await refresh()
        assert not await registry.delete_task(task.id);await quiet()
        assert await registry.add_comment('missing-task','No comment') is None;await quiet()
        set_dashboard_state(None)
        assert await registry.create_task(title='Headless')
        await quiet()
    finally:
        await ws.close();await client.close();await state.close_all_ws()
        set_dashboard_state(previous)
        registry._providers.clear();registry._providers.update(original)
        token_auth.revoke_all_sessions();token_auth.use_persistent_secret()
