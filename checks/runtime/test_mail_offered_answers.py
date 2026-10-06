"""Mail answers bind exact reply text to the current owner, account and prompt."""

import asyncio
import email
import email.policy
import json
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/gideonai-mail-desk"
    ),
)
from mail_desk_runtime import transport as transport_module
from mail_desk_runtime.delivery import MailDeskDelivery, ThreadStore
from mail_desk_runtime.mime import parse_inbound
from mail_desk_runtime.smtp_client import SmtplibSender
from mail_desk_runtime.transport import MailDeskTransport

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
    monkeypatch.setattr(
        transport_module, "load_credentials", lambda: ("local", "local")
    )
    (tmp_path / "config.json").write_text("{}")
    credentials.save_credential(
        credentials.owner_id_credential("mail-desk"), "owner@local.test"
    )
    channel_trust.allow_sender("mail-desk", "owner@local.test")
    channel_trust.allow_sender("mail-desk", "other@local.test")
    messages = []

    async def smtp(reader, writer):
        writer.write(b"220 local ESMTP\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                command = line.decode(errors="replace").strip().upper()
                if command.startswith("EHLO"):
                    response = b"250-local\r\n250-AUTH PLAIN\r\n250 SIZE 1000000\r\n"
                elif command.startswith("AUTH"):
                    response = b"235 Authenticated\r\n"
                elif command.startswith("DATA"):
                    writer.write(b"354 End with dot\r\n")
                    await writer.drain()
                    parts = []
                    while (part := await reader.readline()) not in (b".\r\n", b""):
                        parts.append(part[1:] if part.startswith(b"..") else part)
                    messages.append(
                        email.message_from_bytes(
                            b"".join(parts), policy=email.policy.default
                        )
                    )
                    response = b"250 Delivered\r\n"
                elif command.startswith("QUIT"):
                    writer.write(b"221 Bye\r\n")
                    await writer.drain()
                    break
                else:
                    response = b"250 OK\r\n"
                writer.write(response)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(smtp, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    config = {
        "imap_host": "local",
        "imap_user": "bot@local.test",
        "address": "bot@local.test",
        "smtp_host": "127.0.0.1",
        "smtp_port": port,
        "smtp_user": "bot@local.test",
        "smtp_security": "plain",
    }
    transport = MailDeskTransport(config)
    transport._cursor = 100
    transport._uidvalidity = 7
    sender = SmtplibSender(
        "127.0.0.1", port, "bot@local.test", "local", security="plain"
    )
    delivery = MailDeskDelivery(
        sender,
        "bot@local.test",
        "owner@local.test",
        ThreadStore(lambda: tmp_path / "threads.json"),
        transport=transport,
    )
    transport._delivery = delivery
    transport._cursor_path = lambda: tmp_path / "cursor.json"

    async def denied(*a, **k):
        return SimpleNamespace(allowed=False, canned_reply="")

    transport._services = SimpleNamespace(deliver_channel_inbound=denied)
    incoming = {}

    class Inbox:
        def connect(self):
            pass

        def select_folder(self, folder):
            return transport._uidvalidity

        def fetch_uids_since(self, folder, last_uid):
            return sorted(uid for uid in incoming if uid > last_uid)

        def fetch_message(self, folder, uid):
            return incoming[uid]

        def close(self):
            pass

    transport._client_factory = lambda *a: Inbox()
    channel_transports.register_transport(transport)
    channel_delivery.register(delivery, "mail-desk")
    directory = ConversationDirectory(AppConfig.load())
    state = ConsoleState(directory, time.time())
    session = _ChatSession("ordinary")
    state._sessions["ordinary"] = session
    state.link_channel("ordinary", "<root@local.test>", "owner@local.test", "mail-desk")
    original = EmailMessage()
    original["From"] = "owner@local.test"
    original["To"] = "bot@local.test"
    original["Message-ID"] = "<root@local.test>"
    original.set_content("Read my file")
    original_mail = parse_inbound(original.as_bytes(), 100)
    delivery.note_inbound(original_mail)
    from gideon.integrations.channel_inbound import _SessionIngress

    _SessionIngress(
        state, "mail-desk", transport._to_channel_message(original_mail), "Read my file"
    ).record(session)
    tenant = delivery.approval_identity("owner@local.test")["tenant"]
    credential = session_credentials.begin_turn(
        "dashboard:ordinary",
        on_channel("mail-desk", "owner@local.test", tenant),
        turn_id="one",
        memory_mode="persistent",
    )
    n = SimpleNamespace(
        state=state,
        session=session,
        transport=transport,
        delivery=delivery,
        messages=messages,
        incoming=incoming,
        tenant=tenant,
    )
    try:
        yield n
    finally:
        session_credentials.end_turn(credential)
        for task in tuple(state._background_tasks):
            task.cancel()
        await asyncio.gather(*tuple(state._background_tasks), return_exceptions=True)
        server.close()
        await server.wait_closed()


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
    raise AssertionError("no native mail prompt")


def reply(p, text=None):
    msg = EmailMessage()
    msg["From"] = "owner@local.test"
    msg["To"] = "bot@local.test"
    msg["Message-ID"] = "<answer@local.test>"
    msg["In-Reply-To"] = p.message_id
    msg["References"] = f"{p.thread} {p.message_id}"
    msg.set_content(text or f"TRUST {p.token}")
    return msg


async def poll(n, msg, uid=101, settings=None):
    n.incoming[uid] = msg.as_bytes()
    await n.transport._poll_once(settings or n.transport._settings())


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["APPROVE", "TRUST", "DENY"])
async def test_actual_smtp_mime_poll_callback_applies_only_offered_answer(native, word):
    f = register(native)
    p = await pending(native)
    prompt = native.messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "x" * 5000 in prompt and "Every tool in this chat" in prompt
    assert all(
        label in prompt for label in ("Allow once", "Allow for this chat", "Deny")
    )
    assert (
        native.session.messages[0]["meta"]["ingress"]["principal"]["tenant"]
        == native.tenant
    )
    await poll(native, reply(p, f"{word} {p.token}"))
    assert await asyncio.wait_for(f, 1) == (
        "rejected" if word == "DENY" else "approved"
    )
    assert native.session._trust is (word == "TRUST")
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    assert len(native.messages) == 2
    ending = native.messages[-1].get_body(preferencelist=("plain",)).get_content()
    assert "closed" in ending and ("Every tool in this chat" in ending) is (
        word == "TRUST"
    )
    assert json.loads(native.transport._cursor_path().read_text()) == {
        "last_uid": 101,
        "uidvalidity": 7,
    }
    native.session._trust = False
    await poll(native, reply(p), 102)
    assert not native.session._trust


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "sender",
        "display",
        "recipient",
        "cc",
        "thread",
        "reply_to",
        "token",
        "unknown",
        "substring",
        "multiple",
        "quoted",
        "owner",
        "unpaired",
        "account",
        "uidvalidity",
        "transport",
        "link",
        "ceiling",
        "turn",
        "replay",
    ],
)
async def test_changed_mail_actor_account_destination_or_offer_cannot_grant(
    native, monkeypatch, change
):
    from gideon.core.config import credentials

    f = register(native)
    p = await pending(native)
    msg = reply(p)
    replacement = None
    uid = 101
    if change == "sender":
        msg.replace_header("From", "other@local.test")
    elif change == "display":
        msg.replace_header("From", '"owner@local.test" <other@local.test>')
    elif change == "recipient":
        msg.replace_header("To", "other@local.test")
    elif change == "cc":
        msg["Cc"] = "other@local.test"
    elif change == "thread":
        msg.replace_header("References", "<foreign@local.test>")
    elif change == "reply_to":
        msg.replace_header("In-Reply-To", "<wrong@local.test>")
    elif change == "token":
        msg.set_content("TRUST FFFFFFFF")
    elif change == "unknown":
        msg.set_content(f"YOLO {p.token}")
    elif change == "substring":
        msg.set_content(f"Please TRUST {p.token} thanks")
    elif change == "multiple":
        msg.set_content(f"APPROVE {p.token}\nDENY {p.token}")
    elif change == "quoted":
        msg.set_content(f"> TRUST {p.token}")
    elif change == "owner":
        credentials.save_credential(
            credentials.owner_id_credential("mail-desk"), "other@local.test"
        )
    elif change == "unpaired":
        channel_trust.deny_sender("mail-desk", "owner@local.test")
    elif change == "account":
        native.transport._config["address"] = "changed@local.test"
    elif change == "uidvalidity":
        native.transport._uidvalidity = 8
    elif change == "transport":
        channel_transports.register_transport(
            MailDeskTransport(native.transport._config)
        )
    elif change == "link":
        native.state.link_channel(
            "ordinary", "<foreign@local.test>", "owner@local.test", "mail-desk"
        )
    elif change == "ceiling":
        monkeypatch.setattr(
            "gideon.security.approval_grants.stands", lambda *a, **k: False
        )
    elif change == "turn":
        replacement = session_credentials.begin_turn(
            "dashboard:ordinary", YOU, turn_id="new", memory_mode="persistent"
        )
    elif change == "replay":
        uid = 99
    await poll(native, msg, uid)
    assert not f.done() and not native.session._trust and not p.future.done()
    native.state.cancel_approval("ordinary:call", reason="finished")
    session_credentials.end_turn(replacement)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope", ["cc", "foreign", "unchecked", "app", "room", "background"]
)
async def test_unavailable_thread_or_work_scope_has_no_standing_offer(native, scope):
    replacement = None
    if scope in {"cc", "foreign"}:
        message = EmailMessage()
        message["From"] = (
            "other@local.test" if scope == "foreign" else "owner@local.test"
        )
        message["To"] = "bot@local.test"
        message["Message-ID"] = "<root@local.test>"
        message.set_content("Read a file")
        if scope == "cc":
            message["Cc"] = "other@local.test"
        native.delivery.note_inbound(parse_inbound(message.as_bytes(), 100))
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
    f = register(native, "unchecked" if scope == "unchecked" else "caution")
    p = await pending(native)
    assert [a.key for a in p.answers] == ["approved", "rejected"]
    await poll(native, reply(p))
    assert not f.done()
    native.state.cancel_approval("ordinary:call", reason="finished")
    session_credentials.end_turn(replacement)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["approved", "expired", "cancelled"])
async def test_actual_owner_ending_is_mailed_in_same_thread_without_late_grant(
    native, ending
):
    f = register(native)
    p = await pending(native)
    if ending == "approved":
        native.state.resolve_approval("ordinary:call", True, by=YOU)
    else:
        native.state.end_approval("ordinary:call", outcome=ending)
    await asyncio.gather(*tuple(native.state._background_tasks), return_exceptions=True)
    msg = native.messages[-1]
    assert ending.capitalize() in msg.get_body(preferencelist=("plain",)).get_content()
    assert str(msg["In-Reply-To"]) == p.message_id
    await poll(native, reply(p))
    assert not native.session._trust


@pytest.mark.asyncio
async def test_actual_polled_account_snapshot_cannot_answer_another_transport_account(
    native,
):
    from mail_desk_runtime.settings import MailDeskSettings

    f = register(native)
    p = await pending(native)
    other = MailDeskSettings.from_dict(
        {**native.transport._config, "imap_user": "foreign@local.test"}
    )
    await poll(native, reply(p), settings=other)
    assert not f.done() and not native.session._trust and not p.future.done()
    assert native.delivery.approval_identity("owner@local.test") is None
    native.state.cancel_approval("ordinary:call", reason="finished")
