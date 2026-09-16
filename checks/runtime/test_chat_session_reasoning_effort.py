"""Tests for POST /api/chat/sessions/{session}/reasoning-effort endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.chat import api_chat_session_reasoning_effort
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def _make_app(state: ConsoleState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort",
        api_chat_session_reasoning_effort,
    )
    return app


def _mock_state(session: _ChatSession | None = None) -> ConsoleState:
    state = MagicMock(spec=ConsoleState)
    state._sessions = {}
    if session:
        state._sessions[session.key] = session
    state.push_sessions_update = MagicMock()
    state.sessions = MagicMock()
    state.sessions.reset = AsyncMock()
    return state


class TestChatSessionReasoningEffort:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
    async def test_set_valid_levels(self, level: str):
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                json={"reasoning_effort": level},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data == {"ok": True, "reasoning_effort": level}
            assert session.reasoning_effort == level
            state.sessions.reset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_to_default(self):
        session = _ChatSession("test")
        session.reasoning_effort = "high"
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                json={"reasoning_effort": ""},
            )
            assert resp.status == 200
            assert session.reasoning_effort == ""
            state.sessions.reset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_op_when_unchanged_skips_session_reset(self):
        session = _ChatSession("test")
        session.reasoning_effort = "medium"
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                json={"reasoning_effort": "medium"},
            )
            assert resp.status == 200
            state.sessions.reset.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_value",
        ["LOW", " low", "low ", "0", "; rm -rf /", "max --evil", "../etc"],
    )
    async def test_rejects_malformed_effort(self, bad_value: str):
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                json={"reasoning_effort": bad_value},
            )
            assert resp.status == 400
            assert session.reasoning_effort == ""
            state.sessions.reset.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value", ["low", "high", "max", "minimal", "xhigh", "extreme"]
    )
    async def test_accepts_any_backend_token(self, value: str):
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                json={"reasoning_effort": value},
            )
            assert resp.status == 200
            assert session.reasoning_effort == value

    @pytest.mark.asyncio
    async def test_rejects_non_string(self):
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                json={"reasoning_effort": 5},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_session_returns_404(self):
        state = _mock_state()
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/missing/reasoning-effort",
                json={"reasoning_effort": "low"},
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/reasoning-effort",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400


class TestValidateReasoningEffortPersistence:
    """Persistence-layer allowlist guard prevents subprocess arg injection
    via tampered metadata."""

    @pytest.mark.parametrize(
        "level", ["", "low", "medium", "high", "max", "minimal", "xhigh"]
    )
    def test_passes_through_wellformed(self, level: str):
        from gideon.interfaces.dashboard.chat_persistence import (
            _validate_reasoning_effort,
        )

        assert _validate_reasoning_effort(level) == level

    @pytest.mark.parametrize(
        "tampered",
        ["LOW", "; rm -rf /", "max --evil-flag", "../../../etc", " low"],
    )
    def test_discards_malformed(self, tampered: str):
        from gideon.interfaces.dashboard.chat_persistence import (
            _validate_reasoning_effort,
        )

        assert _validate_reasoning_effort(tampered) == ""

    def test_discards_non_string(self):
        from gideon.interfaces.dashboard.chat_persistence import (
            _validate_reasoning_effort,
        )

        assert _validate_reasoning_effort(5) == ""
        assert _validate_reasoning_effort(None) == ""
        assert _validate_reasoning_effort(["max"]) == ""
