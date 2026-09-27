"""Authenticated decisions for knowledge-backed Idea-list recommendations."""

from __future__ import annotations

import hashlib

from aiohttp import web

from gideon.cognition.recommendation_records import RecommendationRecords
from gideon.core.http_request import RequestBodyTypeError, read_json_body
from gideon.engine.tasks import registry
from gideon.http_errors import json_error
from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
)

_SOURCE_KIND = "knowledge-idea-list"


def _authorized(request: web.Request) -> bool:
    state = request.app["state"]
    return bool(request.get("user")) and not _blocks_reads_session(
        state, request
    ) and not _is_restricted_session(state, request)


def _public(record: dict) -> dict:
    return {
        "source_kind": record["source_kind"],
        "source_id": record["source_id"],
        "source_list_id": record["source_list_id"],
        "source_revision": record["source_revision"],
        "source_title": record["source_title"],
        "source_evidence": record["source_evidence"],
        "decision": record["decision"],
        "edited_prompt": record["edited_prompt"],
        "task_id": record["task_id"],
        "request_id": record["request_id"],
        "updated_at": record["updated_at"],
    }


def _current_idea(request: web.Request, list_id: str, idea_id: str):
    lists = request.app["capability_idea_lists"]
    detail = lists.get(list_id)
    item = next(
        (row for row in detail.get("items", []) if row.get("id") == idea_id), None
    )
    if item is None:
        raise LookupError("The saved Idea is no longer available in its source list")
    return detail, item


async def list_decisions(request: web.Request) -> web.Response:
    if request.query:
        return json_error("invalid_request", message="query overrides are not supported", status=400)
    if not _authorized(request):
        return json_error("forbidden", message="An authenticated owner session is required", status=403)
    rows = request.app["assistant_idea_records"]
    with rows.transaction() as db:
        records = rows.list(db)
    return web.json_response({"items": [_public(row) for row in records]})


async def decide(request: web.Request) -> web.Response:
    if not _authorized(request):
        return json_error("forbidden", message="An authenticated owner session is required", status=403)
    try:
        body = await read_json_body(request)
    except RequestBodyTypeError:
        return json_error("invalid_body", message="body must be an object", status=400)
    if not isinstance(body, dict) or set(body) != {
        "source_kind", "source_list_id", "expected_revision", "decision",
        "edited_prompt", "request_id",
    }:
        return json_error("invalid_request", message="Idea decision fields are incomplete or unknown", status=400)
    source_id = request.match_info["idea_id"]
    source_kind = body.get("source_kind")
    list_id = body.get("source_list_id")
    revision = body.get("expected_revision")
    decision = body.get("decision")
    prompt = body.get("edited_prompt")
    request_id = body.get("request_id")
    if (
        source_kind != _SOURCE_KIND
        or not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 512
        or not isinstance(list_id, str) or not list_id.strip() or len(list_id) > 512
        or not isinstance(revision, str) or not revision.strip() or len(revision) > 256
        or decision not in ("accept", "dismiss")
        or not isinstance(prompt, str) or len(prompt) > 4000
        or not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200
    ):
        return json_error("invalid_request", message="Idea decision values are invalid", status=400)
    prompt = prompt.strip()
    if decision == "accept" and not prompt:
        return json_error("invalid_request", message="Edit the prompt before accepting this Idea", status=400)

    records: RecommendationRecords = request.app["assistant_idea_records"]
    try:
        with records.transaction() as db:
            prior_request = records.by_request(db, request_id)
            if prior_request is not None:
                if (
                    prior_request["source_kind"] != source_kind
                    or prior_request["source_id"] != source_id
                    or prior_request["source_list_id"] != list_id
                    or prior_request["source_revision"] != revision
                    or prior_request["decision"] != ("accepted" if decision == "accept" else "dismissed")
                    or prior_request["edited_prompt"] != prompt
                ):
                    return json_error("conflict", message="This request ID already records a different decision", status=409)
                return web.json_response(_public(prior_request))

            existing = records.get(db, source_kind, list_id, source_id)
            if existing is not None:
                wanted = "accepted" if decision == "accept" else "dismissed"
                if (
                    existing["decision"] == wanted
                    and existing["edited_prompt"] == prompt
                    and existing["source_list_id"] == list_id
                    and existing["source_revision"] == revision
                ):
                    return web.json_response(_public(existing))
                return json_error("conflict", message="This Idea already has a different recorded decision", status=409)

            try:
                detail, item = _current_idea(request, list_id, source_id)
            except (LookupError, ValueError, KeyError) as exc:
                return json_error("not_found", message=str(exc), status=404)
            current_revision = str(detail.get("hash") or "")
            if not current_revision or revision != current_revision:
                return json_error("conflict", message="The Idea source changed. Review the current evidence and retry.", status=409, current_revision=current_revision)
            title = str(item.get("title") or item.get("content") or "Saved Idea").strip()
            evidence = str(item.get("content") or "").strip()
            if not evidence:
                return json_error("conflict", message="The Idea no longer has source evidence", status=409)

            task_id = ""
            if decision == "accept":
                label = "idea-" + hashlib.sha256(
                    f"{source_kind}\0{list_id}\0{source_id}".encode("utf-8")
                ).hexdigest()[:32]
                task_id = await _existing_task_id(label)
                if not task_id:
                    try:
                        task = await registry.create_task(
                            provider_name="native",
                            title=prompt,
                            description=(
                                f"Accepted Idea: {title}\n\n{prompt}\n\n"
                                f"Source evidence: {evidence}\n"
                                f"Knowledge record: #/knowledge/item/{source_id}\n"
                                f"Idea list: {list_id}"
                            ),
                            labels=[label],
                            evidence=[{
                                "kind": source_kind,
                                "source_id": source_id,
                                "source_list_id": list_id,
                                "source_revision": current_revision,
                                "excerpt": evidence,
                            }],
                        )
                        task_id = task.id
                    except Exception as exc:
                        return json_error("task_create_failed", message=f"The task could not be created: {type(exc).__name__}", status=503)
            record = records.write(
                db,
                source_kind=source_kind,
                source_id=source_id,
                source_list_id=list_id,
                source_revision=current_revision,
                source_title=title,
                source_evidence=evidence,
                decision="accepted" if decision == "accept" else "dismissed",
                edited_prompt=prompt,
                task_id=task_id,
                request_id=request_id,
            )
    except Exception:
        return json_error("decision_unavailable", message="The Idea decision could not be saved", status=503)
    return web.json_response(_public(record), status=201)


async def _existing_task_id(label: str) -> str:
    offset = 0
    while True:
        tasks, total = await registry.list_all_tasks(
            provider_filter="native", limit=500, offset=offset
        )
        for task in tasks:
            if label in task.labels:
                return task.id
        offset += len(tasks)
        if not tasks or offset >= total:
            return ""


def register_idea_decision_routes(app: web.Application) -> None:
    app["assistant_idea_records"] = RecommendationRecords()
    app.router.add_get("/api/assistant/ideas/decisions", list_decisions)
    app.router.add_post("/api/assistant/ideas/{idea_id}/decision", decide)
