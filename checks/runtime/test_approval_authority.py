"""Owner-only approval answers and persisted requester identity."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.security.approval_answer import (
    YOU,
    agent,
    app,
    bridge,
    check,
    of_request,
    run,
    trigger,
)


@pytest.mark.parametrize(
    "asker",
    [
        agent("session-1"),
        app("calendar"),
        bridge("desktop"),
        run("run-1"),
        trigger("timer"),
    ],
)
def test_an_approval_raiser_cannot_answer_its_own_request(asker):
    assert check(asker, what="approval:request-1", asked_by=asker.label)


def test_only_authenticated_owner_request_is_an_owner_principal():
    assert of_request({"user": "owner-1"}).kind == "owner"
    assert of_request({"app": "calendar", "user": "owner-1"}).kind == "app"
    assert of_request({}).kind == "unknown"
    assert (
        of_request({"claimed_identity": {"user": "owner-1", "role": "owner"}}).kind
        == "unknown"
    )
    assert of_request({"authenticated": True}).kind == "unknown"


def test_a_workflow_run_cannot_answer_its_own_gate():
    from gideon.automation.workflows.gate_policy import may_answer
    from gideon.automation.workflows.models import RunOrigin, WorkflowRun

    run_record = WorkflowRun(
        id="run-1",
        workflow_name="release",
        origin=RunOrigin(session_key="chat-1"),
    )
    assert may_answer(run_record, responder="run:run-1") == (
        False,
        "only the authenticated owner or a verified paired owner channel may answer",
    )
    assert may_answer(run_record, responder=YOU.label) == (True, "")


@pytest.mark.asyncio
async def test_self_answer_refusal_leaves_request_pending(tmp_path, monkeypatch):
    from gideon.cognition.history import ConversationLog
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.state import ConsoleState

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    state = ConsoleState(
        sessions=ConversationDirectory(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    future = asyncio.get_running_loop().create_future()
    session = state.get_or_create_session("chat-1")
    session.messages.append(
        {
            "role": "permission",
            "cls": json.dumps({"request_id": "request-1", "asked_by": "app:calendar"}),
        }
    )
    session._approval_futures["request-1"] = future

    assert not state.resolve_session_approval(
        session, "request-1", "approved", by=app("calendar")
    )
    assert not future.done()
    assert state.resolve_session_approval(session, "request-1", "approved", by=YOU)
    assert future.result() == "approved"


@pytest.mark.asyncio
async def test_bridge_surface_token_cannot_redeem_but_signed_owner_session_can(
    tmp_path, monkeypatch
):
    """Exercise the real bridge handler with bridge and dashboard authentication."""
    import secrets

    from gideon.integrations.inbound import bridge as bridge_module
    from gideon.interfaces.dashboard import token_auth

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    surface_token = secrets.token_urlsafe(48)
    monkeypatch.setenv("GIDEON_INBOUND_BRIDGE_TOKEN", surface_token)
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    (home / "config.json").write_text(
        json.dumps({"external_access": {"enabled": True, "bridge": {"enabled": True}}}),
        encoding="utf-8",
    )

    action = bridge_module._action("create_task")
    assert action is not None
    bridge_module._pending.clear()
    token = bridge_module._mint_confirmation(
        action, {"title": "approval authority integration"}
    )
    headers = {"Authorization": f"Bearer {surface_token}"}
    try:
        surface_app = web.Application()
        surface_app["state"] = None
        surface_app.router.add_post("/confirm", bridge_module.handle_confirm)
        async with TestClient(TestServer(surface_app)) as surface_client:
            surface_response = await surface_client.post(
                "/confirm", json={"confirm_token": token}, headers=headers
            )
            assert surface_response.status == 403
            assert bridge_module.pending_count() == 1

        owner_app = web.Application(
            middlewares=[token_auth.token_auth_middleware(port=19406)]
        )
        owner_app["state"] = None
        owner_app.router.add_post("/confirm", bridge_module.handle_confirm)
        owner_token = token_auth.generate_token("approval-owner", ttl_seconds=3600)
        async with TestClient(TestServer(owner_app)) as owner_client:
            owner_response = await owner_client.post(
                "/confirm",
                json={"confirm_token": token},
                headers=headers,
                cookies={"gideon_token_19406": owner_token},
            )
            assert owner_response.status == 200
            assert (await owner_response.json())["status"] == "ok"
            assert bridge_module.pending_count() == 0
    finally:
        bridge_module._pending.clear()


@pytest.mark.asyncio
async def test_verified_telegram_answer_is_audited_and_dismisses_dashboard_once(
    tmp_path, monkeypatch
):
    from gideon.core.config.loader import AppConfig
    from gideon.engine.gateway import ApprovalExchange, ApprovalFlow, RuntimeCoordinator
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.channel_trust import allow_sender
    from gideon.integrations.llm.events import EVENT_PERMISSION_REQUEST, AgentEvent
    from gideon.integrations.telegram.delivery import PendingApproval
    from gideon.integrations.telegram.transport import TelegramTransport
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        token_auth_middleware,
    )
    from gideon.interfaces.dashboard.ws import api_ws
    from gideon.security.sel import sel

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    allow_sender("telegram", "12")

    config = AppConfig()
    state = ConsoleState(ConversationDirectory(config), time.time())
    coordinator = RuntimeCoordinator(config)
    coordinator.dashboard_state = state
    request_id = "approval:paired-channel:1"
    event = AgentEvent(
        kind=EVENT_PERMISSION_REQUEST,
        request_id=request_id,
        title="read_file",
        tool_input="/workspace/report.txt",
    )
    exchange = ApprovalExchange(
        ApprovalFlow(coordinator, "tool", None), event, "chat-1"
    )
    telegram = TelegramTransport({"owner_id": "12"})
    pending = PendingApproval(chat_id="12", message_id=77, owner="12")
    application_port = 19407
    app = web.Application(middlewares=[token_auth_middleware(port=application_port)])
    app["state"] = state
    app["port"] = application_port
    app["allowed_origins"] = {f"http://localhost:{application_port}"}
    app.router.add_get("/api/ws", api_ws)
    owner_token = generate_token("dashboard-owner", ttl_seconds=3600)
    cookie = {f"gideon_token_{application_port}": owner_token}

    async with TestClient(TestServer(app)) as client:
        client.session.cookie_jar.update_cookies(cookie)
        websocket = await client.ws_connect(
            "/api/ws",
            headers={"Origin": f"http://localhost:{application_port}"},
        )
        try:
            assert (await websocket.receive_json())["type"] == "sessions"
            telegram.delivery.pending["approval-1"] = pending
            exchange.on_prompted(pending)
            for _ in range(20):
                if request_id in state._approval_futures:
                    break
                await asyncio.sleep(0)
            assert request_id in state._approval_futures
            assert (await websocket.receive_json())["type"] == "approval"

            callback = {
                "data": "approve:approval-1",
                "from": {"id": 99},
                "message": {"chat": {"id": 12}, "message_id": 77},
            }
            assert not telegram.delivery.authorize_callback(callback)
            assert pending.answerer is None
            callback["from"]["id"] = 12
            assert telegram.delivery.authorize_callback(callback)
            assert pending.answerer is not None
            assert pending.answerer.label == "channel:12"
            assert pending.future.result() == "approved"

            text_pending = PendingApproval(chat_id="12", message_id=78, owner="12")
            telegram.delivery.pending["approval-text"] = text_pending
            from gideon.integrations.telegram.transport import normalize

            text_message = normalize(
                {
                    "chat": {"id": 12, "type": "private"},
                    "from": {"id": 12},
                    "text": "yes",
                }
            )
            assert await telegram.delivery.resolve_text_reply(
                text_message, {"reply_to_message": {"message_id": 78}}
            )
            assert text_pending.answerer is not None
            assert text_pending.answerer.label == "channel:12"

            decision = exchange.finish_channel_decision(True)
            assert decision is True
            assert await exchange.dashboard_future is True
            assert request_id not in state._pending_approvals
            resolved = await websocket.receive_json(timeout=1)
            assert resolved == {
                "type": "approval_resolved",
                "data": {"id": request_id, "approved": True},
            }
            assert exchange.finish_channel_decision(True) is True
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(websocket.receive_json(), timeout=0.05)
        finally:
            await websocket.close()
            if exchange.dashboard_future and not exchange.dashboard_future.done():
                exchange.dashboard_future.cancel()
                await asyncio.gather(exchange.dashboard_future, return_exceptions=True)

    rows = [
        json.loads(line)
        for line in sel()._path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    decisions = [
        row
        for row in rows
        if row["operation"] == "approval_decision" and row["request_id"] == request_id
    ]
    assert len(decisions) == 1
    assert decisions[0]["source"] == "paired_channel"
    assert decisions[0]["metadata"]["decided_by"] == "channel:12"
