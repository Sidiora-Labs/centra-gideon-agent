"""Authenticated decisions for knowledge-backed Idea-list recommendations."""

from __future__ import annotations

import asyncio
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


def _pending_public(record: dict) -> dict:
    return {
        "source_kind": record["source_kind"],
        "source_id": record["source_id"],
        "source_list_id": record["source_list_id"],
        "source_revision": record["source_revision"],
        "source_title": record["source_title"],
        "source_evidence": record["source_evidence"],
        "decision": "accepted",
        "edited_prompt": record["edited_prompt"],
        "task_id": "",
        "request_id": record["request_id"],
        "created_at": record["created_at"],
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
    async with rows.exclusive():
        with rows.transaction() as db:
            records = rows.list(db)
            pending = rows.list_pending(db)
    return web.json_response({
        "items": [_public(row) for row in records],
        "pending": [_pending_public(row) for row in pending],
    })


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
        async with records.exclusive():
            with records.transaction() as db:
                prior_request = records.by_request(db, request_id)
                if prior_request is not None:
                    if not _same_intent(
                        prior_request, source_kind, source_id, list_id, revision,
                        decision, prompt,
                    ):
                        return json_error("conflict", message="This request ID already records a different decision", status=409)
                    return web.json_response(_public(prior_request))

                existing = records.get(db, source_kind, list_id, source_id)
                if existing is not None:
                    wanted = "accepted" if decision == "accept" else "dismissed"
                    if (
                        existing["decision"] == wanted
                        and existing["edited_prompt"] == prompt
                        and existing["source_revision"] == revision
                    ):
                        return web.json_response(_public(existing))
                    return json_error("conflict", message="This Idea already has a different recorded decision", status=409)

                pending = records.pending(db, source_kind, list_id, source_id)
                if pending is not None and not _same_intent(
                    pending, source_kind, source_id, list_id, revision,
                    decision, prompt,
                ):
                    return json_error("decision_pending", message="A different decision for this Idea is awaiting recovery", status=409)

                if pending is None:
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
                    if decision == "dismiss":
                        record = records.write(
                            db, source_kind=source_kind, source_id=source_id,
                            source_list_id=list_id, source_revision=current_revision,
                            source_title=title, source_evidence=evidence,
                            decision="dismissed", edited_prompt="", task_id="",
                            request_id=request_id,
                        )
                        return web.json_response(_public(record), status=201)
                    pending = records.stage(
                        db, source_kind=source_kind, source_id=source_id,
                        source_list_id=list_id, source_revision=current_revision,
                        source_title=title, source_evidence=evidence,
                        decision="accepted", edited_prompt=prompt,
                        request_id=request_id,
                    )

            # The intent is durable before any native task side effect. The file lock
            # serializes recovery and creation across app instances and processes.
            label = _intent_label(pending)
            expected_evidence = _task_evidence(pending)
            try:
                task_id, conflict = await _existing_task_id(label, prompt, expected_evidence)
                if conflict:
                    return json_error("decision_pending", message="A native task with this Idea marker has different saved intent", status=409)
                if not task_id:
                    try:
                        detail, _ = _current_idea(request, list_id, source_id)
                        current_revision = str(detail.get("hash") or "")
                    except (LookupError, ValueError, KeyError):
                        current_revision = ""
                    if current_revision != pending["source_revision"]:
                        with records.transaction() as db:
                            current_pending = records.pending(db, source_kind, list_id, source_id)
                            if current_pending is not None and _same_intent(
                                current_pending, source_kind, source_id, list_id,
                                pending["source_revision"], "accept", prompt,
                                request_id=pending["request_id"],
                            ):
                                records.clear_pending(db, source_kind, list_id, source_id)
                        return json_error("conflict", message="The Idea source changed before a native task was created. Review the current evidence and start a new decision.", status=409, current_revision=current_revision)
                    creation = asyncio.create_task(registry.create_task(
                        provider_name="native",
                        title=prompt,
                        description=(
                            f"Accepted Idea: {pending['source_title']}\n\n{prompt}\n\n"
                            f"Source evidence: {pending['source_evidence']}\n"
                            f"Knowledge record: #/knowledge/item/{source_id}\n"
                            f"Idea list: {list_id}"
                        ),
                        labels=[label],
                        evidence=[expected_evidence],
                    ))
                    try:
                        task = await asyncio.shield(creation)
                    except asyncio.CancelledError:
                        while not creation.done():
                            try:
                                await asyncio.shield(creation)
                            except asyncio.CancelledError:
                                continue
                        try:
                            creation.result()
                        except Exception:
                            pass
                        raise
                    task_id = task.id
            except Exception as exc:
                return json_error("task_create_failed", message=f"The task could not be created: {type(exc).__name__}", status=503)

            with records.transaction() as db:
                current_pending = records.pending(db, source_kind, list_id, source_id)
                if current_pending is None or not _same_intent(
                    current_pending, source_kind, source_id, list_id,
                    pending["source_revision"], "accept", prompt,
                    request_id=pending["request_id"],
                ):
                    return json_error("decision_pending", message="The pending Idea decision changed during recovery", status=409)
                record = records.write(
                    db, source_kind=source_kind, source_id=source_id,
                    source_list_id=list_id, source_revision=pending["source_revision"],
                    source_title=pending["source_title"],
                    source_evidence=pending["source_evidence"],
                    decision="accepted", edited_prompt=prompt, task_id=task_id,
                    request_id=request_id,
                )
                records.clear_pending(db, source_kind, list_id, source_id)
    except Exception:
        return json_error("decision_unavailable", message="The Idea decision could not be saved", status=503)
    return web.json_response(_public(record), status=201)


def _same_intent(record, source_kind, source_id, list_id, revision, decision, prompt, request_id=None):
    return (
        record["source_kind"] == source_kind
        and record["source_id"] == source_id
        and record["source_list_id"] == list_id
        and record["source_revision"] == revision
        and record["decision"] == ("accepted" if decision == "accept" else "dismissed")
        and record["edited_prompt"] == prompt
        and (request_id is None or record["request_id"] == request_id)
    )


def _intent_label(record):
    return "idea-" + hashlib.sha256(
        f"{record['source_kind']}\0{record['source_list_id']}\0{record['source_id']}".encode("utf-8")
    ).hexdigest()[:32]


def _task_evidence(record):
    return {
        "kind": record["source_kind"],
        "source_id": record["source_id"],
        "source_list_id": record["source_list_id"],
        "source_revision": record["source_revision"],
        "excerpt": record["source_evidence"],
        "edited_prompt": record["edited_prompt"],
        "request_id": record["request_id"],
    }


async def _existing_task_id(label: str, prompt: str, expected_evidence: dict) -> tuple[str, bool]:
    offset = 0
    while True:
        tasks, total = await registry.list_all_tasks(
            provider_filter="native", limit=500, offset=offset
        )
        for task in tasks:
            if label in task.labels:
                if task.title == prompt and expected_evidence in task.evidence:
                    return task.id, False
                return "", True
        offset += len(tasks)
        if not tasks or offset >= total:
            return "", False


def register_idea_decision_routes(app: web.Application) -> None:
    app["assistant_idea_records"] = RecommendationRecords()
    app.router.add_get("/api/assistant/ideas/decisions", list_decisions)
    app.router.add_post("/api/assistant/ideas/{idea_id}/decision", decide)
