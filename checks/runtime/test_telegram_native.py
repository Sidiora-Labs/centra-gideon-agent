"""Native Telegram renderer, identity, trust and attachment contracts."""

import asyncio
import json
import time
from pathlib import Path
import pytest
from gideon.integrations.telegram.format import to_markdown_v2, split_text
from gideon.integrations.telegram.transport import (
    normalize,
    mentioned,
    TelegramTransport,
)
from gideon.integrations.telegram.delivery import PendingApproval, thread_options
from gideon.integrations.telegram.api import TelegramAPI, TelegramError
from gideon.integrations.channel_trust import allow_sender, deny_sender

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures/telegram/agentcore.json").read_text()
)["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda x: x["id"])
def test_agentcore_renderer_vectors(vector):
    assert to_markdown_v2(vector["input"]) == vector["native_output"]


def test_unicode_split_preserves_every_character():
    text = "😀🚀hello\n" * 2000
    parts = split_text(text)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts)


def test_chat_and_topic_identity_are_distinct():
    base = {
        "message_id": 1,
        "chat": {"id": -100123, "type": "supergroup"},
        "from": {"id": 22},
        "text": "hello",
        "message_thread_id": 5,
    }
    one = normalize(base)
    other = normalize({**base, "chat": {"id": -100456, "type": "supergroup"}})
    topic = normalize({**base, "message_thread_id": 6})
    assert one.message_id != other.message_id
    assert one.thread_id != topic.thread_id
    assert thread_options(one.thread_id) == {"message_thread_id": 5}
    assert thread_options("telegram:123:d987") == {"direct_messages_topic_id": 987}
    assert thread_options("telegram:123:0") == {}


def test_mentions_have_boundaries_and_accept_replies():
    assert mentioned({"text": "hi @gideon_bot"}, "gideon_bot", 1)
    assert not mentioned({"text": "hi @gideon_bot_extra"}, "gideon_bot", 1)
    assert not mentioned({"text": "email@gideon_bot"}, "gideon_bot", 1)
    assert mentioned({"reply_to_message": {"from": {"id": 1}}}, "gideon_bot", 1)


def test_media_without_caption_is_admitted_as_a_message():
    cm = normalize(
        {
            "message_id": 2,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123},
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
        }
    )
    assert cm.text and cm.attachments[0]["file_id"] == "large"
    voice = normalize(
        {
            "chat": {"id": 123, "type": "private"},
            "voice": {"file_id": "voice", "duration": 5},
        }
    )
    assert voice.attachments[0]["file_name"] == "voice.ogg"


def test_start_link_passes_pairing_code_to_existing_gate():
    assert normalize({"text": "/start 12345678"}).text == "12345678"


@pytest.mark.asyncio
async def test_approvals_bind_owner_chat_message_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    allow_sender("telegram", "12")
    transport = TelegramTransport({"owner_id": "12"})
    pending = PendingApproval(chat_id="12", message_id=77, owner="12")
    transport.delivery.pending["key"] = pending
    query = {
        "data": "approve:key",
        "from": {"id": 12},
        "message": {"chat": {"id": 12}, "message_id": 77},
    }
    assert not transport.delivery.authorize_callback({**query, "from": {"id": 99}})
    assert not transport.delivery.authorize_callback(
        {**query, "message": {"chat": {"id": 99}, "message_id": 77}}
    )
    assert not transport.delivery.authorize_callback(
        {**query, "message": {"chat": {"id": 12}, "message_id": 78}}
    )
    assert not transport.delivery.authorize_callback(
        {**query, "from": {"id": 12, "is_bot": True}}
    )
    assert not pending.future.done()
    deny_sender("telegram", "12")
    assert not transport.delivery.authorize_callback(query)
    allow_sender("telegram", "12")
    assert transport.delivery.authorize_callback(query)
    assert await pending.future == "approved"
    assert not transport.delivery.authorize_callback(query)


@pytest.mark.asyncio
async def test_write_guard_blocks_all_outbound_api_methods(monkeypatch):
    monkeypatch.setenv("GIDEON_DISABLE_LIVE_WRITES", "true")
    api = TelegramAPI("123456:local_contract_test")
    try:
        for method in (
            "sendMessage",
            "sendVoice",
            "editMessageText",
            "setMessageReaction",
            "answerCallbackQuery",
        ):
            with pytest.raises(TelegramError, match="GIDEON_DISABLE_LIVE_WRITES"):
                await api.call(method)
    finally:
        await api.close()


