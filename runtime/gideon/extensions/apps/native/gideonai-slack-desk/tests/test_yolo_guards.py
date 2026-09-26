"""YOLO trust-mode delegation through the Slack handler — TTL tiers,
config-permanent guards, the !yolo command noop paths (moved from core
tests/test_shepherd_fixes.py; the canonical state lives in core
gideon.trust_mode, the handler is a thin delegator)."""

from unittest.mock import patch

import pytest

import gideon.trust_mode as tm


class TestYoloExpiry:
    """Tests for YOLO mode tiered auto-timeout."""

    @pytest.fixture(autouse=True)
    def _reset_yolo(self):
        from slack_desk_runtime.handler import disable_yolo
        disable_yolo()
        yield
        disable_yolo()

    def test_slack_desk_yolo_expires(self) -> None:
        """!yolo on expires after _YOLO_TTL_SECS (30min)."""
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import enable_yolo_with_ttl, is_yolo_mode

        enable_yolo_with_ttl(h._YOLO_TTL_SECS)

        assert is_yolo_mode()

        future = tm._TRUST._expires_at + 1
        with patch("time.monotonic", return_value=future):
            assert not is_yolo_mode(), "Slack YOLO should have auto-expired"

    def test_config_yolo_never_expires(self) -> None:
        """set_yolo_mode from config sets no expiry."""
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import is_yolo_mode, set_yolo_mode

        set_yolo_mode(True)

        assert is_yolo_mode()
        assert tm._TRUST._expires_at == 0.0  # no expiry

        # Even far in the future, still active
        with patch("time.monotonic", return_value=9999999999.0):
            assert is_yolo_mode(), "Config YOLO should never expire"

    def test_dashboard_yolo_expires_6h(self) -> None:
        """Dashboard YOLO uses _YOLO_DASHBOARD_TTL_SECS (6h)."""
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import (
            _YOLO_DASHBOARD_TTL_SECS,
            enable_yolo_with_ttl,
            is_yolo_mode,
        )

        enable_yolo_with_ttl(_YOLO_DASHBOARD_TTL_SECS)

        assert is_yolo_mode()
        assert tm._TRUST._expires_at > 0

        future = tm._TRUST._expires_at + 1
        with patch("time.monotonic", return_value=future):
            assert not is_yolo_mode(), "Dashboard YOLO should expire after 6h"

    def test_yolo_disable_clears(self) -> None:
        from slack_desk_runtime.handler import disable_yolo, is_yolo_mode, set_yolo_mode

        set_yolo_mode(True)
        assert is_yolo_mode()
        disable_yolo()
        assert not is_yolo_mode()


class TestYoloSlackDeskCommandPath:
    """Guard test for the !yolo on Slack command path (handler.py)."""

    def test_enable_yolo_with_ttl_sets_expiry(self) -> None:
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import disable_yolo, enable_yolo_with_ttl

        disable_yolo()
        enable_yolo_with_ttl(h._YOLO_TTL_SECS)

        assert tm._TRUST._active is True
        assert tm._TRUST._expires_at > 0
        assert tm._TRUST._active_ttl == h._YOLO_TTL_SECS

        disable_yolo()
        assert tm._TRUST._expires_at == 0.0


class TestYoloFromConfigGuard:
    """Tests for _yolo_from_config flag.

    Bug 1: !yolo on overwrites config-permanent yolo with 30-min TTL.
    Bug 2: dashboard enable_yolo() sets 6h TTL on config-driven yolo.
    """

    @pytest.fixture(autouse=True)
    def _reset_yolo(self):
        from slack_desk_runtime.handler import disable_yolo
        disable_yolo()
        yield
        disable_yolo()

    def test_config_yolo_sets_from_config_flag(self) -> None:
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import set_yolo_mode

        set_yolo_mode(True)
        assert tm._TRUST._from_config is True
        assert tm._TRUST._expires_at == 0.0

    def test_enable_with_ttl_noop_when_config_active(self) -> None:
        """Bug 1: !yolo on must not overwrite config-permanent yolo."""
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import enable_yolo_with_ttl, is_yolo_mode, set_yolo_mode

        set_yolo_mode(True)
        enable_yolo_with_ttl(h._YOLO_TTL_SECS)

        assert is_yolo_mode()
        assert tm._TRUST._expires_at == 0.0, "Config yolo should remain permanent"

    def test_config_yolo_survives_far_future(self) -> None:
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import is_yolo_mode, set_yolo_mode

        set_yolo_mode(True)
        # Force a truthy, past expiry so the _yolo_from_config guard is actually exercised
        tm._TRUST._expires_at = 1.0
        with patch("time.monotonic", return_value=9999999999.0):
            assert is_yolo_mode(), "Config YOLO must never expire"

    def test_disable_clears_from_config_flag(self) -> None:
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import disable_yolo, set_yolo_mode

        set_yolo_mode(True)
        assert tm._TRUST._from_config is True
        disable_yolo()
        assert tm._TRUST._from_config is False

    def test_set_yolo_mode_false_clears_flag(self) -> None:
        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import set_yolo_mode

        set_yolo_mode(True)
        set_yolo_mode(False)
        assert tm._TRUST._from_config is False


class TestYoloFromConfigSlackDeskGuards:
    """Cover _yolo_from_config early-return paths in events.py and handler.py."""

    @pytest.fixture(autouse=True)
    def _reset_yolo(self):
        from slack_desk_runtime.handler import disable_yolo
        disable_yolo()
        yield
        disable_yolo()

    @pytest.mark.asyncio
    async def test_events_yolo_on_noop_when_config_permanent(self) -> None:
        """events.py: /gideon yolo on responds with noop when config-permanent."""
        from unittest.mock import AsyncMock, MagicMock

        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import set_yolo_mode

        set_yolo_mode(True)
        assert tm._TRUST._from_config is True

        orch = MagicMock()
        respond = AsyncMock()

        from slack_desk_runtime.events import _handle_yolo

        with patch("slack_desk_runtime.events.sel") as mock_sel, patch("slack_desk_runtime.events.is_owner", return_value=True):
            await _handle_yolo(orch, "UOWNER", "on", respond)

        respond.assert_awaited_once()
        assert "permanently ON" in respond.call_args[0][0]
        mock_sel.return_value.log_api_access.assert_called_once()
        assert mock_sel.return_value.log_api_access.call_args.kwargs["outcome"] == "noop_config_permanent"

    @pytest.mark.asyncio
    async def test_handler_yolo_on_noop_when_config_permanent(self) -> None:
        """handler.py: !yolo on responds with noop when config-permanent."""
        from unittest.mock import AsyncMock, MagicMock

        import slack_desk_runtime.handler as h
        from slack_desk_runtime.handler import _handle_slash_command, set_yolo_mode

        set_yolo_mode(True)
        assert tm._TRUST._from_config is True

        slack_desk = AsyncMock()
        sessions = MagicMock()

        with patch("slack_desk_runtime.handler.sel") as mock_sel, patch("slack_desk_runtime.handler.is_owner", return_value=True):
            result = await _handle_slash_command("!yolo on", slack_desk, sessions, "C123", "ts1", "ts2", "key1", "UOWNER")

        assert result is not None
        slack_desk.post_message.assert_awaited()
        msg = slack_desk.post_message.call_args[0][1]
        assert "permanently ON" in msg
        sel_call = [c for c in mock_sel.return_value.log_api_access.call_args_list if c.kwargs.get("outcome") == "noop_config_permanent"]
        assert len(sel_call) == 1
