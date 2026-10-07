"""Trigger manual action settlement, history and review request handlers."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from gideon.interfaces.dashboard.state import ConsoleState


def _accepted_action_origin(request):
    from gideon.security.durable_work import accepted_origin_of_request

    return accepted_origin_of_request(request)


async def _dispatch_store_action(
    trigger: Any,
    payload: dict[str, Any],
    *,
    event: str = "manual.run",
    reentry_note_id: str = "",
    reentry_principal: Any = None,
    reentry_state: Any = None,
    admitted_claim: Any = None,
    accepted_origin: Any = None,
) -> tuple[bool, str]:
    """Run a store trigger's declared action through the action-provider registry.

    The same path `gateway._fire_file_trigger` uses — a manual Run and an autonomous fire share one
    dispatch so their behaviour cannot drift. Returns `(ran, note)`: whether the action actually
    executed, and a short status string for the run result.

    `event` labels the source to the action provider the way `gateway._fire_store_trigger` does
    (`file.changed`, `trigger.chained`): a manual Run keeps the default `manual.run`, a pull-on-view
    refresh passes `view.rendered`. It is a label only — the dispatch is the ONE store-action path,
    not a per-caller fork.

    🔴 BOTH ACTION SHAPES, because a real store holds both (#395). This read the FLAT
    `workflow["provider"]` only, and every trigger the API/CLI/app-reconciler/digest writes nests
    its action under `workflow["inline"]` — so `provider_name` was None for essentially every stored
    row and the Run button was a silent no-op on all of them. The docstring above claimed this path
    "cannot drift" from the autonomous fire while `gateway._fire_store_trigger` unwrapped `inline`
    and this one did not. `schedule_view._inline_action` and `screen.requested_capabilities` both
    document the same two-shape contract; this now matches the idiom all three use.

    Provider AND config come from the SAME resolved dict. Taking the provider from `inline` and the
    config from the outer dict would run the right action with an empty config — a worse failure
    than the no-op, because it looks like it worked.

    `ran` is returned rather than folded into the note because the caller answers HTTP `ok` with it:
    a run that resolved no provider is not a success, and reporting `ok: true` for it is what let
    this bug hide behind a 200 for a whole release.
    """
    import time
    import uuid

    from gideon.integrations.action_providers import ActionContext, get_action_provider
    from gideon.integrations.action_providers.registry import (
        _ensure_default_providers_registered,
    )
    from gideon.interfaces.dashboard.handlers.triggers import (
        _trigger_store,
    )

    trigger_id = str(getattr(trigger, "id", "") or "")
    workflow = trigger.workflow or {}
    inline = (
        workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    )
    action = inline or workflow
    provider_name = str(action.get("provider") or "")
    if not provider_name:
        return await _record_manual_refusal(trigger, "no action provider configured")
    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    if provider is None:
        return await _record_manual_refusal(
            trigger, f"unknown action provider {provider_name!r}"
        )
    # recorded NOTHING — no `ExecutionJournal` row, no `last_run_ts` stamp. So the action ran while
    # `_record_manual_run` reuses the SAME `ExecutionJournal` ledger and the SAME
    trigger_id = str(getattr(trigger, "id", "") or "")
    ctx = ActionContext(
        event=event,
        trigger_id=trigger_id,
        context=trigger_id if provider_name == "run-workflow" else "",
        payload=payload,
        accepted_origin=accepted_origin,
    )
    started = time.time()
    holder = _manual_owner(trigger_id, active=True, admitted_claim=admitted_claim)
    if not holder:
        return await _record_manual_refusal(
            trigger,
            "another run owns this trigger, or its process ownership is unknown",
            outcome="skipped_overlap",
        )
    attempt_id = f"manual-{uuid.uuid4().hex}"
    from contextlib import AbstractContextManager

    reentry_context: AbstractContextManager
    if reentry_note_id:
        from gideon.interfaces.dashboard.auto_denials import owner_reentry_attempt

        reentry_context = owner_reentry_attempt(
            reentry_state,
            reentry_note_id,
            reentry_principal,
            origin_kind="trigger",
            origin_id=trigger_id,
            attempt_id=attempt_id,
        )
    else:
        from contextlib import nullcontext

        reentry_context = nullcontext(None)
    with reentry_context as reentry:
        if reentry_note_id and reentry is None:
            _manual_owner(trigger_id, active=False, holder=holder)
            return await _record_manual_refusal(
                trigger, "the unanswered call is no longer open for this trigger"
            )
        try:
            from gideon.automation.triggers.secrets import resolve
            from gideon.integrations.action_providers.command_lifecycle import (
                action_timeout,
            )

            config = resolve(action.get("config") or {})
            timeout = action_timeout(config, {"bash": 300}.get(provider_name, 30))
            from gideon.security.guardrails.policy import unattended_dispatch_key
            from gideon.security.net.policy import egress_held_to

            with egress_held_to(unattended_dispatch_key(f"trigger:{trigger_id}")):
                result = await provider.execute(config, ctx, timeout=timeout)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a failed manual run is RECORDED, not raised (#308)
            await _record_manual_run(
                trigger, started=started, exc=exc, run_id=attempt_id
            )
            _manual_owner(trigger_id, active=False, holder=holder)
            return False, f"failed: {type(exc).__name__}: {exc}"
        recorded = await _record_manual_run(
            trigger, started=started, result=result, run_id=attempt_id
        )
    from gideon.engine.trigger_outcomes import status_for_result
    from gideon.integrations.action_providers.services import get_action_services

    status = status_for_result(result)
    if recorded is None:
        if status not in {"launched", "queued", "waiting"}:
            _manual_owner(trigger_id, active=False, holder=holder)
        return False, "the action ran but its completion could not be recorded"
    if (
        status in {"launched", "queued", "waiting"}
        and getattr(result, "completion", None) is not None
    ):
        services = get_action_services()
        if services is None:
            return False, "completion services unavailable"
        pending = _settle_manual_action(
            trigger, result.completion, started, payload, holder=holder
        )
        try:
            import asyncio

            scheduled = services.spawn_background(pending)
            if isinstance(scheduled, asyncio.Future):
                services.state._background_tasks.add(scheduled)
                scheduled.add_done_callback(services.state._background_tasks.discard)
        except BaseException:
            pending.close()
            raise
        return True, status
    from gideon.automation.triggers import parks

    if status not in {"launched", "queued", "waiting"} or parks.parked(result):
        _manual_owner(trigger_id, active=False, holder=holder)
    if parks.parked(result) and payload.get("review_id"):
        parks.associate_review(trigger, str(payload["review_id"]))
    if event == "manual.answer" and payload.get("review_id"):
        await _finish_manual_review(trigger, payload, result, recorded)
    if status not in {"launched", "queued", "waiting", "interrupted", "failure"}:
        from gideon.automation.triggers.routing import routed
        from gideon.automation.triggers.service import retire_after_run

        retire_after_run(
            routed(_trigger_store()),
            trigger,
            status=status,
            from_review=bool(payload.get("review_id")),
            settled_holder=holder,
        )
    if result is not None and not bool(getattr(result, "success", True)):
        note = str(getattr(result, "error", "") or "") or "the action reported failure"
        return False, f"failed: {note}"
    return True, status if status in {"launched", "queued", "waiting"} else "ran"


async def _record_manual_refusal(
    trigger: Any, reason: str, *, outcome: str = "skipped_gate"
) -> tuple[bool, str]:
    import time

    from gideon.integrations.action_providers.base import ActionResult

    await _record_manual_run(
        trigger,
        started=time.time(),
        result=ActionResult(False, error=reason, outcome=outcome),
    )
    return False, f"refused: {reason}"


def _manual_owner(
    trigger_id: str, *, active: bool, holder: str = "", admitted_claim: Any = None
) -> str:
    import os
    import time
    from uuid import uuid4

    from gideon.automation.triggers import claims
    from gideon.automation.triggers.routing import routed
    from gideon.automation.triggers.scheduling import Claim
    from gideon.interfaces.dashboard.handlers.triggers import (
        _trigger_store,
    )

    store = routed(_trigger_store())
    row = store.get(trigger_id)
    if active:
        if row is None:
            return ""
        if row.trigger.run_owner_pid and not claims.read_claims(
            trigger_id, base_dir=store.base_dir
        ):
            return ""  # Legacy ownership must be reconciled before a new effect starts.
        if admitted_claim is not None and admitted_claim.trigger_id == trigger_id:
            holder = claims.bind_owner(
                trigger_id,
                owner_pid=os.getpid(),
                base_dir=store.base_dir,
                expected_holder=admitted_claim.holder,
            )
        else:
            claim = Claim(trigger_id, f"manual:{uuid4().hex}", time.time())
            if not claims.acquire_claim(
                claim,
                owner_pid=os.getpid(),
                overlap=row.trigger.overlap,
                base_dir=store.base_dir,
            ):
                return ""
            holder = claim.holder
        if not holder:
            return ""
    elif not claims.release_claim(
        trigger_id, base_dir=store.base_dir, holder=holder, owner_pid=os.getpid()
    ):
        return ""
    if row is None:
        return holder
    if active or row.trigger.run_owner_pid == os.getpid():
        row.trigger.run_owner_pid = (
            os.getpid()
            if active
            else next(
                (
                    claim.owner_pid
                    for claim in claims.read_claims(trigger_id, base_dir=store.base_dir)
                ),
                0,
            )
        )
        try:
            store.upsert(row.trigger)
        except BaseException:
            if active:
                claims.release_claim(
                    trigger_id,
                    base_dir=store.base_dir,
                    holder=holder,
                    owner_pid=os.getpid(),
                )
            raise
    return holder


async def _settle_manual_action(
    trigger: Any,
    completion: Any,
    started: float,
    payload: dict[str, Any],
    *,
    holder: str,
) -> None:
    import asyncio

    from gideon.automation.triggers.routing import routed
    from gideon.automation.triggers.service import retire_after_run
    from gideon.engine.trigger_outcomes import status_for_result
    from gideon.integrations.action_providers.base import ActionResult
    from gideon.interfaces.dashboard.handlers.triggers import (
        _trigger_store,
    )

    try:
        try:
            from gideon.security.guardrails.policy import unattended_dispatch_key
            from gideon.security.net.policy import egress_held_to

            with egress_held_to(unattended_dispatch_key(f"trigger:{trigger.id}")):
                result = await completion()
        except asyncio.CancelledError:
            result = ActionResult(
                False,
                outcome="interrupted",
                error="completion observer stopped during shutdown",
            )
        except Exception as error:
            result = ActionResult(False, error=f"{type(error).__name__}: {error}")
        record = await _record_manual_run(trigger, started=started, result=result)
        status = status_for_result(result)
        await _finish_manual_review(trigger, payload, result, record)
        if record is not None and status in {
            "success",
            "ran_late",
            "degraded",
            "skipped_noop",
        }:
            retire_after_run(
                routed(_trigger_store()),
                trigger,
                status=status,
                from_review=bool(payload.get("review_id")),
                settled_holder=holder,
            )
    finally:
        _manual_owner(trigger.id, active=False, holder=holder)


async def _finish_manual_review(
    trigger: Any, payload: dict[str, Any], result: Any, record: dict[str, Any] | None
) -> None:
    from gideon.automation.triggers import parks
    from gideon.automation.triggers.review import (
        TriggerReviewStore,
        record_review_outcome,
    )
    from gideon.engine.trigger_outcomes import status_for_result
    from gideon.interfaces.dashboard.handlers.triggers import (
        _trigger_store,
    )

    review_id = str(payload.get("review_id") or "")
    reviews = TriggerReviewStore(_trigger_store().base_dir)
    card = reviews.get(review_id) if review_id else None
    if card is None:
        return
    status = status_for_result(result)
    if status == "waiting":
        parks.associate_review(trigger, review_id)
        return
    if record is None:
        reviews.release_run(review_id, error="completion could not be recorded")
    elif status in {"success", "ran_late", "degraded", "skipped_noop"}:
        outcome = (
            "interrupted_retried" if card.get("reason") == "interrupted" else "ran_late"
        )
        if reviews.resolve(
            review_id, decision="run_now", outcome=outcome, allow_running=True
        ):
            await record_review_outcome(
                card, outcome, action_record=record, base_dir=reviews.base_dir
            )
    else:
        reviews.release_run(
            review_id, error=str(getattr(result, "error", "") or status)
        )


async def _record_manual_run(
    trigger: Any,
    *,
    started: float,
    result: Any = None,
    exc: BaseException | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Append a MANUAL run record and advance the trigger's last-run stamp (#308).

    Reuses the SAME ledger the autonomous fire path appends to — `ExecutionJournal`, keyed by the
    trigger id (via this module's `_runs_store()`) — and the SAME
    `last_success_at`/`last_failure_at` stamp `gateway._record_fire_outcome` writes, so a Run button
    and an autonomous tick leave the same evidence that a run happened. This is not a parallel
    recorder: it writes the identical `ExecutionRecord` shape to the identical store, and stamps the
    identical trigger fields. The read surfaces (`/history`, `_last_run_ts`, the completion watcher)
    already work — they were simply reading a store nothing wrote to on this path.

    Tagged `trigger="manual"`, not the autonomous exit type, for two behaviours the run store
    already depends on: `ExecutionJournal.count_since` excludes `manual` rows from the hourly cap (a
    person clicking Run is not the machine running away), and `autopause.consecutive_failures_from`
    treats a `manual` exit as transparent — so testing a broken automation by hand can neither
    autopause it nor reset a real failure streak.

    🔴 `run_count` is deliberately NOT incremented and the autopause engine is deliberately NOT run
    — this records the run HISTORY the manual path was missing, never the fire ALLOWANCE it
    correctly skips. `Trigger.run_count` is the `max_fires` fire-budget meter
    (`service._budget_remaining` reads it, written only at the autonomous fire-GRANT in
    `service.tick`), and `tools.MANUAL_NEVER_BYPASSES` pins `budget` among the gates a manual fire
    never spends — the same reason `_run_event` skips `record_fire` and `count_since` excludes
    manual rows. Spending the budget from a Run button would let a user lock themselves out of their
    own automation by testing it. Likewise a manual run must not drive `state`/`health`/`enabled`: a
    hand-run of a healthy trigger that fails once is not the machine deciding to autopause itself.

    Never raises: a bookkeeping failure must not turn a completed manual run into a crashed request,
    the same contract `_record_fire_outcome` holds. Losing a run record is recoverable; losing the
    response is not.
    """
    from gideon.interfaces.dashboard.handlers.triggers import (
        _runs_store,
        _trigger_store,
        logger,
    )

    try:
        import time
        from datetime import datetime, timezone

        from gideon.automation.schedule_history import ExecutionRecord

        trigger_id = str(getattr(trigger, "id", "") or "")
        if not trigger_id:
            return None
        finished = time.time()

        from gideon.engine.trigger_outcomes import status_for_result

        status = status_for_result(result, exc)
        if exc is not None:
            error = f"{type(exc).__name__}: {exc}"
        elif status in {
            "failure",
            "interrupted",
            "refused",
            "skipped_gate",
            "skipped_overlap",
        }:
            error = (
                str(getattr(result, "error", "") or "") or "the action reported failure"
            )
        else:
            error = ""

        from gideon.automation.schedule_history import action_summary

        summary = action_summary(status, result, error)
        trace = str(getattr(result, "stdout", "") or "") if result is not None else ""

        run_id = run_id or f"manual-{int(finished * 1000)}"
        record = ExecutionRecord(
            run_id=run_id,
            job_id=trigger_id,
            trigger="manual",
            started_at=started,
            finished_at=finished,
            duration_ms=int(max(0.0, finished - started) * 1000),
            status=status,
            summary=summary,
            trace=trace,
            error=error,
        )
        await _runs_store().append(record)

        store = _trigger_store()
        row = store.get(trigger_id)
        if row is None:
            return record.to_dict()
        live = row.trigger
        if status not in {"refused", "skipped_gate", "skipped_overlap"}:
            live.last_run_id = run_id
        stamp = datetime.now(timezone.utc).isoformat()
        if status == "failure":
            live.last_failure_at = stamp
            live.last_error_summary = (error or "manual run failed")[:200]
        elif status in {"success", "ran_late", "degraded", "skipped_noop"}:
            live.last_success_at = stamp
        store.upsert(live)
        from gideon.automation.triggers import parks

        parks.settle(trigger, result)
        return record.to_dict()
    except (
        Exception
    ):  # noqa: BLE001 - see the docstring: recording must never fail the run
        logger.debug("could not record the manual run for %s", trigger, exc_info=True)
        return None


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
