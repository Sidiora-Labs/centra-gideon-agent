from __future__ import annotations

import time
from urllib.parse import quote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_channel_history_resolves_and_unauthorized_handoff_does_not_write(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    from gideon.cognition.history import ConversationLog
    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.chat_channel import api_chat_session_handoff
    from gideon.interfaces.dashboard.chat_utils import persisted_history_key
    from gideon.interfaces.dashboard.state import ConsoleState

    history = ConversationLog(tmp_path / "history")
    history.init()
    channel_key = "telegram:acct:thread"
    history.append(channel_key, "user", "The deployment is blocked on migrations.")
    assert persisted_history_key(history, channel_key) == channel_key

    state = ConsoleState(
        ConversationDirectory(AppConfig.load()),
        time.time(),
        conversation_log=history,
    )
    session = state.get_or_create_session(channel_key)
    session.messages.append({"role": "user", "content": "What was blocking it?"})
    before = history._path(channel_key).read_bytes()

    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/handoff", api_chat_session_handoff
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            f"/api/chat/sessions/{quote(channel_key, safe='')}/handoff",
            json={"provider": "unregistered-handoff-destination"},
        )
        body = await response.json()

    assert response.status == 404
    assert body == {"error": "channel unavailable"}
    assert history._path(channel_key).read_bytes() == before
