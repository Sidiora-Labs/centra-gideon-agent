"""Real channel admission, conversation state and history journal checks."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations import channel_inbound as inbound
from gideon.integrations import channel_trust as trust
from gideon.integrations.channel_history import ChannelHistory, HistoryEntry
from gideon.integrations.channel_transports.base import ChannelMessage
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security import trust_mode
from gideon.security.sel import sel


@pytest.fixture
def flow_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"providers": []}')
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    inbound.reset_admissions()
    trust.reset_inbound_reports()
    yield tmp_path
    inbound.reset_admissions()
    trust.reset_inbound_reports()


def test_simultaneous_pairing_redelivery_returns_one_original_verdict(flow_home):
    code = trust.create_pairing_code("local")
    message = ChannelMessage("room", code, sender="visitor", message_id="pairing")
    sel()
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(
            pool.map(lambda _: inbound.admit(None, "local", message), range(32))
        )
    assert all(decision is decisions[0] for decision in decisions)
    assert decisions[0].reason == "paired" and not decisions[0].allowed
    assert trust.is_allowed_sender("local", "visitor")
    assert not trust._pairing_code_outstanding("local")
    followup = ChannelMessage("room", "hello", sender="visitor", message_id="next")
    assert inbound.admit(None, "local", followup).allowed


def test_denial_stays_bound_to_message_after_owner_grant(flow_home):
    message = ChannelMessage("room", "hello", sender="visitor", message_id="one")
    denied = inbound.admit(None, "local", message)
    trust.allow_sender("local", "visitor")
    assert inbound.admit(None, "local", message) is denied
    assert not denied.allowed
    inbound.reset_admissions()
    assert inbound.admit(None, "local", message).allowed


def test_admission_eviction_is_fifo_and_hits_do_not_extend_lifetime(
    flow_home, monkeypatch
):
    monkeypatch.setattr(inbound, "_ADMISSION_CACHE_MAX", 3)
    trust.allow_sender("local", "owner")
    messages = [
        ChannelMessage("room", str(i), sender="owner", message_id=str(i))
        for i in range(4)
    ]
    first = inbound.admit(None, "local", messages[0])
    inbound.admit(None, "local", messages[1])
    inbound.admit(None, "local", messages[2])
    assert inbound.admit(None, "local", messages[0]) is first
    inbound.admit(None, "local", messages[3])
    assert len(inbound._ADMITTED) == 3
    assert inbound._message_key("local", messages[0]) not in inbound._ADMITTED
    assert inbound.admit(None, "local", messages[0]) is not first


def test_zero_capacity_still_returns_the_gate_decision(flow_home, monkeypatch):
    monkeypatch.setattr(inbound, "_ADMISSION_CACHE_MAX", 0)
    trust.allow_sender("local", "owner")
    assert inbound.admit(
        None, "local", ChannelMessage("room", "hello", sender="owner")
    ).allowed
    assert not inbound._ADMITTED


def test_synthetic_identity_hashes_body_and_partitions_provider():
    first = ChannelMessage("room", "private message", ts=42)
    same = ChannelMessage("room", "private message", ts=42)
    changed = ChannelMessage("room", "different message", ts=42)
    key = inbound._message_key("alpha", first)
    assert "private message" not in key
    assert key == inbound._message_key("alpha", same)
    assert key != inbound._message_key("alpha", changed)
    assert key != inbound._message_key("beta", first)
    first.message_id = "server-id"
    assert inbound._message_key("alpha", first) == "alpha|id|server-id"


@pytest.mark.asyncio
async def test_real_gateway_denial_creates_no_conversation(flow_home):
    config = AppConfig.load()
    gateway = RuntimeCoordinator(config, no_dashboard=True, no_crons=True, no_open=True)
    state = ConsoleState(sessions=ConversationDirectory(config), start_time=0)
    gateway.dashboard_state = state
    result = await gateway.deliver_channel_inbound(
        "local", ChannelMessage("room", "hello", sender="visitor", message_id="denied")
    )
    assert not result.allowed
    assert state._sessions == {}
    assert not state._background_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("thread", ["", "thread-42"])
async def test_real_gateway_reuses_link_and_queues_fenced_original(flow_home, thread):
    config = AppConfig.load()
    sessions = ConversationDirectory(config)
    state = ConsoleState(sessions=sessions, start_time=0)
    gateway = RuntimeCoordinator(config, no_dashboard=True, no_crons=True, no_open=True)
    gateway.dashboard_state = state
    trust.track("local", "room")
    raw = "read https://visitor:private-password@example.test/path"
    message = ChannelMessage(
        "room", raw, sender="visitor", thread_id=thread, message_id="one"
    )
    resolved = inbound._SessionIngress(state, "local", message, raw).resolve()
    assert resolved._app == "local"
    assert resolved._channel_thread_ts == (thread or "room")
    assert resolved._channel_id == "room"
    assert len(state._sessions) == 1
    release = asyncio.Event()
    active = asyncio.create_task(release.wait())
    resolved.task = active
    try:
        result = await gateway.deliver_channel_inbound("local", message, is_dm=False)
        assert result.allowed and "untrusted_content" in result.fenced_text
        assert state.get_linked_session(thread or "room") is resolved
        assert resolved.queue_depth == 1
        assert resolved.queue_pop()["content"] == result.fenced_text
        user_lines = [
            item["content"] for item in resolved.messages if item["role"] == "user"
        ]
        assert len(user_lines) == 1
        assert "untrusted_content" in user_lines[0]
        assert "private-password" not in user_lines[0]
        assert resolved.task is active
        assert len(state._sessions) == 1
        from gideon.interfaces.dashboard.chat import _history_key_for

        assert sessions.get_channel_link(_history_key_for(resolved.key)) == (
            thread or "room",
            "room",
        )
    finally:
        release.set()
        await active


def test_observe_load_merges_disk_before_memory_then_applies_capacity(flow_home):
    history = ChannelHistory(
        max_entries=2, observe_max_entries=3, history_dir=flow_home
    )
    history.push("room", "owner", "in memory")
    rows = [
        {"user": "remote", "text": f"disk {i}", "ts": time.time()} for i in range(4)
    ]
    (flow_home / "room.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    history.set_observe("room")
    assert [entry.text for entry in history._channels["room"]] == [
        "disk 2",
        "disk 3",
        "in memory",
    ]
    history.unset_observe("room")
    assert [entry.text for entry in history._channels["room"]] == [
        "disk 3",
        "in memory",
    ]
    assert not (flow_home / "room.jsonl").exists()


def test_history_clear_preserves_observation_and_can_reload_journal(flow_home):
    history = ChannelHistory(history_dir=flow_home)
    history.set_observe("room")
    history.push("room", "owner", "persisted")
    history.clear("room")
    assert history.entry_count("room") == 0
    assert (flow_home / "room.jsonl").exists()
    history.set_observe("room")
    assert "persisted" in history.context_for("room")
    history.push("room", "owner", "continued")
    assert len((flow_home / "room.jsonl").read_text().splitlines()) == 2


def test_invalid_journal_shapes_do_not_discard_valid_neighbor_records(flow_home):
    invalid = [
        [],
        None,
        "text",
        {"ts": "yesterday"},
        {"ts": float("nan")},
        {"ts": time.time(), "text": []},
    ]
    good = {
        "user": "owner",
        "text": "valid λ",
        "thread_ts": "thread",
        "ts": time.time(),
    }
    path = flow_home / "room.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in [*invalid, good]), encoding="utf-8"
    )
    history = ChannelHistory(history_dir=flow_home)
    history.set_observe("room")
    assert history.entry_count("room") == 1
    assert "valid λ" in history.context_for("room", "thread")


def test_expired_replay_compacts_only_retained_capacity(flow_home):
    records = [{"user": "owner", "text": "expired", "ts": time.time() - 500}]
    records.extend(
        {"user": "owner", "text": str(i), "ts": time.time()} for i in range(5)
    )
    path = flow_home / "room.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))
    history = ChannelHistory(
        observe_max_entries=2, observe_ttl_secs=100, history_dir=flow_home
    )
    history.set_observe("room")
    assert [entry.text for entry in history._channels["room"]] == ["3", "4"]
    assert [json.loads(line)["text"] for line in path.read_text().splitlines()] == [
        "3",
        "4",
    ]


def test_concurrent_observed_appends_keep_complete_jsonl_records(flow_home):
    history = ChannelHistory(observe_max_entries=100, history_dir=flow_home)
    history.set_observe("room")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(lambda i: history.push("room", "owner", f"message {i}"), range(80))
        )
    rows = [
        json.loads(line) for line in (flow_home / "room.jsonl").read_text().splitlines()
    ]
    assert len(rows) == history.entry_count("room") == 80
    assert {row["text"] for row in rows} == {f"message {i}" for i in range(80)}
    replayed = ChannelHistory(history_dir=flow_home)
    replayed.set_observe("room")
    assert replayed.entry_count("room") == 80


def test_sibling_prefix_and_symlink_cannot_escape_history_root(flow_home):
    root = flow_home / "history"
    sibling = flow_home / "history-other"
    root.mkdir()
    sibling.mkdir()
    (root / "outside").symlink_to(sibling, target_is_directory=True)
    history = ChannelHistory(history_dir=root)
    for identifier in ("../history-other/room", "outside/room"):
        history.set_observe(identifier)
        history.push(identifier, "owner", "must remain in memory")
        assert history.entry_count(identifier) == 1
        assert history._observe_path(identifier) is None
    assert list(sibling.iterdir()) == []
    history.set_observe("valid")
    history.push("valid", "owner", "persist")
    assert (root / "valid.jsonl").exists()


def test_empty_top_level_frame_and_display_name_clipping_are_preserved():
    history = ChannelHistory()
    history.push("room", "owner", "λ" * 301, thread_ts="thread")
    assert (
        history.context_for("room")
        == "[Recent channel messages for context:]\n\n[End of channel context]\n\n"
    )
    history.set_user_name("owner", "Owner")
    history.set_user_name("owner", "")
    rendered = history.context_for("room", "thread")
    assert "Owner (" in rendered and "λ" * 300 + "…" in rendered
    assert "λ" * 301 not in rendered


def test_mode_switch_keeps_ephemeral_front_entry_eviction_semantics(flow_home):
    history = ChannelHistory(history_dir=flow_home, observe_ttl_secs=1)
    history.push("room", "owner", "ephemeral")
    history.set_observe("room")
    history._channels["room"].append(
        HistoryEntry("owner", "old observed", wall_ts=time.time() - 20)
    )
    assert "old observed" in history.context_for("room")
    history._channels["room"].popleft()
    assert history.context_for("room") == ""


def test_repeated_enable_replays_existing_journal_as_before(flow_home):
    history = ChannelHistory(history_dir=flow_home)
    history.set_observe("room")
    history.push("room", "owner", "one")
    history.set_observe("room")
    assert history.entry_count("room") == 2
    assert len((flow_home / "room.jsonl").read_text().splitlines()) == 1