def test_attachment_metadata_reaches_real_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.integrations.channel_inbound import _SessionIngress

    state = ConsoleState(None, time.time())
    cm = normalize(
        {
            "message_id": 3,
            "chat": {"id": 12, "type": "private"},
            "from": {"id": 12},
            "text": "Read the file",
        }
    )
    cm.attachments = [{"path": str(tmp_path / "uploads" / "document.pdf")}]
    ingress = _SessionIngress(state, "telegram", cm, cm.text)
    session = ingress.resolve()
    ingress.record(session)
    assert session.messages[-1]["meta"]["files"] == [
        str(tmp_path / "uploads" / "document.pdf")
    ]
    assert state.get_linked_session(cm.thread_id) is session


def test_native_manifest_has_masked_token_and_no_model_selection():
    from gideon.extensions.apps.manifest import AppManifest

    path = (
        Path(__file__).parents[2]
        / "runtime/gideon/extensions/apps/native/telegram-channel/app.json"
    )
    manifest = AppManifest.from_json_file(path)
    assert manifest.native and manifest.provider.type == "channel"
    props = manifest.provider.settingsSchema["properties"]
    assert props["bot_token"]["x-meta"]["sensitive"]
    assert "model" not in props


def test_http_logs_redact_bot_token():
    import logging
    from gideon.integrations.telegram.api import TelegramLogFilter

    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        "",
        0,
        "HTTP Request: %s",
        ("https://api.telegram.org/bot123456:test_secret/getMe",),
        None,
    )
    assert TelegramLogFilter().filter(record)
    assert "test_secret" not in record.getMessage()
    assert "getMe" in record.getMessage()


def test_topic_restore_is_durable_and_cannot_cross_owner_or_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.integrations.telegram.topics import TopicStore

    state = ConsoleState(None, time.time())
    store = TopicStore(tmp_path / "topics.json")
    cm = normalize(
        {
            "chat": {"id": 12, "type": "private"},
            "from": {"id": 12},
            "message_thread_id": 4,
            "text": "hello",
        }
    )
    previous = state.get_or_create_session(app="telegram")
    store.remember(cm, previous)
    store = TopicStore(tmp_path / "topics.json")
    assert previous in store.restorable(state, "12", "12")
    intruder = normalize(
        {
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 99},
            "message_thread_id": 4,
            "text": "restore",
        }
    )
    with pytest.raises(TelegramError, match="does not belong"):
        store.restore(state, intruder, previous.key)
    assert store.restore(state, cm, previous.key) is previous
    other_topic = normalize(
        {
            "chat": {"id": 12, "type": "private"},
            "from": {"id": 12},
            "message_thread_id": 5,
            "text": "restore",
        }
    )
    with pytest.raises(TelegramError, match="already linked"):
        store.restore(state, other_topic, previous.key)


def test_configured_group_access_never_grants_dm_access(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.integrations.telegram.policy import (
        settings,
        configured_admission,
        command_allowed,
    )

    config = settings(
        {
            "enabled": True,
            "group_allow_from": '["22"]',
            "group_user_allowed_commands": '["status"]',
        }
    )
    group = normalize(
        {
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 22},
            "text": "hello",
        }
    )
    assert configured_admission(config, group, False).allowed
    assert configured_admission(config, group, True) is None
    assert command_allowed(config, group, "status")
    assert not command_allowed(config, group, "background")
    assert (
        configured_admission(
            settings({"enabled": True, "guest_mode": True}), group, False
        )
        is None
    )
    group.metadata["telegram_direct_mention"] = True
    assert configured_admission(
        settings({"enabled": True, "guest_mode": True}), group, False
    ).fenced_text


@pytest.mark.asyncio
async def test_typed_clarification_and_approval_require_bound_prompt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    allow_sender("telegram", "12")
    transport = TelegramTransport({"owner_id": "12"})
    cm = normalize(
        {"chat": {"id": 12, "type": "private"}, "from": {"id": 12}, "text": "yes"}
    )
    pending = PendingApproval(chat_id="12", message_id=77, owner="12")
    transport.delivery.pending["approval"] = pending
    assert not await transport.delivery.resolve_text_reply(
        cm, {"reply_to_message": {"message_id": 99}}
    )
    assert await transport.delivery.resolve_text_reply(
        cm, {"reply_to_message": {"message_id": 77}}
    )
    assert pending.future.result() == "approved"
    future = asyncio.get_running_loop().create_future()
    transport.delivery.questions["question"] = {
        "chat": "12",
        "owner": "12",
        "thread": cm.thread_id,
        "message": 78,
        "typed": True,
        "future": future,
    }
    deny_sender("telegram", "12")
    assert not await transport.delivery.resolve_text_reply(cm, {})
    allow_sender("telegram", "12")
    assert await transport.delivery.resolve_text_reply(cm, {})
    assert future.result() == "yes"


