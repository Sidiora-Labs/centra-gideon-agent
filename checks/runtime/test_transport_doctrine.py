"""Guardrail tests for the realtime-transport doctrine (SSE M5).

VISION.md "Realtime transport" — single-transport-per-concern:
- Always-on dashboard state (status/sessions/titles/notifications/refresh) rides
  the ONE multiplexed WebSocket; it is NOT also fanned to a global SSE stream.
- Page-scoped feeds (campaigns/logs/file-watch) use per-resource SSE.
- Nothing is delivered over two transports.

These tests pin the structural invariants so a future change can't silently
re-introduce the dual-emit (the debt M3 removed) or split always-on concerns into
their own streams (the connection-budget anti-pattern).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from gideon.interfaces.dashboard import state as state_mod

_ROOT = Path(__file__).resolve().parent.parent.parent


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text()


def _published_loop_events() -> set[str]:
    handler = _read("runtime/gideon/interfaces/dashboard/handlers/loop_routes.py")
    watchdog = _read("runtime/gideon/automation/loop/watchdog.py")
    published = set(
        re.findall(
            r'loop_sse\(\)\.publish\(\s*registry_key\([^)]*\),\s*"([a-z_]+)"',
            handler,
        )
    )
    published |= set(
        re.findall(r'self\._publish\(\s*[a-zA-Z_.]+,\s*"([a-z_]+)"', watchdog)
    )
    kinds_dir = _ROOT / "runtime/gideon/automation/loop/kinds"
    for module in kinds_dir.glob("*.py"):
        published |= set(
            re.findall(
                r'ctx\.publish\(\s*[a-zA-Z_.]+,\s*"([a-z_]+)"',
                module.read_text(),
            )
        )
    return published


def test_state_has_no_global_sse_hub():
    """The dead global SSE hub + accessor must stay gone (M3)."""
    assert not hasattr(
        state_mod.ConsoleState, "sse_hub"
    ), "global SSE hub removed in M3 — dashboard state rides the WebSocket"
    assert hasattr(state_mod.ConsoleState, "loop_sse")


def test_broadcast_does_not_publish_to_a_global_sse_hub():
    """_broadcast must fan to WS only — never to a global per-process SSE hub.

    Pins the single-transport rule structurally: the funnel's source must not
    contain a global ``self._sse.publish(...)`` (the removed dual-emit). A
    per-resource ``loop_sse()`` publish is a different concern and is done
    by the watchdog, not here.
    """
    src = inspect.getsource(state_mod.ConsoleState._broadcast)
    assert "_sse.publish" not in src, (
        "_broadcast must not publish to a global SSE hub — that is the dual-emit "
        "M3 removed; always-on state rides the WebSocket"
    )


def test_no_global_api_stream_route():
    """The dead global /api/stream SSE endpoint must stay removed."""
    server_src = _read("runtime/gideon/interfaces/dashboard/server.py")
    assert not re.search(
        r'add_get\(\s*["\']/api/stream["\']', server_src
    ), "/api/stream (global SSE) was removed in M3"


def test_useSSE_hook_deleted():
    """The dead, never-mounted useSSE.ts frontend hook must stay deleted (M3)."""
    assert not (
        _ROOT / "apps/console/src/hooks/useSSE.ts"
    ).exists(), "useSSE.ts was dead (never mounted) and removed in M3"


def test_per_resource_sse_substrate_present():
    """The reusable SSE substrate must remain — it powers per-resource streams."""
    from gideon.interfaces.dashboard import sse

    assert hasattr(sse, "SseHub")
    assert hasattr(sse, "SseRegistry")
    assert hasattr(sse, "stream_response")


def test_unified_loop_sse_events_match_the_frontend_union_exactly():
    """The listener union is the exact loop-stream contract across routes, watchdog and
    every kind module: missing names are silently dropped by EventSource, while extra names
    pretend that a ledger/workflow event is live when no loop SSE publisher backs it."""
    fe = _read("apps/console/src/features/loops/useRunStream.ts")
    m = re.search(r"const RUN_LIFECYCLE = \[([^\]]*)\]", fe)
    assert m, "couldn't find the RUN_LIFECYCLE union in useRunStream.ts"
    registered = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert registered == _published_loop_events()


def test_workflow_engine_sse_events_are_all_registered_in_the_frontend():
    """Every event the v2 workflow engine publishes MUST be in the FE
    WORKFLOW_LIFECYCLE union — EventSource silently DROPS event types with no registered
    listener, so an unlisted publish is a live update that never arrives, invisible in
    every test that does not assert the list itself. Same drift class as the loop cockpits
    above (C326/C367); pinned here so a new `_publish(...)` without the matching FE
    listener fails CI instead of silently never reaching an open run view.
    """
    controller = _read("runtime/gideon/automation/workflows/controller.py")
    service = _read("runtime/gideon/automation/workflows/service.py")
    fe = _read("apps/console/src/features/workflows/useWorkflowStream.ts")

    published = set(re.findall(r'self\._publish\(\s*"(workflow_[a-z_]+)"', controller))
    published |= set(re.findall(r'_publish\(\s*"(workflow_[a-z_]+)"', service))

    m = re.search(r"export const WORKFLOW_LIFECYCLE = \[([^\]]*)\]", fe)
    assert m, "couldn't find the WORKFLOW_LIFECYCLE union in useWorkflowStream.ts"
    registered = set(re.findall(r"'(workflow_[a-z_]+)'", m.group(1)))

    missing = published - registered
    assert not missing, (
        f"The workflow engine publishes SSE events the FE useWorkflowStream never listens "
        f"for: {sorted(missing)} — add them to WORKFLOW_LIFECYCLE or they'll silently never "
        f"reach the run view."
    )


def test_the_coalesced_batch_frame_is_registered_and_its_members_are_foldable():
    """The coalescer (WF2-R11 batch-5) introduces a frame that is NOT a lifecycle event: it
    is an envelope around several. Two ways that drifts, both silent:

    1. the batch frame itself has no FE listener — EventSource drops it, and every batched
       node update (i.e. every fan-out) vanishes while single events still work, so the
       widget looks correct on small specs and broken on large ones;
    2. an event is added to the backend's coalescing allowlist but not to the FE union — it
       would be batched into a frame whose unwrapper then discards it as unknown.

    Both are pinned here because neither surfaces in a test that does not read both files.
    """
    coalescer = _read("runtime/gideon/automation/workflows/coalescer.py")
    fe = _read("apps/console/src/features/workflows/useWorkflowStream.ts")

    batch_event = re.search(r'BATCH_EVENT = "([a-z_]+)"', coalescer)
    assert batch_event, "couldn't find BATCH_EVENT in coalescer.py"
    assert f"'{batch_event.group(1)}'" in fe, (
        f"the backend emits a coalesced {batch_event.group(1)!r} frame the FE never listens "
        f"for — every batched fan-out update would be silently dropped"
    )
    assert (
        "addEventListener(WORKFLOW_BATCH_EVENT" in fe
    ), "the batch frame name is declared but no listener is registered for it"

    allowlist = re.search(r"COALESCING_EVENTS = frozenset\(\s*\{([^}]*)\}", coalescer)
    assert allowlist, "couldn't find COALESCING_EVENTS in coalescer.py"
    coalescing = set(re.findall(r'"(workflow_[a-z_]+)"', allowlist.group(1)))

    m = re.search(r"export const WORKFLOW_LIFECYCLE = \[([^\]]*)\]", fe)
    assert m
    registered = set(re.findall(r"'(workflow_[a-z_]+)'", m.group(1)))

    missing = coalescing - registered
    assert not missing, (
        f"events the backend coalesces but the FE union does not list: {sorted(missing)} — "
        f"they'd be batched and then discarded by unwrapBatch as unknown."
    )
