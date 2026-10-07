"""Owner review and approval for agent-registered webhook callbacks."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.security.approval_answer import OWNER, of_request
from gideon.security.owner_grants import GrantBook, seal

_CALLBACK_GRANTS = GrantBook("callbacks")


def _store_path() -> Path:
    from gideon.core.config.loader import config_dir

    return Path(config_dir()) / "hooks.json"


def _read_callbacks() -> dict[str, dict]:
    path = _store_path()
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return {}
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


@contextmanager
def _callback_store_lock():
    path = _store_path()
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("callback lock is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _callback_content(hook_id: str, entry: dict) -> str:
    context = entry.get("context_summary", "") or entry.get("summary", "")
    return json.dumps(
        {
            "hook_id": hook_id,
            "session_key": entry.get("session_key", f"hook:{hook_id}"),
            "context_summary": context,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def callback_snapshot(hook_id: str) -> tuple[str, str, bool, str] | None:
    """Return one registered revision, grant state, and its exact saved context."""
    entry = _read_callbacks().get(hook_id)
    if entry is None:
        return None
    content = _callback_content(hook_id, entry)
    revision = seal(content)
    key = f"callback:{hook_id}"
    context = str(entry.get("context_summary", "") or entry.get("summary", ""))
    registered = entry.get("registered_at", 0)
    try:
        age_hours = (time.time() - float(registered)) / 3600 if registered else 25
    except (TypeError, ValueError):
        age_hours = 25
    if age_hours > 24:
        context = ""
    elif age_hours > 1:
        context = f"[Context from {age_hours:.0f}h ago — may be outdated]\n{context}"
    return content, revision, _CALLBACK_GRANTS.holds(key, content), context


def callback_revision(hook_id: str) -> tuple[str, str, bool] | None:
    """Return the current callback revision and grant state, or None if unreadable."""
    snapshot = callback_snapshot(hook_id)
    return snapshot[:3] if snapshot is not None else None


async def api_hooks_pending(request: web.Request) -> web.Response:
    """GET /api/hooks/pending — show callbacks awaiting the owner's decision."""
    principal = of_request(request)
    if principal.kind != OWNER or not principal.name:
        return web.json_response({"error": "owner required"}, status=403)
    rows = []
    for hook_id, entry in sorted(_read_callbacks().items()):
        content = _callback_content(hook_id, entry)
        revision = seal(content)
        allowed = _CALLBACK_GRANTS.holds(f"callback:{hook_id}", content)
        if allowed:
            continue
        summary = str(entry.get("context_summary", "") or entry.get("summary", ""))
        rows.append(
            {
                "id": hook_id,
                "revision": revision,
                "context_summary": summary,
                "question": (
                    "Allow this registered webhook callback to start an agent turn using "
                    f"the saved context below?\n\n{summary[:2000]}"
                ),
            }
        )
    return web.json_response({"callbacks": rows})


async def api_hook_allow(request: web.Request) -> web.Response:
    """POST /api/hooks/{hook_id}/allow — confirm one current callback revision."""
    principal = of_request(request)
    if principal.kind != OWNER or not principal.name:
        return web.json_response({"error": "owner required"}, status=403)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or body.get("confirm") is not True:
        return web.json_response(
            {
                "error": "confirmation_required",
                "question": "Allow this registered webhook callback to start agent turns with its saved context? Review the current callback before confirming.",
            },
            status=409,
        )
    seen = body.get("seen")
    current = callback_revision(request.match_info["hook_id"])
    if current is None or not isinstance(seen, str) or current[1] != seen:
        return web.json_response(
            {
                "error": "callback_changed",
                "message": "Review the current callback again before allowing it.",
            },
            status=409,
        )
    try:
        with _callback_store_lock():
            current = callback_revision(request.match_info["hook_id"])
            if current is None or current[1] != seen:
                return web.json_response(
                    {
                        "error": "callback_changed",
                        "message": "Review the current callback again before allowing it.",
                    },
                    status=409,
                )
            _CALLBACK_GRANTS.give(
                f"callback:{request.match_info['hook_id']}",
                current[0],
                principal=principal.label,
            )
    except OSError:
        return web.json_response({"error": "grant_unavailable"}, status=503)
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=principal.label,
            operation="hook.callback_allow",
            outcome="allowed",
            source="dashboard",
            resources="one reviewed callback revision",
        )
    except Exception:
        return web.json_response({"error": "audit_unavailable"}, status=503)
    return web.json_response({"ok": True})
