"""Trigger manual action settlement, history and review request handlers."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from gideon.interfaces.dashboard.state import ConsoleState



def _action_dispatcher():
    from gideon.automation.triggers.action_dispatch import TriggerDispatcher
    from gideon.interfaces.dashboard.handlers import triggers

    return TriggerDispatcher(triggers._trigger_store, triggers._runs_store, triggers.logger)

def _accepted_action_origin(request):
    from gideon.security.durable_work import accepted_origin_of_request

    return accepted_origin_of_request(request)


async def _dispatch_store_action(trigger: Any, payload: dict[str, Any], *, event: str='manual.run', reentry_note_id: str='', reentry_principal: Any=None, reentry_state: Any=None, admitted_claim: Any=None, accepted_origin: Any=None) -> tuple[bool, str]:
    return await _action_dispatcher()._dispatch_store_action(trigger, payload, event=event, reentry_note_id=reentry_note_id, reentry_principal=reentry_principal, reentry_state=reentry_state, admitted_claim=admitted_claim, accepted_origin=accepted_origin)


async def _record_manual_refusal(trigger: Any, reason: str, *, outcome: str='skipped_gate') -> tuple[bool, str]:
    return await _action_dispatcher()._record_manual_refusal(trigger, reason, outcome=outcome)


def _manual_owner(trigger_id: str, *, active: bool, holder: str='', admitted_claim: Any=None) -> str:
    return _action_dispatcher()._manual_owner(trigger_id, active=active, holder=holder, admitted_claim=admitted_claim)


async def _settle_manual_action(trigger: Any, completion: Any, started: float, payload: dict[str, Any], *, holder: str) -> None:
    return await _action_dispatcher()._settle_manual_action(trigger, completion, started, payload, holder=holder)


async def _finish_manual_review(trigger: Any, payload: dict[str, Any], result: Any, record: dict[str, Any] | None) -> None:
    return await _action_dispatcher()._finish_manual_review(trigger, payload, result, record)


async def _record_manual_run(trigger: Any, *, started: float, result: Any=None, exc: BaseException | None=None, run_id: str | None=None) -> dict[str, Any] | None:
    return await _action_dispatcher()._record_manual_run(trigger, started=started, result=result, exc=exc, run_id=run_id)


async def api_trigger_view_render(request: web.Request) -> web.Response:
    """POST /api/triggers/view/render — the `view` kind's production render caller (WF2AUT-6).

    🔴 THE WIRING THIS CLOSES. `pull_on_view` ships a complete `view`-kind runtime — TTL decide,
    freshness sidecar, render fan-out — whose ONLY caller was its own tests, so `surface_binding`
    was set by authors and read by nothing: a `view` trigger could never actually fire. A real
    render surface (an artifact opening, a dashboard tile mounting) POSTs `{surface}` here as it
    renders; every bound `view` trigger past its TTL refreshes, the rest serve cache.

    It is NOT a poll. §3/R10: a `view` trigger must cost nothing when nobody is looking, so the
    runtime is a function a RENDER calls — a background loop would reintroduce the 1440-run-dirs-a-
    day cost the kind exists to avoid. The `pull_on_view` import is function-local for exactly that
    reason: the gateway module must never import it as a loop (the `test_triggers_chain` runtime map
    and `test_NO_background_loop_polls_this_kind` guard depend on it).

    FIRE-AND-FORGET. A synchronous HTTP render must never block on an LLM turn, so each refresh is
    scheduled on the event loop and the decision (what refreshed, what served cache) returns
    immediately — the same background-task idiom the webhook-agent and MCP-probe handlers use.

    A surface with no bound `view` triggers is a 200 with empty lists, not an error: most renders in
    the product bind no trigger, and a 4xx there would make every artifact-open log a failure.
    """
    import time as _time

    from gideon.automation.triggers import pull_on_view as _view
    from gideon.interfaces.dashboard.handlers.triggers import (
        _trigger_store,
        asyncio,
        read_json_body,
        web,
    )

    state: ConsoleState = request.app["state"]
    try:
        body = await read_json_body(request)
    except Exception:
        body = {}
    surface = (
        str((body or {}).get("surface", "") or "").strip()
        if isinstance(body, dict)
        else ""
    )
    if not surface:
        return web.json_response({"refreshed": [], "served_cache": []})

    store = _trigger_store()
    payloads, cached = _view.renders(store, surface=surface, now=_time.time())

    refreshed: list[str] = []
    for payload in payloads:
        row = store.get(str(payload.get("trigger_id") or ""))
        if row is None:
            continue
        task = asyncio.create_task(
            _dispatch_store_action(row.trigger, payload, event="view.rendered")
        )
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        refreshed.append(row.trigger.id)

    return web.json_response({"refreshed": refreshed, "served_cache": cached})


async def _run_event(raw: str, request: web.Request) -> web.Response:
    """Fire an event trigger through the same owner-sealed firepath as other triggers."""
    from gideon.assurance.validation import sanitize_string
    from gideon.automation.event_triggers import EventOccurrence, _fence_fragment
    from gideon.automation.triggers.firepath import FireContext, evaluate
    from gideon.automation.triggers.grants import is_granted, required_provider
    from gideon.automation.triggers.screen import requested_capabilities
    from gideon.interfaces.dashboard.handlers.triggers import (
        _event_store,
        _redact,
        read_json_body,
        web,
    )

    event_store = _event_store()
    trigger = next((item for item in event_store.load() if item.id == raw), None)
    if trigger is None:
        return web.json_response({"error": "not found"}, status=404)
    canonical = getattr(trigger, "_trigger", None)
    if canonical is None:
        return web.json_response(
            {"error": "event trigger is not in the canonical store"}, status=409
        )
    body = await read_json_body(request)
    body = body if isinstance(body, dict) else {}
    key = sanitize_string(str(body.get("key") or "manual"))[:500]
    value = sanitize_string(str(body.get("value") or "manual fire"))[:10000]
    raw_meta = body.get("meta")
    meta = (
        {str(k): sanitize_string(str(v))[:500] for k, v in raw_meta.items()}
        if isinstance(raw_meta, dict)
        else None
    )
    event_type = str(body.get("event_type") or "MemoryUpdate")
    occurrence = EventOccurrence(trigger.source, event_type, key, value, meta)
    if body.get("dry_run") is True:
        if "_auto_denied_retry_note_id" in body:
            return web.json_response(
                {"error": "an unanswered-call note cannot be used for a dry run"},
                status=400,
            )
        matches = occurrence.accepts(trigger)
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "dry_run": True,
                    "would_fire": matches,
                    "reason": (
                        "matches this trigger"
                        if matches
                        else "event does not match or trigger is paused"
                    ),
                    "action": trigger.action_provider,
                    "trigger_id": f"event:{trigger.id}",
                },
            }
        )
    if getattr(trigger, "_issues", []):
        return web.json_response(
            {"error": "event trigger has parse errors and cannot run"}, status=400
        )
    if required_provider(canonical) and not is_granted(canonical):
        return web.json_response(
            {
                "error": "owner review and an exact action grant are required before this trigger can run"
            },
            status=409,
        )
    if not occurrence.accepts(trigger):
        return web.json_response(
            {
                "ok": False,
                "result": {
                    "ran": False,
                    "reason": "event does not match or trigger is paused",
                },
            }
        )
    context = FireContext(
        trigger_id=canonical.id,
        trigger=canonical,
        payload_text=value,
        gates={},
        capabilities=canonical.capabilities,
        requested=requested_capabilities(canonical),
        holder=f"event:manual:{canonical.id}",
        now=__import__("time").time(),
    )
    decision = await evaluate(context)
    if not decision.allowed:
        return web.json_response(
            {"ok": False, "result": {"ran": False, "reason": _redact(decision.reason)}}
        )
    retry_note_id = ""
    retry_principal = None
    retry_state = None
    if "_auto_denied_retry_note_id" in body:
        retry_note_id = body.get("_auto_denied_retry_note_id", "")
        if (
            not isinstance(retry_note_id, str)
            or not retry_note_id
            or retry_note_id != retry_note_id.strip()
        ):
            return web.json_response(
                {"error": "a valid unanswered-call note is required"}, status=400
            )
        from gideon.security.approval_answer import OWNER, of_request

        retry_principal = of_request(request)
        if retry_principal.kind != OWNER or not retry_principal.name:
            return web.json_response({"error": "owner required"}, status=403)
        retry_state = request.app.get("state")
        if retry_state is None:
            return web.json_response(
                {"error": "trigger runtime unavailable"}, status=503
            )
        from gideon.interfaces.dashboard.auto_denials import unanswered_note

        note = unanswered_note(retry_state, retry_note_id)
        refs = note.refs if note is not None and isinstance(note.refs, dict) else {}
        if refs.get("trigger") != canonical.id:
            return web.json_response(
                {"error": "the unanswered call is not open for this trigger"},
                status=409,
            )
    from gideon.automation.triggers.claims import acquire_claim, release_claim
    from gideon.interfaces.dashboard.handlers.triggers import _dispatch_store_action

    if not acquire_claim(
        decision.claim, overlap=canonical.overlap, base_dir=event_store._store.base_dir
    ):
        await _record_manual_refusal(
            canonical, "another run acquired the event claim", outcome="skipped_overlap"
        )
        return web.json_response(
            {"error": "already running", "running": True}, status=409
        )
    payload = {
        "trigger_id": canonical.id,
        "source": occurrence.source,
        "event_type": occurrence.event_type,
        "key": occurrence.key,
        "value": _fence_fragment(
            occurrence.value,
            2000,
            source=f"trigger:{canonical.id}:{occurrence.source}.{occurrence.event_type}",
            source_type=f"event:{occurrence.source}.{occurrence.event_type}",
            source_id=occurrence.key,
        ),
        "meta": occurrence.meta or {},
        "test": bool(body.get("test")),
    }
    try:
        ran, reason = await _dispatch_store_action(
            canonical,
            payload,
            event=f"{occurrence.source}.{occurrence.event_type}",
            reentry_note_id=retry_note_id,
            reentry_principal=retry_principal,
            reentry_state=retry_state,
            admitted_claim=decision.claim,
        )
    finally:
        release_claim(
            canonical.id,
            base_dir=event_store._store.base_dir,
            holder=decision.claim.holder,
        )
    event_store.record_outcome(
        canonical.id,
        status="success" if ran else "failure",
        error="" if ran else reason,
    )
    return web.json_response(
        {"ok": ran, "result": {"ran": ran, "reason": _redact(reason)}}
    )


async def api_trigger_test(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/test — execute a lifecycle or event trigger's action once."""
    from gideon.assurance.validation import sanitize_string
    from gideon.engine.hooks import run_script_hook
    from gideon.interfaces.dashboard.handlers.triggers import (
        _EVENT,
        _LIFECYCLE,
        _hook_store,
        _redact,
        _split_id,
        read_json_body,
        web,
    )

    state: ConsoleState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind == _EVENT:
        return await _run_event(raw, request)
    if kind != _LIFECYCLE:
        return web.json_response(
            {
                "error": "schedule triggers run their action; use /run?dry_run=1 to preview"
            },
            status=400,
        )
    hook = _hook_store(state).get(raw)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await read_json_body(request)
    except Exception:
        body = {}
    context = sanitize_string(body.get("context", "test"))[:10000]
    result = await run_script_hook(hook, context, test=True)
    return web.json_response(
        {
            "ok": True,
            "result": {
                "stdout": _redact(result.stdout),
                "stderr": _redact(result.stderr),
                "exit_code": result.exit_code,
                "error": _redact(result.error),
                "duration_ms": result.duration_ms,
            },
        }
    )


