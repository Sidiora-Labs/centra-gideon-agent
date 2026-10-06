"""Channel answers bind the shown offer to the current paired owner and live chat."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations import channel_delivery, channel_transports, channel_trust
from gideon.integrations.channel_delivery import APPROVAL_ANSWERS, ONE_CALL_ANSWERS
from gideon.security import session_credentials
from gideon.security.approval_answer import (
    CHANNEL,
    YOU,
    Principal,
    agent,
    app,
    on_channel,
)
from gideon.security.approval_brief import (
    APPROVAL_BRIEF_META_KEY,
    approval_brief_for,
    entry_approval_brief,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from gideon.core.config import credentials, loader
    from gideon.interfaces.dashboard import session_store, token_auth

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DISABLE_LIVE_WRITES", raising=False)
    for module in (loader, session_store):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {"login_enabled": True}}))
    monkeypatch.setattr(
        channel_transports, "_transports", dict(channel_transports._transports)
    )
    monkeypatch.setattr(channel_delivery, "_REGISTRY", dict(channel_delivery._REGISTRY))
    monkeypatch.setattr(channel_delivery, "_QUEUES", dict(channel_delivery._QUEUES))
    channel_trust.allow_sender("telegram", "101")
    credentials.save_credential(credentials.owner_id_credential("telegram"), "101")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()


@pytest.fixture
async def native(tmp_path, monkeypatch):
    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.telegram.api import TelegramAPI
    from gideon.integrations.telegram.delivery import TelegramDelivery
    from gideon.integrations.telegram.manager import DeliveryRouter
    from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession

    calls = []
    posted = asyncio.Event()

    async def endpoint(request):
        payload = await request.json()
        calls.append((request.match_info["method"], payload))
        posted.set()
        return web.json_response({"ok": True, "result": {"message_id": len(calls)}})

    server_app = web.Application()
    server_app.router.add_post("/bot{token}/{method}", endpoint)
    async with TestServer(server_app) as server:
        monkeypatch.setenv(
            "GIDEON_TELEGRAM_API_BASE", str(server.make_url("")).rstrip("/")
        )
        api = TelegramAPI("12345:localfixture")
        transport = SimpleNamespace(
            slot="primary", connected=True, config={"owner_id": "101"}, api=api
        )
        delivery = TelegramDelivery(transport)
        transport.delivery = delivery
        directory = ConversationDirectory(AppConfig.load())
        state = ConsoleState(directory, time.time())
        session = _ChatSession("ordinary")
        state._sessions[session.key] = session
        state.link_channel(session.key, "telegram:101:0", "101", "telegram")
        manager = SimpleNamespace(
            name="telegram",
            connected=True,
            bots={"primary": transport},
            services=SimpleNamespace(dashboard_state=state),
        )
        channel_transports.register_transport(manager)
        channel_delivery.register(DeliveryRouter(manager), "telegram")
        credential = session_credentials.begin_turn(
            "dashboard:ordinary",
            on_channel("telegram", "101", "telegram:primary"),
            turn_id="first",
            memory_mode="persistent",
        )
        value = SimpleNamespace(
            state=state,
            session=session,
            directory=directory,
            transport=transport,
            delivery=delivery,
            manager=manager,
            calls=calls,
            posted=posted,
            credential=credential,
            api=api,
        )
        try:
            yield value
        finally:
            session_credentials.end_turn(credential)
            for task in tuple(state._background_tasks):
                task.cancel()
            await asyncio.gather(
                *tuple(state._background_tasks), return_exceptions=True
            )
            await api.close()


def register_question(native, *, risk="caution"):
    session = native.session
    future = asyncio.get_running_loop().create_future()
    session._approval_futures["call"] = future
    session.append(
        "permission",
        "Read a file",
        json.dumps({"request_id": "call", "asked_by": "agent:ordinary"}),
    )
    native.state._register_chat_approval(
        {
            "id": "call",
            "session": session.key,
            "tool": "read_file",
            "tool_input": '{"path":"notes.txt"}',
            "risk": risk,
            "blast_radius": {"readOnly": True},
        }
    )
    return future


async def prompt(native):
    for _ in range(200):
        if native.delivery.pending:
            return next(iter(native.delivery.pending.items()))
        await asyncio.sleep(0.002)
    raise AssertionError("native channel did not prompt")


def press(key, pending, answer="trust", **changes):
    value = {
        "id": "callback",
        "data": f"answer:{key}:{answer}",
        "from": {"id": 101, "is_bot": False},
        "message": {
            "chat": {"id": pending.chat_id, "type": "private"},
            "message_id": pending.message_id,
        },
    }
    for name, changed in changes.items():
        if name == "actor":
            value["from"]["id"] = changed
        elif name == "bot":
            value["from"]["is_bot"] = changed
        elif name == "chat":
            value["message"]["chat"]["id"] = changed
        elif name == "message":
            value["message"]["message_id"] = changed
        elif name == "topic":
            value["message"]["message_thread_id"] = changed
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["approved", "trust", "rejected"])
async def test_real_http_channel_press_decides_exact_offered_answer(native, answer):
    future = register_question(native)
    key, pending = await prompt(native)
    markup = next(
        payload["reply_markup"]
        for method, payload in native.calls
        if method == "sendMessage"
    )
    assert [button["text"] for button in markup["inline_keyboard"][0]] == [
        a.label for a in APPROVAL_ANSWERS
    ]
    assert "Every tool in this chat" in native.calls[0][1]["text"]
    assert native.delivery.authorize_callback(press(key, pending, answer))
    assert await future == ("rejected" if answer == "rejected" else "approved")
    assert native.session._trust is (answer == "trust")
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    assert not native.delivery.pending
    endings = [
        payload["text"]
        for method, payload in native.calls
        if method == "editMessageText"
    ]
    assert endings and ("Every tool in this chat" in endings[-1]) is (answer == "trust")
    await native.delivery.resolve_callback(press(key, pending, answer))
    assert (
        native.calls[-1][0] == "answerCallbackQuery"
        and native.calls[-1][1]["text"] != "Recorded"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "actor",
        "bot",
        "chat",
        "message",
        "offlist",
        "owner",
        "tenant",
        "turn",
        "link",
        "ceiling",
    ],
)
async def test_changed_identity_destination_or_offer_never_grants_trust(
    native, monkeypatch, mutation
):
    future = register_question(native)
    key, pending = await prompt(native)
    cq = press(key, pending)
    replacement = None
    if mutation == "actor":
        cq["from"]["id"] = 202
    elif mutation == "bot":
        cq["from"]["is_bot"] = True
    elif mutation == "chat":
        cq["message"]["chat"]["id"] = "999"
    elif mutation == "message":
        cq["message"]["message_id"] += 1
    elif mutation == "offlist":
        cq["data"] = f"answer:{key}:yolo"
    elif mutation == "owner":
        native.transport.config["owner_id"] = "202"
        channel_trust.allow_sender("telegram", "202")
    elif mutation == "tenant":
        pending.tenant = "telegram:foreign"
    elif mutation == "turn":
        replacement = session_credentials.begin_turn(
            "dashboard:ordinary", YOU, turn_id="replacement", memory_mode="persistent"
        )
    elif mutation == "link":
        native.state.link_channel("ordinary", "telegram:101:9", "101", "telegram")
    elif mutation == "ceiling":
        monkeypatch.setattr(
            "gideon.security.approval_grants.stands", lambda *a, **kw: False
        )
    assert not native.delivery.authorize_callback(cq)
    assert not future.done() and not native.session._trust and not pending.future.done()
    native.state.cancel_approval("ordinary:call", reason="test complete")
    session_credentials.end_turn(replacement)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variation",
    ["unchecked", "destructive", "app", "foreign", "group", "room", "background"],
)
async def test_unavailable_scopes_offer_only_one_call(native, variation):
    replacement = None
    session = native.session
    if variation == "app":
        session._created_by_app = "example"
    elif variation == "foreign":
        replacement = session_credentials.begin_turn(
            "dashboard:ordinary",
            on_channel("telegram", "202", "telegram:primary"),
            turn_id="foreign",
            memory_mode="persistent",
        )
    elif variation == "group":
        native.state.link_channel("ordinary", "telegram:-999:0", "-999", "telegram")
    elif variation == "room":
        session._app = "room"
    elif variation == "background":
        replacement = session_credentials.begin_turn(
            "dashboard:ordinary",
            YOU,
            turn_id="background",
            memory_mode="persistent",
            durable_run_id="work",
        )
    future = register_question(
        native,
        risk=variation if variation in {"unchecked", "destructive"} else "caution",
    )
    key, pending = await prompt(native)
    assert pending.answers == ONE_CALL_ANSWERS
    assert not native.delivery.authorize_callback(press(key, pending, "trust"))
    assert not future.done()
    native.state.cancel_approval("ordinary:call", reason="test complete")
    session_credentials.end_turn(replacement)


@pytest.mark.asyncio
async def test_dashboard_answer_withdraws_native_channel_and_preserves_owner_actor(
    native,
):
    future = register_question(native)
    key, pending = await prompt(native)
    assert native.state.resolve_session_approval(
        native.session, "call", "approved", by=YOU
    )
    assert await future == "approved"
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    assert (
        pending.future.result() == "approved"
        and not native.delivery.authorize_callback(press(key, pending))
    )
    assert not native.session._trust


@pytest.mark.asyncio
async def test_authenticated_owner_api_uses_shared_scope_authority(native):
    from gideon.interfaces.dashboard import chat_handlers, token_auth
    from gideon.security.auth.modes import AuthMode

    future = register_question(native)
    await prompt(native)
    a = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
    a["port"] = 10000
    a["state"] = native.state
    a["auth_cfg"] = SimpleNamespace(mode=AuthMode.LOCAL_TOKEN)
    a["allowed_origins"] = {"http://localhost:10000"}
    a.router.add_post(
        "/api/chat/sessions/{session}/approve", chat_handlers.api_chat_session_approve
    )
    token = token_auth.generate_token("owner")
    async with TestClient(TestServer(a)) as client:
        response = await client.post(
            "/api/chat/sessions/ordinary/approve",
            json={"action": "trust", "request_id": "call"},
            headers={
                "Authorization": "Bearer " + token,
                "Origin": "http://localhost:10000",
            },
        )
        assert response.status == 200 and (await response.json())["decision"] == "trust"
    assert await future == "approved" and native.session._trust


@pytest.mark.asyncio
async def test_wrong_principal_and_tenant_cannot_answer_stored_offer(native):
    future = register_question(native)
    await prompt(native)
    for by in [
        agent("ordinary"),
        app("example"),
        on_channel("telegram", "101", "telegram:foreign"),
        on_channel("telegram", "202", "telegram:primary"),
    ]:
        assert not native.state.answer_on_channel("ordinary:call", "trust", by=by)
    assert not future.done() and not native.session._trust
    native.state.cancel_approval("ordinary:call", reason="test complete")


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        [{"key": "trust"}],
        [a.as_dict() for a in reversed(APPROVAL_ANSWERS)],
        [dict(a.as_dict(), promise="different") for a in APPROVAL_ANSWERS],
    ],
)
def test_unrecognized_stamped_answer_vocabulary_falls_back_to_one_call(invalid):
    brief = entry_approval_brief({"tool": "read_file", "risk": "safe"})
    brief["answers"] = invalid
    event = SimpleNamespace(
        title="read_file",
        risk_level="safe",
        tool_input="",
        tool_meta={APPROVAL_BRIEF_META_KEY: brief},
    )
    assert approval_brief_for(event)["answers"] == [
        a.as_dict() for a in ONE_CALL_ANSWERS
    ]


@pytest.mark.asyncio
async def test_actual_admitted_channel_row_and_queue_carry_typed_signed_principal(
    native,
):
    from gideon.integrations.channel_inbound import deliver_inbound
    from gideon.integrations.channel_transports.base import ChannelMessage
    from gideon.security.durable_work import verified_ingress

    arrived = asyncio.Event()

    async def runner(state, session, text):
        arrived.set()
        await asyncio.Event().wait()

    services = SimpleNamespace(dashboard_state=native.state)
    for index in range(2):
        msg = ChannelMessage(
            channel_id="101",
            text="hello",
            sender="101",
            thread_id="telegram:101:0",
            message_id=f"message{index}",
            metadata={"telegram_bot_id": "primary"},
        )
        decision = await deliver_inbound(
            services, "telegram", msg, is_dm=True, turn_runner=runner
        )
        assert decision.allowed
        if index == 0:
            await asyncio.wait_for(arrived.wait(), 1)
    row = native.session.messages[-1]
    queued = native.session._queue[-1]
    for value in [row, queued]:
        ingress = value["meta"]["ingress"]
        assert verified_ingress(ingress)
        assert ingress["principal"] == {
            "kind": "channel",
            "name": "101",
            "tenant": "telegram:primary",
        }
        assert value["source_user"] == "101"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["expired", "cancelled"])
async def test_unanswered_native_channel_prompt_closes_with_actual_ending(
    native, ending
):
    future = register_question(native)
    key, pending = await prompt(native)
    native.state.end_approval("ordinary:call", outcome=ending)
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    assert pending.future.result() == ending and not native.session._trust
    await native.delivery.resolve_callback(press(key, pending))
    assert ending.title() in native.calls[-1][1]["text"]


@pytest.mark.asyncio
async def test_registered_question_answered_before_channel_task_never_posts(native):
    future = register_question(native)
    assert native.state.resolve_session_approval(
        native.session, "call", "approved", by=YOU
    )
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    assert not native.calls and future.result() == "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["yolo", "trust", "bogus"])
async def test_background_exchange_rejects_answer_not_in_its_one_call_offer(
    native, answer
):
    from gideon.engine.gateway import ApprovalExchange

    event = SimpleNamespace(
        request_id="background",
        title="read_file",
        risk_level="safe",
        tool_input="",
        tool_meta={},
    )
    flow = SimpleNamespace(
        coordinator=SimpleNamespace(dashboard_state=None), source="background"
    )
    exchange = ApprovalExchange(flow, event, "")
    exchange._channel_provider = "telegram"
    future = asyncio.get_running_loop().create_future()
    future.set_result(answer)
    exchange.channel_pending = SimpleNamespace(
        future=future,
        answerer=on_channel("telegram", "101", "telegram:primary"),
        transport=native.transport,
    )
    assert exchange.finish_channel_decision(True) is None