def test_network_fallback_preserves_tls_identity():
    import httpx
    from gideon.integrations.telegram.network import (
        _rewrite_request_for_ip,
        parse_fallback_ip_env,
    )

    request = httpx.Request(
        "POST", "https://api.telegram.org/bot123:token/getMe", json={}
    )
    alternate = _rewrite_request_for_ip(request, "149.154.166.110")
    assert alternate.url.host == "149.154.166.110"
    assert alternate.headers["host"] == "api.telegram.org"
    assert alternate.extensions["sni_hostname"] == "api.telegram.org"
    assert parse_fallback_ip_env("127.0.0.1,10.0.0.1,149.154.166.110") == [
        "149.154.166.110"
    ]


def test_raw_audio_is_marked_without_automatic_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.integrations.channel_inbound import _SessionIngress

    state = ConsoleState(None, time.time())
    cm = normalize(
        {
            "chat": {"id": 12, "type": "private"},
            "from": {"id": 12},
            "voice": {"file_id": "voice"},
        }
    )
    cm.attachments[0].update(
        path=str(tmp_path / "uploads/voice.ogg"), skip_extract=True
    )
    ingress = _SessionIngress(state, "telegram", cm, cm.text)
    session = ingress.resolve()
    ingress.record(session)
    assert session.messages[-1]["meta"]["raw_files"] == [
        str(tmp_path / "uploads/voice.ogg")
    ]


def test_menu_never_exposes_hosted_model_selection():
    from gideon.integrations.telegram.commands import menu

    commands = menu(
        {"command_menu": {"priority": ["model", "topic"], "max_commands": 3}}
    )
    assert commands[0]["command"] == "topic"
    assert len(commands) == 3
    assert all(c["command"] != "model" for c in commands)


def test_mention_regex_is_bounded_and_ignores_invalid_patterns():
    from gideon.integrations.telegram.policy import pattern_trigger

    assert pattern_trigger([r"^\s*gideon\b"], " Gideon help")
    assert not pattern_trigger([r"^\s*gideon\b"], "other gideon")
    assert not pattern_trigger(["["], "text")
    assert not pattern_trigger(["(a+)+$"], "a" * 15000 + "!")


@pytest.mark.asyncio
async def test_webhook_authentication_and_durable_duplicate_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gideon.integrations import channel_delivery
    from gideon.integrations.telegram.webhook import receive_webhook

    transport = TelegramTransport(
        {"transport": "webhook", "webhook_secret": "private-test-webhook-secret"}
    )
    transport.state = "ready"
    transport._inbox = tmp_path / "pending"
    transport._inbox.mkdir()
    transport._webhook_seen_path = tmp_path / "seen.json"
    previous = channel_delivery.delivery_for("telegram")
    channel_delivery.register(transport.delivery, "telegram")
    app = web.Application()
    app.router.add_post("/api/telegram/webhook", receive_webhook)
    client = TestClient(TestServer(app))
    try:
        await client.start_server()
        response = await client.post("/api/telegram/webhook", json={"update_id": 1})
        assert response.status == 401
        headers = {"X-Telegram-Bot-Api-Secret-Token": "private-test-webhook-secret"}
        response = await client.post(
            "/api/telegram/webhook", headers=headers, json={"update_id": 1}
        )
        assert response.status == 200
        assert json.loads(transport._webhook_seen_path.read_text()) == [1]
        response = await client.post(
            "/api/telegram/webhook", headers=headers, json={"update_id": 1}
        )
        assert response.status == 200
        assert list(transport._webhook_seen) == [1]
        response = await client.post(
            "/api/telegram/webhook", headers=headers, json={"update_id": "invalid"}
        )
        assert response.status == 400
    finally:
        await client.close()
        channel_delivery.register(previous, "telegram")


