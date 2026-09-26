"""Tests for handler.py: !link-to-dashboard command and linked thread intercept."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


def _make_slack_desk():
    """Create a fully async-mocked Slack client."""
    slack_desk = MagicMock()
    slack_desk.post_message = AsyncMock()
    slack_desk.post_blocks = AsyncMock()
    return slack_desk


# ── !link-to-dashboard command tests ──


class TestLinkToDashboardCommand:
    """Cover handler.py lines 994-1011."""

    @pytest.mark.asyncio
    async def test_no_dashboard_state(self):
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        with (
            patch.object(handler, "_dashboard_state", None),
            patch.object(handler, "is_allowed_user", return_value=True),
        ):
            result = await handler._handle_slash_command(
                "!link-to-dashboard", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "U1",
            )
        assert result == ""
        assert any("not available" in str(c).lower() for c in slack_desk.post_message.call_args_list)

    @pytest.mark.asyncio
    async def test_not_in_thread(self):
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        ds = MagicMock()
        ds.get_or_create_session = MagicMock()
        with (
            patch.object(handler, "_dashboard_state", ds),
            patch.object(handler, "is_allowed_user", return_value=True),
        ):
            result = await handler._handle_slash_command(
                "!link-to-dashboard", slack_desk, MagicMock(), "C1", "msg1", "msg1", "msg1", "U1",
            )
        assert result == ""
        assert any("thread" in str(c).lower() for c in slack_desk.post_message.call_args_list)

    @pytest.mark.asyncio
    async def test_empty_thread_returns_error(self):
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        ds = MagicMock()
        with (
            patch.object(handler, "_dashboard_state", ds),
            patch.object(handler, "is_allowed_user", return_value=True),
            patch(
                "slack_desk_runtime.interactions._import_thread_to_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await handler._handle_slash_command(
                "!link-to-dashboard", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "U1",
            )
        assert result == ""
        assert any("could not" in str(c).lower() for c in slack_desk.post_message.call_args_list)

    @pytest.mark.asyncio
    async def test_unauthorized_user_blocked(self):
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        with patch.object(handler, "is_allowed_user", return_value=False):
            result = await handler._handle_slash_command(
                "!link-to-dashboard", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "UBAD",
            )
        assert result == ""
        assert any("not authorized" in str(c).lower() for c in slack_desk.post_message.call_args_list)

    @pytest.mark.asyncio
    async def test_success_emits_sel_audit(self):
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        ds = MagicMock()
        session = MagicMock()
        session.key = "s1"
        session.messages = [{"role": "user", "content": "hi"}]
        mock_sel_inst = MagicMock()
        orig_sel = handler.sel
        handler.sel = lambda: mock_sel_inst
        try:
            with (
                patch.object(handler, "_dashboard_state", ds),
                patch.object(handler, "is_allowed_user", return_value=True),
                patch(
                    "slack_desk_runtime.interactions._import_thread_to_session",
                    new_callable=AsyncMock,
                    return_value=session,
                ),
            ):
                result = await handler._handle_slash_command(
                    "!link-to-dashboard", slack_desk, MagicMock(), "C1", "t1", "msg1", "t1", "U1",
                )
        finally:
            handler.sel = orig_sel
        assert result == ""
        mock_sel_inst.log_tool_invocation.assert_called_once()
        kw = mock_sel_inst.log_tool_invocation.call_args[1]
        assert kw["tool_name"] == "link_to_dashboard"
        assert kw["outcome"] == "success"


# ── Linked thread intercept tests ──
#
# The intercept routes through the platform's guarded inbound door (EA-7):
# handler builds a ChannelMessage and calls services.deliver_channel_inbound —
# core owns the trust gate, redaction, the WS broadcast/session-list push and
# the turn. These tests wire a REAL door (channel_inbound.deliver_inbound) with
# a captured turn_runner, so they exercise the shipped routing rather than a
# re-implementation; the trust verdict is patched at `admit`, the door's one
# decision seam, keeping the tests hermetic to the trust store.


@pytest.fixture(autouse=True)
def _fresh_admission_cache():
    """The guarded door caches verdicts per message id and these tests reuse ids like
    "msg1", so a stale entry would answer the next test's admission. The reset lives
    HERE and not in conftest because the apps boundary lint exempts only ``test_*.py``
    files — the same placement the discord/telegram/email suites use."""
    from gideon.channel_inbound import reset_admissions

    reset_admissions()
    yield
    reset_admissions()


def _door_services(state):
    from gideon.channel_inbound import deliver_inbound

    class _Services:
        def __init__(self):
            self.dashboard_state = state
            self.turns = []

        async def deliver_channel_inbound(self, provider, msg, *, is_dm=True):
            async def turn_runner(st, session, text):
                self.turns.append((session, text))

            return await deliver_inbound(self, provider, msg, is_dm=is_dm, turn_runner=turn_runner)

    return _Services()


def _allowed_verdict(*a, **kw):
    from gideon.sdk.channel import TrustVerdict

    return TrustVerdict(allowed=True, reason="allowed")


class TestLinkedThreadIntercept:
    """The intercept: owner gate, then the guarded door."""

    @pytest.mark.asyncio
    async def test_unauthorized_user_denied_with_sel(self):
        """The pre-door owner gate: unauthorized senders never reach the door."""
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        ds = MagicMock()
        _session = MagicMock(key="session1")
        type(_session).running = PropertyMock(return_value=False)
        ds.get_linked_session = MagicMock(return_value=_session)
        mock_sel_inst = MagicMock()
        orig_sel = handler.sel
        handler.sel = lambda: mock_sel_inst
        try:
            with (
                patch.object(handler, "_dashboard_state", ds),
                patch.object(handler, "is_allowed_user", return_value=False),
            ):
                await handler.handle_message(
                    slack_desk, MagicMock(), "C1", "hello", "t1", "msg1", "UBAD",
                )
                mock_sel_inst.log_tool_invocation.assert_called_once()
                kw = mock_sel_inst.log_tool_invocation.call_args[1]
                assert kw["outcome"] == "denied"
                assert kw["metadata"]["user_id"] == "UBAD"
        finally:
            handler.sel = orig_sel

    @pytest.mark.asyncio
    async def test_authorized_routes_to_session_not_running(self):
        """An owner message drives one turn through the door."""
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        session = MagicMock()
        type(session).running = PropertyMock(return_value=False)
        session.key = "session1"
        ds = MagicMock()
        ds.get_linked_session = MagicMock(return_value=session)
        ds._background_tasks = set()
        ds.broadcast_ws = MagicMock()
        ds.push_sessions_update = MagicMock()
        services = _door_services(ds)

        with (
            patch.object(handler, "_dashboard_state", ds),
            patch.object(handler, "_gateway_services", services),
            patch.object(handler, "is_allowed_user", return_value=True),
            patch("gideon.channel_inbound.admit", _allowed_verdict),
        ):
            await handler.handle_message(
                slack_desk, MagicMock(), "C1", "hello", "t1", "msg1", "U1",
            )
            import asyncio as _aio

            await _aio.sleep(0)
            session.append.assert_called_once()
            assert [t[1] for t in services.turns] == ["hello"]
            # The live WS broadcast + session-list push now happen inside core's
            # door and are pinned by core's chokepoint suite, not re-pinned here.

    @pytest.mark.asyncio
    async def test_redact_for_ui_original_for_llm(self):
        """Core's door appends the redacted line but hands the LLM the original
        text — the same UI-vs-LLM split the hand-rolled intercept had."""
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        session = MagicMock()
        type(session).running = PropertyMock(return_value=False)
        session.key = "session1"
        ds = MagicMock()
        ds.get_linked_session = MagicMock(return_value=session)
        ds._background_tasks = set()
        ds.broadcast_ws = MagicMock()
        ds.push_sessions_update = MagicMock()
        services = _door_services(ds)

        with (
            patch.object(handler, "_dashboard_state", ds),
            patch.object(handler, "_gateway_services", services),
            patch.object(handler, "is_allowed_user", return_value=True),
            patch("gideon.channel_inbound.admit", _allowed_verdict),
            patch("gideon.security.redact_exfiltration_urls", return_value=("[REDACTED-URL]", True)),
            patch("gideon.security.redact_credentials", return_value=("[REDACTED]", True)),
        ):
            await handler.handle_message(
                slack_desk, MagicMock(), "C1", "hello http://evil.com", "t1", "msg1", "U1",
            )
            import asyncio as _aio

            await _aio.sleep(0)
            # UI gets redacted text
            session.append.assert_called_once_with("user", "[REDACTED]", "msg msg-u")
            # LLM gets original text
            assert [t[1] for t in services.turns] == ["hello http://evil.com"]

    @pytest.mark.asyncio
    async def test_authorized_queues_when_running(self):
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        session = MagicMock()
        type(session).running = PropertyMock(return_value=True)
        session.key = "session1"
        session._queue = []
        session.queue_append = lambda content: (session._queue.append({"id": "test", "content": content}) or "test")
        ds = MagicMock()
        ds.get_linked_session = MagicMock(return_value=session)
        ds.broadcast_ws = MagicMock()
        ds.push_sessions_update = MagicMock()
        services = _door_services(ds)

        with (
            patch.object(handler, "_dashboard_state", ds),
            patch.object(handler, "_gateway_services", services),
            patch.object(handler, "is_allowed_user", return_value=True),
            patch("gideon.channel_inbound.admit", _allowed_verdict),
        ):
            await handler.handle_message(
                slack_desk, MagicMock(), "C1", "hello", "t1", "msg1", "U1",
            )
            assert len(session._queue) == 1
            assert services.turns == []

    @pytest.mark.asyncio
    async def test_door_denial_is_sel_audited_and_not_routed(self):
        """When the door denies (e.g. an untracked group), nothing is routed and
        the SEL row records the door's reason — the intercept still returns, so a
        linked thread's message never falls through to the ACP path."""
        from gideon.sdk.channel import TrustVerdict
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        session = MagicMock()
        session.key = "session1"
        ds = MagicMock()
        ds.get_linked_session = MagicMock(return_value=session)
        services = _door_services(ds)
        mock_sel_inst = MagicMock()
        orig_sel = handler.sel
        handler.sel = lambda: mock_sel_inst

        def _denied(*a, **kw):
            return TrustVerdict(allowed=False, reason="untracked_channel")

        try:
            with (
                patch.object(handler, "_dashboard_state", ds),
                patch.object(handler, "_gateway_services", services),
                patch.object(handler, "is_allowed_user", return_value=True),
                patch("gideon.channel_inbound.admit", _denied),
            ):
                await handler.handle_message(
                    slack_desk, MagicMock(), "C1", "hello", "t1", "msg1", "U1",
                )
        finally:
            handler.sel = orig_sel

        session.append.assert_not_called()
        assert services.turns == []
        kw = mock_sel_inst.log_tool_invocation.call_args[1]
        assert kw["outcome"] == "denied"
        assert kw["metadata"]["reason"] == "untracked_channel"

    @pytest.mark.asyncio
    async def test_bang_commands_still_fall_through(self):
        """A bang command in a linked thread bypasses the door and reaches the
        normal command handling — the fall-through the door must not swallow."""
        from slack_desk_runtime import handler

        slack_desk = _make_slack_desk()
        session = MagicMock(key="session1")
        ds = MagicMock()
        ds.get_linked_session = MagicMock(return_value=session)
        services = _door_services(ds)

        with (
            patch.object(handler, "_dashboard_state", ds),
            patch.object(handler, "_gateway_services", services),
            patch.object(handler, "is_allowed_user", return_value=True),
            patch.object(handler, "is_owner", return_value=True),
            patch.object(handler, "_handle_slash_command", new_callable=AsyncMock, return_value="") as slash,
        ):
            await handler.handle_message(
                slack_desk, MagicMock(), "C1", "!yolo", "t1", "msg1", "U1",
            )
            assert services.turns == []
            slash.assert_called_once()


