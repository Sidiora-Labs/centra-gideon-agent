"""Visibility of private and chat-owned batch runs from verified native work ancestry."""

from __future__ import annotations

from typing import Any

from gideon.automation.workflows import ownership, store
from gideon.automation.workflows.models import OriginKind

OWNER_KEY = "chat_owner"


def whose(run: Any) -> dict[str, Any] | None:
    root = (
        store.get(run.root_run_id)
        if run.root_run_id and run.root_run_id != run.id
        else run
    )
    root = root or run
    mode = ownership.run_mode(root)
    batch = OriginKind.SUBAGENT_TOOL in (run.origin.kind, root.origin.kind)
    if mode is ownership.MemoryMode.NORMAL and not batch:
        return None
    return {
        "session": str(root.extra.get(OWNER_KEY) or root.origin.session_key or ""),
        "mode": mode.value,
        "batch": batch,
    }


def reads(
    run: Any,
    *,
    session_key: str = "",
    origin_session_key: str = "",
    owner: bool = False,
) -> bool:
    if owner:
        return True
    chat = whose(run)
    if chat is None:
        return True
    source = origin_session_key or session_key
    return bool(source and chat["session"] and source == chat["session"])


def request_reader(request: Any) -> dict[str, Any]:
    from gideon.security.approval_answer import OWNER, work_principal_of_request
    from gideon.security.session_credentials import work_of_request

    proof = work_of_request(request)
    actor = work_principal_of_request(request)
    return {
        "session_key": proof.session_key if proof else "",
        "origin_session_key": proof.origin_session_key if proof else "",
        "owner": actor.kind == OWNER,
    }


def current_reader() -> dict[str, Any]:
    from gideon.security.session_credentials import current_work

    proof = current_work()
    return {
        "session_key": proof.session_key if proof else "",
        "origin_session_key": proof.origin_session_key if proof else "",
    }
