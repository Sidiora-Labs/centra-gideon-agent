"""Real Mail Desk unknown-sender hold through owner HTTP actions and UI resolver."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.loader import load_factory
from gideon.extensions.providers.registry import RegisteredProvider
from gideon.integrations import channel_inbound, channel_trust
from gideon.integrations.channel_transports import (
    get_transport,
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.base import ChannelMessage
from gideon.integrations.inbox import InboxStore, ItemStatus, thread_mute_key
from gideon.interfaces.dashboard import handlers_inbox
from gideon.interfaces.dashboard.handlers import messaging
from gideon.interfaces.dashboard.state import ConsoleState


def _real_transport(provider: str):
    native = (
        Path(__file__).resolve().parents[2] / "runtime/gideon/extensions/apps/native"
    )
    app_name = {"mail-desk": "gideonai-mail-desk", "telegram": "telegram-channel"}[
        provider
    ]
    manifest = AppManifest.from_json_file(native / app_name / "app.json")
    channel = next(item for item in manifest.all_providers() if item.type == "channel")
    factory = load_factory(
        RegisteredProvider(app_name, manifest, channel, enabled=True)
    )
    return factory({})


def _run_notification_resolver(repo: Path) -> None:
    console = repo / "apps/console"
    compiler = console / "node_modules/typescript"
    dependency_console = console
    source = console / "src/features/notifications/notificationMeta.ts"
    script = r"""
