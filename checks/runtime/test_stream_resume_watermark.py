import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.chat_handlers import api_chat_session_detail
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.mark.asyncio
async def test_stream_detail_watermark_tracks_active_and_completed_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    state = ConsoleState(
        sessions=ConversationDirectory(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    session = _ChatSession("stream-resume")
    state._sessions[session.key] = session
    first_cursor = session.begin_stream()
    session.append("user", "count to three", "msg msg-u")
    first = session.next_stream_chunk()
    session.append("chunk", "one, ", "chunk", meta=first)
    second = session.next_stream_chunk()
    session.append("chunk", "two, ", "chunk", meta=second)
    session.task = asyncio.create_task(asyncio.Event().wait())

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)

    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get(f"/api/chat/sessions/{session.key}")
            active = await response.json()
            assert response.status == 200
            assert active["running"] is True
            assert active["stream_cursor"] == {**first_cursor, "stream_seq": 2}
            partial = next(message for message in active["messages"] if message["role"] == "streaming")
            assert partial["content"] == "one, two, "
            assert partial["meta"] == second

            third = session.next_stream_chunk()
            session.append("chunk", "three", "chunk", meta=third)
            response = await client.get(f"/api/chat/sessions/{session.key}")
            resumed = await response.json()
            assert resumed["stream_cursor"] == third
            partial = next(message for message in resumed["messages"] if message["role"] == "streaming")
            assert partial["content"] == "one, two, three"
            assert partial["meta"] == third

            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
            session.task = None
            session.append("assistant", "one, two, three", "msg msg-a", meta=third)
            response = await client.get(f"/api/chat/sessions/{session.key}")
            completed = await response.json()
            assert completed["running"] is False
            assert completed["stream_cursor"] == third
            assert [message["content"] for message in completed["messages"] if message["role"] == "assistant"] == [
                "one, two, three",
            ]
            terminal_partial = next(message for message in completed["messages"] if message["role"] == "streaming")
            assert terminal_partial["meta"] == third
    finally:
        if session.task is not None and not session.task.done():
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
