"""Resolve whether the work that requested an approval still exists and is live."""

from __future__ import annotations

from typing import Any

UNVERIFIABLE = "the work that asked for it could not be confirmed as still running"


def owner_ended(entry: dict[str, Any], *, state: Any) -> str:
    """Return a concrete owner-ending reason, or fail closed when its record is unreadable."""
    session = str(entry.get("session") or "")
    try:
        if session.startswith(("workflow:", "run:")):
            from gideon.automation.workflows import ownership, store
            from gideon.automation.workflows.models import TERMINAL_RUN_STATUSES

            parsed = ownership.parse_owned(session)
            if parsed is None:
                return UNVERIFIABLE
            run = store.get(parsed[0])
            if run is None:
                return "the workflow run that asked for it was deleted"
            if run.status in TERMINAL_RUN_STATUSES:
                return f"the workflow run that asked for it ended as {run.status.value}"
            if store.cancel_requested(parsed[0]):
                return "the workflow run that asked for it was cancelled"

        if session.startswith("loop-"):
            from gideon.automation.loop import files
            from gideon.automation.loop import store as loop_store
            from gideon.automation.loop.loop import ENDED_STATUSES

            loop_id = (
                session.removeprefix("loop-plan-")
                if session.startswith("loop-plan-")
                else session.removeprefix("loop-")
            )
            if not files.valid_loop_id(loop_id):
                return UNVERIFIABLE
            loop = loop_store.get(loop_id)
            if loop is None:
                return "the loop that asked for it was deleted"
            status = str(getattr(loop.status, "value", loop.status))
            if status in {s.value for s in ENDED_STATUSES}:
                return f"the loop that asked for it is {status}"

        approval_id = str(entry.get("id") or "")
        agent_id = ""
        if approval_id.startswith("spawn:"):
            agent_id = approval_id.removeprefix("spawn:")
        elif approval_id.startswith("subagent:"):
            agent_id = approval_id.removeprefix("subagent:").split(":", 1)[0]
        if agent_id and state.subagents is None:
            return UNVERIFIABLE
        if agent_id:
            info = state.subagents.get(agent_id)
            if info is None:
                return "the subagent that asked for it is no longer running"
            if getattr(info, "cancelled", False):
                return "the subagent that asked for it was cancelled"
            if getattr(info, "done", False):
                return "the subagent that asked for it has already finished"

        if entry.get("request_id"):
            session_obj = state._sessions.get(session.removeprefix("dashboard:"))
            if session_obj is None:
                return "the chat turn that asked for it has ended"
            future = session_obj._approval_futures.get(str(entry["request_id"]))
            if future is None:
                return UNVERIFIABLE
            if future.done():
                return "the chat turn that asked for it has ended"
        return ""
    except Exception:
        state._log.warning(
            "could not check the owner of approval %s", entry.get("id"), exc_info=True
        )
        return UNVERIFIABLE
