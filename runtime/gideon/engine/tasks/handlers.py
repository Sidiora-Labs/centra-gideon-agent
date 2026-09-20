"""Task HTTP request admission, batch execution and response projection."""

from aiohttp import web

from gideon.core.http_request import RequestBodyTypeError, read_json_body
from gideon.engine.tasks import reconcile, registry
from gideon.engine.tasks.models import Task
from gideon.engine.tasks.provider import task_page_window
from gideon.engine.tasks.rules import resolve_reject_write
from gideon.http_errors import json_error

_SUPPLIED_AUTHOR_ERROR = "author is server-derived and must not be supplied"
_INVALID_JSON = object()
_INVALID_BODY = object()


async def _request_body(request: web.Request):
    try:
        return await read_json_body(request)
    except RequestBodyTypeError:
        return _INVALID_BODY
    except Exception:
        return _INVALID_JSON


def _supplies_author(payload: object) -> bool:
    return isinstance(payload, dict) and "author" in payload


def _owner_username() -> str:
    try:
        from gideon.cognition.identity import current_username

        return current_username()
    except Exception:
        return ""


def _with_block_reason(task: Task, task_map: dict[str, Task]) -> dict:
    return {
        **task.to_dict(),
        "block_reason": reconcile.block_reason(task, task_map),
        "comment_count": getattr(task, "_comment_count", 0),
    }


def _task_rows(tasks: list[Task]) -> list[dict]:
    siblings = {task.id: task for task in tasks}
    return [_with_block_reason(task, siblings) for task in tasks]


class TaskQuery:
    def __init__(self, request: web.Request):
        self.values = request.query

    @property
    def list_id(self):
        return self.values.get("task_list") or self.values.get("task_list_id")

    def enabled(self, name: str) -> bool:
        return str(self.values.get(name, "")).strip().lower() in {"1", "true", "yes"}

    async def page(self) -> dict:
        registry.validate_provider(self.values.get("provider"))
        limit, offset = task_page_window(
            self.values.get("limit", "500"), self.values.get("offset", "0")
        )
        tasks, total = await registry.list_all_tasks(
            **{
                name: self.values.get(name)
                for name in ("status", "assignee", "project")
            },
            task_list_id=self.list_id,
            provider_filter=self.values.get("provider"),
            limit=limit,
            offset=offset,
        )
        if self.enabled("mine"):
            from gideon.cognition.identity import current_username

            owner = current_username()
            if owner:
                tasks = [task for task in tasks if task.belongs_to(owner)]
                total = len(tasks)
        return {
            "tasks": _task_rows(tasks),
            "total": total,
            "limit": limit,
            "offset": offset,
            "owner": _owner_username(),
        }

    async def ready(self) -> dict:
        rows = await registry.ready_tasks(
            project=self.values.get("project"),
            task_list_id=self.list_id,
            mine_only=not self.enabled("everyone"),
        )
        return {"tasks": _task_rows(rows)}


class TaskBatch:
    def __init__(self, operation: str, items: list):
        self.operation = operation
        self.items = items

    async def admission_errors(self) -> list[dict]:
        errors = []
        for index, item in enumerate(self.items):
            if self.operation != "delete" and _supplies_author(item):
                errors.append({"index": index, "error": _SUPPLIED_AUTHOR_ERROR})
            if self.operation == "create":
                valid = bool(
                    isinstance(item, dict) and str(item.get("title", "")).strip()
                )
                reason = "title required"
            else:
                valid = bool(item.get("id") if isinstance(item, dict) else item)
                reason = "id required"
            if not valid:
                errors.append({"index": index, "error": reason})
            if isinstance(item, dict):
                try:
                    if self.operation == "create":
                        registry._resolve(item.get("provider", "native"))
                    elif item.get("id"):
                        await registry._resolve_one(item["id"], item.get("provider"))
                except ValueError as exc:
                    errors.append({"index": index, "error": str(exc)})
        if errors:
            return errors
        if self.operation == "delete":
            return errors
        for index, item in enumerate(self.items):
            try:
                task_id = item["id"] if self.operation == "update" else None
                if task_id is not None:
                    fields = {
                        key: value
                        for key, value in item.items()
                        if key not in ("id", "provider")
                    }
                    refusal = await resolve_reject_write(
                        task_id, fields, provider_name=item.get("provider")
                    )
                    if refusal:
                        errors.append(
                            {
                                "index": index,
                                "code": "engine_owned_field",
                                "error": refusal,
                            }
                        )
                        continue
                await registry.validate_task_write(
                    task_id,
                    provider_name=item.get("provider"),
                    **{
                        key: value
                        for key, value in item.items()
                        if key not in ("id", "provider")
                    },
                )
            except ValueError as exc:
                errors.append({"index": index, "error": str(exc)})
        return errors

    def receipt(self, results: list, errors: list, *, refused=False) -> dict:
        return {
            "total": len(self.items),
            "succeeded": len(results),
            "failed": len(self.items) if refused else len(errors),
            "results": results,
            "errors": errors,
        }

    async def apply_item(self, item) -> dict:
        if self.operation == "create":
            task = await registry.create_task(
                provider_name=item.get("provider", "native"),
                **{key: value for key, value in item.items() if key != "provider"},
            )
            return {"task_id": task.id, "status": "created"}
        if self.operation == "update":
            updated = await registry.update_task(
                item["id"],
                provider_name=item.get("provider"),
                **{
                    key: value
                    for key, value in item.items()
                    if key not in ("id", "provider")
                },
            )
            return {
                "task_id": item["id"],
                "status": "updated" if updated else "not_found",
            }
        identifier = item.get("id") if isinstance(item, dict) else item
        provider = item.get("provider") if isinstance(item, dict) else None
        removed = (
            await registry.delete_task(str(identifier), provider_name=provider)
            if identifier
            else False
        )
        return {"task_id": identifier, "status": "deleted" if removed else "not_found"}

    async def execute(self) -> web.Response:
        errors = await self.admission_errors()
        if errors:
            refusal = next(
                (
                    error
                    for error in errors
                    if error.get("code") == "engine_owned_field"
                ),
                None,
            )
            if refusal:
                return json_error(
                    "engine_owned_field",
                    message=refusal["error"],
                    status=409,
                    **self.receipt([], errors, refused=True),
                )
            return web.json_response(self.receipt([], errors, refused=True), status=400)
        results, errors = [], []
        for index, item in enumerate(self.items):
            try:
                result = await self.apply_item(item)
                results.append({"index": index, **result})
            except Exception as exc:
                errors.append({"index": index, "error": str(exc)})
        return web.json_response(self.receipt(results, errors))