def test_multiple_bots_have_distinct_routes_and_config():
    from gideon.integrations.telegram.manager import TelegramManager
    from gideon.integrations.telegram.policy import bot_configs
    from gideon.integrations.telegram.transport import create_provider

    manager = create_provider()
    assert isinstance(manager, TelegramManager)
    configs = bot_configs(
        {
            "enabled": True,
            "bot_token": "1:primary",
            "owner_id": "99",
            "webhook_secret": "primary-secret",
            "additional_bots": [{"bot_token": "2:secondary"}],
            "bot_preferences": {"2": {"voice_replies": True, "owner_id": "attacker"}},
        }
    )
    assert configs["2"]["transport"] == "polling"
    assert configs["2"]["webhook_secret"] == ""
    assert configs["2"]["owner_id"] == "99"
    assert configs["2"]["voice_replies"] is True
    for slot in configs:
        bot = TelegramTransport(configs[slot], manager=manager, slot=slot)
        bot.state = "ready"
        manager.bots[slot] = bot
    assert (
        manager.delivery.select("99", "telegram:99:4")[0]
        is manager.bots["primary"].delivery
    )
    assert (
        manager.delivery.select("99", "telegram:2:99:4")[0]
        is manager.bots["2"].delivery
    )
    assert manager.delivery.select("2/99")[1] == "99"
    assert thread_options("telegram:2:99:4") == {"message_thread_id": 4}
    assert thread_options("telegram:2:99:d7") == {"direct_messages_topic_id": 7}
    manager.bots["2"].state = "offline"
    with pytest.raises(TelegramError):
        manager.delivery.select("99", "telegram:2:99:4")


def test_multiple_bots_reject_ambiguous_tokens_and_webhooks():
    from gideon.integrations.telegram.policy import bot_configs

    with pytest.raises(ValueError):
        bot_configs({"bot_token": "1:one", "additional_bots": [{"bot_token": "1:two"}]})
    with pytest.raises(ValueError):
        bot_configs(
            {
                "bot_token": "1:one",
                "webhook_secret": "shared",
                "additional_bots": [{"bot_token": "2:two", "webhook_secret": "shared"}],
            }
        )
    configs = bot_configs(
        {"enabled": False, "additional_bots": [{"bot_token": "2:two", "enabled": True}]}
    )
    assert configs["2"]["enabled"] is False


@pytest.mark.asyncio
async def test_setup_pairing_code_redeems_once_and_replaces_old_code():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gideon.interfaces.dashboard.handlers.channel_trust import api_telegram_pairing
    from gideon.integrations.channel_trust import redeem_pairing_code, is_allowed_sender

    app = web.Application()
    app.router.add_post('/api/channels/telegram/pairing', api_telegram_pairing)
    async with TestClient(TestServer(app)) as client:
        response = await client.post('/api/channels/telegram/pairing')
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        first = await response.json()
        assert len(first['code']) == 8 and first['code'].isdigit()
        assert first['expires_in'] > 0
        response = await client.post('/api/channels/telegram/pairing')
        second = await response.json()
        assert not redeem_pairing_code('telegram', '773', first['code'])
        assert redeem_pairing_code('telegram', '773', second['code'])
        assert is_allowed_sender('telegram', '773')
        assert not redeem_pairing_code('telegram', '774', second['code'])


def test_telegram_reply_resolves_the_runners_history_session_key():
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.interfaces.dashboard.chat_utils import _history_key_for
    from gideon.integrations.telegram.manager import TelegramManager
    from gideon.integrations import channel_delivery

    state = ConsoleState(None, time.time())
    session = state.get_or_create_session(app="telegram")
    state.link_channel(session.key, "telegram:672:0", "672")
    manager = TelegramManager()
    previous = channel_delivery.delivery_for("telegram")
    channel_delivery.register(manager.delivery, "telegram")
    try:
        assert state.channel_provider_for(session.key) == "telegram"
        assert state.channel_provider_for(_history_key_for(session.key)) == "telegram"
        assert state.delivery_for(state.channel_provider_for(_history_key_for(session.key))).delivery is manager.delivery
        session._app = ""
        from gideon.integrations.channel_inbound import _SessionIngress
        message = normalize({"message_id": 7, "chat": {"id": 672, "type": "private"}, "from": {"id": 672}, "text": "hello"})
        assert _SessionIngress(state, "telegram", message, "hello").resolve() is session
        assert state.channel_provider_for(_history_key_for(session.key)) == "telegram"
        local = state.get_or_create_session()
        assert state.channel_provider_for(_history_key_for(local.key)) == ""
        assert state.channel_provider_for("dashboard:missing") == ""
    finally:
        channel_delivery.register(previous, "telegram")
