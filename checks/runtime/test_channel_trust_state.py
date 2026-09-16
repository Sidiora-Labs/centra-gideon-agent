"""Channel trust through real atomic storage, notification state and local HTTP routes."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.atomic_write import (
    register_post_write_hook,
    unregister_post_write_hook,
)
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.providers.entity_routes import _entity_settings_path
from gideon.integrations import channel_trust as trust
from gideon.interfaces.dashboard import state as dashboard_state
from gideon.interfaces.dashboard.handlers import channel_trust as routes
from gideon.security import trust_mode
from gideon.security.sel import sel


@pytest.fixture
def trust_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}')
    trust.reset_inbound_reports()
    yield tmp_path
    trust.reset_inbound_reports()


def test_provider_records_are_detached_and_keep_extension_fields():
    source = {
        "one": {
            "policies": {"dm": "owner_only"},
            "allowed_senders": {"alice": {"name": "Alice"}},
            "future": {"version": 2},
        }
    }
    first = trust._provider_record(source, "one")
    assert first["policies"] == {"dm": "owner_only", "group": "tracked_only"}
    first["allowed_senders"]["alice"]["name"] = "Changed"
    first["future"]["version"] = 3
    assert source["one"]["allowed_senders"]["alice"]["name"] == "Alice"
    assert source["one"]["future"]["version"] == 2
    second = trust._default_provider()
    second["policies"]["dm"] = "open"
    assert trust._default_provider()["policies"]["dm"] == "pairing"


def test_malformed_sections_fall_back_to_closed_defaults(trust_home):
    trust._write_store(
        {
            "broken": {
                "allowed_senders": ["stranger"],
                "tracked_channels": None,
                "pairing": False,
                "policies": "open",
                "rate": 42,
            }
        }
    )
    assert trust.is_allowed_sender("broken", "stranger") is False
    assert trust.is_tracked_channel("broken", "room") is False
    assert trust.trust_policies("broken") == {"dm": "pairing", "group": "tracked_only"}
    assert (
        trust.guard_inbound(None, "broken", "stranger", text="hello").allowed is False
    )
    projection = trust.provider_trust("broken")
    assert projection["allowed_senders"] == [] and projection["pairing_active"] is False


def test_concurrent_directory_mutations_preserve_all_partitions(trust_home):
    sel()
    tasks = [
        (provider, index) for provider in ("first", "second") for index in range(16)
    ]

    def add_subjects(task):
        provider, index = task
        trust.allow_sender(provider, f"sender-{index}", f"Person {index}")
        trust.track(provider, f"room-{index}", f"Room {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add_subjects, tasks))
    for provider in ("first", "second"):
        projected = trust.provider_trust(provider)
        assert {row["sender_id"] for row in projected["allowed_senders"]} == {
            f"sender-{i}" for i in range(16)
        }
        assert {row["channel_id"] for row in projected["tracked_channels"]} == {
            f"room-{i}" for i in range(16)
        }
    assert trust.list_providers() == ["first", "second"]
    assert not list((trust_home / "entity_settings").glob("*.tmp"))


def test_simultaneous_redemptions_have_exactly_one_winner(trust_home):
    code = trust.create_pairing_code("shared")
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(
            pool.map(
                lambda index: trust.redeem_pairing_code(
                    "shared", f"sender-{index}", code
                ),
                range(24),
            )
        )
    assert sum(accepted) == 1
    projection = trust.provider_trust("shared")
    assert len(projection["allowed_senders"]) == 1
    assert projection["pairing_active"] is False
    winner = accepted.index(True)
    assert projection["allowed_senders"][0]["sender_id"] == f"sender-{winner}"
    assert projection["allowed_senders"][0]["via"] == "pairing"


def test_pairing_consumption_and_grant_publish_in_one_atomic_record(trust_home):
    code = trust.create_pairing_code("one")
    destination = _entity_settings_path("channel_trust")
    snapshots = []

    def capture_committed_state(path):
        if path == destination:
            snapshots.append(json.loads(path.read_text()))

    register_post_write_hook(capture_committed_state)
    try:
        assert trust.redeem_pairing_code("one", "alice", code)
    finally:
        unregister_post_write_hook(capture_committed_state)
    assert len(snapshots) == 1
    assert snapshots[0]["one"]["pairing"] == {}
    assert snapshots[0]["one"]["allowed_senders"]["alice"]["via"] == "pairing"


@pytest.mark.parametrize(
    "deadline", ["1970-01-01T00:00:00+00:00", "invalid", None, "2000-01-01T00:00:00"]
)
def test_expired_or_unusable_deadlines_are_consumed_without_grant(trust_home, deadline):
    code = trust.create_pairing_code("one")
    record = trust._read_store()["one"]
    record["pairing"]["expires_at"] = deadline
    trust._save_provider("one", record)
    assert trust.redeem_pairing_code("one", "alice", code) is False
    assert trust.provider_trust("one")["pairing_active"] is False
    assert not trust.is_allowed_sender("one", "alice")
    assert any(row.get("outcome") == "expired_code" for row in sel().recent(20))


def test_pairing_expiry_boundary_and_wrong_attempt_preserve_ticket():
    deadline = datetime(2030, 1, 1, tzinfo=timezone.utc)
    ticket = trust._PairingTicket(trust._hash_code("00123456"), deadline.isoformat())
    assert ticket.verdict("00123456", deadline) == "paired"
    assert ticket.verdict("00123457", deadline) == "wrong_code"
    assert (
        ticket.verdict("00123456", deadline + timedelta(microseconds=1))
        == "expired_code"
    )


def test_projection_omits_code_digest_and_contact_history(trust_home):
    code = trust.create_pairing_code("one")
    trust.note_unknown_sender(None, "one", "unknown-person")
    trust.allow_sender("one", "approved", name="Alice")
    projected = trust.provider_trust("one")
    blob = json.dumps(projected)
    assert trust._hash_code(code) not in blob and "code_hash" not in blob
    assert "unknown-person" not in blob and "rate" not in projected
    assert [row["sender_id"] for row in projected["allowed_senders"]] == ["approved"]


def test_contact_window_survives_report_reset_and_concurrent_contacts(trust_home):
    sel()
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(
                lambda _: trust.note_unknown_sender(None, "one", "stranger"), range(20)
            )
        )
    assert sum(outcomes) == 1
    trust.reset_inbound_reports()
    assert trust.note_unknown_sender(None, "one", "stranger") is False
    rows = [row for row in sel().recent(100) if row.get("outcome") == "unknown_sender"]
    assert len(rows) == 1
    store = trust._read_store()
    store["one"]["rate"]["stranger"] = (
        trust._now() - timedelta(seconds=trust.UNKNOWN_SENDER_RENOTIFY_SECS + 1)
    ).isoformat()
    trust._write_store(store)
    assert trust.note_unknown_sender(None, "one", "stranger") is True


def test_visible_report_window_is_bounded_and_reannounces_evicted_subjects(trust_home):
    first = "oldest"
    assert trust._visible_line_is_deduped(first) is False
    assert trust._visible_line_is_deduped(first) is True
    for index in range(trust._REPORT_WINDOW_MAX):
        assert trust._visible_line_is_deduped(f"subject-{index}") is False
    assert len(trust._REPORTED) == trust._REPORT_WINDOW_MAX
    assert first not in trust._REPORTED
    assert trust._visible_line_is_deduped(first) is False
    trust._REPORTED[first] = trust._now() - timedelta(
        seconds=trust.UNKNOWN_SENDER_RENOTIFY_SECS
    )
    assert trust._visible_line_is_deduped(first) is False


def test_group_and_dm_decisions_preserve_fences_and_never_log_body(trust_home, caplog):
    caplog.set_level(logging.DEBUG, logger=trust.__name__)
    body = "private message body"
    trust.track("one", "room")
    unknown = trust.guard_inbound(
        None, "one", "stranger", channel_id="room", is_dm=False, text=body
    )
    assert unknown.allowed and "untrusted_content" in unknown.fenced_text
    trust.allow_sender("one", "owner")
    owner = trust.guard_inbound(
        None, "one", "owner", channel_id="room", is_dm=False, text=body
    )
    assert owner.allowed and owner.fenced_text == ""
    assert body not in caplog.text
    code = trust.create_pairing_code("one")
    paired = trust.guard_inbound(None, "one", "new", text="  " + code + "  ")
    assert not paired.allowed and paired.meta == {"paired": True}
    assert paired.canned_reply == trust.CANNED_PAIRED_REPLY
    assert trust.guard_inbound(None, "one", "new", text=body).allowed


@pytest.mark.asyncio
async def test_real_dashboard_notification_persists_actionable_owner_decision(
    trust_home, monkeypatch
):
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    sessions = ConversationDirectory(AppConfig.load())
    state = dashboard_state.ConsoleState(sessions=sessions, start_time=0)
    assert trust.note_unknown_sender(state, "local-channel", "visitor", "Visitor")
    assert not trust.note_unknown_sender(state, "local-channel", "visitor", "Visitor")
    persisted = dashboard_state._load_notifications()
    actionable = [
        note for note in persisted if note.get("event") == "channel.unknown_sender"
    ]
    assert len(actionable) == 1
    note = actionable[0]
    assert note["actions"] == ["allow", "deny"]
    assert note["provider"] == "local-channel" and note["sender_name"] == "Visitor"
    assert trust.apply_trust_action(
        " ALLOW ", note["provider"], note["sender_id"], note["sender_name"]
    )
    assert trust.guard_inbound(state, "local-channel", "visitor", text="hello").allowed
    assert (
        trust.apply_trust_action("deny", note["provider"], note["sender_id"]) is False
    )
    assert not trust.is_allowed_sender("local-channel", "visitor")


@pytest.mark.asyncio
async def test_real_http_projection_and_revoke_follow_persisted_trust(trust_home):
    trust.allow_sender("email", "visitor@example.test", "Visitor")
    trust.create_pairing_code("email")
    app = web.Application()
    app.router.add_get("/api/channels/trust", routes.api_channel_trust)
    app.router.add_delete(
        "/api/channels/trust/{provider}/senders/{sender_id}",
        routes.api_channel_trust_revoke,
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/channels/trust")
        assert response.status == 200
        body = await response.json()
        assert (
            body["providers"][0]["allowed_senders"][0]["sender_id"]
            == "visitor@example.test"
        )
        assert "code_hash" not in json.dumps(body)
        response = await client.delete(
            "/api/channels/trust/email/senders/visitor%40example.test"
        )
        assert response.status == 200 and (await response.json())["ok"] is True
        response = await client.delete(
            "/api/channels/trust/email/senders/visitor%40example.test"
        )
        assert response.status == 404
        assert (await response.json())["error"][
            "code"
        ] == "channel_trust_sender_unknown"
    assert not trust.is_allowed_sender("email", "visitor@example.test")


def test_store_uses_current_home_for_every_operation(trust_home, monkeypatch):
    trust.allow_sender("one", "first")
    second = trust_home / "another-home"
    second.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(second))
    assert trust.list_providers() == []
    trust.allow_sender("one", "second")
    assert trust.is_allowed_sender("one", "first") is False
    monkeypatch.setenv("GIDEON_HOME", str(trust_home))
    assert trust.is_allowed_sender("one", "first") is True
    assert trust.is_allowed_sender("one", "second") is False
