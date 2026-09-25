"""Owner workspace context endpoints using authoritative runtime references."""
import asyncio
import json

from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.workspace import ConflictError, SnapshotStore


def store():
    from .files import _dashboard_roots
    return SnapshotStore(config_dir() / "capabilities" / "workspace", allowed_roots=[p for _, p in _dashboard_roots()])


async def references(request, task_ids):
    from .terminal import api_terminal_list
    from gideon.engine.tasks.registry import get_task
    response = await api_terminal_list(request)
    if response.status != 200:
        raise ValueError("Terminal inventory unavailable")
    terminals = [s["session_id"] for s in json.loads(response.body)["sessions"] if s.get("alive")]
    tasks = [task_id for task_id in task_ids if await get_task(task_id) is not None]
    return {"terminal_ids": terminals, "task_ids": tasks}


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        return web.json_response({"error": "Owner authentication required"}, status=403)
    try:
        snapshots = store()
        snapshot_id = request.match_info.get("id")
        if request.method == "GET":
            result = snapshots.get(snapshot_id) if snapshot_id else snapshots.list(offset=int(request.query.get("offset", 0)), limit=int(request.query.get("limit", 100)))
        elif request.method == "DELETE":
            result = snapshots.delete(snapshot_id, int(request.query.get("revision", "")))
        elif snapshot_id:
            selected = snapshots.get(snapshot_id)
            refs = await references(request, selected["task_ids"])
            result = await asyncio.to_thread(snapshots.reconcile, snapshot_id, **refs)
        else:
            body = await read_json_body(request)
            from gideon.workspace.capabilities.workspace.store import identifiers
            refs = await references(request, identifiers(body.get("task_ids", [])))
            result = await asyncio.to_thread(snapshots.capture, body, **refs)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except FileNotFoundError:
        return web.json_response({"error": "Snapshot or workspace not found"}, status=404)
    except (ValueError, TypeError, AttributeError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except (OSError, TimeoutError):
        return web.json_response({"error": "Workspace context temporarily unavailable"}, status=503)


def processes(request):
    from gideon.workspace.capabilities.workspace.processes import get_registry
    from .files import _dashboard_roots
    registry = request.app.get("workspace_process_registry")
    if registry is None:
        registry = get_registry(config_dir() / "capabilities" / "workspace", allowed_roots=[p for _, p in _dashboard_roots()])
        request.app["workspace_process_registry"] = registry
    return registry


async def process_endpoint(request):
    if not request.get("user") or request.get("app"):
        return web.json_response({"error": "Owner authentication required"}, status=403)
    try:
        registry = processes(request)
        process_id = request.match_info.get("id")
        operation = request.match_info.get("operation")
        if request.method == "GET":
            if operation == "logs":
                result = registry.logs(process_id, limit=int(request.query.get("limit", 65536)))
            else:
                result = registry.get(process_id) if process_id else registry.list(offset=int(request.query.get("offset", 0)))
        elif operation == "stop":
            body = await read_json_body(request)
            result = await registry.stop(process_id, body.get("revision"))
        else:
            result = await registry.start(await read_json_body(request))
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except FileNotFoundError:
        return web.json_response({"error": "Process or workspace not found"}, status=404)
    except PermissionError as error:
        return web.json_response({"error": str(error)}, status=403)
    except (ValueError, TypeError, AttributeError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except OSError:
        return web.json_response({"error": "Process launch unavailable"}, status=503)


async def close_processes(app):
    from gideon.workspace.capabilities.workspace.processes import close_registry
    await close_registry(config_dir() / "capabilities" / "workspace")


def register(app):
    prefix = "/api/capabilities/workspace"
    app.router.add_get(prefix, endpoint)
    app.router.add_post(prefix, endpoint)
    app.router.add_get(prefix + "/processes", process_endpoint)
    app.router.add_post(prefix + "/processes", process_endpoint)
    app.router.add_get(prefix + "/processes/{id}", process_endpoint)
    app.router.add_get(prefix + "/processes/{id}/{operation:logs}", process_endpoint)
    app.router.add_post(prefix + "/processes/{id}/{operation:stop}", process_endpoint)
    app.on_cleanup.append(close_processes)
    app.router.add_get(prefix + "/{id}", endpoint)
    app.router.add_delete(prefix + "/{id}", endpoint)
    app.router.add_post(prefix + "/{id}/reconcile", endpoint)
