"""Message adapters exercised with actual queues, stores, runtime state and HTTP."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations import (
    channel_inbound,
    channel_transports,
    channel_trust,
    inbox_providers,
)
from gideon.integrations.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    OutboundMessage,
)
from gideon.integrations.channel_transports.manager import ChannelManager
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport
from gideon.integrations.channel_transports.webui import WebUITransport
from gideon.integrations.inbox import InboxState, InboxStore
from gideon.integrations.inbox_providers import native_source, registry
from gideon.integrations.inbox_providers.filesystem_source import (
    FilesystemSourceProvider,
)
from gideon.integrations.inbox_service import InboxService
from gideon.interfaces.dashboard import handlers_inbox
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.ws import api_ws
from gideon.security import trust_mode


@pytest.fixture(autouse=True)
def adapter_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}')
    monkeypatch.setattr(channel_transports, "_transports", {})
    monkeypatch.setattr(registry, "_sources", {})
    monkeypatch.setattr(inbox_providers, "_cache", None)
    monkeypatch.setattr(native_source, "_dashboard_state", None)
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    channel_inbound.reset_admissions()
    yield tmp_path
    channel_inbound.reset_admissions()


def console_state():
    return ConsoleState(sessions=ConversationDirectory(AppConfig.load()), start_time=0)


def drop_batch(home, name, messages):
    directory = home / "inbox" / "incoming"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(json.dumps({"messages": messages}), encoding="utf-8")
    return target


def test_default_transport_registration_retains_external_catalog_entries():
    echo = ReferenceEchoTransport()
    channel_transports.register_transport(echo)
    channel_transports.register_default_transports()
    assert channel_transports.list_transports() == ["reference-echo", "webui"]
    assert channel_transports.get_transport("reference-echo") is echo
    prior = channel_transports.get_transport("webui")
    channel_transports.register_default_transports()
    assert channel_transports.get_transport("webui") is not prior
    assert channel_transports.list_transports() == ["reference-echo", "webui"]
    channel_transports.unregister_transport("reference-echo")
    channel_transports.unregister_transport("absent")
    assert channel_transports.list_transports() == ["webui"]


@pytest.mark.asyncio
async def test_manager_probes_and_sends_through_actual_echo():
    transport = ReferenceEchoTransport()
    channel_transports.register_transport(transport)
    manager = ChannelManager()
    assert (await manager.get(transport.name))["health"]["state"] == "offline"
    assert await manager.connect(transport.name) == {
        "ok": True,
        "health": {"state": "ready", "detail": "connected"},
    }
    assert await manager.test(transport.name) == {"ok": True, "detail": "connected"}
    sent = OutboundMessage("room", "hello", thread_id="thread")
    assert await manager.send(transport.name, sent)
    incoming = await anext(transport.receive())
    assert incoming.text == "echo: hello" and incoming.thread_id == "thread"
    assert transport.sent == [sent]
    assert [row["name"] for row in await manager.list()] == [transport.name]
    assert (await manager.disconnect(transport.name))["health"]["state"] == "offline"
    assert not await manager.send(transport.name, sent)
    assert await manager.get("missing") is None
    for operation in (manager.connect, manager.disconnect, manager.test):
        assert await operation("missing") == {
            "ok": False,
            "detail": "unknown transport",
        }
    assert not await manager.send("missing", sent)


@pytest.mark.asyncio
async def test_cancelled_echo_receiver_leaves_mailbox_usable():
    transport = ReferenceEchoTransport()
    await transport.connect()
    stream = transport.receive()
    waiter = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await transport.send(OutboundMessage("room", "after cancellation"))
    message = await asyncio.wait_for(anext(transport.receive()), timeout=2)
    assert message.text == "echo: after cancellation"
    await transport.disconnect()
    with pytest.raises(StopAsyncIteration):
        await anext(transport.receive())


@pytest.mark.asyncio
async def test_echo_reconnect_preserves_pending_messages_and_sent_history():
    transport = ReferenceEchoTransport()
    await transport.connect()
    await transport.send(OutboundMessage("room", "queued"))
    await transport.disconnect()
    await transport.connect()
    message = await anext(transport.receive())
    assert message.text == "echo: queued"
    assert len(transport.sent) == 1
    await transport.disconnect()


@pytest.mark.asyncio
async def test_reference_pairing_uses_real_gateway_and_echoes_confirmation():
    gateway = RuntimeCoordinator(
        AppConfig.load(), no_dashboard=True, no_crons=True, no_open=True
    )
    transport = ReferenceEchoTransport()
    await transport.connect()
    await transport.start_inbound(gateway)
    code = channel_trust.create_pairing_code(transport.name)
    result = await transport.handle_inbound(
        ChannelMessage("room", code, sender="visitor", message_id="pair")
    )
    assert result.paired and not result.allowed and result.delivered_text == ""
    assert channel_trust.is_allowed_sender(transport.name, "visitor")
    receipt = await anext(transport.receive())
    assert receipt.text == "echo: " + channel_trust.CANNED_PAIRED_REPLY
    await transport.stop_inbound()
    blocked = await transport.handle_inbound(
        ChannelMessage("room", "hello", sender="visitor")
    )
    assert not blocked.allowed and blocked.reason == "no_services"
    await transport.disconnect()


@pytest.mark.asyncio
async def test_webui_readiness_depends_on_bound_state_not_connect_flag():
    transport = WebUITransport()
    channel_transports.register_transport(transport)
    manager = ChannelManager()
    assert (await manager.connect("webui"))["ok"] is True
    assert (await manager.get("webui"))["connected"] is False
    assert not await manager.send("webui", OutboundMessage("unknown", "hello"))
    state = console_state()
    manager = ChannelManager(state)
    session = state.get_or_create_session()
    assert (await manager.get("webui"))["connected"] is True
    assert await manager.send(
        "webui", OutboundMessage(session.key, "assistant message")
    )
    assert session.messages[-1]["content"] == "assistant message"
    assert session.messages[-1]["role"] == "assistant"
    assert (await manager.disconnect("webui"))["health"]["state"] == "ready"
    transport.bind_state(None)
    assert not transport.connected


def test_capability_dictionary_does_not_alias_dataclass_fields():
    capabilities = ChannelCapabilities(inbound=True, max_text_len=4000)
    projected = capabilities.to_dict()
    projected["inbound"] = False
    assert capabilities.inbound
    assert len(projected) == 8 and projected["max_text_len"] == 4000


@pytest.mark.asyncio
async def test_filesystem_spool_maps_fields_and_archives_in_name_order(adapter_home):
    drop_batch(adapter_home, "b.json", [{"text": "second", "kind": "email"}])
    first = drop_batch(
        adapter_home,
        "a.json",
        [
            {
                "text": "first",
                "thread_context": [{"user": "one", "text": "prior"}],
                "is_dm": True,
                "thread_id": "thread",
                "sender_name": "One",
            }
        ],
    )
    checkpoints = {"keep": "original"}
    messages, returned = await FilesystemSourceProvider().poll(
        ["ignored-filter"], checkpoints, "ignored-user"
    )
    assert returned is checkpoints
    assert [message.id for message in messages] == ["a_0", "b_1"]
    assert [message.text for message in messages] == ["first", "second"]
    assert messages[0].is_dm and messages[0].thread_id == "thread"
    assert messages[0].sender_name == "One" and messages[0].kind == ""
    assert messages[1].kind == "email" and messages[1].timestamp > 0
    assert messages[0].thread_context == [{"user": "one", "text": "prior"}]
    assert not first.exists() and (first.parent / "processed" / first.name).exists()
    assert (await FilesystemSourceProvider().poll([], checkpoints, ""))[0] == []


@pytest.mark.asyncio
async def test_concurrent_filesystem_instances_do_not_replay_same_batch(adapter_home):
    drop_batch(
        adapter_home,
        "batch.json",
        [{"id": str(i), "text": f"message {i}"} for i in range(40)],
    )
    results = await asyncio.gather(
        *(FilesystemSourceProvider().poll([], {}, "") for _ in range(8))
    )
    delivered = [message for messages, _ in results for message in messages]
    assert len(delivered) == 40
    assert {message.id for message in delivered} == {str(i) for i in range(40)}


@pytest.mark.asyncio
async def test_bad_json_is_retained_while_other_files_are_consumed(
    adapter_home, caplog
):
    good = drop_batch(adapter_home, "good.json", [{"id": "valid", "text": "hello"}])
    corrupt = good.parent / "bad.json"
    corrupt.write_text("{broken")
    messages, _ = await FilesystemSourceProvider().poll([], {}, "")
    assert [message.id for message in messages] == ["valid"]
    assert corrupt.exists() and not good.exists()
    assert "Failed to read message file" in caplog.text


@pytest.mark.asyncio
async def test_existing_filesystem_instance_follows_active_home(
    adapter_home, monkeypatch
):
    provider = FilesystemSourceProvider()
    drop_batch(adapter_home, "first.json", [{"id": "first"}])
    assert (await provider.poll([], {}, ""))[0][0].id == "first"
    alternate = adapter_home / "alternate"
    drop_batch(alternate, "second.json", [{"id": "second"}])
    monkeypatch.setenv("GIDEON_HOME", str(alternate))
    assert (await provider.poll([], {}, ""))[0][0].id == "second"
    assert await provider.send_reply("room", "reply")
    assert await provider.add_reaction("room", "1", "ok")
    assert await provider.get_channel_history("room", "0") == []
    assert await provider.resolve_user_name("owner") == "owner"


def test_real_source_instance_wins_and_unregister_restores_class_resolution(
    monkeypatch,
):
    monkeypatch.setattr(
        inbox_providers, "_cache", {"filesystem": FilesystemSourceProvider}
    )
    provider = FilesystemSourceProvider()
    assert registry.register_source(provider) == "filesystem"
    assert inbox_providers.get_default_provider("filesystem") is provider
    assert registry.list_source_names() == ["filesystem"]
    registry.unregister_source("filesystem")
    assert inbox_providers.get_default_provider("filesystem") is not provider
    assert isinstance(
        inbox_providers.get_default_provider("unknown"), FilesystemSourceProvider
    )
    registry.unregister_source("missing")


def test_native_writer_prefers_running_service_store(adapter_home):
    state = console_state()
    service = InboxService(
        state=InboxState(adapter_home / "state.json"),
        store=InboxStore(adapter_home / "service.json"),
    )
    state._inbox_svc = service
    state._inbox_store = InboxStore(adapter_home / "other.json")
    item = native_source.post_to_inbox(
        "question", kind="question", reply_target="chat:one", state=state
    )
    assert item is service.inbox.items[item.id]
    assert state._inbox_store.items == {}
    assert item.can_reply and item.reply_target == "chat:one"
    reloaded = InboxStore(adapter_home / "service.json")
    reloaded.load()
    assert item.id in reloaded.items


def test_concurrent_native_pushes_keep_all_persisted_items(adapter_home):
    state = console_state()
    native_source.set_dashboard_state(state)
    assert native_source.get_dashboard_state() is state
    with ThreadPoolExecutor(max_workers=8) as pool:
        posted = list(
            pool.map(
                lambda i: native_source.post_to_inbox(
                    f"result {i}", kind="unknown", reply_target="discarded"
                ),
                range(24),
            )
        )
    reloaded = InboxStore(adapter_home / "inbox.json")
    reloaded.load()
    assert {item.id for item in posted} == set(reloaded.items)
    assert len(reloaded.items) == 24
    assert all(
        item.classification == "fyi" and not item.can_reply and item.reply_target == ""
        for item in posted
    )
    native_source.set_dashboard_state(None)
    assert native_source.post_to_inbox("unbound") is None


@pytest.mark.asyncio
async def test_native_push_reaches_actual_websocket_and_http_with_redacted_payload():
    state = console_state()
    app = web.Application()
    app["state"] = state
    allowed_origins = set()
    app["allowed_origins"] = allowed_origins
    app.router.add_get("/api/ws", api_ws)
    app.router.add_get("/api/inbox", handlers_inbox.api_inbox_list)
    async with TestClient(TestServer(app)) as client:
        origin = str(client.make_url("/").origin())
        allowed_origins.add(origin)
        async with client.ws_connect("/api/ws", origin=origin) as socket:
            initial = await socket.receive_json(timeout=2)
            assert initial["type"] == "sessions"
            item = native_source.post_to_inbox(
                "see https://owner:private-password@example.test/path", state=state
            )
            notice = await socket.receive_json(timeout=2)
            assert notice["type"] == "inbox_new_item"
            assert notice["data"]["id"] == item.id
            assert "private-password" not in json.dumps(notice)
            response = await client.get("/api/inbox")
            assert response.status == 200
            body = await response.json()
            assert [row["id"] for row in body] == [item.id]
            assert "private-password" not in json.dumps(body)
    assert not state._ws_clients
