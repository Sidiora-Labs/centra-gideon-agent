"""The triage digest over HTTP (PROACTIVE-ASSISTANT §5.1, §5.4) — PA-5.

Three endpoints, and the split between them is §5.1's "strictly read-only on view; acting is
explicit" made structural:

``GET  /api/proactive/digest``        the whole card. Reads only.
``POST /api/proactive/digest/reply``  one tap, or one typed channel reply. The only writer.
``POST /api/proactive/install``       §5.4's pack card: install the schedule, or reconcile it.

**The reply route is the ONE new caller of an existing execution seam, not a new seam.** A tap
on "yes" runs through :func:`gideon.cognition.proactive.autoexec.auto_execute` with a synthetic
approve rule standing for the user's click — so the incident kill switch, the action denylist,
``enforce_action``'s SEL row and the NEW-1 budget floor all apply to an attended approval exactly
as they apply to an unattended one. Writing a second dispatch here would have been a sixth
unattended-write seam (AG §1.2) that the chokepoint test would have caught and that nothing
would have gated in the meantime.

**Idempotency is the run's own ledger, not a new store.** Every answered ordinal leaves a
``triage_reply`` row on the digest's run, so a reply that arrives twice — a double tap, a retried
channel delivery, a reply typed after the gateway restarted — finds the first row and acks
instead of acting again (criterion 9). A reply naming a run that is no longer the current digest
is refused with ``digest_expired``: the ordinals in an old digest number a different window, so
best-effort execution there is precisely the wrong-target execution the criterion forbids.

**Nothing here reports an unmeasured value as a zero.** A failed read returns the error and the
card renders it; see :mod:`gideon.cognition.proactive.surface` for the state vocabulary that keeps
"off", "never run", "empty" and "broken" four different answers.

Every failure leaves through :func:`~gideon.http_errors.json_error` — the ONE structured
wire envelope `AGENTS.md` §"Shared conventions" declares. Not a style choice: the flat
``{"error": "<prose>"}`` shape is a RATCHETED, shrinking population
(`tests/test_wire_error_envelope_census.py`), so a new route emitting it would be a new site a
client can only branch on by matching prose. The digest card branches on the codes below.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.http_errors import json_error

logger = logging.getLogger(__name__)

TRIAGE_TRIGGER_ID = "system:triage:digest"
TRIAGE_CREATED_BY = "system"
REPLY_RULE = "reply:you-approved"


def _sel():
    from gideon.interfaces.dashboard import handlers as _h

    return _h.sel()


async def _body(request: web.Request) -> dict:
    try:
        body = await read_json_body(request)
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _config() -> Any:
    from gideon.core.config.loader import AppConfig

    return AppConfig.load()


def _proactive(config: Any) -> Any:
    return getattr(config, "proactive", None)


def _trigger_store() -> Any:
    from gideon.automation.triggers.store import TriggerStore

    return TriggerStore()


def _find_schedule(store: Any) -> Any:
    """The digest schedule, by its deterministic id, or None.

    `store.get` returns a `LoadedTrigger` — the row PLUS whatever was wrong with reading it. The
    entity to write is the `.trigger` inside; upserting the pair would set attributes on the
    wrapper and persist something with no id.
    """
    row = store.get(TRIAGE_TRIGGER_ID)
    return None if row is None else row.trigger


def _schedule_payload(trigger: Any) -> dict[str, Any]:
    spec = getattr(trigger, "spec", None) or {}
    return {
        "id": str(getattr(trigger, "id", "") or ""),
        "name": str(getattr(trigger, "name", "") or ""),
        "cron": str(spec.get("expr", "") or "") if isinstance(spec, dict) else "",
        "enabled": bool(getattr(trigger, "enabled", False)),
        "created_by": str(getattr(trigger, "created_by", "") or ""),
    }


def _install_state() -> dict[str, Any]:
    """Installedness + the drift between the config switch and the schedule's own flag.

    ``drift`` is reported rather than silently repaired on a READ. Criterion 10 wants disabling
    ``triage_enabled`` to retire the schedule, and the reconcile that does it is a POST — so a
    GET that quietly fixed the divergence would hide from the user that two switches had
    disagreed, and would make the read a writer.
    """
    config = _config()
    proactive = _proactive(config)
    enabled = bool(getattr(proactive, "triage_enabled", False))
    trigger = _find_schedule(_trigger_store())
    if trigger is None:
        return {
            "installed": False,
            "enabled": enabled,
            "schedule": None,
            "drift": False,
        }
    payload = _schedule_payload(trigger)
    return {
        "installed": True,
        "enabled": enabled,
        "schedule": payload,
        "drift": payload["enabled"] != enabled,
    }


def _latest_digest() -> tuple[dict | None, dict | None, list[dict]]:
    """The most recent triage run, its node output and its ledger slice.

    Returns ``(None, None, [])`` when no run exists — which the view turns into ``never_run``,
    never into an empty digest.
    """
    from gideon.automation.workflows import journal, service, store
    from gideon.cognition.proactive.surface import TRIAGE_NODE_ID, TRIAGE_WORKFLOW

    runs, _total = store.list_runs(workflow_name=TRIAGE_WORKFLOW, limit=1, offset=0)
    if not runs:
        return None, None, []
    run = runs[0].to_dict()
    run_id = str(run.get("run_id", "") or run.get("id", "") or "")
    result = service.output(run_id, TRIAGE_NODE_ID)
    output: dict | None = None
    if result.get("ok"):
        output = _decode_output(result.get("output"))
    return run, output, journal.ledger(run_id)


def _decode_output(value: Any) -> dict | None:
    """The triage node's output as a dict, whether it was stored as JSON text or as an object.

    The provider returns its summary as `ActionResult.stdout` (a JSON string), and the engine may
    hand it back either already-parsed or verbatim depending on the node's transform. Both are
    accepted; anything else is `None`, which the view reports as `never_run` rather than as a
    digest with every section empty.
    """
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def api_proactive_digest(request: web.Request) -> web.Response:
    """GET /api/proactive/digest — §5.1's card, assembled from the last digest run.

    Off the event loop: this reads the run store, one node's persisted output and a ledger file,
    which is real file work. A read that RAISES becomes ``state: "error"`` with the message, not
    an empty card — "nothing happened yet" is the most confident possible way to say the opposite
    of what is known.
    """
    from gideon.cognition.proactive.surface import build_digest_view

    def read() -> dict:
        state = _install_state()
        if not state["installed"] or not state["enabled"]:
            view = build_digest_view(
                enabled=state["enabled"], installed=state["installed"]
            )
        else:
            run, output, events = _latest_digest()
            view = build_digest_view(
                enabled=True, installed=True, run=run, output=output, events=events
            )
        view["schedule"] = state["schedule"]
        view["schedule_drift"] = state["drift"]
        view["notice"] = _digest_notice(
            title=str(view.get("title") or ""), body=str(view.get("body") or "")
        )
        notice = view["notice"]
        view["quiet_hours"] = {
            "known": notice["known"],
            "mute_all": notice.get("mute_all", False),
            "enabled": False,
            "start": "",
            "end": "",
            **notice.get("quiet_hours", {}),
        }
        from gideon.engine.proactive_decisions import recent

        view["commitment_decisions"] = recent()
        return view

    try:
        view = await asyncio.to_thread(read)
    except Exception as exc:  # noqa: BLE001 - a broken read must READ as broken
        logger.warning("proactive: digest read failed", exc_info=True)
        view = build_digest_view(
            enabled=False, installed=False, error=f"{type(exc).__name__}: {exc}"
        )
        return json_error(
            "triage_digest_unreadable",
            message=str(view.get("error") or "the digest could not be read"),
            status=500,
            **{k: v for k, v in view.items() if k != "error"},
        )
    return web.json_response({**view})


def _digest_notice(*, title: str, body: str) -> dict[str, Any]:
    """Explain current settings with the same rule decision used by delivery."""
    try:
        from gideon.cognition.proactive.rank import DIGEST_NOTIFY_KIND
        from gideon.extensions.providers.entity_routes import (
            _load_entity_settings,
            load_notifications_settings,
            notification_posture,
            quiet_window_moments,
        )
        from gideon.workspace import notification_rules as rules

        if _load_entity_settings("notifications") is None:
            return {"known": False}
        settings = load_notifications_settings()
        moments = quiet_window_moments(settings)
        text = f"{title}\n{body}"

        def mode(now: object | None) -> str:
            posture = notification_posture(DIGEST_NOTIFY_KIND, now=now)
            return (
                "dropped"
                if posture == "blocked"
                else rules.rule_outcome(DIGEST_NOTIFY_KIND, text, posture=posture).mode
            )

        rule = rules.resolve_rule_for_legacy(DIGEST_NOTIFY_KIND)
        return {
            "known": True,
            "mute_all": bool(settings.get("mute_all")),
            "min_severity": str(settings.get("min_severity", "info")),
            "quiet_hours": {
                "enabled": bool(settings.get("quiet_hours_enabled")),
                "start": str(settings.get("quiet_hours_start", "") or ""),
                "end": str(settings.get("quiet_hours_end", "") or ""),
            },
            "rule": rule.mode,
            "inside": mode(moments[0] if moments else None),
            "outside": mode(moments[1] if moments else None),
        }
    except Exception:
        logger.debug("proactive: notification settings unreadable", exc_info=True)
        return {"known": False}


async def api_proactive_install(request: web.Request) -> web.Response:
    """POST /api/proactive/install — §5.4's pack card. Idempotent; also the reconcile.

    Creates the schedule when it is absent, and on every call brings its ``enabled`` flag into
    line with ``proactive.triage_enabled`` — which is criterion 10's retirement (disable ⇒ the
    schedule stops firing) and its losslessness (re-enable ⇒ the same row, same cron, fires
    again) in one path. The row is never DELETED on disable: deleting it would lose the cron the
    user edited, and "dormant but kept" is exactly what the criterion asks for.

    An explicit ``cron`` in the body edits the schedule (that is what "installs an editable
    trigger" means).

    🔴 CAUGHT BY DRIVING IT: the first version fell back to the config's ``digest_schedule``
    whenever the body carried no cron, so the reconcile the enable/disable toggle fires **silently
    rewrote a cron the user had edited** — install at ``30 7 * * 1-5``, flip triage on, and the row
    came back ``0 8 * * *``. An "editable trigger" that a switch elsewhere in the app resets is not
    editable. So the precedence is now: the body's cron (an explicit edit) → the INSTALLED row's
    own cron (the edit is the state) → the config default (only ever for a first install).
    """
    from gideon.automation.schedule import validate_cron_expr
    from gideon.cognition.proactive.surface import TRIAGE_WORKFLOW
    from gideon.interfaces.dashboard.handlers import _is_restricted_session

    if _is_restricted_session(request.app["state"], request):
        return json_error(
            "forbidden",
            message="Automation writes are not allowed in this session mode.",
            status=403,
        )
    body = await _body(request)
    config = _config()
    proactive = _proactive(config)
    asked = str(body.get("cron", "") or "").strip()
    default_cron = str(getattr(proactive, "digest_schedule", "") or "")
    if asked and not validate_cron_expr(asked):
        return json_error(
            "invalid_request",
            message=f"{asked!r} is not a 5-field cron expression",
            status=422,
        )
    enabled = bool(getattr(proactive, "triage_enabled", False))

    def ensure() -> tuple[dict[str, Any], bool]:
        from gideon.automation.triggers import screen as _screen
        from gideon.automation.triggers.arm import arm
        from gideon.automation.triggers.models import Trigger

        store = _trigger_store()
        trigger = _find_schedule(store)
        created = trigger is None
        if trigger is None:
            trigger = Trigger(
                id=TRIAGE_TRIGGER_ID,
                name="Morning triage",
                kind="clock",
                created_by=TRIAGE_CREATED_BY,
                # `delivery: none` — the digest delivers ITSELF, through `ConsoleState.notify`
                delivery="none",
            )
        spec = dict(getattr(trigger, "spec", None) or {})
        resolved = asked or str(spec.get("expr", "") or "") or default_cron
        if not validate_cron_expr(resolved):
            resolved = default_cron
        if not validate_cron_expr(resolved):
            raise RuntimeError(f"{resolved!r} is not a 5-field cron expression")
        spec["kind"] = "cron"
        spec["expr"] = resolved
        trigger.spec = spec
        trigger.workflow = {
            "inline": {
                "provider": "run-workflow",
                "config": {"workflow": TRIAGE_WORKFLOW},
            }
        }
        trigger.enabled = enabled
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        if enabled:
            when = arm(trigger)
            if when:
                trigger.next_fire_at = when
        store.upsert(trigger)
        return _schedule_payload(trigger), created

    try:
        payload, created = await asyncio.to_thread(ensure)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive: triage schedule install failed", exc_info=True)
        return json_error(
            "triage_schedule_write_failed",
            message=f"{type(exc).__name__}: {exc}",
            status=500,
        )
    request.app["state"].push_refresh("crons")
    _sel().log_api_access(
        caller=request.headers.get("X-Session-Key", ""),
        operation="triage_schedule.install" if created else "triage_schedule.reconcile",
        outcome="success",
        source="dashboard",
        resources=f"trigger:schedule:{payload['id']}:enabled={enabled}",
    )
    return web.json_response({"ok": True, "created": created, "schedule": payload})


def _pending_row(view: dict, ordinal: str) -> dict | None:
    for row in view.get("pending") or []:
        if str(row.get("ordinal", "")) == ordinal:
            return row
    return None


def _write_reply_row(
    run_id: str, ordinal: str, *, verb: str, outcome: str, detail: str
) -> bool:
    """Record the answer on the digest's own run. Returns False when there is no run to write to.

    The row IS the idempotency record, so a failure to write it is reported to the caller rather
    than swallowed: a reply that acted but left no row would act again on the next tap.
    """
    from gideon.assurance.ledger.kinds import TRIAGE_REPLY
    from gideon.automation.workflows.journal import Journal
    from gideon.cognition.proactive.surface import TRIAGE_NODE_ID

    try:
        Journal(run_id=run_id).write(
            TRIAGE_REPLY,
            node_id=TRIAGE_NODE_ID,
            instance_path=TRIAGE_NODE_ID,
            epoch=0,
            actor="user",
            item_ordinal=ordinal,
            verb=verb,
            outcome=outcome,
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "proactive: reply row not written for %s/%s", run_id, ordinal, exc_info=True
        )
        return False
    return True


def _persist_rule(request: web.Request, pattern: str, approve: bool) -> tuple[str, str]:
    """Teach one approval rule through the SAME guarded write the rules manager POSTs to.

    Returns ``(key, error)``. The write goes through ``MemoryService.set_semantic``, so the
    injection scanner still sees the pattern text even though the user ratified it (§1.4).
    """
    from gideon.cognition.proactive.approval import ApprovalRule, Verdict, rule_to_value
    from gideon.interfaces.dashboard.handlers.memory import _get_service

    rule = ApprovalRule(
        pattern=pattern,
        verdict=Verdict.APPROVE if approve else Verdict.DENY,
        created_from_digest="digest-card",
    )
    svc = _get_service(request.app["state"])
    err = svc.set_semantic(rule.key, rule_to_value(rule), 1.0, "user_explicit")
    if err is not None:
        _code, message = err
        return rule.key, message
    return rule.key, ""


async def _dispatch_approved(
    view: dict, row: dict, *, session_key: str, run_id: str
) -> tuple[bool, str]:
    """Run ONE approved proposal through PA-3's stage. Returns ``(executed, detail)``.

    The user's tap is expressed as an in-memory approve rule for exactly this proposal's pattern
    — never persisted, so a single "yes" does not silently become an "always" — and `cap=1`, so a
    tap can dispatch one action and no more. Every guard PA-3 put in front of an unattended write
    therefore runs here too, in the order it runs there.
    """
    from datetime import datetime, timezone

    from gideon.cognition.proactive.approval import ApprovalRule, Verdict
    from gideon.cognition.proactive.autoexec import auto_execute
    from gideon.cognition.proactive.manifest import manifest_from_projection
    from gideon.cognition.proactive.proposals import Proposal

    pattern = str(row.get("pattern_key", "") or "")
    if not pattern:
        return (
            False,
            "this proposal has no recorded pattern, so it cannot be authorised",
        )
    manifest = manifest_from_projection(
        [
            {
                "ordinal": str(item.get("ordinal", "") or ""),
                "source": str(item.get("source", "") or ""),
                "source_id": str(item.get("source_id", "") or ""),
                "title": str(item.get("title", "") or ""),
                "permalink": str(item.get("item_permalink", "") or ""),
                "materiality": str(item.get("materiality", "") or ""),
            }
            for item in (view.get("pending") or []) + (view.get("auto_done") or [])
        ],
        window_start=str(view.get("window_start", "") or ""),
    )
    proposal = Proposal(
        item_id=str(row.get("ordinal", "") or ""),
        action_type=str(row.get("action_type", "") or ""),
        tier=str(row.get("tier", "") or ""),
        pattern_key=pattern,
    )
    result = await auto_execute(
        [proposal],
        manifest=manifest,
        rules=[ApprovalRule(pattern=pattern, verdict=Verdict.APPROVE)],
        now=datetime.now(timezone.utc),
        enabled=True,
        cap=1,
        session_key=session_key,
        ledger=_run_ledger(run_id),
    )
    if result.executed:
        action = result.executed[0]
        return (
            True,
            f"{proposal.action_type} on {action.source_id}",
        )
    if result.deferred:
        deferred = result.deferred[0]
        return False, deferred.detail or deferred.reason
    return False, "the action stage returned nothing"


def _run_ledger(run_id: str):
    """PA-3's `LedgerFn`, bound to the digest's run so an approved action lands in ITS journal."""
    from gideon.automation.workflows.journal import Journal
    from gideon.cognition.proactive.surface import TRIAGE_NODE_ID

    journal = Journal(run_id=run_id)

    def write(kind: str, fields: dict) -> None:
        journal.write(
            kind,
            node_id=TRIAGE_NODE_ID,
            instance_path=TRIAGE_NODE_ID,
            epoch=0,
            actor="user",
            **fields,
        )

    return write


async def api_proactive_reply(request: web.Request) -> web.Response:
    """POST /api/proactive/digest/reply — one tap or one typed reply. Body ``{run_id, text}``.

    The card's door to the one answer path (:func:`gideon.cognition.proactive.answer.answer`), as
    the owner's signed-in session or whoever else the request proves (`approval_answer`), who is
    refused before anything is read. The response always says which of five things happened,
    because a card that cannot tell them apart will show the wrong one: ``expired`` (the run is
    not the current digest), ``help`` (the grammar refused and returned a help line — never an
    interpretation), ``already`` (this ordinal was answered before, so nothing ran again),
    ``acted``, or an error.
    """
    from gideon.cognition.proactive import answer as triage_answer
    from gideon.interfaces.dashboard.handlers import _is_restricted_session
    from gideon.interfaces.dashboard.handlers.memory import _get_service
    from gideon.security import approval_answer

    if _is_restricted_session(request.app["state"], request):
        return json_error(
            "forbidden",
            message="Digest replies are not allowed in this session mode.",
            status=403,
        )
    body = await _body(request)
    if body.get("commitment_key"):
        return await _commitment_reply(request, body)
    run_id = str(body.get("run_id", "") or "").strip()
    text = str(body.get("text", "") or "")
    if not run_id:
        return json_error("invalid_request", message="run_id is required", status=400)

    from gideon.security.session_credentials import work_of_request

    proof = work_of_request(request)
    session_key = proof.session_key if proof is not None else ""
    state = request.app["state"]
    done = await triage_answer.answer(
        run_id,
        text,
        door=triage_answer.Door(
            by=approval_answer.of_request(request),
            caller=session_key,
            source="dashboard",
            session_key=session_key,
            memory=lambda: _get_service(state),
        ),
    )
    if done.outcome == triage_answer.REFUSED:
        return json_error("approval_owner_only", message=done.error, status=403)
    if done.outcome == triage_answer.UNREADABLE:
        return json_error("triage_digest_unreadable", message=done.error, status=500)
    if done.outcome == triage_answer.EXPIRED:
        return json_error(
            "triage_digest_expired",
            message="that digest expired — open the current one and answer there",
            status=409,
            ok=False,
            outcome="expired",
            current_run_id=done.current_run_id,
        )
    if done.outcome == triage_answer.HELP:
        # A 200, not an error envelope: the grammar REFUSED and answered with a help line, which
        # is the documented outcome ("ambiguity gets a help line, not a guess"), not a
        # failure of the request. `help_text` rather than `error` so the census's flat shape is not
        # minted for something that is not an error at all.
        return web.json_response(
            {
                "ok": False,
                "outcome": "help",
                "help": done.help,
                "help_reason": done.help_reason,
            }
        )
    return web.json_response(
        {"ok": True, "outcome": "acted", "results": list(done.results)}
    )


async def _commitment_reply(request: web.Request, body: dict) -> web.Response:
    """An explicit dismissal or reviewed background workflow from a persisted decision."""
    from gideon.engine import proactive_decisions

    topic = str(body.get("commitment_key") or "").strip()
    decision = await asyncio.to_thread(proactive_decisions.get, topic)
    if decision is None:
        return json_error(
            "commitment_not_found",
            message="that commitment decision is no longer available",
            status=404,
        )
    action = body.get("action")
    if action == "dismiss":
        updated = await asyncio.to_thread(proactive_decisions.dismiss, topic)
        return web.json_response(
            {"ok": True, "outcome": "dismissed", "decision": updated}
        )
    if action != "approve_background":
        return json_error(
            "invalid_request",
            message="action must be dismiss or approve_background",
            status=400,
        )
    if proactive_decisions.suppressed(decision):
        return json_error(
            "commitment_suppressed",
            message="that topic is in its dismissal cooldown",
            status=409,
        )
    if decision.get("run_id"):
        return web.json_response(
            {"ok": True, "outcome": "already", "run_id": decision["run_id"]}
        )
    name = str(body.get("workflow_name") or "").strip()
    inputs = body.get("inputs") or {}
    if not name or not isinstance(inputs, dict):
        return json_error(
            "invalid_request",
            message="a saved workflow and input object are required",
            status=400,
        )
    from gideon.automation.workflows import service as workflow_service
    from gideon.automation.workflows.handlers import _guard, _supervisor
    from gideon.automation.workflows.models import OriginKind

    denied = _guard(request, "workflow_run_start")
    if denied is not None:
        return denied
    supervisor = _supervisor(request)
    if supervisor is None:
        return json_error(
            "engine_unavailable",
            message="workflow supervisor is unavailable",
            status=503,
        )
    result = await workflow_service.start_run(
        name=name,
        inputs=inputs,
        supervisor=supervisor,
        origin_kind=OriginKind.MANUAL,
        session_key=str(request.headers.get("X-Session-Key") or ""),
        idempotency_key=f"proactive:{topic}",
    )
    if not result.get("ok"):
        return json_error(
            "background_launch_failed",
            message=str(result.get("message") or "workflow did not start"),
            status=409,
        )
    updated = await asyncio.to_thread(
        proactive_decisions.approved_run, topic, result["run_id"]
    )
    _sel().log_api_access(
        caller=request.headers.get("X-Session-Key", ""),
        operation="commitment_background_launch",
        outcome="approved",
        source="dashboard",
        resources=f"run:{result['run_id']}",
    )
    return web.json_response(
        {
            "ok": True,
            "outcome": "launched",
            "run_id": result["run_id"],
            "decision": updated,
        }
    )


def _verb(parsed: Any) -> str:
    from gideon.cognition.proactive.approval import ReplyAction

    return {
        ReplyAction.APPROVE_ONCE: "yes",
        ReplyAction.DENY_ONCE: "no",
        ReplyAction.APPROVE_ALWAYS: "always yes",
        ReplyAction.DENY_ALWAYS: "always no",
        ReplyAction.APPROVE_ALL: "yes all",
        ReplyAction.DENY_ALL: "no all",
    }.get(parsed.action, str(parsed.action))
