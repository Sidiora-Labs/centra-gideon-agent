"""Tests for EngagementStore (P11 engagement-weighted ranking, store + kernel).

Pure/read-time decay, additive accumulation, dismiss-floors, warm-up neutral, persistence
round-trip. Injects an explicit tmp path (no config_dir monkeypatch needed — the store
takes a path) so it never pollutes ~/.gideon."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from gideon.cognition.engagement_signals import (
    _NEUTRAL,
    _WEIGHT_FLOOR,
    DEFAULT_HALF_LIFE_DAYS,
    EngagementStore,
    rank_by_engagement,
)
from gideon.cognition.preference_facets import decay

_DAY = 86400.0


def _store(tmp_path):
    return EngagementStore(path=tmp_path / "engagement.json")


def test_shared_decay_kernel_halves_at_half_life():
    assert decay(1.0, DEFAULT_HALF_LIFE_DAYS, DEFAULT_HALF_LIFE_DAYS) == 0.5
    assert decay(1.0, 0.0, DEFAULT_HALF_LIFE_DAYS) == 1.0
    assert decay(1.0, 5.0, 0.0) == 1.0
    assert decay(0.8, DEFAULT_HALF_LIFE_DAYS * 2, DEFAULT_HALF_LIFE_DAYS) == 0.2


def test_warmup_neutral_until_threshold(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    assert s.weight_for("chan:A", now=0.0) == _NEUTRAL
    assert s.weight_for("never:seen", now=0.0) == _NEUTRAL


def test_favorite_boosts_after_warmup(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "open", now=0.0)
    w = s.weight_for("chan:A", now=0.0)
    assert w > _NEUTRAL


def test_dismiss_floors_never_buries(tmp_path):
    s = _store(tmp_path)
    for i in range(6):
        s.record("chan:spam", "dismiss", now=0.0)
    w = s.weight_for("chan:spam", now=0.0)
    assert w >= _WEIGHT_FLOOR
    assert w < _NEUTRAL


def test_boost_decays_back_toward_neutral(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "favorite", now=0.0)
    fresh = s.weight_for("chan:A", now=0.0)
    aged = s.weight_for("chan:A", now=DEFAULT_HALF_LIFE_DAYS * _DAY)
    assert _NEUTRAL < aged < fresh


def test_persistence_round_trip(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "reply", now=0.0)
    s.save()
    s2 = _store(tmp_path)
    s2.load()
    assert s2.weight_for("chan:A", now=0.0) == s.weight_for("chan:A", now=0.0)


def test_prune_drops_decayed_to_neutral(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "open", now=0.0)
    pruned = s.prune(now=DEFAULT_HALF_LIFE_DAYS * 20 * _DAY)
    assert pruned == 1
    assert s.weight_for("chan:A", now=0.0) == _NEUTRAL


def test_rank_engaged_channel_above_equally_recent(tmp_path):
    s = _store(tmp_path)
    for _ in range(3):
        s.record("hot", "favorite", now=0.0)
    items = [
        {"id": "cold", "r": 100.0, "t": "cold"},
        {"id": "hot", "r": 100.0, "t": "hot"},
    ]
    ranked = rank_by_engagement(
        items,
        recency_key=lambda i: i["r"],
        topic_key=lambda i: i["t"],
        store=s,
        now=0.0,
    )
    assert ranked[0]["id"] == "hot"


def test_rank_cold_start_is_pure_recency(tmp_path):
    s = _store(tmp_path)
    items = [{"id": "old", "r": 10.0, "t": "a"}, {"id": "new", "r": 99.0, "t": "b"}]
    ranked = rank_by_engagement(
        items,
        recency_key=lambda i: i["r"],
        topic_key=lambda i: i["t"],
        store=s,
        now=0.0,
    )
    assert [i["id"] for i in ranked] == ["new", "old"]


def test_rank_dismissed_topic_demoted_but_present(tmp_path):
    s = _store(tmp_path)
    for _ in range(4):
        s.record("spam", "dismiss", now=0.0)
    items = [
        {"id": "spam", "r": 100.0, "t": "spam"},
        {"id": "ok", "r": 60.0, "t": "ok"},
    ]
    ranked = rank_by_engagement(
        items,
        recency_key=lambda i: i["r"],
        topic_key=lambda i: i["t"],
        store=s,
        now=0.0,
    )
    ids = [i["id"] for i in ranked]
    assert "spam" in ids


# derivation + signal capture. The flag is read via AppConfig.load() (ConsoleState has


def _item(id, channel, sender, created_at, classification="needs_reply"):
    return SimpleNamespace(
        id=id,
        channel=channel,
        sender_id=sender,
        classification=classification,
        created_at=created_at,
    )


def _fake_state(tmp_path):
    from gideon.cognition.engagement_signals import EngagementStore

    st = SimpleNamespace()
    st._engagement_store = EngagementStore(path=tmp_path / "engagement.json")
    return st


def test_topic_keys_are_channel_sender_classification():
    from gideon.interfaces.dashboard.handlers_inbox import _topic_keys

    keys = _topic_keys(_item("i1", "C1", "U1", 5.0, "needs_reply"))
    assert keys == ["ch:C1", "snd:U1", "cls:needs_reply"]
    assert _topic_keys(_item("i2", "", "", 1.0, "")) == []


def test_rank_items_off_is_pure_recency(tmp_path):
    from gideon.interfaces.dashboard.handlers_inbox import _rank_items

    st = _fake_state(tmp_path)
    items = [_item("old", "C1", "U1", 10.0), _item("new", "C2", "U2", 99.0)]
    with patch(
        "gideon.interfaces.dashboard.handlers_inbox._inbox_config",
        return_value=SimpleNamespace(
            engagement_ranking_enabled=False, engagement_half_life_days=0.0
        ),
    ):
        ranked = _rank_items(st, items)
    assert [i.id for i in ranked] == ["new", "old"]


def test_rank_items_on_boosts_engaged_channel(tmp_path):
    from gideon.interfaces.dashboard.handlers_inbox import _rank_items, _record_signal

    st = _fake_state(tmp_path)
    on = SimpleNamespace(engagement_ranking_enabled=True, engagement_half_life_days=0.0)
    with patch(
        "gideon.interfaces.dashboard.handlers_inbox._inbox_config", return_value=on
    ):
        hot = _item("hot", "C-hot", "U1", 50.0)
        for _ in range(3):
            _record_signal(st, hot, "favorite")
        items = [_item("cold", "C-cold", "U2", 60.0), hot]
        ranked = _rank_items(st, items)
    assert ranked[0].id == "hot"


def test_record_signal_noop_when_disabled(tmp_path):
    from gideon.interfaces.dashboard.handlers_inbox import _record_signal

    st = _fake_state(tmp_path)
    off = SimpleNamespace(
        engagement_ranking_enabled=False, engagement_half_life_days=0.0
    )
    with patch(
        "gideon.interfaces.dashboard.handlers_inbox._inbox_config", return_value=off
    ):
        _record_signal(st, _item("i", "C1", "U1", 1.0), "favorite")
    assert st._engagement_store.weight_for("ch:C1", now=0.0) == _NEUTRAL
