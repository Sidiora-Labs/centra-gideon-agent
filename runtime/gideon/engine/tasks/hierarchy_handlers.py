"""Hierarchy request policies and project/list route composition."""

import logging
import time
from typing import Any

from aiohttp import web

from gideon.automation.workflows import containers, leases
from gideon.automation.workflows import store as run_store
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.project_views import (
    BoardProjection,
    LinkedProjectInventory,
    ProjectBoard,
)
from gideon.http_errors import json_error
from gideon.security.security import is_sensitive_path, is_system_path

logger = logging.getLogger(__name__)

_CLAIM_TTL_SECS = 300

_PROJECT_UPDATABLE = frozenset(
    {
        "name",
        "name_locked",
        "status",
        "brief",
        "workspace_dir",
        "agent_instructions_template",
    }
)
_TASK_LIST_UPDATABLE = frozenset({"name", "project_id", "agent_instructions_template"})


class HierarchyRequest:
    @staticmethod
    async def body(request):
        try:
            return await request.json()
        except Exception:
            return json_error("invalid_json", status=400)

    @classmethod
    async def patch(cls, request, allowed):
        body = await cls.body(request)
        if isinstance(body, web.Response):
            return body
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "body must be a JSON object"}, status=400
            )
        rejected = _unwritable_field(body, allowed)
        if rejected is not None:
            return web.json_response(
                {"error": f"'{rejected}' is not an updatable field"}, status=400
            )
        return body

    @staticmethod
    def record(record, project, *, status=200):
        if not record:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(project(record), status=status)

    @classmethod
    def write(cls, operation, project, *, status=200):
        try:
            record = operation()
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return cls.record(record, project, status=status)


class ProjectRetirement:
    def __init__(self, request):
        self.project_id = request.match_info["project_id"]
        self.state = request.app.get("state")
        self.force = request.query.get("force") in ("1", "true", "yes")
        self.store = _store()

    async def detach(self):
        if self.force:
            await _teardown_bound_loops(self.project_id)
            _unbind_bound_chats(self.state, self.project_id)
            return None
        loops, code = _bound_work_counts(self.project_id)
        chats = len(_bound_chat_sessions(self.state, self.project_id))
        counts = {"loops": loops, "code": code, "chats": chats}
        if any(counts.values()):
            return web.json_response(
                {"error": "project has bound work", **counts}, status=409
            )
        return None

    async def remove_tasks(self):
        project = self.store.get_project(self.project_id)
        if project is None:
            return
        await _remove_task_list_tasks(
            row.id for row in self.store.list_task_lists(project_id=self.project_id)
        )

    async def respond(self):
        refusal = await self.detach()
        if refusal is not None:
            return refusal
        await self.remove_tasks()
        return HierarchyRequest.write(
            lambda: self.store.delete_project(self.project_id), lambda _: {"ok": True}
        )


class TaskListRetirement:
    def __init__(self, request):
        self.list_id = request.match_info["list_id"]
        self.store = _store()

    async def respond(self):
        if self.store.get_task_list(self.list_id) is None:
            return web.json_response({"error": "not found"}, status=404)
        await _remove_task_list_tasks((self.list_id,))
        return HierarchyRequest.record(
            self.store.delete_task_list(self.list_id), lambda _: {"ok": True}
        )


async def _remove_task_list_tasks(list_ids):
    from gideon.engine.tasks import registry

    for list_id in dict.fromkeys(list_ids):
        await registry.delete_tasks(task_list_id=list_id)


class RepeatableListReset:
    def __init__(self, list_id):
        self.list_id = list_id

    async def respond(self):
        from gideon.engine.tasks import registry
        from gideon.engine.tasks.models import TERMINAL_STATUSES

        store = _store()
        record = store.get_task_list(self.list_id)
        if not record:
            return web.json_response({"error": "not found"}, status=404)
        project = store.get_project(record.project_id)
        if not project or project.name != "Repeatable":
            return web.json_response(
                {"error": "only task lists under the Repeatable project can be reset"},
                status=400,
            )
        tasks, _ = await registry.list_all_tasks(
            task_list_id=self.list_id, limit=10_000
        )
        if any(task.status not in TERMINAL_STATUSES for task in tasks):
            return web.json_response(
                {"error": "all tasks must be complete before the list can be reset"},
                status=400,
            )
        reset_ids = []
        for task in tasks:
            task.reset_for_repeat()
            await registry.update_task(
                task.id,
                status=task.status.value,
                exit_criteria=task.exit_criteria,
                action_plan=task.action_plan,
                execution_notes=[],
                blocked_reason_kind="",
                blocked_kind="",
                preview="",
                evidence=[],
                attempts=[],
            )
            reset_ids.append(task.id)
        return web.json_response({"ok": True, "reset_task_ids": reset_ids})


