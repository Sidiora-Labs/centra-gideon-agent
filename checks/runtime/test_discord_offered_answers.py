"""Discord answers bind a live native offer to its authenticated application."""

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/gideonai-discord-desk"
    ),
)
from discord_desk.api import DiscordDeskHttpApi
from discord_desk.delivery import DiscordDeskDelivery
from discord_desk.gateway import DiscordDeskGateway
from discord_desk.transport import DiscordDeskTransport

from gideon.integrations import channel_delivery, channel_transports, channel_trust
from gideon.security import session_credentials
from gideon.security.approval_answer import YOU, on_channel


@pytest.fixture
async def native(tmp_path, monkeypatch):
    from gideon.core.config import AppConfig, credentials, loader
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard import session_store
    from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DISABLE_LIVE_WRITES", raising=False)
    for module in (loader, session_store):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        channel_transports, "_transports", dict(channel_transports._transports)
    )
    monkeypatch.setattr(channel_delivery, "_REGISTRY", dict(channel_delivery._REGISTRY))
    monkeypatch.setattr(channel_delivery, "_QUEUES", dict(channel_delivery._QUEUES))
    (tmp_path / "config.json").write_text("{}")
    credentials.save_credential(credentials.owner_id_credential("discord"), "101")
    channel_trust.allow_sender("discord", "101")
    calls = []
    channel = {"id": "1001", "type": 1, "recipients": [{"id": "101"}]}
    failure = {"value": False}
    counter = {"value": 0}

    async def endpoint(request):
        body = await request.json() if request.can_read_body else {}
        path = request.match_info["path"]
        calls.append((request.method, path, body))
        if request.method == "GET" and path == "channels/1001":
            if failure["value"]:
                return web.json_response({"message": "Unavailable"}, status=403)
            result = channel
        elif request.method == "POST" and path.endswith("/messages"):
            counter["value"] += 1
            result = {"id": str(2000 + counter["value"]), "channel_id": "1001"}
        else:
            result = {}
        return web.json_response(result)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", endpoint)
    async with TestServer(app) as server:
        async with httpx.AsyncClient(
            base_url=str(server.make_url("/")),
            headers=DiscordDeskHttpApi.auth_headers("local"),
        ) as client:
            api = DiscordDeskHttpApi("local", client=client, max_retries=0)
            transport = DiscordDeskTransport({"bot_token": "local"})
            transport._api = api
            delivery = DiscordDeskDelivery(api, "101", transport=transport)
            transport._delivery = delivery
            gateway = DiscordDeskGateway(
                "local",
                on_ready=transport._on_ready,
                on_interaction=transport._on_interaction_create,
            )
            transport._gateway = gateway
            await gateway._handle(
                {
                    "op": 0,
                    "t": "READY",
                    "s": 1,
                    "d": {
                        "session_id": "local-session",
                        "user": {"id": "999"},
                        "application": {"id": "300"},
                    },
                }
            )
            channel_transports.register_transport(transport)
            channel_delivery.register(delivery, "discord")
            directory = ConversationDirectory(AppConfig.load())
            state = ConsoleState(directory, time.time())
            session = _ChatSession("ordinary")
            state._sessions["ordinary"] = session
            state.link_channel("ordinary", "1001", "1001", "discord")
            credential = session_credentials.begin_turn(
                "dashboard:ordinary",
                on_channel("discord", "101", "discord:300"),
                turn_id="one",
                memory_mode="persistent",
            )
            n = SimpleNamespace(
                state=state,
                session=session,
                transport=transport,
                delivery=delivery,
                gateway=gateway,
                calls=calls,
                channel=channel,
                failure=failure,
            )
            try:
                yield n
            finally:
                session_credentials.end_turn(credential)
                for task in tuple(state._background_tasks):
                    task.cancel()
                await asyncio.gather(
                    *tuple(state._background_tasks), return_exceptions=True
                )


def register(n, risk="caution"):
    future = asyncio.get_running_loop().create_future()
    n.session._approval_futures["call"] = future
    n.session.append(
        "permission",
        "Read file",
        json.dumps({"request_id": "call", "asked_by": "agent:ordinary"}),
    )
    n.state._register_chat_approval(
        {
            "id": "call",
            "session": "ordinary",
            "tool": "read_file",
            "tool_input": "x" * 5000,
            "risk": risk,
            "blast_radius": {"readOnly": True},
        }
    )
    return future


async def pending(n):
    for _ in range(300):
        if n.delivery._pending:
            return next(iter(n.delivery._pending.values()))
        await asyncio.sleep(0.005)
    raise AssertionError("no native Discord prompt")


def press(p, answer="trust"):
    return {
        "type": 3,
        "id": "400",
        "token": "local-interaction",
        "application_id": "300",
        "user": {"id": "101", "bot": False},
        "channel_id": p.channel_id,
        "message": {"id": p.message_id},
        "data": {"custom_id": f"{answer}:{p.request_id}"},
    }


