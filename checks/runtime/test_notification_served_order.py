from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard import state as state_module
from gideon.interfaces.dashboard.handlers.messaging import api_notifications
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace.notification_order import newest_first


@pytest.mark.asyncio
async def test_persisted_notifications_served_by_instant_after_app_filter(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    rows = [
        {'ts':'2026-10-07T09:00:00+02:00','title':'Older offset','created_by_app':'alpha'},
        {'ts':'invalid','title':'Unknown time','created_by_app':'alpha'},
        {'ts':'2026-10-07T07:30:00Z','title':'Equal first','created_by_app':'alpha'},
        {'ts':'2026-10-07T09:30:00+02:00','title':'Equal later write','created_by_app':'alpha','acked':True},
        {'ts':'2026-10-07T07:45:00Z','title':'Other app newest','created_by_app':'beta'},
    ]
    state_module._rewrite_notifications(rows)
    before=state_module._notifications_path().read_bytes()
    state=state_module.ConsoleState(sessions=SimpleNamespace(),start_time=0)
    assert state._notification_log==rows
    app=web.Application(middlewares=[token_auth_middleware(port=0)]);app['state']=state
    app.router.add_get('/notifications',api_notifications)
    async with TestClient(TestServer(app)) as client:
        owner=generate_token('owner',kind='browser')
        response=await client.get('/notifications',headers={'Authorization':'Bearer '+owner})
        assert response.status==200
        listed=await response.json()
        assert [n['title'] for n in listed['notifications']]==['Other app newest','Equal later write','Equal first','Older offset','Unknown time']
        assert listed['unread']==state.unread_count()==0
        token=generate_token('owner',app='alpha',kind='cli')
        response=await client.get('/notifications',params={'app_token':token},headers={'Authorization':'Bearer '+owner})
        assert response.status==200
        listed=await response.json()
        assert [n['title'] for n in listed['notifications']]==['Equal later write','Equal first','Older offset','Unknown time']
        assert listed['unread']==3
        assert state._notification_log==rows
        assert state_module._notifications_path().read_bytes()==before


def test_unknown_times_last_and_equal_numeric_times_last_written_first():
    rows=[{'ts':True,'title':'Boolean'},{'ts':float('nan'),'title':'NaN'},{'ts':1,'title':'First'},{'ts':1.0,'title':'Later'},{'ts':2,'title':'Newest'}]
    assert [r['title'] for r in newest_first(rows)]==['Newest','Later','First','NaN','Boolean']
