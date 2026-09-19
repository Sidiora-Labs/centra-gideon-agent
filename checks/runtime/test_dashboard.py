"""Tests for the dashboard module."""

import json
import os
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.interfaces.dashboard.handlers import prompts, sessions
from gideon.interfaces.dashboard.handlers_system import api_healthz
from gideon.interfaces.dashboard.server import _apply_response_policies
from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _fmt_duration,
    _load_notifications,
    _maybe_trim_notifications,
    _persist_notification,
)


class TestDeleteNotFound:
    @pytest.mark.asyncio
    async def test_session_delete_returns_404_when_history_is_missing(self) -> None:
        state = MagicMock()
        state.conversation_log.delete_session.return_value = False
        request = MagicMock()
        request.app = {"state": state}
        request.match_info = {"key": "missing"}

        response = await sessions.api_session_delete(request)

        assert response.status == 404
        assert json.loads(response.body)["error"] == "session_not_found"
        state.push_sessions_update.assert_not_called()
        state.push_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_prompt_delete_returns_404_when_prompt_is_missing(
        self, monkeypatch
    ) -> None:
        provider = MagicMock()
        provider.get_prompt.return_value = None
        provider.delete_prompt.return_value = True
        monkeypatch.setattr(prompts, "_get_default_prompt_provider", lambda: provider)
        request = MagicMock()
        request.match_info = {"name": "missing"}

        response = await prompts.api_prompt_delete(request)

        assert response.status == 404
        assert json.loads(response.body) == {"error": "not found"}
        provider.delete_prompt.assert_not_called()


class TestDashboard:
    @pytest.mark.asyncio
    async def test_healthz_identifies_the_answering_gateway(self) -> None:
        app = web.Application()
        app["gateway_id"] = "test-gateway-123"
        app["port"] = 7420
        request = make_mocked_request("GET", "/api/healthz", app=app)

        response = await api_healthz(request)
        payload = json.loads(response.text)

        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["gateway_id"] == "test-gateway-123"
        assert payload["pid"] == os.getpid()
        assert payload["port"] == 7420

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


class TestDashboardResponsePolicies:
    def _request(self, path: str = "/", origin: str = "") -> web.Request:
        app = web.Application()
        app["allowed_origins"] = {
            "http://localhost:10000",
            "https://dashboard.example.com",
        }
        headers = {"Origin": origin} if origin else None
        return make_mocked_request("GET", path, headers=headers, app=app)

    def test_dashboard_declares_same_origin_framing(self) -> None:
        response = web.Response()

        _apply_response_policies(self._request(), response)

        assert "frame-ancestors 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"

    def test_a2a_card_reflects_only_explicitly_allowed_origin(self) -> None:
        response = web.Response()

        _apply_response_policies(
            self._request("/a2a/agent-card", "https://dashboard.example.com"),
            response,
        )

        assert (
            response.headers["Access-Control-Allow-Origin"]
            == "https://dashboard.example.com"
        )
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert response.headers["Vary"] == "Origin"

    @pytest.mark.parametrize(
        "origin",
        [
            "https://attacker.example.com",
            "http://localhost:9999",
            "https://dashboard.example.com/path",
            "https://dashboard.example.com/",
            "null",
        ],
    )
    def test_a2a_card_does_not_grant_unlisted_origin(self, origin: str) -> None:
        response = web.Response(
            headers={"Access-Control-Allow-Origin": "*", "Vary": "Accept-Encoding"}
        )

        _apply_response_policies(self._request("/a2a/agent-card", origin), response)

        assert "Access-Control-Allow-Origin" not in response.headers
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert response.headers["Vary"] == "Accept-Encoding, Origin"

    def test_non_card_response_gets_no_cors_grant(self) -> None:
        response = web.Response()

        _apply_response_policies(
            self._request("/api/status", "https://dashboard.example.com"), response
        )

        assert "Access-Control-Allow-Origin" not in response.headers
        assert "Cross-Origin-Resource-Policy" not in response.headers


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
