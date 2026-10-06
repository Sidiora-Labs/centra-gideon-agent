"""Authenticated ingress and canonical durable session lookups over real HTTP."""

import asyncio
import hashlib

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.chat_handlers import api_chat
from gideon.interfaces.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    save_session_to_history,
)
from gideon.interfaces.dashboard.chat_utils import _dequeue_next_message
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.security.approval_answer import (
    APP,
    OWNER,
    Principal,
    ingress_record,
    of_request,
)


def state_at(path):
    return ConsoleState(
        sessions=ConversationDirectory(AppConfig()),
        start_time=0,
        conversation_log=ConversationLog(base_dir=path),
    )


async def inspect_scope(request):
    state = request.app["state"]
    return web.json_response(
        {
            "actor": of_request(request).label,
            "write_blocked": _is_restricted_session(state, request),
            "read_blocked": _blocks_reads_session(state, request),
        }
    )


def application(state):
    app = web.Application(middlewares=[token_auth_middleware(port=8000)])
    app["state"] = state
    app.router.add_get("/scope", inspect_scope)
    app.router.add_post("/api/chat", api_chat)
    return app


@pytest.mark.asyncio
async def test_real_authenticated_queue_cannot_forge_actor_and_survives_row_restore(
    tmp_path,
):
    state = state_at(tmp_path)
    session = state.get_or_create_session("provenance")
    task = asyncio.create_task(asyncio.Event().wait())
    session.task = task
    try:
        async with TestClient(TestServer(application(state))) as client:
            response = await client.post(
                "/api/chat",
                headers={"Authorization": "Bearer " + generate_token("sir")},
                json={
                    "session": session.key,
                    "message": "own request",
                    "queue_mode": "queue",
                    "meta": {
                        "ingress": {"principal": {"kind": "app"}},
                        "source_user": "forged",
                    },
                },
            )
            assert response.status == 200, await response.text()
        queued_state = state_at(tmp_path)
        queued_restore = _rehydrate_session_from_history(queued_state, session.key)
        assert queued_restore._queue == session._queue
        text, consumed = _dequeue_next_message(session, merge_enabled=True)
        ingress = consumed[0]["meta"]["ingress"]
        assert ingress["principal"] == {"kind": "owner", "name": "sir", "tenant": ""}
        assert ingress["source_thread"] == "dashboard:provenance"
        assert ingress["source_digest"] == hashlib.sha256(b"own request").hexdigest()
        assert "source_user" not in consumed[0]["meta"]
        session.append("user", text, meta=consumed[0]["meta"])
        save_session_to_history(state, session, force=True)
        state._sessions.clear()
        restored = _rehydrate_session_from_history(state, session.key)
        assert restored._initiator == ingress["principal"]
        assert restored.messages[-1]["meta"]["ingress"] == ingress
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,read_blocked,write_blocked",
    [
        ("persistent", False, False),
        ("incognito", False, True),
        ("temporary", True, True),
    ],
)
async def test_authenticated_scope_live_and_durable_fail_closed(
    tmp_path, mode, read_blocked, write_blocked
):
    state = state_at(tmp_path)
    session = state.get_or_create_session("scope", memory_mode=mode)
    session._initiator = {"kind": "owner", "name": "sir", "tenant": ""}
    session.append(
        "user",
        "request",
        meta={
            "ingress": ingress_record(
                Principal(OWNER, "sir"), "dashboard:scope", "request"
            )
        },
    )
    async with TestClient(TestServer(application(state))) as client:
        token = generate_token("sir")
        headers = {
            "Authorization": "Bearer " + token,
            "X-Session-Key": "dashboard:scope",
        }
        result = await (await client.get("/scope", headers=headers)).json()
        assert (result["read_blocked"], result["write_blocked"]) == (
            read_blocked,
            write_blocked,
        )
        if mode == "persistent":
            save_session_to_history(state, session, force=True)
            state._sessions.clear()
            result = await (await client.get("/scope", headers=headers)).json()
            assert result["read_blocked"] is False
            wrong = {**headers, "Authorization": "Bearer " + generate_token("other")}
            assert (await (await client.get("/scope", headers=wrong)).json())[
                "read_blocked"
            ] is True
            path = state.conversation_log._path("dashboard:scope")
            path.write_text("{invalid")
            assert (await (await client.get("/scope", headers=headers)).json())[
                "read_blocked"
            ] is True
        unauth = await client.get(
            "/scope", headers={"X-Session-Key": "dashboard:scope"}
        )
        assert unauth.status in (401, 403)
        session.lifecycle = "archived"
        state._sessions[session.key] = session
        assert (await (await client.get("/scope", headers=headers)).json())[
            "read_blocked"
        ] is True
        missing = {**headers, "X-Session-Key": "dashboard:missing"}
        assert (await (await client.get("/scope", headers=missing)).json())[
            "read_blocked"
        ] is True


def test_queue_never_merges_different_authenticated_initiators(tmp_path):
    session = state_at(tmp_path).get_or_create_session("mixed")
    for actor in [Principal(OWNER, "sir"), Principal(APP, "external")]:
        session.queue_append(
            actor.label,
            meta={"ingress": ingress_record(actor, "dashboard:mixed", actor.label)},
        )
    text, consumed = _dequeue_next_message(session, merge_enabled=True)
    assert text == "owner:sir" and len(consumed) == 1 and session.queue_depth == 1
