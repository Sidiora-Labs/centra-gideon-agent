"""Slack events queue plumbing — _handle_message_deleted, _dispatch_queued,
queue routing in _route_message (moved from core tests/test_message_queue.py)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHandleMessageDeleted:
    """Tests for the extracted _handle_message_deleted function."""

    @staticmethod
    def _make_event(deleted_ts="ts_del", thread_ts="thread1", channel="C1", user="U_ALLOWED"):
        return {
            "deleted_ts": deleted_ts,
            "channel": channel,
            "previous_message": {"thread_ts": thread_ts, "user": user},
        }

    @staticmethod
    def _make_orch():
        orch = MagicMock()
        orch.sessions = MagicMock()
        orch.sessions.cancel_queued = MagicMock(return_value=False)
        orch._pending_queue = {}
        return orch

    @pytest.mark.asyncio
    async def test_unauthorized_user_ignored(self):
        from slack_desk_runtime.events import _handle_message_deleted

        orch = self._make_orch()
        event = self._make_event(user="U_BAD")
        with patch("slack_desk_runtime.events.is_allowed_user", return_value=False), \
             patch("slack_desk_runtime.events.sel"):
            await _handle_message_deleted(orch, event)
        orch.sessions.cancel_queued.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancels_from_session_queue(self):
        from slack_desk_runtime.events import _handle_message_deleted

        orch = self._make_orch()
        orch.sessions.cancel_queued.return_value = True
        event = self._make_event()
        with patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.sel") as mock_sel:
            await _handle_message_deleted(orch, event)
        orch.sessions.cancel_queued.assert_called_once_with("thread1", "ts_del")
        mock_sel().log_api_access.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancels_from_pending_queue(self):
        from slack_desk_runtime.events import _handle_message_deleted

        orch = self._make_orch()
        orch._pending_queue = {"thread1": [("ts_del", "hello", {}), ("ts_other", "keep", {})]}
        event = self._make_event()
        with patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.sel"):
            await _handle_message_deleted(orch, event)
        assert orch._pending_queue == {"thread1": [("ts_other", "keep", {})]}

    @pytest.mark.asyncio
    async def test_pending_queue_cleaned_when_empty(self):
        from slack_desk_runtime.events import _handle_message_deleted

        orch = self._make_orch()
        orch._pending_queue = {"thread1": [("ts_del", "hello", {})]}
        event = self._make_event()
        with patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.sel"):
            await _handle_message_deleted(orch, event)
        assert "thread1" not in orch._pending_queue

    @pytest.mark.asyncio
    async def test_session_key_falls_back_to_deleted_ts(self):
        from slack_desk_runtime.events import _handle_message_deleted

        orch = self._make_orch()
        orch.sessions.cancel_queued.return_value = True
        event = {"deleted_ts": "ts_dm", "channel": "D1", "previous_message": {"user": "U1"}}
        with patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.sel"):
            await _handle_message_deleted(orch, event)
        # No thread_ts → session_key = deleted_ts
        orch.sessions.cancel_queued.assert_called_once_with("ts_dm", "ts_dm")

    @pytest.mark.asyncio
    async def test_pending_queue_cleaned_when_sessions_none(self):
        """_pending_queue cleanup must work even when orch.sessions is None."""
        from slack_desk_runtime.events import _handle_message_deleted

        orch = self._make_orch()
        orch.sessions = None  # startup window — no session manager yet
        orch._pending_queue = {"thread1": [("ts_del", "hello", {})]}
        event = self._make_event()
        with patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.sel"):
            await _handle_message_deleted(orch, event)
        assert "thread1" not in orch._pending_queue


# ── Events.py: _dispatch_queued ──


class TestDispatchQueued:
    @pytest.mark.asyncio
    async def test_removes_reaction_and_calls_handler(self):
        from slack_desk_runtime.events import _dispatch_queued

        orch = MagicMock()
        orch.slack_desk = AsyncMock()
        orch.sessions = MagicMock()
        orch.sessions.is_cancelled = MagicMock(return_value=False)
        orch.sessions.dequeue = MagicMock(return_value=None)
        orch.sessions.clear_queue = MagicMock()
        orch.sessions.enqueue = MagicMock(return_value=False)
        orch.ctx_builder = None
        orch.cron_svc = None
        orch.conv_log = None
        orch.consolidator = None
        orch.subagent_mgr = None
        orch.task_runner = None
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock) as mock_hm:
            await _dispatch_queued(orch, "thread1", "ts_q", "hello", {"channel": "C1", "thread_ts": "thread1"})
        orch.slack_desk.remove_reaction.assert_awaited_once_with("C1", "ts_q", "hourglass_flowing_sand")
        mock_hm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_reaction_error(self):
        from slack_desk_runtime.events import _dispatch_queued

        orch = MagicMock()
        orch.slack_desk = AsyncMock()
        orch.slack_desk.remove_reaction = AsyncMock(side_effect=Exception("gone"))
        orch.sessions = MagicMock()
        orch.sessions.is_cancelled = MagicMock(return_value=False)
        orch.sessions.dequeue = MagicMock(return_value=None)
        orch.sessions.clear_queue = MagicMock()
        orch.sessions.enqueue = MagicMock(return_value=False)
        orch.ctx_builder = None
        orch.cron_svc = None
        orch.conv_log = None
        orch.consolidator = None
        orch.subagent_mgr = None
        orch.task_runner = None
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock) as mock_hm:
            await _dispatch_queued(orch, "thread1", "ts_q", "hello", {"channel": "C1"})
        mock_hm.assert_awaited_once()


# ── Events.py: queue routing in _route_message ──


def _make_route_orch() -> MagicMock:
    """Minimal mock orch that passes _route_message guards."""
    from slack_desk_runtime.settings import ACTIVATION_ALWAYS, SlackDeskSettings

    orch = MagicMock()
    settings = SlackDeskSettings(channels={}, dm_activation=ACTIVATION_ALWAYS)
    import slack_desk_runtime.settings as _st
    _st._current = settings
    orch.settings = settings
    orch.channel_history = MagicMock()
    orch.slack_desk = AsyncMock()
    orch.sessions = MagicMock()
    orch.sessions.enqueue = MagicMock(return_value=False)
    orch.sessions.dequeue = MagicMock(return_value=None)
    orch.sessions.cancel_queued = MagicMock(return_value=False)
    orch.sessions.is_cancelled = MagicMock(return_value=False)
    orch.sessions.clear_queue = MagicMock()
    orch.sessions.has_session = MagicMock(return_value=False)
    orch.ctx_builder = None
    orch.cron_svc = None
    orch.conv_log = None
    orch.consolidator = None
    orch.subagent_mgr = None
    orch.task_runner = None
    orch._handler_tasks = set()
    orch._session_tasks = {}
    orch._pending_queue = {}
    return orch


_ROUTE_PATCHES = [
    patch("slack_desk_runtime.events.is_allowed_user", return_value=True),
    patch("slack_desk_runtime.events.check_message_origin", return_value=True),
]


class TestQueueRouting:
    @pytest.mark.asyncio
    async def test_busy_session_enqueues_with_force(self):
        from slack_desk_runtime.events import SeenCache, _route_message

        orch = _make_route_orch()
        orch._session_tasks["ts_new"] = MagicMock()  # DM: session_key = msg_ts
        orch.sessions.enqueue.return_value = True
        event = {"user": "U1", "text": "queued", "ts": "ts_new", "channel": "D1", "channel_type": "im", "team": "T1"}
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock):
            for p in _ROUTE_PATCHES:
                p.start()
            try:
                await _route_message(orch, event, SeenCache(), is_mention=True)
            finally:
                for p in _ROUTE_PATCHES:
                    p.stop()
        orch.sessions.enqueue.assert_called_once()
        assert orch.sessions.enqueue.call_args[1].get("force") is True
        orch.slack_desk.add_reaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_busy_session_falls_back_to_pending_queue(self):
        from slack_desk_runtime.events import SeenCache, _route_message

        orch = _make_route_orch()
        orch._session_tasks["thread1"] = MagicMock()
        orch.sessions.enqueue.return_value = False  # no session object
        event = {"user": "U1", "text": "queued", "ts": "ts_new", "thread_ts": "thread1", "channel": "C1", "channel_type": "channel", "team": "T1"}
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock):
            for p in _ROUTE_PATCHES:
                p.start()
            try:
                await _route_message(orch, event, SeenCache(), is_mention=True)
            finally:
                for p in _ROUTE_PATCHES:
                    p.stop()
        assert hasattr(orch, "_pending_queue")
        assert "thread1" in orch._pending_queue
        assert orch._pending_queue["thread1"][0][0] == "ts_new"

    @pytest.mark.asyncio
    async def test_non_busy_enqueue_returns_true_queues(self):
        """elif branch: no task running but enqueue returns True (semaphore locked)."""
        from slack_desk_runtime.events import SeenCache, _route_message

        orch = _make_route_orch()
        orch.sessions.enqueue.return_value = True  # semaphore locked
        event = {"user": "U1", "text": "queued", "ts": "ts_new", "channel": "D1", "channel_type": "im", "team": "T1"}
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock):
            for p in _ROUTE_PATCHES:
                p.start()
            try:
                await _route_message(orch, event, SeenCache(), is_mention=True)
            finally:
                for p in _ROUTE_PATCHES:
                    p.stop()
        orch.slack_desk.add_reaction.assert_awaited_once()


class TestOnDoneDrain:
    @pytest.mark.asyncio
    async def test_drains_session_queue_after_task(self):
        from slack_desk_runtime.events import SeenCache, _route_message

        orch = _make_route_orch()
        orch.sessions.enqueue.return_value = False
        # After task completes, dequeue returns a queued message once then None
        orch.sessions.dequeue.side_effect = [
            ("ts_q", "queued text", {"channel": "C1", "thread_ts": "thread1"}),
            None,
        ]
        event = {"user": "U1", "text": "first", "ts": "ts1", "channel": "D1", "channel_type": "im", "team": "T1"}
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock), \
             patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.check_message_origin", return_value=True):
            await _route_message(orch, event, SeenCache(), is_mention=True)
            await asyncio.sleep(0.05)
            # Drain should have dispatched the queued message via _dispatch_queued
            tasks = list(orch._handler_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        # dequeue was called in _on_done
        orch.sessions.dequeue.assert_called()

    @pytest.mark.asyncio
    async def test_drains_pending_queue_after_task(self):
        from slack_desk_runtime.events import SeenCache, _route_message

        orch = _make_route_orch()
        orch.sessions.enqueue.return_value = False
        orch.sessions.dequeue.return_value = None  # session queue empty
        # Stash in pending queue
        orch._pending_queue = {"ts1": [("ts_pq", "pending", {"channel": "C1"})]}
        event = {"user": "U1", "text": "first", "ts": "ts1", "channel": "D1", "channel_type": "im", "team": "T1"}
        with patch("slack_desk_runtime.events.handle_message", new_callable=AsyncMock), \
             patch("slack_desk_runtime.events.is_allowed_user", return_value=True), \
             patch("slack_desk_runtime.events.check_message_origin", return_value=True):
            await _route_message(orch, event, SeenCache(), is_mention=True)
            await asyncio.sleep(0.05)
            tasks = list(orch._handler_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        # pending queue should have been drained
        assert "ts1" not in getattr(orch, "_pending_queue", {})
