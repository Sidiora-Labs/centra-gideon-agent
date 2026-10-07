"""Owner review and content-sealed grants for heartbeat task lines."""

from __future__ import annotations

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.security.approval_answer import OWNER, of_request


def _owner(request: web.Request) -> bool:
    principal = of_request(request)
    return principal.kind == OWNER and bool(principal.name)


async def api_heartbeat_tasks(request: web.Request) -> web.Response:
    """GET /api/heartbeat/tasks — show current tasks and their grant state."""
    if not _owner(request):
        return web.json_response({"error": "owner required"}, status=403)
    from gideon.engine.heartbeat import heartbeat_task_rows

    return web.json_response({"tasks": heartbeat_task_rows()})


async def api_heartbeat_task_allow(request: web.Request) -> web.Response:
    """POST /api/heartbeat/tasks/{task_id}/allow — allow one reviewed revision."""
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
                "question": "Allow this exact heartbeat task to run unattended? Review its text and destination before confirming.",
            },
            status=409,
        )
    seen = body.get("seen")
    if not isinstance(seen, str) or len(seen) != 64:
        return web.json_response({"error": "invalid task revision"}, status=400)
    from gideon.engine.heartbeat import allow_heartbeat_task

    if not allow_heartbeat_task(
        request.match_info["task_id"], seen=seen, principal=principal.label
    ):
        return web.json_response(
            {
                "error": "task_changed",
                "message": "The task changed or its grant could not be saved. Review the current task again.",
            },
            status=409,
        )
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=principal.label,
            operation="heartbeat.task_allow",
            outcome="allowed",
            source="dashboard",
            resources="one reviewed task revision",
        )
    except Exception:
        return web.json_response({"error": "audit_unavailable"}, status=503)
    return web.json_response({"ok": True})
