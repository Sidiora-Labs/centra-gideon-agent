"""Customer campaign and recorded workflow-run inspection routes."""

from __future__ import annotations

from aiohttp import web

from gideon.assurance.experiments import campaigns, replay
from gideon.automation.workflows import service
from gideon.automation.workflows.handlers import _audit, _guard, _supervisor
from gideon.core.http_request import read_json_body


def _error(code: str, message: str, status: int) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


async def _body(request: web.Request) -> dict:
    value = await read_json_body(request)
    if not isinstance(value, dict):
        raise ValueError("body must be an object")
    return value


async def campaigns_list(request: web.Request) -> web.Response:
    return web.json_response({"campaigns": campaigns.listing()})


async def campaigns_create(request: web.Request) -> web.Response:
    denied = _guard(request, "experiment_campaign_create")
    if denied is not None:
        return denied
    try:
        body = await _body(request)
        name = str(body.get("workflow_name") or "")
        if not (await service.get_def(name)).get("ok"):
            return _error("workflow_not_found", "Select an existing workflow definition.", 404)
        result = campaigns.create(body)
    except (ValueError, TypeError) as exc:
        return _error("invalid_campaign", str(exc), 400)
    _audit(request, "experiment_campaign_create", "success", result["id"])
    return web.json_response(result, status=201)


async def campaigns_detail(request: web.Request) -> web.Response:
    result = campaigns.detail(request.match_info["campaign_id"])
    return web.json_response(result) if result is not None else _error("not_found", "Campaign not found.", 404)


async def campaigns_advance(request: web.Request) -> web.Response:
    denied = _guard(request, "experiment_campaign_advance")
    if denied is not None:
        return denied
    try:
        result = await campaigns.advance(
            request.match_info["campaign_id"], supervisor=_supervisor(request),
            session_key=request.headers.get("X-Session-Key", ""),
        )
    except KeyError:
        return _error("not_found", "Campaign not found.", 404)
    except RuntimeError as exc:
        return _error("engine_unavailable", str(exc), 503)
    _audit(request, "experiment_campaign_advance", "success", result["id"])
    return web.json_response(result)


async def campaigns_observe(request: web.Request) -> web.Response:
    denied = _guard(request, "experiment_campaign_observe")
    if denied is not None:
        return denied
    try:
        body = await _body(request)
        if not isinstance(body.get("score"), (int, float)) or isinstance(body["score"], bool):
            raise ValueError("score must be numeric")
        if not isinstance(body.get("valid"), bool):
            raise ValueError("valid must be a boolean")
        result = campaigns.observe(
            request.match_info["campaign_id"], int(request.match_info["ordinal"]),
            score=float(body["score"]), valid=body["valid"],
            observation=str(body.get("observation") or ""),
        )
    except KeyError:
        return _error("not_found", "Campaign not found.", 404)
    except (ValueError, TypeError) as exc:
        return _error("invalid_observation", str(exc), 400)
    _audit(request, "experiment_campaign_observe", "success", result["id"])
    return web.json_response(result)


async def campaigns_stop(request: web.Request) -> web.Response:
    denied = _guard(request, "experiment_campaign_stop")
    if denied is not None:
        return denied
    try:
        result = campaigns.stop(request.match_info["campaign_id"])
    except KeyError:
        return _error("not_found", "Campaign not found.", 404)
    _audit(request, "experiment_campaign_stop", "success", result["id"])
    return web.json_response(result)


async def replay_capture(request: web.Request) -> web.Response:
    denied = _guard(request, "experiment_replay_capture")
    if denied is not None:
        return denied
    try:
        result = replay.capture(request.match_info["run_id"])
    except KeyError:
        return _error("not_found", "Workflow run not found.", 404)
    _audit(request, "experiment_replay_capture", "success", result["run_id"])
    return web.json_response(result, status=201)


async def replay_compare(request: web.Request) -> web.Response:
    try:
        result = replay.compare(request.match_info["replay_id"], request.query.get("candidate_run", ""))
    except KeyError:
        return _error("not_found", "Recording or workflow run not found.", 404)
    return web.json_response(result)


def register_experiment_routes(app: web.Application) -> None:
    app.router.add_get("/api/experiments/campaigns", campaigns_list)
    app.router.add_post("/api/experiments/campaigns", campaigns_create)
    app.router.add_get("/api/experiments/campaigns/{campaign_id}", campaigns_detail)
    app.router.add_post("/api/experiments/campaigns/{campaign_id}/advance", campaigns_advance)
    app.router.add_post("/api/experiments/campaigns/{campaign_id}/stop", campaigns_stop)
    app.router.add_post("/api/experiments/campaigns/{campaign_id}/attempts/{ordinal}/observation", campaigns_observe)
    app.router.add_post("/api/experiments/runs/{run_id}/record", replay_capture)
    app.router.add_get("/api/experiments/replays/{replay_id}", replay_compare)