async def api_trigger_to_chat(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/to-chat — open a schedule trigger as a chat session."""
    from gideon.interfaces.dashboard.handlers.triggers import (
        _SCHEDULE,
        _job_shim_for,
        _last_result_for,
        _split_id,
        asyncio,
        web,
    )
    from gideon.interfaces.dashboard.schedule_inject import (
        inject_schedule_result_to_session,
    )

    state: ConsoleState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _SCHEDULE:
        return web.json_response(
            {"error": "only schedule triggers open as a chat"}, status=400
        )
    # row plus `ExecutionJournal` serves this completely, and the run store survives the cutover
    job = _job_shim_for(state, raw)

    history = None
    if state.conversation_log is not None:
        try:
            history = await asyncio.to_thread(
                state.conversation_log.read_messages, f"cron:{raw}"
            )
        except Exception:
            history = None

    if job is None:
        if not history:
            return web.json_response({"error": "not found"}, status=404)
        from gideon.automation.schedule import ScheduleJob

        job = ScheduleJob(id=raw, name=f"cron-{raw}")

    last_result = await _last_result_for(state, raw)
    session = inject_schedule_result_to_session(
        state, job, last_result, history=history
    )
    return web.json_response({"ok": True, "session": session.key})


def _redact_run(run: dict[str, Any], *, job_name: str | None = None) -> dict[str, Any]:
    from gideon.interfaces.dashboard.handlers.triggers import (
        _redact,
        redact_values_for_display,
    )

    out = redact_values_for_display(dict(run))
    if job_name is not None:
        out["job_name"] = _redact(job_name)
    return out


async def api_trigger_history(request: web.Request) -> web.Response:
    """GET /api/triggers/{id}/history — run records; other kinds answer `supported: false`.

    No longer touches `state` (S105): the run records come straight from `ExecutionJournal`, so this
    handler is fully decoupled from `ScheduleService`.
    """
    from gideon.interfaces.dashboard.handlers.triggers import (
        _EVENT,
        _LIFECYCLE,
        _event_store,
        _runs_store,
        _split_id,
        web,
    )

    kind, raw = _split_id(request.match_info["id"])
    if kind == _EVENT:
        trigger = next((t for t in _event_store().load() if t.id == raw), None)
        if trigger is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {
                "runs": [],
                "total": 0,
                "supported": False,
                "reason": "event triggers record a fire count, not per-run records",
                "fire_count": trigger.fire_count,
                "last_fired_at": trigger.last_fired_at,
            }
        )
    if kind == _LIFECYCLE:
        return web.json_response(
            {
                "runs": [],
                "total": 0,
                "supported": False,
                "reason": "lifecycle triggers run inline with the agent loop and keep no run store",
            }
        )
    try:
        limit = max(1, min(int(request.query.get("limit", "10")), 100))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"error": "invalid limit/offset"}, status=400)
    try:
        runs, total = await _runs_store().list_for_job(raw, offset, limit)
    except ValueError:
        return web.json_response({"error": "invalid trigger id"}, status=400)
    return web.json_response({"runs": [_redact_run(r) for r in runs], "total": total})


async def api_trigger_history_detail(request: web.Request) -> web.Response:
    """GET /api/triggers/{id}/history/{run_id} — one full run record.

    Reads the run store directly (S105), so this handler no longer touches `state` at all — the
    clearest possible evidence that the run-record surface is fully decoupled from
    `ScheduleService`.
    """
    from gideon.interfaces.dashboard.handlers.triggers import (
        _SCHEDULE,
        _STORE,
        _runs_store,
        _split_id,
        web,
    )

    kind, raw = _split_id(request.match_info["id"])
    if kind not in (_SCHEDULE, _STORE):
        return web.json_response({"error": "not found"}, status=404)
    run_id = request.match_info["run_id"]
    try:
        run = await _runs_store().get_run(raw, run_id)
    except ValueError:
        return web.json_response({"error": "invalid trigger id"}, status=400)
    if run is None:
        return web.json_response({"error": "run not found"}, status=404)
    return web.json_response({"run": _redact_run(run)})


async def api_triggers_week(request: web.Request) -> web.Response:
    """GET /api/triggers/week — the week-grid projection, from `?start=` (AUTO-A1 — S70).

    Read-only, and NO store changes: every occurrence is computed from the recurrence the trigger
    already carries. Quiet windows come back as ANNOTATIONS on each slot rather than as filters — a
    grid that hid suppressed fires would show a schedule the user does not have, and explaining why
    a trigger is not firing when they expect it to is the whole point of the view.

    The duty gate is deliberately NOT evaluated. It is async, provider-backed, and answers about a
    moment in time; asking a calendar app about next Thursday 200 times would be both slow and
    meaningless.
    """
    from datetime import datetime, timedelta

    from gideon.automation.schedule import get_local_tz
    from gideon.interfaces.dashboard.handlers.triggers import (
        _SCHEDULE,
        _project_one,
        _trigger_store,
        _week_triggers,
        web,
    )

    tz_name, local_tz = get_local_tz()

    def local_bound(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=local_tz)
            if value.tzinfo is None
            else value.astimezone(local_tz)
        )

    state: ConsoleState = request.app["state"]
    raw_start = (request.query.get("start") or "").strip()
    try:
        start = (
            local_bound(datetime.fromisoformat(raw_start))
            if raw_start
            else datetime.now(local_tz)
        )
    except ValueError:
        return web.json_response({"error": "start must be an ISO date"}, status=400)
    try:
        days = max(1, min(int(request.query.get("days", "7")), 31))
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)
    until = None
    raw_until = (request.query.get("until") or "").strip()
    if raw_until:
        try:
            until = local_bound(datetime.fromisoformat(raw_until))
        except ValueError:
            return web.json_response({"error": "until must be an ISO date"}, status=400)
        if until <= start or until > start + timedelta(days=31):
            return web.json_response(
                {"error": "until must be after start and within 31 days"}, status=400
            )

    occurrences: list[dict[str, Any]] = []
    truncated: list[str] = []
    for trigger in _week_triggers(state):
        rows, cut = _project_one(trigger, start=start, days=days, until=until)
        occurrences.extend(row.to_dict() for row in rows)
        if cut:
            truncated.append(f"{_SCHEDULE}:{trigger.id}")

    return web.json_response(
        {
            "start": start.isoformat(),
            "end": (until or (start + timedelta(days=days))).isoformat(),
            "server_tz": tz_name,
            "occurrences": occurrences,
            "truncated": truncated,
            "unreadable": _trigger_store().unreadable_status(),
        }
    )


async def api_triggers_doctor(request: web.Request) -> web.Response:
    """GET /api/triggers/doctor — structural problems across every trigger (§7 criterion 12).

    Every finding here is invisible at runtime: the trigger looks configured and behaves differently
    than its author intended. An orphaned workflow ref fires and fails forever; a broad watch glob
    fires on everything the user owns; an unknown duty gate fails OPEN, so the automation runs
    unfiltered — the opposite of what its author asked for.
    """
    from gideon.automation.triggers.calendar import diagnose
    from gideon.interfaces.dashboard.handlers.triggers import (
        _EVENT,
        _LIFECYCLE,
        _SCHEDULE,
        _STORE,
        _event_store,
        _hook_store,
        _trigger_store,
        logger,
        web,
    )

    known_workflows: set[str] | None = None
    try:
        from gideon.automation.workflows import service as _wf

        listing = await _wf.list_defs()
        known_workflows = {
            str(d.get("name"))
            for d in (listing.get("defs") or [])
            if isinstance(d, dict)
        }
    except Exception:
        logger.debug("doctor: workflow defs unavailable", exc_info=True)

    rows: list[dict[str, Any]] = []
    store = _trigger_store()
    store_rows = store.load()
    if store_rows:
        for row in store_rows:
            rows.append(
                {
                    "id": (
                        f"{_SCHEDULE}:{row.trigger.id}"
                        if row.trigger.kind == "clock"
                        else f"{_STORE}:{row.trigger.id}"
                    ),
                    "gates": row.trigger.gates or {},
                    "workflow": row.trigger.workflow or {},
                    "spec": dict(row.trigger.spec or {}),
                    "capabilities": dict(row.trigger.capabilities or {}),
                }
            )
    for trigger in _event_store().load():
        rows.append(
            {
                "id": f"{_EVENT}:{trigger.id}",
                "gates": {},
                "workflow": {
                    "provider": trigger.action_provider,
                    "config": trigger.action_config,
                },
                "spec": {"glob": trigger.key_glob or ""},
            }
        )

    state: ConsoleState = request.app["state"]
    for hook in _hook_store(state).list_all():
        rows.append(
            {
                "id": f"{_LIFECYCLE}:{hook.id}",
                "gates": {},
                "workflow": {
                    "provider": hook.provider,
                    "config": hook.provider_config,
                },
                "spec": {},
                "capabilities": {},
            }
        )

    report = diagnose(rows, known_workflows=known_workflows)
    from gideon.automation.triggers.calendar import Finding

    for row in store_rows:
        for issue in row.issues:
            is_error = issue.severity == "error"
            trigger_id = (
                f"{_SCHEDULE}:{row.trigger.id}"
                if row.trigger.kind == "clock"
                else f"{_STORE}:{row.trigger.id}"
            )
            report.findings.append(
                Finding(
                    trigger_id=trigger_id,
                    code="invalid_trigger" if is_error else "trigger_warning",
                    detail=f"{issue.path}: {issue.message}",
                    fix=(
                        "correct this trigger field before enabling it"
                        if is_error
                        else "confirm this is intended, or adjust the trigger"
                    ),
                )
            )
    return web.json_response(report.to_dict())


async def api_trigger_reviews(request: web.Request) -> web.Response:
    from gideon.automation.triggers.review import TriggerReviewStore
    from gideon.interfaces.dashboard.handlers.triggers import (
        _trigger_store,
        web,
    )
    from gideon.security.approval_answer import OWNER, of_request

    if of_request(request).kind != OWNER:
        return web.json_response({"error": "owner required"}, status=403)
    store = _trigger_store()
    return web.json_response(
        {
            "cards": [
                card
                for card in TriggerReviewStore(store.base_dir).list(pending_only=False)
                if card.get("status") in {"pending", "running"}
            ]
        }
    )


async def api_trigger_review(request: web.Request) -> web.Response:
    from gideon.automation.triggers import grants
    from gideon.automation.triggers import tools as trigger_tools
    from gideon.automation.triggers.review import (
        TriggerReviewStore,
        record_review_outcome,
    )
    from gideon.interfaces.dashboard.handlers.triggers import (
        _STORE,
        _redact,
        _runs_store,
        _split_id,
        _trigger_store,
        read_json_body,
        web,
    )
    from gideon.security.approval_answer import OWNER, of_request

    if of_request(request).kind != OWNER:
        return web.json_response({"error": "owner required"}, status=403)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    required = {"trigger_id", "review_id", "decision", "expected_revision"}
    if not isinstance(body, dict) or set(body) != required:
        return web.json_response({"error": "invalid review decision"}, status=400)
    trigger_id = body.get("trigger_id")
    review_id = body.get("review_id")
    decision = body.get("decision")
    expected_revision = body.get("expected_revision")
    if (
        not isinstance(trigger_id, str)
        or not trigger_id
        or not isinstance(review_id, str)
        or not review_id
        or not isinstance(expected_revision, str)
    ):
        return web.json_response(
            {"error": "invalid review decision fields"}, status=400
        )
    if decision not in {"run_now", "dismiss"}:
        return web.json_response(
            {"error": "decision must be run_now or dismiss"}, status=400
        )

    store = _trigger_store()
    reviews = TriggerReviewStore(store.base_dir)
    card = reviews.get(review_id)
    if (
        card is None
        or card.get("status") != "pending"
        or card.get("trigger_id") != trigger_id
        or card.get("action_revision") != expected_revision
        or card.get("frozen_action_fingerprint") != expected_revision
    ):
        return web.json_response(
            {"error": "review card is stale or already resolved"}, status=409
        )

    if decision == "dismiss":
        if not reviews.resolve(review_id, decision="dismiss", outcome="dismissed"):
            return web.json_response(
                {"error": "review card is already being decided"}, status=409
            )
        await record_review_outcome(card, "dismissed", base_dir=store.base_dir)
        return web.json_response({"ok": True, "outcome": "dismissed"})

    if not expected_revision:
        return web.json_response(
            {"error": "review has no valid action revision"}, status=409
        )

    kind, raw = _split_id(trigger_id)
    if kind != _STORE:
        return web.json_response(
            {"error": "review trigger is not a store automation"}, status=400
        )
    row = store.get(raw)
    if row is None:
        return web.json_response(
            {"error": "review trigger no longer exists"}, status=409
        )
    current_revision = grants.action_revision(row.trigger)
    if not current_revision or current_revision != expected_revision:
        return web.json_response(
            {"error": "trigger action or reach changed; review the current automation"},
            status=409,
        )
    if row.errors:
        return web.json_response(
            {
                "error": "trigger action is invalid",
                "details": [e.message for e in row.errors],
            },
            status=409,
        )
    missing = grants.missing(row.trigger)
    if missing:
        return web.json_response(
            {"error": "owner action grant is required", "providers": missing},
            status=409,
        )
    refusal = trigger_tools.manual_refusal()
    if refusal:
        return web.json_response({"error": refusal}, status=409)

    if reviews.begin_run(review_id) is None:
        return web.json_response(
            {"error": "review card is already being decided"}, status=409
        )
    # Re-read after the durable single-use claim so a concurrent edit cannot replace the
    # reviewed action between the first revision check and dispatch.
    row = store.get(raw)
    if (
        row is None
        or grants.action_revision(row.trigger) != expected_revision
        or grants.missing(row.trigger)
        or row.errors
    ):
        reviews.release_run(review_id, error="trigger changed before dispatch")
        return web.json_response(
            {"error": "trigger changed before dispatch"}, status=409
        )

    previous_runs, _total = await _runs_store().list_for_job(raw, 0, 1)
    previous_record = (
        previous_runs[0] if previous_runs and isinstance(previous_runs[0], dict) else {}
    )
    ran, note = await _dispatch_store_action(
        row.trigger,
        {
            "trigger_id": raw,
            "manual": True,
            "review_id": review_id,
            "review_reason": str(card.get("reason") or "missed"),
        },
        event="restart.review",
        accepted_origin=_accepted_action_origin(request),
    )
    action_runs, _total = await _runs_store().list_for_job(raw, 0, 1)
    action_record = (
        action_runs[0] if action_runs and isinstance(action_runs[0], dict) else {}
    )
    if (
        action_record
        and action_record.get("run_id") == previous_record.get("run_id")
        and action_record.get("finished_at") == previous_record.get("finished_at")
    ):
        action_record = {}
    if action_record:
        action_record = (
            await _runs_store().get_run(raw, str(action_record.get("run_id") or ""))
            or action_record
        )
    if ran and action_record.get("status") in {"launched", "queued", "waiting"}:
        return web.json_response(
            {
                "ok": True,
                "outcome": "pending",
                "status": action_record["status"],
                "run_id": action_record.get("run_id", ""),
            },
            status=202,
        )
    if not ran:
        reviews.release_run(review_id, error=note)
        review_run_id = await record_review_outcome(
            card,
            "failed",
            error=note,
            action_record=action_record or None,
            base_dir=store.base_dir,
        )
        response = {"ok": False, "outcome": "failed", "result": _redact(note)}
        if action_record:
            response.update(
                {
                    "run_id": str(action_record.get("run_id") or ""),
                    "status": str(action_record.get("status") or ""),
                    "summary": _redact(
                        str(
                            action_record.get("summary")
                            or action_record.get("error")
                            or ""
                        )
                    ),
                }
            )
        if review_run_id:
            response["run_id"] = review_run_id
        return web.json_response(response, status=500)
    outcome = (
        "interrupted_retried" if card.get("reason") == "interrupted" else "ran_late"
    )
    if not reviews.resolve(
        review_id,
        decision="run_now",
        outcome=outcome,
        allow_running=True,
    ):
        return web.json_response(
            {"error": "review decision could not be committed"}, status=500
        )
    review_run_id = await record_review_outcome(
        card,
        outcome,
        action_record=action_record or None,
        base_dir=store.base_dir,
    )
    state = request.app.get("state")
    if state is not None:
        state.push_refresh("crons")
    response = {"ok": True, "outcome": outcome, "result": _redact(note)}
    if action_record:
        response.update(
            {
                "run_id": str(action_record.get("run_id") or ""),
                "status": str(action_record.get("status") or ""),
                "summary": _redact(
                    str(
                        action_record.get("summary") or action_record.get("error") or ""
                    )
                ),
            }
        )
    if review_run_id:
        response["run_id"] = review_run_id
    return web.json_response(response)


async def api_trigger_history_all(request: web.Request) -> web.Response:
    """GET /api/triggers/history — the run feed across ALL THREE kinds (AUTO crit 4).

    Criterion 4: "a hook, an event trigger, and a cron all show run history in the same
    feed with the same record shape and typed outcomes". This route existed and was
    **schedule-only** — its own docstring said "(schedule runs)" — so the feed a user opens
    to answer "what did my machine do" showed one kind of automation and silently omitted
    the other two.

    `?shape=legacy` keeps the raw `ExecutionRecord` dicts for the cron-history UI, which renders
    `trace`/`summary` fields the typed row does not carry. The default is the UNIFIED shape:
    a caller asking for history without naming a shape wants the honest cross-kind answer,
    and defaulting to legacy would mean the criterion is met only by a flag nobody sets.
    """
    from gideon.automation.triggers import history as H
    from gideon.interfaces.dashboard.handlers.triggers import (
        _EVENT,
        _LIFECYCLE,
        _SCHEDULE,
        _event_store,
        _hook_store,
        _runs_store,
        _split_id,
        _trigger_names,
        logger,
        web,
    )

    state: ConsoleState = request.app["state"]
    try:
        limit = max(1, min(int(request.query.get("limit", "20")), 100))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"error": "invalid limit/offset"}, status=400)
    raw_filter = request.query.get("trigger_id") or None
    kind_filter = ""
    if raw_filter:
        kind_filter, raw_filter = _split_id(raw_filter)
    journal = _runs_store()
    legacy = (request.query.get("shape") or "").lower() == "legacy"
    if legacy:
        runs, total = await journal.list_all(offset, limit, raw_filter)
    else:
        _, total = await journal.list_all(0, 1, raw_filter)
        runs, _ = await journal.list_all(0, max(1, total), raw_filter)
    names = _trigger_names(state)
    enriched = [
        _redact_run(r, job_name=names.get(r.get("job_id", ""), "")) for r in runs
    ]

    if legacy:
        return web.json_response({"runs": enriched, "total": total})

    hooks: list[Any] = []
    events: list[Any] = []
    if not raw_filter or kind_filter == _LIFECYCLE:
        try:
            store = _hook_store(state)
            hooks = [
                h for h in store.list_all() if not raw_filter or h.id == raw_filter
            ]
        except Exception:
            logger.debug("unified history: hook store unavailable", exc_info=True)
    if not raw_filter or kind_filter == _EVENT:
        try:
            events = [
                t for t in _event_store().load() if not raw_filter or t.id == raw_filter
            ]
        except Exception:
            logger.debug("unified history: event store unavailable", exc_info=True)

    records = H.unified_feed(
        schedule_runs=enriched if (not raw_filter or kind_filter == _SCHEDULE) else [],
        hooks=hooks,
        event_triggers=events,
        limit=max(1, total + len(hooks) + len(events)),
    )
    page = records[offset : offset + limit]
    payload = H.feed_response(page, total=len(records))
    payload["schedule_total"] = total
    payload["outcomes"] = H.outcome_counts(page)
    return web.json_response(payload)


def register_trigger_routes(app: web.Application) -> None:
    """Register /api/triggers/* — the unified Trigger surface."""
    from gideon.interfaces.dashboard.handlers.triggers import (
        api_trigger_answer,
        api_trigger_budget,
        api_trigger_create,
        api_trigger_detail,
        api_trigger_fire,
        api_trigger_grant,
        api_trigger_run,
        api_trigger_toggle,
        api_trigger_variables,
        api_triggers,
    )

    app.router.add_get("/api/triggers", api_triggers)
    app.router.add_post("/api/triggers", api_trigger_create)
    app.router.add_get("/api/triggers/variables", api_trigger_variables)
    app.router.add_get("/api/triggers/budget", api_trigger_budget)
    app.router.add_get("/api/triggers/history", api_trigger_history_all)
    app.router.add_get("/api/triggers/week", api_triggers_week)
    app.router.add_get("/api/triggers/doctor", api_triggers_doctor)
    app.router.add_get("/api/triggers/review", api_trigger_reviews)
    app.router.add_post("/api/triggers/review", api_trigger_review)
    app.router.add_post("/api/triggers/view/render", api_trigger_view_render)
    app.router.add_put("/api/triggers/{id}", api_trigger_detail)
    app.router.add_delete("/api/triggers/{id}", api_trigger_detail)
    app.router.add_post("/api/triggers/{id}/toggle", api_trigger_toggle)
    app.router.add_post("/api/triggers/{id}/grant", api_trigger_grant)
    app.router.add_post("/api/triggers/{id}/run", api_trigger_run)
    app.router.add_post("/api/triggers/{id}/answer", api_trigger_answer)
    app.router.add_post("/api/triggers/{id}/fire", api_trigger_fire)
    app.router.add_post("/api/triggers/{id}/test", api_trigger_test)
    app.router.add_post("/api/triggers/{id}/to-chat", api_trigger_to_chat)
    app.router.add_get("/api/triggers/{id}/history", api_trigger_history)
    app.router.add_get(
        "/api/triggers/{id}/history/{run_id}", api_trigger_history_detail
    )
