"""Tests for the dashboard module."""

import json
from unittest.mock import MagicMock

import pytest

from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _fmt_duration,
    _load_notifications,
    _maybe_trim_notifications,
    _persist_notification,
)


class TestDashboard:
    def test_fmt_duration_minutes(self) -> None:
        assert _fmt_duration(125) == "2m 5s"

    def test_fmt_duration_hours(self) -> None:
        assert _fmt_duration(3661) == "1h 1m"

    def test_fmt_duration_zero(self) -> None:
        assert _fmt_duration(0) == "0m 0s"

    def test_state_init(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = ConsoleState(
            sessions=MagicMock(count=3),
            start_time=0.0,
        )
        assert state.sessions.count == 3
        assert not hasattr(state, "messages_received")

    def test_state_init_with_channel_delivery(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
            owner_id="U123",
        )
        assert state.channel_delivery is None
        state.channel_delivery = MagicMock()
        assert state.channel_delivery is not None
        assert state.owner_id == "U123"


class TestNotificationPersistence:
    def test_persist_and_load(self, monkeypatch, tmp_path) -> None:
        """Notifications are persisted to JSONL and loaded on restart."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        _persist_notification({"kind": "cron", "title": "Job A", "body": "result"})
        _persist_notification({"kind": "subagent", "title": "Sub B", "body": "done"})

        loaded = _load_notifications()
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Job A"
        assert loaded[1]["title"] == "Sub B"

    def test_load_empty(self, monkeypatch, tmp_path) -> None:
        """Loading from nonexistent file returns empty list."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        assert _load_notifications() == []

    def test_load_corrupted_lines_skipped(self, monkeypatch, tmp_path) -> None:
        """Corrupted JSON lines are skipped during load."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        path = tmp_path / "notifications.jsonl"
        lines = [
            json.dumps({"kind": "cron", "title": "Good", "body": "ok"}),
            "this is not json",
            json.dumps({"kind": "cron", "title": "Also good", "body": "ok"}),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        loaded = _load_notifications()
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Good"
        assert loaded[1]["title"] == "Also good"

    def test_trim_large_file(self, monkeypatch, tmp_path) -> None:
        """File is trimmed when exceeding 2x max notifications."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state._MAX_PERSISTED_NOTIFICATIONS", 5
        )
        path = tmp_path / "notifications.jsonl"
        lines: list[str] = []
        for i in range(11):
            lines.append(json.dumps({"kind": "cron", "title": f"n{i}", "body": "x"}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _maybe_trim_notifications(path)

        remaining = path.read_text(encoding="utf-8").splitlines()
        assert len(remaining) == 5
        assert json.loads(remaining[0])["title"] == "n6"
        assert json.loads(remaining[-1])["title"] == "n10"

    def test_notify_persists(self, monkeypatch, tmp_path) -> None:
        """ConsoleState.notify() persists to disk."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )
        state.notify("cron", "Test Job", "Result text")

        assert len(state._notification_log) == 1
        assert state._notification_log[0]["title"] == "Test Job"

        loaded = _load_notifications()
        assert len(loaded) == 1
        assert loaded[0]["title"] == "Test Job"
        assert "ts" in loaded[0]

    def test_delete_notifications_for_loop(self, monkeypatch, tmp_path) -> None:
        """Deleting a loop purges its notifications (no dead 'Open goal' links)."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )
        state.notify(
            "success", "Goal loop complete", "done", meta={"loop_id": "aaaa1111"}
        )
        state.notify("error", "Goal loop failed", "boom", meta={"loop_id": "bbbb2222"})
        state.notify("info", "Goal loop progress", "p", meta={"loop_id": "aaaa1111"})
        state.notify("cron", "Unrelated", "x")
        removed = state.delete_notifications_for_loop("aaaa1111")
        assert removed == 2
        titles = [n["title"] for n in state._notification_log]
        assert "Goal loop complete" not in titles and "Goal loop progress" not in titles
        assert "Goal loop failed" in titles and "Unrelated" in titles
        assert len(_load_notifications()) == 2
        assert state.delete_notifications_for_loop("") == 0

    def test_state_loads_existing_on_init(self, monkeypatch, tmp_path) -> None:
        """ConsoleState.__init__ loads existing notifications from disk."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        _persist_notification({"kind": "cron", "title": "Old", "body": "data"})
        _persist_notification({"kind": "cron", "title": "Old2", "body": "data2"})

        state = ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )
        assert len(state._notification_log) == 2
        assert state._notification_log[0]["title"] == "Old"
        assert state._notification_log[1]["title"] == "Old2"


class TestUnreadDerived:
    """unread is DERIVED from unresolved INBOX items — no cached counter, no log flags.

    Plan 42 T5.2 moved the badge off the notification log's `acked` flags. The two stores had
    become two answers to one question: the log tracked "was a toast acknowledged", the inbox
    tracks "is this dealt with". A user who handled a request in the inbox still saw a badge,
    and dismissing a toast cleared the badge for outstanding work.
    """

    def _state(self, monkeypatch, tmp_path) -> ConsoleState:
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr("gideon.integrations.inbox.config_dir", lambda: tmp_path)
        return ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )

    def _add_item(self, tmp_path, status: str, item_id: str = "") -> str:
        from gideon.integrations.inbox import InboxItem, InboxStore

        store = InboxStore(tmp_path / "inbox.json")
        store.load()
        item = InboxItem(
            id=item_id or f"C1_{len(store.items) + 1}.0",
            channel="C1",
            channel_name="#t",
            thread_ts=None,
            message="m",
            sender_id="U1",
            sender_name="A",
        )
        item.status = status
        store.add(item)
        store.save()
        return item.id

    def test_counts_pending_inbox_items(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.unread_count() == 0
        self._add_item(tmp_path, "pending")
        self._add_item(tmp_path, "pending")
        state._inbox_store = None
        assert state.unread_count() == 2

    def test_seen_does_not_count(self, monkeypatch, tmp_path) -> None:
        """The badge means "new since you last looked"; opening an item marks it SEEN.

        Counting SEEN would keep the badge lit until everything was RESOLVED, which is what
        the list itself is for.
        """
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, "pending")
        self._add_item(tmp_path, "seen")
        state._inbox_store = None
        assert state.unread_count() == 1

    @pytest.mark.parametrize("resolved", ["handled", "dismissed", "sent"])
    def test_resolved_items_do_not_count(self, monkeypatch, tmp_path, resolved) -> None:
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, resolved)
        state._inbox_store = None
        assert state.unread_count() == 0

    def test_notifications_no_longer_drive_the_badge(
        self, monkeypatch, tmp_path
    ) -> None:
        """THE demotion, asserted directly: a delivered toast is not unresolved work.

        This is the one accepted state break of plan 42 — a user's badge resets once on
        upgrade because it stops counting unacked log entries.
        """
        state = self._state(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        state.notify("cron", "B", "b")
        assert len(state._notification_log) == 2, "the log still records the delivery"
        assert state.unread_count() == 0, "but it does not claim your attention"

    def test_clearing_notifications_does_not_change_the_badge(
        self, monkeypatch, tmp_path
    ) -> None:
        """Previously, clearing toasts zeroed the badge — hiding outstanding work."""
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, "pending")
        state._inbox_store = None
        assert state.unread_count() == 1
        state.clear_notifications()
        state._inbox_store = None
        assert state.unread_count() == 1, "clearing the audit must not hide real work"

    def test_survives_restart(self, monkeypatch, tmp_path) -> None:
        """A fresh state object reads the persisted inbox, not an in-memory counter."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr("gideon.integrations.inbox.config_dir", lambda: tmp_path)
        self._add_item(tmp_path, "pending")
        self._add_item(tmp_path, "handled")
        state = ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )
        assert state.unread_count() == 1

    def test_fails_to_zero_rather_than_raising(self, monkeypatch, tmp_path) -> None:
        """A badge is chrome; a broken read must not break the sessions payload."""
        state = self._state(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "gideon.integrations.inbox.InboxStore",
            MagicMock(side_effect=OSError("gone")),
        )
        state._inbox_store = None
        assert state.unread_count() == 0

    def test_prefers_the_running_services_store(self, monkeypatch, tmp_path) -> None:
        """A live gateway's in-memory store is authoritative over a fresh disk read."""
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, "pending")
        svc = MagicMock()
        svc.inbox = MagicMock(items={})
        state._inbox_svc = svc
        assert state.unread_count() == 0, "the service's store won"

    def test_no_cached_counter_attribute(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert not hasattr(state, "_unread_count")
        assert not hasattr(state, "mark_notifications_read")


class TestNotificationRemovalBroadcast:
    """delete/clear must broadcast a WS event so open views don't stay stale."""

    def _state_with_ws(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        state = ConsoleState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )
        ws = MagicMock(closed=False)
        ws.send_str = AsyncMock()
        state.register_ws(ws)
        return state, ws

    def _sent_types(self, ws) -> list[tuple[str, object]]:
        out = []
        for call in ws.send_str.call_args_list:
            msg = json.loads(call[0][0])
            out.append((msg["type"], msg.get("data")))
        return out

    def test_delete_notification_broadcasts(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        ts = state._notification_log[0]["ts"]
        ws.send_str.reset_mock()
        assert state.delete_notification(ts)
        assert ("notification_removed", {"ts": ts}) in self._sent_types(ws)

    def test_delete_miss_does_not_broadcast(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        assert not state.delete_notification("no-such-ts")
        assert self._sent_types(ws) == []

    def test_delete_for_loop_broadcasts(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        state.notify("info", "L", "x", meta={"loop_id": "aaaa1111"})
        state.notify("info", "L2", "y", meta={"loop_id": "aaaa1111"})
        removed_ts = [n["ts"] for n in state._notification_log]
        ws.send_str.reset_mock()
        assert state.delete_notifications_for_loop("aaaa1111") == 2
        assert ("notification_removed", {"ts": removed_ts}) in self._sent_types(ws)

    def test_clear_broadcasts_wildcard(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        ws.send_str.reset_mock()
        state.clear_notifications()
        assert ("notification_removed", {"ts": "*"}) in self._sent_types(ws)


class TestNotificationRetention:
    """#49 — changing one notification must not erase the history behind it.

    ``_rewrite_notifications`` re-applied the ``[-200:]`` cap on every rewrite, and every
    ack/unack/delete rewrote from ``_notification_log`` — the tail this process loaded at
    boot. So one acknowledgement republished that tail as the whole file: a 350-row history
    became 200 rows, permanently, with nothing to show for it.
    """

    def _state(self, monkeypatch, tmp_path, rows: int = 350) -> ConsoleState:
        """A fresh process over an existing *rows*-row history (the real load path)."""
        monkeypatch.setattr(
            "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
        )
        path = tmp_path / "notifications.jsonl"
        path.write_text(
            "".join(
                json.dumps({"kind": "cron", "title": f"n{i}", "ts": f"t{i:04d}"}) + "\n"
                for i in range(rows)
            ),
            encoding="utf-8",
        )
        return ConsoleState(sessions=MagicMock(count=0), start_time=0.0)

    def _on_disk(self, tmp_path) -> list[dict]:
        return [
            json.loads(line)
            for line in (tmp_path / "notifications.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]

    def test_boot_loads_the_tail_but_the_file_keeps_everything(
        self, monkeypatch, tmp_path
    ) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert len(state._notification_log) == 200
        assert len(self._on_disk(tmp_path)) == 350

    def test_ack_keeps_every_older_row(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.ack_notification("t0349")
        rows = self._on_disk(tmp_path)
        assert len(rows) == 350
        assert rows[0]["ts"] == "t0000"
        assert rows[-1]["acked"] is True

    def test_unack_keeps_every_older_row(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.ack_notification("t0300") and state.unack_notification("t0300")
        rows = self._on_disk(tmp_path)
        assert len(rows) == 350
        assert {r["ts"] for r in rows if r.get("acked")} == set()
        assert rows[0]["ts"] == "t0000"

    def test_delete_removes_exactly_one_row(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.delete_notification("t0349")
        rows = self._on_disk(tmp_path)
        assert len(rows) == 349
        assert rows[0]["ts"] == "t0000"
        assert "t0349" not in {r["ts"] for r in rows}

    def test_delete_for_loop_only_drops_its_own_rows(
        self, monkeypatch, tmp_path
    ) -> None:
        state = self._state(monkeypatch, tmp_path)
        path = tmp_path / "notifications.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "info", "ts": "t9999", "loop_id": "L1"}) + "\n")
        state._notification_log.append({"kind": "info", "ts": "t9999", "loop_id": "L1"})
        assert state.delete_notifications_for_loop("L1") == 1
        assert len(self._on_disk(tmp_path)) == 350

    def test_a_fresh_process_that_acks_loses_nothing(self, monkeypatch, tmp_path):
        """The reported shape end to end: restart, ack the newest item, restart again."""
        first = self._state(monkeypatch, tmp_path)
        assert first.ack_notification("t0349")
        second = ConsoleState(sessions=MagicMock(count=0), start_time=0.0)
        assert len(second._notification_log) == 200
        assert len(self._on_disk(tmp_path)) == 350
        assert second.delete_notification("t0348")
        assert len(self._on_disk(tmp_path)) == 349

    def test_append_trims_memory_and_disk_together(self, monkeypatch, tmp_path) -> None:
        """The cap still exists — it just lives on the append path, where both sides of it
        move at once. 2x the cap on disk is the trim point; memory follows to the same rows.
        """
        state = self._state(monkeypatch, tmp_path, rows=400)
        assert len(state._notification_log) == 200
        state.notify("cron", "newest", "x")
        on_disk = self._on_disk(tmp_path)
        assert len(on_disk) == 200
        assert len(state._notification_log) == 200
        assert on_disk[-1]["title"] == "newest"
        assert state._notification_log[-1]["title"] == "newest"
        assert [n["ts"] for n in state._notification_log] == [r["ts"] for r in on_disk]

    def test_an_ack_never_trims(self, monkeypatch, tmp_path) -> None:
        """Vacuity pair for the trim test: the cap must not fire on a change."""
        state = self._state(monkeypatch, tmp_path, rows=400)
        assert state.ack_notification("t0399")
        assert len(self._on_disk(tmp_path)) == 400

    def test_a_missing_timestamp_still_reports_miss(
        self, monkeypatch, tmp_path
    ) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.ack_notification("nope") is False
        assert state.unack_notification("nope") is False
        assert state.delete_notification("nope") is False
        assert len(self._on_disk(tmp_path)) == 350

    def test_ack_all_keeps_every_older_row(self, monkeypatch, tmp_path) -> None:
        """Ack-all is an acknowledgement too: it rewrote the file from the in-memory tail."""
        state = self._state(monkeypatch, tmp_path)
        assert state.ack_all_notifications() == 350
        rows = self._on_disk(tmp_path)
        assert len(rows) == 350
        assert all(r.get("acked") for r in rows)
        assert rows[0]["ts"] == "t0000"
