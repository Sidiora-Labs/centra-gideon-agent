from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.cognition.planning import session as PS
from gideon.core.config.loader import AppConfig, config_dir
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard import chat_plan
from gideon.interfaces.dashboard.chat_persistence import resolve_session
from gideon.interfaces.dashboard.state import ConsoleState

KEY = "imported_claude_code_83c14d7c1d8746d5"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path
    return tmp_path


def state(home):
    return ConsoleState(
        ConversationDirectory(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "sessions"),
    )


def persist(home):
    log = ConversationLog(base_dir=home / "sessions")
    log.append(KEY, "user", "Plan Oct 23 to Nov 1")
    log.append(KEY, "assistant", "Here is a plan.")
    log.update_metadata(
        KEY,
        {
            "title": "Plan the Portugal trip",
            "import_source": "claude_code",
            "import_key": "session-123",
        },
    )


def app(console):
    application = web.Application()
    application["state"] = console
    application.router.add_get(
        "/api/chat/sessions/{session}/plan-session", chat_plan.api_chat_plan_session
    )
    return application


@pytest.mark.asyncio
async def test_an_imported_chat_answers_that_it_has_no_plan_session(home):
    persist(home)
    console = state(home)
    assert KEY not in console._sessions
    async with TestClient(TestServer(app(console))) as client:
        response = await client.get(f"/api/chat/sessions/{KEY}/plan-session")
        assert response.status == 200, await response.text()
        body = await response.json()
    assert body["session"] is None
    assert body["awaiting_step_id"] == ""
    assert console._sessions[KEY].messages[0]["content"] == "Plan Oct 23 to Nov 1"


@pytest.mark.asyncio
async def test_a_chat_back_from_restart_shows_its_waiting_plan(home):
    persist(home)
    before = state(home)
    chat = resolve_session(before, KEY)
    session, binding = chat_plan.activate(chat, running=False)
    step = session.steps[0]
    assert PS.submit_artifact(session, step.id, {"markdown": "1. Lisbon\n2. Porto"})
    chat_plan.write(session, binding)
    after = state(home)
    assert KEY not in after._sessions
    async with TestClient(TestServer(app(after))) as client:
        response = await client.get(f"/api/chat/sessions/{KEY}/plan-session")
        assert response.status == 200, await response.text()
        body = await response.json()
    assert body["awaiting_step_id"] == step.id
    assert body["session"]["steps"][0]["artifact"]["markdown"] == "1. Lisbon\n2. Porto"


@pytest.mark.asyncio
async def test_a_key_that_names_no_chat_is_still_not_found(home):
    async with TestClient(TestServer(app(state(home)))) as client:
        response = await client.get("/api/chat/sessions/nothing-here/plan-session")
        assert response.status == 404
        assert (await response.json())["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_another_home_cannot_resolve_the_persisted_chat(home):
    persist(home)
    other = home / "other-owner"
    async with TestClient(TestServer(app(state(other)))) as client:
        response = await client.get(f"/api/chat/sessions/{KEY}/plan-session")
        assert response.status == 404
