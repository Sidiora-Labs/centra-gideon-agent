"""Progress timestamps for streamed model calls and nested workflow waits."""

from __future__ import annotations

from typing import Any

HEARTBEAT_SECS = 0.5


def last_heard(last_progress: float, calls: Any) -> float:
    return max(float(last_progress or 0.0), float(getattr(calls, "last_activity", 0.0) or 0.0))


async def wait_with_progress(controller: Any, timeout: float, on_progress: Any) -> Any:
    """Wait for a nested run while refreshing the parent's existing progress clock."""
    if not callable(on_progress):
        return await controller.wait_for_terminal(timeout=timeout)
    import asyncio

    task = asyncio.ensure_future(controller.wait_for_terminal(timeout=timeout))
    while not task.done():
        on_progress()
        await asyncio.wait({task}, timeout=HEARTBEAT_SECS)
    return task.result()