const fs = require('node:fs');
const ts = require(process.argv[1]);
const source = fs.readFileSync(process.argv[2], 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const mod = { exports: {} };
const requireFromConsole = require('node:module').createRequire(process.argv[3]);
new Function('require', 'module', 'exports', output)(requireFromConsole, mod, mod.exports);
const resolve = mod.exports.canAnswerUnknownSender;
if (typeof resolve !== 'function') throw new Error('notification resolver was not exported');
const valid = { event: 'channel.unknown_sender', sender_id: 'visitor', actions: ['allow', 'deny'] };
if (!resolve(valid) || resolve({ ...valid, created_by_app: 'sample' }) || resolve({ ...valid, raised_by_app: 'sample' }) || resolve({ ...valid, actions: [] })) {
  throw new Error('unknown-sender notification resolver returned an unsafe result');
}
"""
    if not compiler.exists():
        raise AssertionError(
            "installed TypeScript compiler is required for the real UI resolver path"
        )
    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(compiler / "lib/typescript.js"),
            str(source),
            str(dependency_console / "package.json"),
        ],
        cwd=console,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.asyncio
async def test_stranger_is_held_until_owner_replies_pairs_or_ignores(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}', encoding="utf-8")
    channel_inbound.reset_admissions()
    channel_trust.reset_inbound_reports()
    mail = _real_transport("mail-desk")
    telegram = _real_transport("telegram")
    assert mail.capabilities().speaks_as_owner is True
    assert telegram.capabilities().speaks_as_owner is False
    register_transport(mail)
    register_transport(telegram)

    config = AppConfig.load()
    state = ConsoleState(sessions=ConversationDirectory(config), start_time=0)
    gateway = RuntimeCoordinator(config, no_dashboard=True, no_crons=True, no_open=True)
    gateway.dashboard_state = state

    import gideon.integrations.inbox as inbox_module

    monkeypatch.setattr(inbox_module, "owner_username", lambda: "operator-a")
    monkeypatch.setattr(handlers_inbox, "owner_username", lambda: "operator-a")

    @web.middleware
    async def stamp_verified_app(request, handler):
        if request.headers.get("X-Test-App"):
            request["app"] = request.headers["X-Test-App"]
        return await handler(request)

    app = web.Application(middlewares=[stamp_verified_app])
    app["state"] = state
    app.router.add_post("/api/inbox/send", handlers_inbox.api_inbox_send)
    app.router.add_post("/api/inbox/{id}/pair", handlers_inbox.api_inbox_pair)
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    app.router.add_post("/api/notifications/trust", messaging.api_notification_trust)

    try:
        paired_message = ChannelMessage(
            "mailbox",
            "private held body https://visitor:private-password@example.test/path",
            sender="pair@example.test",
            thread_id="thread-pair",
            message_id="message-pair",
            ts=1727700000.0,
            metadata={"sender_name": "Pair Sender"},
        )
        held = await gateway.deliver_channel_inbound("mail-desk", paired_message)
        duplicate = await gateway.deliver_channel_inbound("mail-desk", paired_message)
        assert not held.allowed and held.reason == "unknown_sender"
        assert held.canned_reply == "" and duplicate.canned_reply == ""
        assert duplicate.meta.get("duplicate") is True
        assert state._sessions == {} and not state._background_tasks

        store = InboxStore()
        store.load()
        held_rows = [
            row for row in store.items.values() if row.sender_id == "pair@example.test"
        ]
        assert len(held_rows) == 1
        held_item = held_rows[0]
        assert held_item.owner == "operator-a"
        assert "private held body" in held_item.message
        assert "private-password" not in held_item.message
        assert held_item.refs["someone_new"] == "mail-desk"
        assert (tmp_path / "inbox.json").is_file()
        assert (
            sum(
                note.get("sender_id") == "pair@example.test"
                for note in state._notification_log
            )
            == 1
        )

        async with TestClient(TestServer(app)) as client:
            owner_send = await client.post(
                "/api/inbox/send", json={"id": held_item.id, "text": "Owner reply"}
            )
            assert owner_send.status == 503
            malformed_pair = await client.post(
                f"/api/inbox/{held_item.id}/pair",
                data="not-json",
                headers={"Content-Type": "application/json"},
            )
            assert malformed_pair.status == 400
            app_pair = await client.post(
                f"/api/inbox/{held_item.id}/pair",
                json={"confirm": True},
                headers={"X-Test-App": "sample"},
            )
            assert app_pair.status == 403
            monkeypatch.setattr(inbox_module, "owner_username", lambda: "operator-b")
            monkeypatch.setattr(handlers_inbox, "owner_username", lambda: "operator-b")
            other_owner_pair = await client.post(
                f"/api/inbox/{held_item.id}/pair", json={"confirm": True}
            )
            assert other_owner_pair.status == 404
            monkeypatch.setattr(inbox_module, "owner_username", lambda: "operator-a")
            monkeypatch.setattr(handlers_inbox, "owner_username", lambda: "operator-a")
            pair = await client.post(
                f"/api/inbox/{held_item.id}/pair", json={"confirm": True}
            )
            assert pair.status == 200
            assert channel_trust.is_allowed_sender("mail-desk", "pair@example.test")
            store.load()
            assert (
                store.items[held_item.id].status_for("operator-a")
                == ItemStatus.HANDLED.value
            )

            denied_message = ChannelMessage(
                "mailbox",
                "please do not process",
                sender="deny@example.test",
                thread_id="thread-deny",
                message_id="message-deny",
                ts=1727700100.0,
                metadata={"sender_name": "Deny Sender"},
            )
            denied_verdict = await gateway.deliver_channel_inbound(
                "mail-desk", denied_message
            )
            assert not denied_verdict.allowed and denied_verdict.canned_reply == ""
            deny_note = next(
                note
                for note in state._notification_log
                if note.get("sender_id") == "deny@example.test"
            )
            malformed_answer = await client.post(
                "/api/notifications/trust",
                data="[]",
                headers={"Content-Type": "application/json"},
            )
            empty_answer = await client.post("/api/notifications/trust", json={})
            app_answer = await client.post(
                "/api/notifications/trust",
                json={"ts": deny_note["ts"], "action": "deny"},
                headers={"X-Test-App": "sample"},
            )
            unconfirmed_allow = await client.post(
                "/api/notifications/trust",
                json={"ts": deny_note["ts"], "action": "allow"},
            )
            assert malformed_answer.status == 400
            assert empty_answer.status == 400
            assert app_answer.status == 403
            assert unconfirmed_allow.status == 409
            assert not channel_trust.is_allowed_sender("mail-desk", "deny@example.test")
            deny = await client.post(
                "/api/notifications/trust",
                json={"ts": deny_note["ts"], "action": "deny"},
            )
            assert deny.status == 200
            assert (await deny.json())["answer"] == "denied"
            assert not channel_trust.is_allowed_sender("mail-desk", "deny@example.test")
            assert deny_note["trust_answer"] == "denied"
            assert any(
                row.sender_id == "deny@example.test"
                and row.status_for("operator-a") == ItemStatus.DISMISSED.value
                for row in state._inbox_store.items.values()
            )

            allowed_message = ChannelMessage(
                "mailbox",
                "please let me speak",
                sender="allow@example.test",
                thread_id="thread-allow",
                message_id="message-allow",
                ts=1727700150.0,
                metadata={"sender_name": "Allow Sender"},
            )
            allowed_verdict = await gateway.deliver_channel_inbound(
                "mail-desk", allowed_message
            )
            assert not allowed_verdict.allowed and allowed_verdict.canned_reply == ""
            allow_note = next(
                note
                for note in state._notification_log
                if note.get("sender_id") == "allow@example.test"
            )
            allow = await client.post(
                "/api/notifications/trust",
                json={"ts": allow_note["ts"], "action": "allow", "confirm": True},
            )
            assert allow.status == 200
            assert (await allow.json())["answer"] == "allowed"
            assert channel_trust.is_allowed_sender("mail-desk", "allow@example.test")
            assert allow_note["trust_answer"] == "allowed"
            store.load()
            allowed_item = next(
                row
                for row in store.items.values()
                if row.sender_id == "allow@example.test"
            )
            assert allowed_item.refs["paired"] is True
            assert allowed_item.status_for("operator-a") == ItemStatus.HANDLED.value
            next_message = ChannelMessage(
                "mailbox",
                "a later message",
                sender="allow@example.test",
                thread_id="thread-allow",
                message_id="message-allow-next",
                ts=1727700160.0,
            )
            assert channel_inbound.admit(state, "mail-desk", next_message).allowed

            ignored_message = ChannelMessage(
                "mailbox",
                "ignore me",
                sender="ignore@example.test",
                thread_id="thread-ignore",
                message_id="message-ignore",
                ts=1727700200.0,
            )
            await gateway.deliver_channel_inbound("mail-desk", ignored_message)
            store.load()
            ignored = next(
                row
                for row in store.items.values()
                if row.sender_id == "ignore@example.test"
            )
            ignore_response = await client.put(
                f"/api/inbox/{ignored.id}", json={"status": ItemStatus.DISMISSED.value}
            )
            assert ignore_response.status == 200
            assert ignored.id in state._inbox_state.dismissed
            before = len(store.items)
            channel_inbound.reset_admissions()
            await gateway.deliver_channel_inbound("mail-desk", ignored_message)
            store.load()
            assert len(store.items) == before

            muted_message = ChannelMessage(
                "mailbox",
                "muted sender",
                sender="muted@example.test",
                thread_id="muted-thread",
                message_id="muted-message",
                ts=1727700300.0,
            )
            mute_key = thread_mute_key("channel:mail-desk", "mailbox", "muted-thread")
            state._inbox_state.muted_threads.add(mute_key)
            state._inbox_state.save()
            before = len(store.items)
            await gateway.deliver_channel_inbound("mail-desk", muted_message)
            store.load()
            assert len(store.items) == before

            bot_message = ChannelMessage(
                "chat", "hello", sender="telegram-stranger", message_id="bot-message"
            )
            bot_verdict = await gateway.deliver_channel_inbound("telegram", bot_message)
            assert not bot_verdict.allowed
            assert bot_verdict.canned_reply == channel_trust.CANNED_PAIRING_REPLY
            assert state._sessions == {} and not state._background_tasks

        _run_notification_resolver(Path(__file__).resolve().parents[2])
    finally:
        unregister_transport("mail-desk")
        unregister_transport("telegram")
        channel_inbound.reset_admissions()
