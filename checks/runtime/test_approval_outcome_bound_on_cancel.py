"""Rail for #1536 — the approval-wait `finally` must not raise UnboundLocalError.

The mirrored-approval block binds ``outcome`` on the success and grace-timeout
paths and via the outer TimeoutError handler — but NOT when the inner
``wait_for(fut, 7200)`` is cancelled (pytest-timeout, gateway shutdown, client
disconnect, navigation away). On that path the ``finally`` referenced an unbound
``outcome`` and raised ``UnboundLocalError``, which:

- REPLACED the cancellation in the traceback, so a CI hang read as an unrelated
  error (the reported symptom), and
- skipped ``_resolve_mirrored_approval``, stranding the mirrored inbox item
  asking for a decision the turn was already tearing down (the production defect).

Binding ``outcome = "rejected"`` before the try fixes both: the mirror resolves
and the cancellation propagates unmasked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from test_dashboard_approval import (
    _complete_event,
    _context_builder,
    _make_session,
    _make_state,
    _patch_stats,
    _permission_event,
    _set_stream,
)

import gideon.interfaces.dashboard.chat_runner as cr
from gideon.interfaces.dashboard.chat import run_chat


@pytest.mark.asyncio
async def test_cancelling_a_mirrored_approval_wait_resolves_the_mirror_and_propagates(
    tmp_path,
):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _make_session()
    _set_stream(client, [_permission_event(), _complete_event()])

    resolve_calls: list[tuple[str, str]] = []

    with (
        _patch_stats(),
        patch.object(cr, "_APPROVAL_MIRROR_GRACE_SECS", 0.01),
        patch.object(
            cr, "_mirror_approval_to_inbox", MagicMock(return_value="inbox-1")
        ),
        patch.object(
            cr,
            "_resolve_mirrored_approval",
            MagicMock(
                side_effect=lambda item, outcome: resolve_calls.append((item, outcome))
            ),
        ),
    ):
        task = asyncio.create_task(run_chat(state, session, "hello"))

        for _ in range(200):
            await asyncio.sleep(0.01)
            if (
                cr._mirror_approval_to_inbox.called
                and "req-1" in session._approval_futures
            ):
                break
        assert (
            cr._mirror_approval_to_inbox.called
        ), "the grace timeout should have mirrored"

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert resolve_calls == [("inbox-1", "rejected")], resolve_calls


def test_outcome_is_bound_before_the_try(tmp_path):
    import inspect

    src = inspect.getsource(cr)
    src = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    anchor = 'mirrored_item = ""'
    i = src.index(anchor)
    window = src[i : i + 400]
    assert (
        'outcome = "rejected"' in window
    ), "outcome must be bound right after mirrored_item"
    assert window.index('outcome = "rejected"') < window.index(
        "try:"
    ), "the outcome default must come BEFORE the try, or the cancel path is still unbound"