async def dispatch(n, body):
    await n.gateway._handle({"op": 0, "t": "INTERACTION_CREATE", "s": 2, "d": body})


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["approved", "trust", "rejected"])
async def test_actual_rest_and_gateway_callback_apply_exact_offer(native, answer):
    f = register(native)
    p = await pending(native)
    posts = [
        body
        for method, path, body in native.calls
        if method == "POST" and path.endswith("/messages")
    ]
    assert sum(body["content"].count("x") for body in posts) == 5000
    assert all(len(body["content"]) <= 2000 for body in posts)
    assert [button["label"] for button in posts[-1]["components"][0]["components"]] == [
        "Allow once",
        "Allow for this chat",
        "Deny",
    ]
    assert "Every tool in this chat" in "".join(body["content"] for body in posts)
    await dispatch(native, press(p, answer))
    assert await asyncio.wait_for(f, 1) == (
        "rejected" if answer == "rejected" else "approved"
    )
    assert native.session._trust is (answer == "trust")
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    edits = [body for method, path, body in native.calls if method == "PATCH"]
    assert edits and edits[-1]["components"] == []
    assert ("Every tool in this chat" in edits[-1]["content"]) is (answer == "trust")
    native.session._trust = False
    await dispatch(native, press(p))
    assert not native.session._trust
    assert any(path.startswith("interactions/") for method, path, body in native.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "actor",
        "bot",
        "application",
        "guild",
        "channel",
        "message",
        "request",
        "unknown",
        "owner",
        "unpaired",
        "transport",
        "link",
        "ceiling",
        "turn",
        "ready",
    ],
)
async def test_changed_identity_destination_or_offer_cannot_grant(
    native, monkeypatch, change
):
    from gideon.core.config import credentials

    f = register(native)
    p = await pending(native)
    body = press(p)
    replacement = None
    if change == "actor":
        body["user"]["id"] = "202"
    elif change == "bot":
        body["user"]["bot"] = True
    elif change == "application":
        body["application_id"] = "301"
    elif change == "guild":
        body["guild_id"] = "500"
    elif change == "channel":
        body["channel_id"] = "1002"
    elif change == "message":
        body["message"]["id"] = "2222"
    elif change == "request":
        body["data"]["custom_id"] = "trust:wrong"
    elif change == "unknown":
        body["data"]["custom_id"] = f"yolo:{p.request_id}"
    elif change == "owner":
        credentials.save_credential(credentials.owner_id_credential("discord"), "202")
    elif change == "unpaired":
        channel_trust.deny_sender("discord", "101")
    elif change == "transport":
        channel_transports.register_transport(
            DiscordDeskTransport({"bot_token": "other"})
        )
    elif change == "link":
        native.state.link_channel("ordinary", "1002", "1002", "discord")
    elif change == "ceiling":
        monkeypatch.setattr(
            "gideon.security.approval_grants.stands", lambda *a, **k: False
        )
    elif change == "turn":
        replacement = session_credentials.begin_turn(
            "dashboard:ordinary", YOU, turn_id="new", memory_mode="persistent"
        )
    elif change == "ready":
        await native.gateway._handle(
            {
                "op": 0,
                "t": "READY",
                "d": {"user": {"id": "998"}, "application": {"id": "301"}},
            }
        )
    await dispatch(native, body)
    assert not f.done() and not native.session._trust and not p.future.done()
    native.state.cancel_approval("ordinary:call", reason="finished")
    session_credentials.end_turn(replacement)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        "guild",
        "group_dm",
        "foreign_recipient",
        "api_failure",
        "unchecked",
        "app",
        "room",
        "background",
    ],
)
async def test_unavailable_scopes_never_offer_standing_permission(native, scope):
    replacement = None
    if scope == "guild":
        native.channel.update(type=0, guild_id="500")
    elif scope == "group_dm":
        native.channel.update(type=3, recipients=[{"id": "101"}, {"id": "202"}])
    elif scope == "foreign_recipient":
        native.channel["recipients"] = [{"id": "202"}]
    elif scope == "api_failure":
        native.failure["value"] = True
    elif scope == "app":
        native.session._created_by_app = "example"
    elif scope == "room":
        native.session._app = "room"
    elif scope == "background":
        replacement = session_credentials.begin_turn(
            "dashboard:ordinary",
            YOU,
            turn_id="background",
            memory_mode="persistent",
            durable_run_id="work",
        )
    f = register(native, risk="unchecked" if scope == "unchecked" else "caution")
    p = await pending(native)
    assert [a.key for a in p.answers] == ["approved", "rejected"]
    await dispatch(native, press(p))
    assert not f.done()
    native.state.cancel_approval("ordinary:call", reason="finished")
    session_credentials.end_turn(replacement)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["approved", "expired", "cancelled"])
async def test_owner_endings_remove_components_and_late_grants(native, ending):
    f = register(native)
    p = await pending(native)
    if ending == "approved":
        native.state.resolve_approval("ordinary:call", True, by=YOU)
    else:
        native.state.end_approval("ordinary:call", outcome=ending)
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    await dispatch(native, press(p))
    assert not native.session._trust
    assert any(
        ending.capitalize() in body["content"] and body["components"] == []
        for method, path, body in native.calls
        if method == "PATCH"
    )