def _store() -> HierarchyStore:
    return HierarchyStore()


def _unwritable_field(body: dict, allowed: frozenset[str]) -> str | None:
    return next((key for key in body if key not in allowed), None)


def _workspace_refusal(workspace_dir: str) -> web.Response | None:
    if workspace_dir and (
        is_sensitive_path(workspace_dir) or is_system_path(workspace_dir)
    ):
        return web.json_response(
            {"error": "Workspace directory points to a system or sensitive location."},
            status=403,
        )
    return None


def _project_payload(
    store: HierarchyStore, project, *, list_counts: dict | None = None
) -> dict:
    count = (
        len(store.list_task_lists(project_id=project.id))
        if list_counts is None
        else list_counts.get(project.id, 0)
    )
    return {
        **project.to_dict(),
        "context_dir": str(store.context_dir(project.id)),
        "task_list_count": count,
    }


async def api_projects_list(request: web.Request) -> web.Response:
    from collections import Counter

    store = _store()
    projects = store.list_projects()
    counts = Counter(row.project_id for row in store.list_task_lists())
    return web.json_response(
        {
            "projects": [
                _project_payload(store, row, list_counts=counts) for row in projects
            ]
        }
    )


async def api_projects_create(request: web.Request) -> web.Response:
    body = await HierarchyRequest.body(request)
    if isinstance(body, web.Response):
        return body
    store = _store()
    refusal = _workspace_refusal(str(body.get("workspace_dir") or "").strip())
    if refusal is not None:
        return refusal

    def create():
        return store.create_project(
            **{
                key: body.get(key, "")
                for key in (
                    "name",
                    "agent_instructions_template",
                    "brief",
                    "workspace_dir",
                )
            },
            name_locked=bool(body.get("name_locked", False)),
        )

    def project_payload(project):
        return {
            **_project_payload(store, project),
            "pack_proposals": _fingerprint_proposals(project),
        }

    return HierarchyRequest.write(create, project_payload, status=201)


def _fingerprint_proposals(project: Any) -> list[dict[str, Any]]:
    from gideon.extensions.packs.fingerprint import SCAN_REASON_CREATE, scan_project

    try:
        proposals = scan_project(project, reason=SCAN_REASON_CREATE, with_inspect=False)
        return list(map(lambda proposal: proposal.to_dict(), proposals))
    except Exception:
        logger.warning("fingerprint scan failed for a new project", exc_info=True)
        return []


async def api_projects_get(request: web.Request) -> web.Response:
    store = _store()
    record = store.get_project(request.match_info["project_id"])
    return HierarchyRequest.record(
        record, lambda project: _project_payload(store, project)
    )