# ── EA-7 trust write-through tests ──


class TestChannelTrustWriteThrough:
    """Owner actions land in core's channel_trust store, not only SlackDeskSettings."""

    def test_persist_allowed_user_writes_through(self, monkeypatch):
        import gideon.channel_trust as ct
        from slack_desk_runtime import allowlist

        monkeypatch.setattr(
            "slack_desk_runtime.settings.persist_list_entry", lambda *a, **kw: None
        )
        monkeypatch.setattr("slack_desk_runtime.settings.reload_settings", lambda: None)
        allowlist.persist_allowed_user("U7", "Friend")
        assert ct.is_allowed_sender("slack", "U7")
        allowlist.persist_allowed_user("U7", remove=True)
        assert not ct.is_allowed_sender("slack", "U7")

    def test_persist_tracking_channel_writes_through(self, monkeypatch):
        import gideon.channel_trust as ct
        from slack_desk_runtime import allowlist

        monkeypatch.setattr(
            "slack_desk_runtime.settings.persist_list_entry", lambda *a, **kw: None
        )
        monkeypatch.setattr("slack_desk_runtime.settings.reload_settings", lambda: None)
        allowlist.persist_tracking_channel("C9", "eng")
        assert ct.is_tracked_channel("slack", "C9")
        allowlist.persist_tracking_channel("C9", remove=True)
        assert not ct.is_tracked_channel("slack", "C9")

    def test_sync_channel_trust_mirrors_owner_and_channels(self):
        import gideon.channel_trust as ct
        from slack_desk_runtime.allowlist import sync_channel_trust

        sync_channel_trust("UOWNER", {"C1", "C2"})
        assert ct.is_allowed_sender("slack", "UOWNER")
        assert ct.is_tracked_channel("slack", "C1")
        assert ct.is_tracked_channel("slack", "C2")
        # Idempotent: a second boot re-mirror writes nothing new (no assertion on
        # SEL here — the guard is the is_* checks — but it must not raise).
        sync_channel_trust("UOWNER", {"C1", "C2"})

    def test_claim_owner_seeds_channel_trust(self, monkeypatch):
        import gideon.channel_trust as ct
        from slack_desk_runtime import handler

        monkeypatch.setattr(handler, "_owner_id", "")
        monkeypatch.setattr(
            "gideon.sdk.channel.save_credential", lambda *a, **kw: None
        )
        assert handler.claim_owner("UNEW") is True
        assert ct.is_allowed_sender("slack", "UNEW")

    def test_track_linked_channel_tracks_groups_not_dms(self):
        import gideon.channel_trust as ct
        from slack_desk_runtime.handler import _track_linked_channel

        _track_linked_channel("C42")
        assert ct.is_tracked_channel("slack", "C42")
        _track_linked_channel("D42")
        assert not ct.is_tracked_channel("slack", "D42")
