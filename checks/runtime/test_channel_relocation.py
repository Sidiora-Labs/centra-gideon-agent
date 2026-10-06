import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.cognition.history import ConversationLog
from gideon.core.config.credentials import owner_id_credential, save_credential
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.session_map import SessionMap
from gideon.integrations import channel_delivery, channel_trust
from gideon.integrations.channel_transports import (
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport
from gideon.integrations.telegram.api import TelegramAPI
from gideon.integrations.telegram.transport import TelegramTransport
from gideon.interfaces.dashboard import channel_links
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
async def channels(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}')
    requests = []

    async def receive(request):
        requests.append(await request.json())
        return web.json_response({"ok": True, "result": {"message_id": len(requests)}})

    app = web.Application()
    app.router.add_post("/bot123:test/sendMessage", receive)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv(
        "GIDEON_TELEGRAM_API_BASE", str(server.make_url("/")).rstrip("/")
    )
    monkeypatch.setenv("GIDEON_DISABLE_LIVE_WRITES", "0")
    telegram = TelegramTransport({"owner_id": "42"})
    telegram.api = TelegramAPI("123:test")
    telegram.state = "ready"
    echo = ReferenceEchoTransport()
    await echo.connect()
    register_transport(telegram)
    register_transport(echo)
    channel_delivery.register(telegram.delivery, provider="telegram")
    channel_trust.allow_sender("telegram", "42", "Owner")
    save_credential(owner_id_credential("telegram"), "42")
    state = ConsoleState(
        sessions=ConversationDirectory(AppConfig.load()),
        start_time=0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    yield state, requests, telegram, echo
    if channel_links._NOTICE_TASKS:
        await asyncio.gather(*tuple(channel_links._NOTICE_TASKS))
    channel_delivery.register(None)
    unregister_transport("telegram")
    unregister_transport("reference-echo")
    await telegram.api.close()
    await server.close()


async def notices_settled():
    if channel_links._NOTICE_TASKS:
        await asyncio.gather(*tuple(channel_links._NOTICE_TASKS))


@pytest.mark.asyncio
async def test_move_notifies_exact_previous_owner_dm_without_changing_ingress(channels):
    state, requests, _, _ = channels
    session = state.get_or_create_session("chat-1", app="original-source")
    session._initiator = {
        "kind": "channel_sender",
        "provider": "telegram",
        "subject": "42",
    }
    state.link_channel(session.key, "telegram:42:7", "42", "telegram")
    state.link_channel(session.key, "echo-thread", "echo-room", "reference-echo")
    await notices_settled()
    assert len(requests) == 1
    assert requests[0]["chat_id"] == "42" and requests[0]["message_thread_id"] == 7
    assert "Reference" in requests[0]["text"] and "chat-1" not in requests[0]["text"]
    assert session._app == "original-source"
    assert session._initiator["subject"] == "42"
    assert state.get_linked_session("telegram:42:7", "telegram") is None
    assert state.get_linked_session("echo-thread", "reference-echo") is session
    assert SessionMap().get_channel_provider("dashboard:chat-1") == "reference-echo"


@pytest.mark.asyncio
@pytest.mark.parametrize("old_channel", ["-100123", "84"])
async def test_move_says_nothing_to_group_or_someone_elses_dm(channels, old_channel):
    state, requests, _, _ = channels
    state.get_or_create_session("chat-1")
    state.link_channel("chat-1", "old-thread", old_channel, "telegram")
    state.link_channel("chat-1", "next-thread", "next-room", "reference-echo")
    await notices_settled()
    assert requests == []


@pytest.mark.asyncio
async def test_same_destination_no_notice_and_same_provider_move_uses_old_thread(
    channels,
):
    state, requests, _, _ = channels
    state.get_or_create_session("chat-1")
    state.link_channel("chat-1", "telegram:42:7", "42", "telegram")
    state.link_channel("chat-1", "telegram:42:7", "42", "telegram")
    await notices_settled()
    assert requests == []
    state.link_channel("chat-1", "telegram:42:8", "42", "telegram")
    await notices_settled()
    assert len(requests) == 1 and requests[0]["message_thread_id"] == 7
    assert "another Telegram conversation" in requests[0]["text"]


@pytest.mark.asyncio
async def test_revoked_owner_and_disconnected_transport_cannot_send_notice(channels):
    state, requests, telegram, _ = channels
    state.get_or_create_session("chat-1")
    state.link_channel("chat-1", "old", "42", "telegram")
    channel_trust.deny_sender("telegram", "42")
    state.link_channel("chat-1", "new", "room", "reference-echo")
    await notices_settled()
    assert requests == []
    telegram.state = "offline"
    state.link_channel("chat-1", "old", "42", "telegram")
    state.link_channel("chat-1", "new", "room", "reference-echo")
    await notices_settled()
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["temporary", "incognito"])
async def test_live_private_session_keeps_link_but_cannot_resurrect_from_disk(
    channels, mode
):
    state, _, _, _ = channels
    session = state.get_or_create_session("private", memory_mode=mode)
    state.link_channel("private", "private-thread", "room", "reference-echo")
    assert state.get_linked_session("private-thread", "reference-echo") is session
    state._sessions.pop("private")
    assert state.get_linked_session("private-thread", "reference-echo") is None