def _attach_project_general_list(body: dict) -> None:
    project_id = body.pop("project_id", "")
    has_task_list = "task_list_id" in body
    if not project_id and not has_task_list:
        return
    from gideon.engine.tasks.hierarchy import HierarchyStore

    body["task_list_id"] = (
        HierarchyStore()
        .task_destination(
            task_list_id=body.get("task_list_id", ""), project_id=project_id
        )
        .resolve()
    )


class TaskWrite:
    @staticmethod
    async def respond(request: web.Request, *, create: bool) -> web.Response:
        task_id = None if create else request.match_info["task_id"]
        body: dict = await _request_body(request)
        if body is _INVALID_BODY:
            return json_error(
                "invalid_body", message="body must be an object", status=400
            )
        if body is _INVALID_JSON:
            return json_error("invalid_json", message="invalid JSON", status=400)
        if _supplies_author(body):
            return json_error(
                "invalid_request", message=_SUPPLIED_AUTHOR_ERROR, status=400
            )
        if create:
            title = body.get("title")
            if not isinstance(title, str) or not title.strip():
                return json_error(
                    "invalid_request", message="title required", status=400
                )
        provider = body.pop("provider", "native" if create else None)
        created_task = None
        updated_task = None
        try:
            registry.validate_provider(provider)
            if not create or provider == "native":
                _attach_project_general_list(body)
            if create:
                created_task = await registry.create_task(
                    provider_name=provider, **body
                )
            else:
                if task_id is None:
                    return json_error(
                        "invalid_request", message="task id required", status=400
                    )
                refusal = await resolve_reject_write(
                    task_id, body, provider_name=provider
                )
                if refusal:
                    return json_error("engine_owned_field", message=refusal, status=409)
                updated_task = await registry.update_task(
                    task_id, provider_name=provider, **body
                )
        except reconcile.DependencyCycleError as exc:
            return json_error(
                "invalid_request", message=str(exc), status=400, cycle=exc.cycle
            )
        except ValueError as exc:
            return json_error("invalid_request", message=str(exc), status=400)
        if create:
            if created_task is None:
                return json_error(
                    "action_failed", message="task creation failed", status=500
                )
            return web.json_response(created_task.to_dict(), status=201)
        if not updated_task:
            return json_error("not_found", message="not found", status=404)
        changed = getattr(updated_task, "_reconciled", [updated_task])
        siblings = {row.id: row for row in changed}
        return web.json_response(
            {
                **_with_block_reason(updated_task, siblings),
                "reconciled": [_with_block_reason(row, siblings) for row in changed],
            }
        )


async def api_tasks_list(request: web.Request) -> web.Response:
    try:
        page = await TaskQuery(request).page()
    except (TypeError, ValueError) as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return web.json_response(page)


async def api_tasks_ready(request: web.Request) -> web.Response:
    return web.json_response(await TaskQuery(request).ready())


async def api_tasks_graph(request: web.Request) -> web.Response:
    provider = request.query.get("provider")
    try:
        registry.validate_provider(provider)
        projection = await registry.task_graph(provider_filter=provider)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return web.json_response(projection)