async def api_projects_linked(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    if _store().get_project(project_id) is None:
        return web.json_response({"error": "not found"}, status=404)
    inventory = LinkedProjectInventory(project_id, request.app)
    return web.json_response(inventory.collect())


_LOOP_STATE = {
    "running": containers.BoardState.WORKING,
    "intake": containers.BoardState.WORKING,
    "planning": containers.BoardState.WORKING,
    "review": containers.BoardState.REVIEW,
    "ready": containers.BoardState.QUEUED,
    "paused": containers.BoardState.SUSPENDED,
    "blocked": containers.BoardState.NEEDS_INPUT,
    "stagnant": containers.BoardState.NEEDS_INPUT,
    "needs_input": containers.BoardState.NEEDS_INPUT,
    "complete": containers.BoardState.DONE,
    "stopped": containers.BoardState.DONE,
    "failed": containers.BoardState.DONE,
}

_TASK_STATE = {
    "in_progress": containers.BoardState.WORKING,
    "blocked": containers.BoardState.NEEDS_INPUT,
    "done": containers.BoardState.DONE,
    "cancelled": containers.BoardState.DONE,
    "skipped": containers.BoardState.DONE,
    "open": containers.BoardState.QUEUED,
}


def _as_board_row(d: dict) -> containers.BoardRow:
    return BoardProjection.decode(d)


def _run_rows(pid: str, now: float) -> list[dict]:
    records, _ = run_store.list_runs(project_id=pid, limit=500)
    result = []
    for record in records:
        claim = leases.read_claim(record.id)
        row = containers.board_row(record, claim_record=claim, now=now)
        result.append(row.to_dict())
    return result


def _loop_rows(pid: str) -> list[dict]:
    from gideon.automation.loop import store as loop_store

    return [
        BoardProjection.loop(record, pid, _LOOP_STATE)
        for record in loop_store.list_for_project(pid)
    ]


def _task_rows(tasks: list, pid: str) -> list[dict]:
    standalone = (
        record for record in tasks if getattr(record, "workflow_binding", None) is None
    )
    return [BoardProjection.task(record, pid, _TASK_STATE) for record in standalone]


async def api_projects_work(request: web.Request) -> web.Response:
    project = _store().get_project(request.match_info["project_id"])
    if project is None:
        return web.json_response({"error": "not found"}, status=404)
    board = ProjectBoard(project, time.time())
    await board.prefetch()
    return web.json_response(
        board.response(_run_rows, _loop_rows, _task_rows, _as_board_row)
    )


async def _claim_body(request: web.Request) -> tuple[str, str] | web.Response:
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    target_id = str(body.get("target_id", "") or "").strip()
    holder = str(body.get("holder", "") or "").strip()
    if target_id and holder:
        return target_id, holder
    return web.json_response({"error": "target_id and holder are required"}, status=400)


class ProjectClaimRequest:
    def __init__(self, request):
        self.request = request

    async def respond(self, *, release):
        if _store().get_project(self.request.match_info["project_id"]) is None:
            return web.json_response({"error": "not found"}, status=404)
        parsed = await _claim_body(self.request)
        if isinstance(parsed, web.Response):
            return parsed
        target, holder = parsed
        if release:
            claim, reason = leases.release_claim(target, holder)
            outcome = {"released": claim is None and not reason}
        else:
            claim, reason = leases.acquire_claim(target, holder, ttl=_CLAIM_TTL_SECS)
            outcome = {"granted": claim is not None}
        return web.json_response(
            {**outcome, "claim": claim.to_dict() if claim else None, "reason": reason}
        )


async def api_projects_work_claim(request: web.Request) -> web.Response:
    return await ProjectClaimRequest(request).respond(release=False)


async def api_projects_work_release(request: web.Request) -> web.Response:
    return await ProjectClaimRequest(request).respond(release=True)


async def api_projects_update(request: web.Request) -> web.Response:
    body = await HierarchyRequest.patch(request, _PROJECT_UPDATABLE)
    if isinstance(body, web.Response):
        return body
    if "workspace_dir" in body:
        refusal = _workspace_refusal(str(body["workspace_dir"] or "").strip())
        if refusal is not None:
            return refusal
    store = _store()
    return HierarchyRequest.write(
        lambda: store.update_project(request.match_info["project_id"], **body),
        lambda project: _project_payload(store, project),
    )


def _bound_work_counts(pid: str) -> tuple[int, int]:
    counts = {"loops": 0, "code": 0}
    try:
        from gideon.automation.loop import store

        for record in store.list_all():
            if pid in (record.project_id, record.tasks_project_id):
                counts["code" if record.kind == "code" else "loops"] += 1
    except Exception:
        pass
    return counts["loops"], counts["code"]


def _bound_chat_sessions(state, pid: str) -> list:
    records = []
    try:
        sessions = (getattr(state, "_sessions", {}) or {}).values()
        for session in sessions:
            manual = not str(getattr(session, "_app", "") or "")
            if getattr(session, "project_id", "") == pid and manual:
                records.append(session)
    except Exception:
        logger.debug("bound-chat scan failed for %s", pid, exc_info=True)
    return records


def _unbind_bound_chats(state, pid: str) -> int:
    detached = []
    for session in _bound_chat_sessions(state, pid):
        try:
            session.project_id = ""
        except Exception:
            logger.debug(
                "unbind chat %s failed", getattr(session, "key", "?"), exc_info=True
            )
        else:
            detached.append(session)
    return len(detached)


async def _teardown_bound_loops(pid: str) -> None:
    try:
        from gideon.automation.loop import manager, store
        from gideon.automation.triggers.nudge import get_instance

        service = get_instance()
        identifiers = [
            record.id
            for record in store.list_all()
            if pid in (record.project_id, record.tasks_project_id)
        ]
        for identifier in identifiers:
            try:
                if service is not None:
                    await manager.teardown_for_delete(service, identifier)
                store.delete(identifier)
            except Exception:
                logger.debug(
                    "force-delete: teardown of bound loop %s failed",
                    identifier,
                    exc_info=True,
                )
    except Exception:
        logger.debug(
            "force-delete: bound-loop teardown sweep failed for %s", pid, exc_info=True
        )


async def api_projects_delete(request: web.Request) -> web.Response:
    return await ProjectRetirement(request).respond()


async def api_task_lists_list(request: web.Request) -> web.Response:
    records = _store().list_task_lists(project_id=request.query.get("project_id"))
    return web.json_response(
        {"task_lists": list(map(lambda record: record.to_dict(), records))}
    )


async def api_task_lists_create(request: web.Request) -> web.Response:
    body = await HierarchyRequest.body(request)
    if isinstance(body, web.Response):
        return body
    return HierarchyRequest.write(
        lambda: _store().create_task_list(
            **{
                key: body.get(key, "")
                for key in (
                    "name",
                    "project_id",
                    "project_name",
                    "agent_instructions_template",
                )
            },
            repeatable=bool(body.get("repeatable", False)),
        ),
        lambda record: record.to_dict(),
        status=201,
    )


async def api_task_lists_get(request: web.Request) -> web.Response:
    record = _store().get_task_list(request.match_info["list_id"])
    return HierarchyRequest.record(record, lambda record: record.to_dict())


async def api_task_lists_update(request: web.Request) -> web.Response:
    body = await HierarchyRequest.patch(request, _TASK_LIST_UPDATABLE)
    if isinstance(body, web.Response):
        return body
    return HierarchyRequest.write(
        lambda: _store().update_task_list(request.match_info["list_id"], **body),
        lambda record: record.to_dict(),
    )


async def api_task_lists_delete(request: web.Request) -> web.Response:
    return await TaskListRetirement(request).respond()


async def api_task_lists_reset(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or not body.get("confirm"):
        return web.json_response(
            {
                "error": {
                    "code": "confirm_required",
                    "message": "reset clears execution notes and un-completes every exit criterion — pass confirm: true",
                }
            },
            status=400,
        )
    return await RepeatableListReset(request.match_info["list_id"]).respond()


async def api_projects_export(request: web.Request) -> web.Response:
    from gideon.core.config.loader import config_dir
    from gideon.engine.tasks.project_transfer import ProjectExport

    identifier = request.match_info["project_id"]
    project = _store().get_project(identifier)
    if project is None:
        return web.json_response({"error": "not found"}, status=404)
    transfer = ProjectExport(
        project,
        config_dir() / "projects" / identifier,
        request.query.get("passphrase", ""),
    )
    return await transfer.response()


async def api_projects_import(request: web.Request) -> web.Response:
    from gideon.engine.tasks.project_transfer import ProjectImport

    transfer = ProjectImport(request, _store, _read_project_upload, _import_summary)
    return await transfer.response()


def _import_summary(plan) -> str:
    from gideon.automation.workflows.project_export import import_summary

    return import_summary(plan)


async def _read_project_upload(request: web.Request):
    from gideon.engine.tasks.project_transfer import ArchiveUpload

    return await ArchiveUpload.receive(request)


def register_hierarchy_routes(app: web.Application) -> None:
    projects = (
        ("POST", "/import", api_projects_import),
        ("GET", "", api_projects_list),
        ("POST", "", api_projects_create),
        ("GET", "/{project_id}", api_projects_get),
        ("GET", "/{project_id}/export", api_projects_export),
        ("GET", "/{project_id}/linked", api_projects_linked),
        ("GET", "/{project_id}/work", api_projects_work),
        ("POST", "/{project_id}/work/claim", api_projects_work_claim),
        ("POST", "/{project_id}/work/release", api_projects_work_release),
        ("PUT", "/{project_id}", api_projects_update),
        ("DELETE", "/{project_id}", api_projects_delete),
    )
    lists = (
        ("GET", "", api_task_lists_list),
        ("POST", "", api_task_lists_create),
        ("GET", "/{list_id}", api_task_lists_get),
        ("PUT", "/{list_id}", api_task_lists_update),
        ("DELETE", "/{list_id}", api_task_lists_delete),
    )
    for method, suffix, handler in projects:
        getattr(app.router, f"add_{method.lower()}")(f"/api/projects{suffix}", handler)
    app.router.add_post("/api/task-lists/{list_id}/reset", api_task_lists_reset)
    for method, suffix, handler in lists:
        getattr(app.router, f"add_{method.lower()}")(
            f"/api/task-lists{suffix}", handler
        )
