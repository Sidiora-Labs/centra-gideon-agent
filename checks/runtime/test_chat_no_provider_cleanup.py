"""Regression for #2856 — a chat turn whose provider fails to resolve.

On a fresh install with no model provider bound (`needs_model: true`), building the
native runtime raises ``ProviderResolutionError`` for use case ``chat`` *before*
``run_chat`` reaches its per-turn telemetry block. The user saw the correct streamed
error, but the turn's background task then crashed with

    UnboundLocalError: cannot access local variable '_turn_tool_call_count' ...

because the ``finally`` block's done-branch reads ``_turn_tool_call_count`` (via
``maybe_offer_check_work``) on every turn exit, and the counter was only bound deep
inside the ``try``. The crash replaced the streamed error, skipped end-of-turn cleanup
(the done marker, the autonudge re-arm, the "check this work" offer), and logged as an
unretrieved task exception. The fix binds the counter before the ``try``.

The real-home writers (``config_dir()``, SEL) are already redirected to a tmp dir by
the autouse ``_isolate_real_home_writers`` fixture in ``conftest.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gideon.cognition.history import ConversationLog
from gideon.extensions.providers.provider_bridge import ProviderResolutionError
from gideon.interfaces.dashboard.chat_runner import run_chat
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


def _make_state(tmp_path) -> ConsoleState:
    """A state whose runtime build fails exactly as it does with no provider bound."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.get_or_create = AsyncMock(
        side_effect=ProviderResolutionError(
            "WHAT: no model provider resolves for use case 'chat'\n"
            "WHY: no provider is bound\nFIX: add a model provider in Settings"
        )
    )
    state = ConsoleState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.context_builder = MagicMock()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state


@pytest.mark.asyncio
async def test_no_provider_turn_runs_cleanup_without_unbound_error(tmp_path):
    state = _make_state(tmp_path)
    session = _ChatSession("chat-1-test")
    session._titled = True

    with (
        patch(
            "gideon.interfaces.dashboard.chat_runner.maybe_offer_check_work"
        ) as offer,
        patch(
            "gideon.interfaces.dashboard.chat_runner._maybe_followups", new=AsyncMock()
        ),
        patch("gideon.interfaces.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "hi")

    assert any(m.get("role") == "error" for m in session.messages)
    assert session._last_turn_errored is True
    state.sessions.record_failure.assert_awaited_once()

    # End-of-turn cleanup ran to completion: the done-branch reached the offer with the
    # initialized counter (no tools ran on a failed turn), appended the "done" marker,
    # broadcast chat_done, and cleared the task handle.
    offer.assert_called_once()
    assert offer.call_args.args[2] == 0
    assert any(m.get("role") == "done" for m in session.messages)
    state.broadcast_ws.assert_any_call("chat_done", {"session": session.key})
    assert session.task is None


@pytest.mark.asyncio
async def test_the_no_provider_error_reaches_the_user_intact(tmp_path):
    """The other half of the fix: the crash REPLACED the message the user needed.

    The ``UnboundLocalError`` fired inside the turn's own error handling, so what the user
    got was a dead turn instead of the provider's WHAT/WHY/FIX guidance. Asserted on the
    message text, not merely on the presence of an ``error`` row — a turn that appended a
    generic "something went wrong" would satisfy a role-only check while losing exactly
    the thing that makes an immediate resolution failure actionable.
    """
    state = _make_state(tmp_path)
    session = _ChatSession("chat-2-test")
    session._titled = True

    with (
        patch("gideon.interfaces.dashboard.chat_runner.maybe_offer_check_work"),
        patch(
            "gideon.interfaces.dashboard.chat_runner._maybe_followups", new=AsyncMock()
        ),
        patch("gideon.interfaces.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "hi")

    errors = [m for m in session.messages if m.get("role") == "error"]
    assert len(errors) == 1
    text = errors[0]["content"]
    assert "no model provider resolves for use case 'chat'" in text
    assert "FIX: add a model provider in Settings" in text


@pytest.mark.asyncio
async def test_the_turn_counter_is_bound_before_provider_setup(tmp_path):
    """ac 84.1 stated on the source shape the fix is: the counter's initialization must
    precede the ``try`` whose ``finally`` reads it. The behaviour test above proves the
    turn survives; this pins WHY, so a later edit that moves the binding back inside the
    ``try`` (where every provider-setup line can jump over it) fails here even if that
    turn happens not to reach the reading branch."""
    import inspect

    from gideon.interfaces.dashboard import chat_runner

    src = inspect.getsource(chat_runner.run_chat)
    binding = src.index("_turn_tool_call_count = 0")
    assert binding < src.index(
        "maybe_offer_check_work(state, session, _turn_tool_call_count)"
    )
    tail = src[binding:].removeprefix("_turn_tool_call_count = 0").strip()
    assert tail.startswith(
        "try:"
    ), f"the counter must be the last statement before the turn's try block, not {tail[:60]!r}"