async def api_tasks_search(request: web.Request) -> web.Response:
    body = await _request_body(request)
    if body is _INVALID_BODY:
        return json_error("invalid_body", message="body must be an object", status=400)
    if body is _INVALID_JSON:
        return json_error("invalid_json", message="invalid JSON", status=400)
    try:
        limit, offset = task_page_window(body.get("limit", 50), body.get("offset", 0))
        tasks, total = await registry.search_tasks(
            query=body.get("query", ""),
            statuses=body.get("status") or body.get("statuses"),
            priorities=body.get("priority") or body.get("priorities"),
            tags=body.get("tags"),
            project=body.get("project") or body.get("project_id"),
            task_list_id=body.get("task_list_id"),
            sort_by=body.get("sort_by", "relevance"),
            limit=limit,
            offset=offset,
        )
    except (TypeError, ValueError) as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return web.json_response(
        {
            "tasks": _task_rows(tasks),
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


async def api_tasks_bulk(request: web.Request) -> web.Response:
    body = await _request_body(request)
    if body is _INVALID_BODY:
        return json_error("invalid_body", message="body must be an object", status=400)
    if body is _INVALID_JSON:
        return json_error("invalid_json", message="invalid JSON", status=400)
    operation, items = body.get("op", ""), body.get("items")
    if operation not in ("create", "update", "delete") or not isinstance(items, list):
        return json_error(
            "invalid_request",
            message="body must be {op: create|update|delete, items: [...]}",
            status=400,
        )
    return await TaskBatch(operation, items).execute()


async def api_tasks_get(request: web.Request) -> web.Response:
    provider = request.query.get("provider")
    try:
        registry.validate_provider(provider)
        task = await registry.get_task(
            request.match_info["task_id"], provider_name=provider
        )
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    if not task:
        return json_error("not_found", message="not found", status=404)
    return web.json_response(
        {**task.to_dict(), "comment_count": getattr(task, "_comment_count", 0)}
    )


async def api_tasks_create(request: web.Request) -> web.Response:
    return await TaskWrite.respond(request, create=True)


async def api_tasks_update(request: web.Request) -> web.Response:
    return await TaskWrite.respond(request, create=False)


async def _delete_response(operation, **payload) -> web.Response:
    try:
        removed = await operation
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return (
        web.json_response({"ok": True, **payload})
        if removed
        else json_error("not_found", message="not found", status=404)
    )


async def api_tasks_delete(request: web.Request) -> web.Response:
    provider = request.query.get("provider")
    try:
        registry.validate_provider(provider)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return await _delete_response(
        registry.delete_task(request.match_info["task_id"], provider_name=provider)
    )


async def api_tasks_comments_get(request: web.Request) -> web.Response:
    provider = request.query.get("provider")
    try:
        registry.validate_provider(provider)
        records = await registry.get_comments(
            request.match_info["task_id"], provider_name=provider
        )
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return web.json_response({"comments": [record.to_dict() for record in records]})


async def api_tasks_comments_post(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    body = await _request_body(request)
    if body is _INVALID_BODY:
        return json_error("invalid_body", message="body must be an object", status=400)
    if body is _INVALID_JSON:
        return json_error("invalid_json", message="invalid JSON", status=400)
    if not isinstance(body, dict):
        return json_error(
            "invalid_request", message="body must be an object", status=400
        )
    if _supplies_author(body):
        return json_error("invalid_request", message=_SUPPLIED_AUTHOR_ERROR, status=400)
    raw = body.get("body")
    if raw is not None and not isinstance(raw, str):
        return json_error(
            "invalid_request", message="body must be a string", status=400
        )
    message = (raw or "").strip()
    if not message:
        return json_error("invalid_request", message="body required", status=400)
    try:
        registry.validate_provider(body.get("provider"))
        comment = await registry.add_comment(
            task_id,
            body=message,
            author=_owner_username(),
            provider_name=body.get("provider"),
        )
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return (
        web.json_response(comment.to_dict(), status=201)
        if comment
        else json_error("not_found", message="task not found", status=404)
    )


async def api_tasks_comments_delete(request: web.Request) -> web.Response:
    comment_id = request.match_info["comment_id"]
    provider = request.query.get("provider")
    try:
        registry.validate_provider(provider)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    return await _delete_response(
        registry.delete_comment(
            request.match_info["task_id"],
            comment_id,
            provider_name=provider,
        ),
        id=comment_id,
    )


async def api_tasks_providers(request: web.Request) -> web.Response:
    return web.json_response({"providers": registry.list_providers()})


def register_task_routes(app: web.Application) -> None:
    from gideon.engine.tasks.hierarchy_handlers import register_hierarchy_routes

    routes = (
        ("GET", "/providers", api_tasks_providers),
        ("GET", "/graph", api_tasks_graph),
        ("GET", "/ready", api_tasks_ready),
        ("POST", "/search", api_tasks_search),
        ("POST", "/bulk", api_tasks_bulk),
        ("GET", "", api_tasks_list),
        ("POST", "", api_tasks_create),
        ("GET", "/{task_id}", api_tasks_get),
        ("PUT", "/{task_id}", api_tasks_update),
        ("DELETE", "/{task_id}", api_tasks_delete),
        ("GET", "/{task_id}/comments", api_tasks_comments_get),
        ("POST", "/{task_id}/comments", api_tasks_comments_post),
        ("DELETE", "/{task_id}/comments/{comment_id}", api_tasks_comments_delete),
    )
    for method, suffix, handler in routes:
        register = getattr(app.router, f"add_{method.lower()}")
        register(f"/api/tasks{suffix}", handler)
    register_hierarchy_routes(app)
