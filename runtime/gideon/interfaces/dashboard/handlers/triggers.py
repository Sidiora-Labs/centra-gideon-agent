"""Unified Trigger API — /api/triggers/*.

A **Trigger** is "when something happens, run an action". Two kinds share one
surface:

- ``schedule`` — a clock tick fires (every / cron / at). Backed by
  :class:`gideon.automation.schedule.ScheduleService` (``state.crons``).
- ``lifecycle`` — an agent-loop event fires (PreToolUse, Stop, …). Backed by
  :class:`gideon.engine.hooks.ScriptHookStore`.

This handler is a **facade**: there is no ``triggers.json`` and no migration. It
presents both stores through one ``Trigger`` shape and routes each mutation to the
owning store by a namespaced id (``schedule:<rawId>`` / ``lifecycle:<rawId>``).

Every trigger carries ``action: {provider, config}`` chosen from the action
provider catalog (``/api/action-providers``). For lifecycle triggers the action
is the hook's ``provider`` + ``provider_config``; for schedule triggers it is
``ScheduleJob.action`` — the sole source of what the job runs. The schedule
executor dispatches every provider straight from that action (``invoke-agent``
runs an LLM turn, every other provider runs through the action registry).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from gideon.core.config import loader as config_loader
from gideon.http_errors import json_error
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.security import redact_credentials, redact_exfiltration_urls


def config_dir():
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_SCHEDULE = "schedule"
_LIFECYCLE = "lifecycle"
_EVENT = "event"
_STORE = "store"

_STORE_ONLY_KINDS: frozenset[str] = frozenset(
    {"file", "web_watch", "idle", "run_completed", "view", "webhook"}
)


def _event_store():
    from gideon.automation.event_triggers import EventTriggerStore
    from gideon.core.config.loader import config_dir

    return EventTriggerStore(config_dir() / "event_triggers.json")


def _serialize_event(t) -> dict[str, Any]:
    last_run_ts = float(t.last_fired_at or 0.0) or None
    return {
        "kind": _EVENT,
        "id": f"{_EVENT}:{t.id}",
        "name": t.id,
        "enabled": t.enabled,
        "source": t.source,
        "pattern": t.pattern,
        "key_glob": t.key_glob,
        "content_re": t.content_re,
        "sender_glob": t.sender_glob,
        "address_glob": t.address_glob,
        "event_glob": t.event_glob,
        "max_fires": t.max_fires,
        "fire_count": t.fire_count,
        "last_run_ts": last_run_ts,
        "last_run_status": "ran" if last_run_ts is not None else None,
        "action": {"provider": t.action_provider, "config": t.action_config},
        "state": t.state,
        "health": _event_health(t),
        "last_error": t.park_reason,
    }


def _event_health(t) -> str:
    """The `TriggerHealth` rollup for an event trigger.

    Derived, not stored: this store keeps no run history to roll up, so the only honest answer comes
    from the lifecycle state. Mapped through `TriggerHealth` rather than invented so the FE's one
    `triggerHealthMeta` mapper renders a parked event trigger the same way it renders a parked store
    trigger — S164's finding was that a second local copy of this vocabulary rendered three distinct
    states as one grey dot.
    """
    from gideon.automation.triggers.models import TriggerHealth, TriggerState

    if t.state == TriggerState.PARKED.value:
        return TriggerHealth.PARKED.value
    return TriggerHealth.OK.value


def _sel():
    import gideon.interfaces.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


def _redact(s: str) -> str:
    return redact_credentials(redact_exfiltration_urls(s or "")[0])[0]


def _split_id(trigger_id: str) -> tuple[str, str]:
    """``schedule:abc`` → (``schedule``, ``abc``); bare id defaults to schedule.

    A `store` id keeps its own `<kind>:<slug>` form (e.g. `file:my-notes`) as the RAW id, because
    that IS the id in `TriggerStore` — splitting it would break the lookup. So `store:` is stripped
    once and the remainder handed to the store verbatim.
    """
    kind, _, raw = trigger_id.partition(":")
    if raw and kind == _STORE:
        return _STORE, raw
    if raw and kind in (_SCHEDULE, _LIFECYCLE, _EVENT):
        return kind, raw
    return _SCHEDULE, trigger_id


def _trigger_store():
    """The unified store, rooted at the active home.

    Resolved through this module's `config_dir` so there is exactly ONE place to redirect the
    handler's store — which is what `tests/conftest.py::_isolate_trigger_store` patches. Importing
    it inside the function instead would defeat that fixture, and S98 already paid for that
    lesson: the boot migration took `config_dir()` from its caller and wrote to the real home.
    """
    from gideon.automation.triggers.store import TriggerStore

    return TriggerStore(base_dir=config_dir())


def _job_shim_for(state: ConsoleState, raw: str) -> Any:
    """The minimal job-shaped object `inject_schedule_result_to_session` needs (S104).

    Measured: the injection reads exactly `job.id`, `job.name` and `job.agent_id` — nothing else. So
    a store row is projected onto that tiny surface rather than the whole legacy entity, and the
    handler stops needing `ScheduleService` at all. Returns None when neither store nor legacy
    service knows the id, so the caller can still fall back to a history-only session.
    """
    from gideon.automation.schedule import ScheduleJob

    row = _trigger_store().get(raw)
    if row is not None:
        config = {}
        workflow = row.trigger.workflow or {}
        inline = (
            workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
        )
        raw_config = (inline or workflow).get("config")
        if isinstance(raw_config, dict):
            config = raw_config
        return ScheduleJob(
            id=row.trigger.id,
            name=row.trigger.name,
            action={
                "provider": (inline or workflow).get("provider", ""),
                "config": config,
            },
        )
    return None


def _trigger_names(state: ConsoleState) -> dict[str, str]:
    """`{trigger_id: name}` for labelling run rows, from the store.

    Includes EVERY kind, not just clock: the unified history feed carries file/web_watch/event runs
    too, and a name map that only knew about schedules would blank exactly the rows the new kinds
    contribute.

    Store-only since S110: the boot migration imports every legacy job, INCLUDING the ones it
    refuses (which it now writes disabled rather than dropping), so there is no id the legacy
    service could name that the store cannot.
    """
    names: dict[str, str] = {}
    for row in _trigger_store().load():
        names[row.trigger.id] = row.trigger.name
    return names


async def _last_result_for(state: ConsoleState, raw: str) -> str:
    """The newest run's output for a trigger, or "".

    Reads `ExecutionJournal` rather than a `last_result` field: `LEGACY_FIELD_MAP` maps that field
    to None deliberately — the RUN RECORD owns a run's output, and a copy on the trigger was a
    second truth that could disagree with it. The run store is keyed by a plain id, so it serves a
    store-backed trigger and a legacy job identically.
    """
    try:
        runs, _total = await _runs_store().list_for_job(raw, 0, 1)
    except Exception:
        logger.debug("could not read the last run for %s", raw, exc_info=True)
        return ""
    if not runs:
        return ""
    newest = runs[0] if isinstance(runs[0], dict) else {}
    return str(newest.get("summary") or newest.get("error") or "")


def _runs_store() -> Any:
    """The run-record store, held DIRECTLY rather than through `ScheduleService` (S105).

    🔴 Named `_runs_store`, not `_run_store`: this module ALREADY has an
    `async def _run_store(raw, request)` handler (S94's manual-fire path), and defining a second
    function with that name silently SHADOWED it — driven, the history endpoint raised
    "_run_store() missing 2 required positional arguments". A same-name redefinition is a real
    hazard in a 1400-line handler module, and Python reports it only at the call site.

    🔴 Measured: all four run-record methods on `ScheduleService` are one-line passthroughs to
    `ExecutionJournal` (`list_runs` → `list_for_job`, `list_all_runs` → `list_all`, `get_run`,
    `delete_runs` → `delete_for_job`), and the store constructs and answers standalone from a bare
    `base_dir`. So the facade's dependency on the legacy service for run HISTORY was pure
    indirection, and this removes it without changing a single stored byte.

    Keyed by a plain id string, which is why this store survives the whole cutover unchanged: a
    store-created trigger's runs and a legacy job's runs live in the same place, addressed the same
    way. Rooted through this module's `config_dir` so the test fixture redirects it with everything
    else.
    """
    from gideon.automation.schedule_history import ExecutionJournal

    return ExecutionJournal(config_dir())


def _last_run_status_for(trigger_id: str) -> str:
    """The newest run's PERSISTENT status, or "" — sync, for the list serializer.

    Same contract `ScheduleService.last_run_status` documented and for the same reason (T7): the
    honest status survives restarts and distinguishes `launched` from `ok`, where a trigger's own
    field would report a fire-and-forget run as a success. Reads the store's own sync path, so the
    list serializer stays cheap.
    """
    try:
        rows, _total = _runs_store().list_for_job_sync(trigger_id, 0, 1)
    except Exception:
        logger.debug("last-run status unavailable for %s", trigger_id, exc_info=True)
        return ""
    return str(rows[0].get("status", "")) if rows else ""


def _week_triggers(state: ConsoleState) -> list[Any]:
    """Enabled clock triggers to plot, from the store (S103).

    Only ENABLED ones: a disabled trigger has no fires, and drawing them would make the grid a wish
    list rather than a forecast. Broken rows are excluded too — a row the entity refuses has no
    knowable schedule, and plotting a guess is worse than an absence.

    Store-only since S110: the legacy translation retired with `ScheduleService`'s CRUD, because the
    boot migration imports every legacy job — including the ones it refuses, which it now writes
    disabled rather than dropping.
    """
    store = _trigger_store()
    rows = [
        row.trigger
        for row in store.load()
        if row.trigger.kind == "clock" and row.trigger.enabled and row.ok
    ]
    return rows


def _project_one(
    trigger: Any, *, start: Any, days: int, until: Any = None
) -> tuple[list[Any], bool]:
    """Project ONE clock trigger's fires across the window.

    🔴 A CRON NOW PLOTS. The old caller skipped every non-interval trigger with its own admission
    ("a cron trigger is omitted rather than mis-plotted"), which made the week view a forecast of
    only half a user's automations — silently. S96's `arm.next_fire` can step a cron, so it is
    passed to `project_occurrences` as `next_after`. An interval keeps the arithmetic path, because
    a constant step is cheaper and exactly right for it.

    `skip_dates` and `tz_name` are read off the trigger for the reason AUTO-A3 requires: the
    SCHEDULER compares skip dates against the date in the trigger's OWN zone, so a grid on server
    time would strike the wrong column for any job that declares one.
    """
    from gideon.automation.triggers.arm import cadence_next_fire as raw_next_fire
    from gideon.automation.triggers.calendar import project_occurrences
    from gideon.automation.triggers.models import next_fire_projection

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    kind = str(spec.get("kind") or "")
    interval = float(spec.get("interval_secs") or 0)
    common = {
        "trigger_id": f"{_SCHEDULE}:{trigger.id}",
        "trigger_name": trigger.name,
        "start": start,
        "days": days,
        "until": until,
        "gates": getattr(trigger, "gates", None) or {},
        "skip_dates": [str(d) for d in (spec.get("skip_dates") or [])],
        "tz_name": str(spec.get("timezone") or ""),
    }
    first = next_fire_projection(trigger)
    if kind in ("interval", "sequence") and interval > 0:
        if first <= 0:
            return [], False
        return project_occurrences(
            interval_secs=interval, first_fire_at=first, **common
        )
    if kind == "at" and first > 0:
        return project_occurrences(
            interval_secs=0,
            first_fire_at=0,
            next_after=lambda after: first if after < first else 0,
            **common,
        )
    if (kind == "cron" and spec.get("expr")) or kind == "adaptive":
        return project_occurrences(
            interval_secs=0,
            first_fire_at=0,
            next_after=lambda after: raw_next_fire(trigger, now=after),
            **common,
        )
    return [], False


def _arm_if_needed(store: Any, trigger_id: str) -> None:
    """Arm a clock trigger that has no next fire (S101).

    Called after any write that can make a row newly firable — a create, or a re-enable. Without it
    the row sits `enabled=True` with an empty `next_fire_at`, and `service.due_ids` only surfaces
    rows that HAVE one: enabled and inert until the next boot sweep. `arm.needs_arming` selects
    exactly that population, so a row already carrying a next fire is left alone (re-arming a live
    schedule mid-flight is how a fire gets skipped or doubled).
    """
    from gideon.automation.triggers.arm import arm, needs_arming

    row = store.get(trigger_id)
    if row is None or not needs_arming(row.trigger):
        return
    when = arm(row.trigger)
    if not when:
        return
    row.trigger.next_fire_at = when
    store.upsert(row.trigger)


def _attribution(trigger: Any, *, owner: str) -> dict[str, Any]:
    """The two attribution keys every store-backed projection carries (TSE-4).

    `read_only` is the FRONTEND's whole instruction for a foreign row (§2.2: "rendered read-only —
    author chip, no enable/edit/delete"). Computed server-side from the same
    `ownership.is_owner_authored` predicate the arm path uses, so the page can never offer a control
    for a row the service would refuse to arm — a UI that derived it from a string comparison of its
    own would be a second opinion about who owns a trigger, and the two would drift.
    """
    from gideon.automation.triggers.ownership import is_owner_authored

    return {
        "author": str(getattr(trigger, "author", "") or ""),
        "read_only": not is_owner_authored(trigger, owner=owner),
    }


def _serialize_store(
    trigger: Any, *, broken: list[str] | None = None, owner: str = ""
) -> dict[str, Any]:
    """A `TriggerStore` trigger in the shared list shape. Id is `store:<kind>:<slug>` so the
    mutation routes back to the store; `raw_id` is the store's own id."""
    latest: dict[str, Any] = {}
    try:
        rows, _total = _runs_store().list_for_job_sync(trigger.id, 0, 1)
        latest = rows[0] if rows else {}
    except Exception:
        logger.debug("last run unavailable for %s", trigger.id, exc_info=True)
    return {
        "kind": _STORE,
        "store_kind": trigger.kind,
        "id": f"{_STORE}:{trigger.id}",
        "raw_id": trigger.id,
        "name": trigger.name,
        "enabled": trigger.enabled,
        "created_by": trigger.created_by,
        "spec": dict(trigger.spec or {}),
        "action": dict(trigger.workflow or {}),
        "delivery": trigger.delivery,
        "failure_delivery": trigger.failure_delivery,
        "failure_policy": dict(trigger.failure_policy or {}),
        "silent": trigger.delivery == "none",
        "health": trigger.health_status,
        "state": trigger.state,
        "run_count": trigger.run_count,
        "last_run_ts": latest.get("finished_at") or latest.get("started_at") or None,
        "last_run_status": str(latest.get("status") or "") or None,
        "last_error": _redact(trigger.last_error_summary or ""),
        "broken": list(broken or []),
        **_attribution(trigger, owner=owner),
    }


def _last_run_status(state: ConsoleState, job_id: str) -> str | None:
    """The newest run record's status for the honest UI badge (T7), or None.

    Reads the RUN STORE directly (S105). `ScheduleService.last_run_status` was itself a two-line
    read of the same store's sync path, so going through the service was pure indirection — and it
    meant a dashboard whose legacy service was a test double or absent showed no badge at all.
    Still defensive (None on any failure) so the serializer stays robust + JSON-safe.
    """
    status = _last_run_status_for(job_id)
    return status or None


def _schedule_rows(state: ConsoleState) -> list[dict[str, Any]]:
    """Every schedule trigger, read from the unified store (§6 re-point — S99).

    The store is the source of truth once the boot migration has run (S98). The legacy service is
    consulted ONLY when the store holds no clock rows, which happens on a home whose migration has
    not run yet — reading the old file for one more boot is strictly better than showing a user zero
    schedules. That fallback is what retires when `ScheduleService` does.

    Names/results are redacted on the way out exactly as `_serialize_schedule` did: the projection
    is a data mapping and knows nothing about credential scrubbing.
    """
    from gideon.automation.triggers.ownership import owner_username
    from gideon.automation.triggers.provider import all_rows

    store = _trigger_store()
    owner = owner_username()
    clock_rows = [row for row in all_rows(store) if row.trigger.kind == "clock"]
    if clock_rows:
        return [
            _schedule_row_for(
                state, row.trigger, issues=[i.message for i in row.errors], owner=owner
            )
            for row in clock_rows
        ]
    return []


def _schedule_row_for(
    state: ConsoleState,
    trigger: Any,
    *,
    issues: list[str] | None = None,
    owner: str = "",
) -> dict[str, Any]:
    """ONE schedule row, projected and redacted (S101).

    Factored out of `_schedule_rows` so the list and the single-row write responses (create,
    update) answer in exactly the same shape. Two projections would drift, and a create that
    returned a different shape than the list is how a UI ends up with two ideas of one trigger.
    """
    import time as _time

    from gideon.automation.triggers.schedule_view import to_schedule_row

    store = _trigger_store()
    projected = to_schedule_row(
        trigger,
        now=_time.time(),
        base_dir=store.base_dir,
        last_run_status=_last_run_status(state, trigger.id) or "",
    )
    projected["name"] = _redact(projected.get("name") or "")
    for key in ("message", "last_error", "schedule"):
        if projected.get(key):
            projected[key] = _redact(str(projected[key]))
    policy = trigger.failure_policy if isinstance(trigger.failure_policy, dict) else {}
    projected["failure_delivery"] = str(trigger.failure_delivery or "")
    projected["dedupe_hash"] = policy.get("dedupe_hash") is True
    projected["broken"] = list(issues or [])
    projected["delivery"] = trigger.delivery
    projected["failure_delivery"] = trigger.failure_delivery
    projected["failure_policy"] = dict(trigger.failure_policy or {})
    projected.update(_attribution(trigger, owner=owner))
    return projected


def _outcome_control_patch(
    body: dict[str, Any], trigger: Any | None = None
) -> tuple[dict[str, Any], str]:
    from gideon.automation.triggers.models import (
        validate_failure_policy,
        validate_outcome_route,
    )

    patch: dict[str, Any] = {}
    for field in ("delivery", "failure_delivery"):
        if field not in body:
            continue
        issues = validate_outcome_route(body[field], field)
        if issues:
            return {}, f"{issues[0].path}: {issues[0].message}"
        patch[field] = body[field].strip()
    if "failure_policy" in body:
        incoming = body["failure_policy"]
        issues = validate_failure_policy(incoming)
        if issues:
            return {}, f"{issues[0].path}: {issues[0].message}"
        current = getattr(trigger, "failure_policy", {}) if trigger is not None else {}
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(incoming)
        patch["failure_policy"] = merged
    return patch, ""


def _serialize_lifecycle(hook, used_by: list[str]) -> dict[str, Any]:
    from gideon.engine.hooks import BLOCKING_EVENTS, hook_enforcement

    return {
        "kind": _LIFECYCLE,
        "id": f"{_LIFECYCLE}:{hook.id}",
        "raw_id": hook.id,
        "name": hook.name,
        "enabled": hook.enabled,
        "action": {"provider": hook.provider, "config": hook.provider_config},
        "event": hook.event,
        "matcher": hook.matcher,
        "timeout": hook.timeout,
        "last_run": hook.last_run,
        "last_status": hook.last_status,
        "run_count": hook.run_count,
        "used_by": sorted(used_by),
        "blocking": hook.event in BLOCKING_EVENTS,
        "enforcement": hook_enforcement(
            hook.event, enabled=bool(hook.enabled), bound=bool(used_by)
        ),
    }


def _hook_store(state: ConsoleState):
    from gideon.interfaces.dashboard.handlers.hooks import _get_hook_store

    return _get_hook_store(state)


def _used_by_index() -> dict[str, list[str]]:
    """hook_id → [agent names that reference it] (agents are lifecycle-scoped)."""
    from gideon.core.config.loader import AppConfig

    idx: dict[str, list[str]] = {}
    try:
        cfg = AppConfig.load()
        for agent_name, prof in (cfg.agents or {}).items():
            for tid in getattr(prof, "triggers", []) or []:
                idx.setdefault(str(tid), []).append(agent_name)
    except Exception:
        logger.debug("triggers used_by index failed", exc_info=True)
    return idx


async def api_trigger_variables(request: web.Request) -> web.Response:
    """GET /api/triggers/variables — the ``$variables`` each trigger kind exposes.

    The single server-sourced catalog both UIs read instead of mirroring it:
    ``{schedule: [...], lifecycle: [{event, label, desc, vars, blocking?}, ...],
    app_sources: [{app, label, events: [{event, source_event}]}]}``.
    Lifecycle entries come from :data:`gideon.engine.hooks.LIFECYCLE_EVENT_CATALOG`
    (co-located with the payload assembly that produces those vars); schedule vars
    from :data:`gideon.automation.schedule.SCHEDULE_VARS`.

    ``app_sources`` (AUTO-A4) is the LIVE app-contributed event vocabulary, read from the
    ``trigger_sources`` registry rather than from manifests: a declared source whose app is
    disabled is not registered, and offering its events would let a user author a trigger that
    cannot fire until they realise the app is off. Served here rather than on a new route for the
    same reason the lifecycle dormancy badge rides here — one catalog fetch, one source of truth.
    """
    from gideon.automation.schedule import SCHEDULE_VARS
    from gideon.automation.triggers.events import (
        AGENT_SCOPED_EVENTS,
        DORMANCY_NOTES,
        DORMANT_EVENTS,
    )
    from gideon.engine.hooks import LIFECYCLE_EVENT_CATALOG

    lifecycle = [
        {
            "event": e["event"],
            "label": e["label"],
            "desc": e["desc"],
            "vars": list(e["vars"]),
            "blocking": bool(e.get("blocking")),
            "dormant": e["event"] in DORMANT_EVENTS,
            "dormant_reason": DORMANCY_NOTES.get(e["event"], ""),
            "agent_scoped": e["event"] in AGENT_SCOPED_EVENTS,
        }
        for e in LIFECYCLE_EVENT_CATALOG
    ]
    return web.json_response(
        {
            "schedule": list(SCHEDULE_VARS),
            "lifecycle": lifecycle,
            "app_sources": _app_source_catalog(),
        }
    )


def _app_source_catalog() -> list[dict[str, Any]]:
    """The live app-contributed event vocabulary (AUTO-A4), sorted by app then by event.

    Each event carries BOTH its bare name and its full namespaced form, because those answer
    different questions: the bare name is what the app's own docs call it, and `source_event` is the
    literal string a trigger's `event_glob` matches. Handing the UI only the bare name would make it
    re-derive the prefix — a second place for the namespace rule to drift from
    `trigger_sources.namespace`.

    Sorted here rather than in the UI so there is ONE ordering rule: a list the server describes and
    a list the user scans that disagree is a small thing that costs a real minute to reconcile.
    """
    from gideon.automation.trigger_sources import declared_events, get_source, namespace

    declared = declared_events()
    out: list[dict[str, Any]] = []
    for app in sorted(declared):
        provider = get_source(app)
        out.append(
            {
                "app": app,
                "label": str(getattr(provider, "display_name", "") or app),
                "events": [
                    {"event": event, "source_event": namespace(app, event)}
                    for event in sorted(declared[app])
                ],
            }
        )
    return out


def unified_trigger_count(state: ConsoleState) -> int:
    """The number of triggers ``GET /api/triggers`` lists — the tally the dashboard
    SystemHealth rail renders under "triggers" so it AGREES with the Triggers page (#773).

    Counts the SAME four sources :func:`api_triggers` gathers — clock schedules and the
    store-only kinds from the unified store, lifecycle hooks, and data-event triggers —
    but never serializes or redacts a row: a status poll only needs the tally. This is
    deliberately WIDER than ``ConsoleState.trigger_counts()`` / the ``cron`` status
    block, which count the schedule store alone and stay as they are; the rail exists to
    over-claim otherwise — it labels a schedule-store count "triggers" while the Triggers
    page adds the (7 globally-fired) lifecycle hooks the store never held.

    Never raises: ``GET /api/status`` is what a user opens when something is already
    wrong, so a source that cannot be read contributes 0 rather than 500ing the surface —
    exactly the contract ``trigger_counts()`` keeps for the schedule half.
    """
    total = 0
    try:
        from gideon.automation.triggers.provider import all_rows

        for row in all_rows(_trigger_store()):
            if row.trigger.kind == "clock" or row.trigger.kind in _STORE_ONLY_KINDS:
                total += 1
    except Exception:  # noqa: BLE001 - a status read must never fail on the store half
        logger.debug("schedule/store trigger count unavailable", exc_info=True)
    try:
        total += len(_hook_store(state).list_all())
    except Exception:  # noqa: BLE001 - nor on the lifecycle-hook half
        logger.debug("lifecycle hook count unavailable", exc_info=True)
    try:
        total += len(_event_store().load())
    except Exception:  # noqa: BLE001 - nor on the data-event half
        logger.debug("event trigger count unavailable", exc_info=True)
    return total


async def api_triggers(request: web.Request) -> web.Response:
    """GET /api/triggers?type=schedule|lifecycle — every trigger, both kinds.

    ``?type=`` filters to one kind. The response also carries ``server_tz`` for
    the schedule cadence rendering the list does client-side.
    """
    state: ConsoleState = request.app["state"]
    want = request.query.get("type", "").strip().lower()

    triggers: list[dict[str, Any]] = []
    if want in ("", _SCHEDULE):
        triggers.extend(_schedule_rows(state))
    if want in ("", _LIFECYCLE):
        used_by = _used_by_index()
        for hook in _hook_store(state).list_all():
            triggers.append(_serialize_lifecycle(hook, used_by.get(hook.id, [])))
    if want in ("", _EVENT):
        for t in _event_store().load():
            triggers.append(_serialize_event(t))
    if want in ("", _STORE):
        from gideon.automation.triggers.ownership import owner_username
        from gideon.automation.triggers.provider import all_rows

        owner = owner_username()
        for row in all_rows(_trigger_store()):
            if row.trigger.kind in _STORE_ONLY_KINDS:
                triggers.append(
                    _serialize_store(
                        row.trigger, broken=[i.message for i in row.errors], owner=owner
                    )
                )

    from gideon.automation.schedule import get_local_tz
    from gideon.automation.triggers.ownership import owner_username

    tz_name, _ = get_local_tz()
    return web.json_response(
        {"triggers": triggers, "server_tz": tz_name, "owner": owner_username()}
    )


async def api_trigger_create(request: web.Request) -> web.Response:
    """POST /api/triggers — create a schedule or lifecycle trigger.

    Body: ``{trigger_type, name, action: {provider, config}, ...}``. Schedule
    triggers also take the schedule mechanism (``cron``/``every``/``at`` +
    delivery); lifecycle triggers take ``event`` + ``matcher``.
    """
    state: ConsoleState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    trigger_type = str(body.get("trigger_type") or "").strip().lower()
    if trigger_type == _LIFECYCLE:
        return await _create_lifecycle(state, body, request)
    if trigger_type == _SCHEDULE:
        return await _create_schedule(state, body, request)
    if trigger_type == _EVENT:
        return _create_event(body)
    return web.json_response(
        {"error": "trigger_type must be 'schedule', 'lifecycle', or 'event'"},
        status=400,
    )


def _create_event(body: dict) -> web.Response:
    """Create a data-event trigger (#38)."""
    import uuid

    from gideon.automation.event_triggers import (
        EVENT_PATTERNS,
        INBOX_SENDER,
        PATTERN_SOURCE,
        EventTrigger,
    )

    pattern = str(body.get("pattern") or "").strip()
    if pattern not in EVENT_PATTERNS:
        return web.json_response(
            {"error": f"pattern must be one of {list(EVENT_PATTERNS)}"}, status=400
        )
    sender_glob = str(body.get("sender_glob") or "")
    if pattern == INBOX_SENDER and not sender_glob:
        return web.json_response(
            {
                "error": "InboxSender requires a sender_glob",
                "code": "sender_glob_required",
            },
            status=400,
        )
    action = body.get("action") or {}
    t = EventTrigger(
        id=str(body.get("name") or uuid.uuid4().hex[:8]).strip(),
        pattern=pattern,
        source=PATTERN_SOURCE[pattern],
        action_provider=str(action.get("provider") or "notify"),
        action_config=dict(action.get("config") or {}),
        key_glob=str(body.get("key_glob") or ""),
        content_re=str(body.get("content_re") or ""),
        sender_glob=sender_glob,
        address_glob=str(body.get("address_glob") or ""),
        event_glob=str(body.get("event_glob") or ""),
        max_fires=int(body.get("max_fires", 0) or 0),
    )
    _event_store().upsert(t)
    payload = _serialize_event(t)
    hint = _regex_hint(t.content_re)
    if hint:
        payload["warning"] = hint
    return web.json_response(payload, status=201)


def _regex_hint(pattern: str) -> str:
    """The catastrophic-backtracking warning for a `content_re`, or "".

    Thin wrapper so both the create and update handlers ask the same question of the same function —
    a per-handler copy is how one of them ends up not warning.
    """
    from gideon.automation.event_triggers import catastrophic_regex_hint

    return catastrophic_regex_hint(pattern or "")


async def _create_lifecycle(
    state: ConsoleState, body: dict, request: web.Request
) -> web.Response:
    from gideon.assurance.validation import (
        HOOK_CREATE_SCHEMA,
        ValidationError,
        validate_tool_args,
    )

    action = body.get("action") or {}
    payload = {
        "name": body.get("name", ""),
        "event": body.get("event", ""),
        "matcher": body.get("matcher", ""),
        "provider": action.get("provider", ""),
        "provider_config": action.get("config") or {},
    }
    if "timeout" in body:
        payload["timeout"] = body["timeout"]
    try:
        validated = validate_tool_args(payload, HOOK_CREATE_SCHEMA)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    hook = _hook_store(state).create(validated)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:lifecycle:{hook.id}:{hook.name}:{hook.event}",
    )
    return web.json_response({"ok": True, "trigger": _serialize_lifecycle(hook, [])})


async def _create_schedule(
    state: ConsoleState, body: dict, request: web.Request
) -> web.Response:
    from zoneinfo import available_timezones

    from gideon.assurance.validation import CHANNEL_ID_RE, CHANNEL_MAX_LEN
    from gideon.automation.schedule import normalize_action

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    try:
        action = normalize_action(body.get("action"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    every = body.get("every")
    cron_expr = body.get("cron")
    at_ts = body.get("at")
    channel = str(body.get("channel", "")).strip() or None
    if channel and (len(channel) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(channel)):
        return web.json_response({"error": "invalid channel ID format"}, status=400)
    timezone_val = str(body.get("timezone") or "").strip()
    if timezone_val and timezone_val not in available_timezones():
        return web.json_response(
            {"error": f"invalid timezone: {_redact(timezone_val)!r}"}, status=400
        )
    outcome_patch, outcome_error = _outcome_control_patch(body)
    if outcome_error:
        return web.json_response({"error": outcome_error}, status=400)

    spec: dict[str, Any] = {}
    if every:
        try:
            spec = {"kind": "interval", "interval_secs": int(every)}
        except (ValueError, TypeError):
            return web.json_response(
                {"error": "'every' must be an integer"}, status=400
            )
    elif cron_expr:
        spec = {"kind": "cron", "expr": str(cron_expr).strip()}
    elif at_ts:
        try:
            spec = {"kind": "at", "at": float(at_ts), "delete_after_run": True}
        except (ValueError, TypeError):
            return web.json_response(
                {"error": "'at' must be a Unix timestamp in seconds"}, status=400
            )
    else:
        return web.json_response({"error": "every, cron, or at required"}, status=400)

    if timezone_val:
        spec["timezone"] = timezone_val
    if body.get("strict_schedule"):
        spec["strict"] = True
    if isinstance(body.get("skip_dates"), list):
        spec["skip_dates"] = [str(d) for d in body["skip_dates"]]

    from gideon.automation.triggers import tools as _tools

    enabled_raw = body.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        return json_error(
            "invalid_request", message="'enabled' must be a boolean", status=400
        )
    dedupe_hash = body.get("dedupe_hash", False)
    if not isinstance(dedupe_hash, bool):
        return json_error(
            "invalid_request", message="'dedupe_hash' must be a boolean", status=400
        )

    store = _trigger_store()
    result = _tools.create(
        store,
        name=name,
        kind="clock",
        spec=spec,
        enabled=enabled_raw,
        workflow={"inline": action},
        created_by="user",
    )
    if not result.ok:
        return web.json_response({"error": result.text}, status=400)

    raw_id = str((result.data.get("trigger") or {}).get("id") or "")
    row = store.get(raw_id)
    if row is not None:
        trigger = row.trigger
        trigger.delivery = (
            "none" if body.get("silent") else (f"channel:{channel}" if channel else "")
        )
        if "failure_delivery" in body:
            trigger.failure_delivery = str(body.get("failure_delivery") or "")
        if "dedupe_hash" in body:
            trigger.failure_policy = {
                **dict(trigger.failure_policy or {}),
                "dedupe_hash": dedupe_hash,
            }
        for field, value in outcome_patch.items():
            setattr(trigger, field, value)
        store.upsert(trigger)
        _arm_if_needed(store, raw_id)
        row = store.get(raw_id)

    state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:schedule:{raw_id}:{name}",
    )
    projected = _schedule_row_for(state, row.trigger) if row is not None else {}
    return web.json_response({"ok": True, "trigger": projected})


async def api_trigger_detail(request: web.Request) -> web.Response:
    """PUT / DELETE /api/triggers/{id}."""
    state: ConsoleState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])

    if request.method == "DELETE":
        if kind == _STORE:
            store = _trigger_store()
            if store.get(raw) is None:
                return web.json_response({"error": "not found"}, status=404)
            store.delete(raw)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:store:{raw}",
            )
            return web.json_response({"ok": True})
        if kind == _EVENT:
            if not _event_store().delete(raw):
                return web.json_response({"error": "not found"}, status=404)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:event:{raw}",
            )
            return web.json_response({"ok": True})
        if kind == _LIFECYCLE:
            store = _hook_store(state)
            hook = store.get(raw)
            if not store.delete(raw):
                return web.json_response({"error": "not found"}, status=404)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:lifecycle:{raw}:{hook.name if hook else 'unknown'}",
            )
            return web.json_response({"ok": True})
        # `ExecutionJournal` (keyed by a plain id, so it survives the cutover unchanged), so the
        store = _trigger_store()
        if store.get(raw) is None:
            return web.json_response({"error": "not found"}, status=404)
        store.delete(raw)
        try:
            await _runs_store().delete_for_job(raw)
        except Exception:
            logger.debug("Failed to delete run history for %s", raw, exc_info=True)
        state.push_refresh("crons")
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation="trigger.delete",
            outcome="success",
            source="dashboard",
            resources=f"trigger:schedule:{raw}",
        )
        return web.json_response({"ok": True})

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    if kind == _EVENT:
        return _update_event(raw, body)
    if kind == _LIFECYCLE:
        return await _update_lifecycle(state, raw, body)
    return await _update_schedule(state, raw, body, store_response=kind == _STORE)


def _update_event(raw: str, body: dict) -> web.Response:
    """PUT an ``event`` trigger (S67 parity).

    Measured before writing: every field a caller could send returned 400 "no fields to update" or
    404 "not found" and wrote NOTHING — `enabled`, `pattern`, `max_fires` and `action` all silently
    failed because the PUT fell through to `_update_schedule`, which looked for a cron job with this
    id and did not find one. A user toggling an event trigger off was told it does not exist while
    it kept firing.

    `pattern` is validated against `EVENT_PATTERNS` rather than accepted: an unrecognized pattern
    matches nothing, so a typo would silently retire a working trigger — the exact failure the
    create path already guards.
    """
    from gideon.automation.event_triggers import (
        EVENT_PATTERNS,
        INBOX_SENDER,
        PATTERN_SOURCE,
    )

    store = _event_store()
    trigger = next((t for t in store.load() if t.id == raw), None)
    if trigger is None:
        return web.json_response({"error": "not found"}, status=404)

    if "pattern" in body:
        pattern = str(body.get("pattern") or "").strip()
        if pattern not in EVENT_PATTERNS:
            return web.json_response(
                {"error": f"pattern must be one of {list(EVENT_PATTERNS)}"}, status=400
            )
        trigger.pattern = pattern
        trigger.source = PATTERN_SOURCE[pattern]
    if "sender_glob" in body:
        trigger.sender_glob = str(body.get("sender_glob") or "")
    if "address_glob" in body:
        trigger.address_glob = str(body.get("address_glob") or "")
    if "event_glob" in body:
        trigger.event_glob = str(body.get("event_glob") or "")
    if trigger.pattern == INBOX_SENDER and not trigger.sender_glob:
        return web.json_response(
            {
                "error": "InboxSender requires a sender_glob",
                "code": "sender_glob_required",
            },
            status=400,
        )
    if "enabled" in body:
        trigger.enabled = bool(body["enabled"])
    if "key_glob" in body:
        trigger.key_glob = str(body.get("key_glob") or "")
    if "content_re" in body:
        trigger.content_re = str(body.get("content_re") or "")
    if "max_fires" in body:
        try:
            trigger.max_fires = max(0, int(body.get("max_fires") or 0))
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "max_fires must be an integer"}, status=400
            )
    if "debounce_secs" in body:
        try:
            trigger.debounce_secs = max(0.0, float(body.get("debounce_secs") or 0.0))
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "debounce_secs must be a number"}, status=400
            )
    if isinstance(body.get("action"), dict):
        action = body["action"]
        if action.get("provider"):
            trigger.action_provider = str(action["provider"])
        if "config" in action:
            trigger.action_config = dict(action["config"] or {})

    store.upsert(trigger)
    result: dict[str, Any] = {"ok": True, "trigger": _serialize_event(trigger)}
    hint = _regex_hint(trigger.content_re)
    if hint:
        result["warning"] = hint
    return web.json_response(result)


async def _update_lifecycle(state: ConsoleState, raw: str, body: dict) -> web.Response:
    from gideon.assurance.validation import (
        HOOK_UPDATE_SCHEMA,
        ValidationError,
        validate_tool_args,
    )

    patch: dict[str, Any] = {}
    for k in ("name", "event", "matcher", "timeout", "enabled"):
        if k in body:
            patch[k] = body[k]
    if "action" in body and isinstance(body["action"], dict):
        if body["action"].get("provider"):
            patch["provider"] = body["action"]["provider"]
        if "config" in body["action"]:
            patch["provider_config"] = body["action"]["config"] or {}
    try:
        validated = validate_tool_args(patch, HOOK_UPDATE_SCHEMA)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    try:
        hook = _hook_store(state).update(raw, validated)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(
        {
            "ok": True,
            "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, [])),
        }
    )


async def _update_schedule(
    state: ConsoleState, raw: str, body: dict, *, store_response: bool = False
) -> web.Response:
    from zoneinfo import available_timezones

    from gideon.assurance.validation import CHANNEL_ID_RE, CHANNEL_MAX_LEN

    kwargs: dict[str, Any] = {}
    for key in (
        "name",
        "channel",
        "silent",
        "strict_schedule",
        "failure_delivery",
        "dedupe_hash",
        "delivery",
        "failure_policy",
    ):
        if key in body:
            kwargs[key] = body[key]
    if "dedupe_hash" in kwargs and not isinstance(kwargs["dedupe_hash"], bool):
        return json_error(
            "invalid_request", message="'dedupe_hash' must be a boolean", status=400
        )
    if "action" in body and isinstance(body["action"], dict):
        kwargs["action"] = body["action"]
    if "channel" in kwargs:
        ch = (kwargs["channel"] or "").strip() or None
        kwargs["channel"] = ch
        if ch and (len(ch) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(ch)):
            return web.json_response({"error": "invalid channel ID format"}, status=400)
    if "cron" in body:
        kwargs["cron_expr"] = body["cron"]
    if "every" in body:
        kwargs["every_secs"] = body["every"]
    if "timezone" in body:
        tz_val = (body["timezone"] or "").strip()
        if tz_val and tz_val not in available_timezones():
            return web.json_response(
                {"error": f"invalid timezone: {_redact(tz_val)!r}"}, status=400
            )
        kwargs["timezone"] = tz_val
    if isinstance(body.get("skip_dates"), list):
        kwargs["skip_dates"] = [str(d) for d in body["skip_dates"]]
    if not kwargs:
        return web.json_response({"error": "no fields to update"}, status=400)

    store = _trigger_store()
    row = store.get(raw)
    if row is not None:
        from gideon.automation.triggers import tools as _tools
        from gideon.automation.triggers.schedule_view import channel_of

        spec = dict(row.trigger.spec or {})
        outcome_patch, outcome_error = _outcome_control_patch(body, row.trigger)
        if outcome_error:
            return web.json_response({"error": outcome_error}, status=400)
        cadence_changed = False
        if "cron_expr" in kwargs and kwargs["cron_expr"]:
            spec = {
                "kind": "cron",
                "expr": str(kwargs["cron_expr"]).strip(),
                **_carried(spec),
            }
            cadence_changed = True
        elif "every_secs" in kwargs and kwargs["every_secs"]:
            spec = {
                "kind": "interval",
                "interval_secs": int(kwargs["every_secs"]),
                **_carried(spec),
            }
            cadence_changed = True
        if "timezone" in kwargs:
            spec["timezone"] = kwargs["timezone"]
            cadence_changed = True
        if "strict_schedule" in kwargs:
            spec["strict"] = bool(kwargs["strict_schedule"])
        if "skip_dates" in kwargs:
            spec["skip_dates"] = kwargs["skip_dates"]
            cadence_changed = True

        patch: dict[str, Any] = {"spec": spec}
        if "name" in kwargs:
            patch["name"] = str(kwargs["name"])
        if "action" in kwargs and isinstance(kwargs["action"], dict):
            patch["workflow"] = {"inline": kwargs["action"]}
        if "channel" in kwargs or "silent" in kwargs:
            silent = bool(kwargs.get("silent", row.trigger.delivery == "none"))
            channel_id = kwargs.get("channel", channel_of(row.trigger))
            patch["delivery"] = (
                "none" if silent else (f"channel:{channel_id}" if channel_id else "")
            )
        if "failure_delivery" in kwargs:
            patch["failure_delivery"] = str(kwargs["failure_delivery"] or "")
        for field in ("delivery", "failure_delivery"):
            if field in outcome_patch:
                patch[field] = outcome_patch[field]

        result = _tools.update(store, trigger_id=raw, patch=patch)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        if "dedupe_hash" in kwargs:
            updated = store.get(raw).trigger
            updated.failure_policy = {
                **dict(updated.failure_policy or {}),
                "dedupe_hash": kwargs["dedupe_hash"],
            }
            store.upsert(updated)
        if cadence_changed:
            updated = store.get(raw).trigger
            updated.next_fire_at = ""
            store.upsert(updated)
            _arm_if_needed(store, raw)
        if "failure_policy" in outcome_patch:
            updated = store.get(raw).trigger
            updated.failure_policy = outcome_patch["failure_policy"]
            store.upsert(updated)
        state.push_refresh("crons")
        updated_row = store.get(raw)
        if store_response:
            from gideon.automation.triggers.ownership import owner_username

            projected = _serialize_store(
                updated_row.trigger,
                broken=[issue.message for issue in updated_row.errors],
                owner=owner_username(),
            )
        else:
            projected = _schedule_row_for(state, updated_row.trigger)
        return web.json_response({"ok": True, "trigger": projected})

    return web.json_response({"error": "not found"}, status=404)


def _carried(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec keys that survive a CADENCE change (S101).

    Replacing `{kind, expr}` wholesale would silently drop `timezone`/`skip_dates`/`strict` — the
    quietly-losable class §1.3 warns about, and the exact fields S91's `verify-migration` exists to
    catch going missing. A user changing `0 9 * * *` to `0 10 * * *` must not lose their holidays.
    """
    return {k: v for k, v in spec.items() if k in ("timezone", "skip_dates", "strict")}


async def api_trigger_toggle(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/toggle — enable/disable."""
    state: ConsoleState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind == _STORE:
        from gideon.automation.triggers import tools as T

        store = _trigger_store()
        row = store.get(raw)
        if row is None:
            return web.json_response({"error": "not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        want = body.get("enabled") if isinstance(body, dict) else None
        paused = row.trigger.enabled if want is None else (not bool(want))
        result = T.set_paused(store, trigger_id=raw, paused=paused)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        return web.json_response(
            {"ok": True, "trigger": _serialize_store(store.get(raw).trigger)}
        )
    if kind == _LIFECYCLE:
        hook = _hook_store(state).toggle(raw)
        if not hook:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {
                "ok": True,
                "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, [])),
            }
        )
    if kind == _EVENT:
        store = _event_store()
        trigger = next((t for t in store.load() if t.id == raw), None)
        if trigger is None:
            return web.json_response({"error": "not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        want = body.get("enabled") if isinstance(body, dict) else None
        trigger.enabled = (not trigger.enabled) if want is None else bool(want)
        if (
            trigger.enabled
            and trigger.max_fires
            and trigger.fire_count >= trigger.max_fires
        ):
            trigger.fire_count = 0
        store.upsert(trigger)
        return web.json_response({"ok": True, "trigger": _serialize_event(trigger)})
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = body.get("enabled")
    store = _trigger_store()
    row = store.get(raw)
    if row is not None:
        from gideon.automation.triggers import tools as _tools

        want = (not row.trigger.enabled) if enabled is None else bool(enabled)
        result = _tools.set_paused(store, trigger_id=raw, paused=not want)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        if want:
            _arm_if_needed(store, raw)
        state.push_refresh("crons")
        return web.json_response({"ok": True})
    return web.json_response({"error": "not found"}, status=404)


async def api_trigger_run(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/run — fire now.

    Schedule triggers run via the schedule service (non-blocking). This is also
    the path the ``schedule_trigger`` MCP tool posts to with the internal secret.
    Lifecycle triggers have no standalone "run" (they fire on agent events) — use
    the test endpoint instead.

    ``?dry_run=1`` (or JSON ``{"dry_run": true}``) runs a **dry-run replay** (T9):
    write-capable tools don't execute, so it previews what the trigger's current
    action WOULD do with no side effects — tagged ``trigger="replay"`` in history.

    Reads no `state` at all since S110 — the clearest evidence the manual-run path is fully
    store-backed.
    """
    kind, raw = _split_id(request.match_info["id"])
    if kind == _STORE:
        return await _run_store(raw, request)
    if kind == _LIFECYCLE:
        return web.json_response(
            {"error": "lifecycle triggers fire on events; use /test"}, status=400
        )
    if kind == _EVENT:
        return await _run_event(raw, request)
    store = _trigger_store()
    if store.get(raw) is not None:
        from gideon.automation.triggers import claims as _claims

        if _claims.is_running(raw, base_dir=store.base_dir):
            return web.json_response(
                {"error": "already running", "running": True}, status=409
            )
        return await _run_store(raw, request)

    return web.json_response({"error": "not found"}, status=404)


_NO_STORE = {"Cache-Control": "no-store"}

_WEBHOOK_SURFACE = "webhook"


async def api_trigger_fire(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/fire — fire a `webhook` trigger from an EXTERNAL caller (WF2AUT-12).

    The external twin of `/run`. `/run` is the OWNER's dashboard-authenticated "fire now" button;
    `/fire` admits an OUTSIDE caller that presents a per-client **scoped** bearer token, fences the
    inbound body as untrusted data, and dispatches the trigger's action fire-and-forget.

    The gate, in order — the inbound-surface discipline `inbound/mcp_http.py` follows:

    1. **incident kill switch** (`gate.incident_problem` → 503): an active incident suspends all
       unattended inbound. The surface-mount switches (master/per-surface) do NOT apply: this is an
       always-registered dashboard route, not one of the five `EXTERNAL_ACCESS_SURFACES`, and the
       per-integration on/off switch is the scoped client's own `disabled` flag (enforced inside
       `lookup_by_token`) plus revocation.
    2. **token → client** (`clients.lookup_by_token` → 401): verifies the bearer against the
       SHA-256-hash registry, honouring the client's `disabled` flag and its `"webhook"` surface
       binding. There is deliberately NO surface-token fallback (unlike `/mcp`): the Done-when
       requires a *scoped* token, which an un-scoped operator token is not.
    3. **scope pin** (→ 403 + SEL): the client must be pinned to THIS trigger
       (`scope.trigger == <id>`). `check_bindings` refuses a DISAGREEING pin; the explicit equality
       below also refuses an ABSENT pin, so a scope-less client cannot fire an arbitrary webhook
       (fail-closed). A violation is a security event — logged and audited, never a silent
       substitution.
    4. **rate cap** (→ 429): per client, so one noisy integration cannot starve another.
    5. **resolve** (→ 404): only a `webhook`-kind store trigger is fireable here; an unknown id or a
       non-webhook kind answers 404 rather than confirming a non-webhook trigger's existence. Done
       AFTER auth+scope, so a misscoped caller learns nothing about which triggers exist.
    6. **fence + fire**: the raw body is capped and fenced (`framing.fence_payload`) so it reaches
       the agent as data and never instructions, then the action is dispatched fire-and-forget (202)
       — a webhook sender must not block on an LLM turn (the `view`-render idiom).

    Network reachability (loopback vs remote) is governed by the dashboard server's own binding and
    by the deferred owner E4 remote-exposure decision, not by this handler; the scoped bearer is the
    admission gate wherever the route is reachable.
    """
    from gideon.integrations.inbound import audit as audit_mod
    from gideon.integrations.inbound import caps as caps_mod
    from gideon.integrations.inbound import clients as clients_mod
    from gideon.integrations.inbound import framing
    from gideon.integrations.inbound.gate import incident_problem

    trigger_id = request.match_info["id"]
    route = "POST /api/triggers/{id}/fire"

    incident = incident_problem()
    if incident:
        audit_mod.audit(_WEBHOOK_SURFACE, route=route, status=503, refused=incident)
        return json_error("service_unavailable", status=503, headers=_NO_STORE)

    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :].strip()
    client, reason = clients_mod.lookup_by_token(presented, _WEBHOOK_SURFACE)
    if client is None:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=401,
            refused=reason or "bad or missing bearer token",
        )
        return json_error("unauthorized", status=401, headers=_NO_STORE)
    client_id = client.client_id

    violation = clients_mod.check_bindings(client, {"scope": {"trigger": trigger_id}})
    if not violation and str(client.scope.get("trigger", "")) != trigger_id:
        violation = (
            f"client {client_id} is not scoped to trigger {trigger_id!r} "
            f"(scope.trigger={str(client.scope.get('trigger', ''))!r})"
        )
    if violation:
        clients_mod.log_binding_violation(client_id, violation)
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=403,
            refused=violation,
            client_id=client_id,
        )
        return json_error(
            "forbidden",
            status=403,
            headers=_NO_STORE,
            error_extra={"detail": "request conflicts with a client binding"},
        )

    caps = caps_mod.caps_for(client)
    peer_fallback = request.headers.get("Host", "") + "|" + (request.remote or "")
    if not caps_mod.check_rate_for_client(
        _WEBHOOK_SURFACE, client_id, peer_fallback, caps
    ):
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=429,
            refused="rate limit",
            client_id=client_id,
            rate_limited=True,
        )
        return json_error(
            "rate_limited",
            status=429,
            headers={
                **_NO_STORE,
                "Retry-After": str(
                    caps_mod.retry_after_for_client(
                        _WEBHOOK_SURFACE, client_id, peer_fallback, caps
                    )
                ),
            },
        )
    clients_mod.touch_last_seen(client_id)

    kind, raw = _split_id(trigger_id)
    store = _trigger_store()
    row = store.get(raw) if kind == _STORE else None
    if row is None or row.trigger.kind != "webhook":
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=404,
            refused="unknown or non-webhook trigger",
            client_id=client_id,
        )
        return json_error("not_found", status=404, headers=_NO_STORE)

    declared = request.content_length or 0
    if declared > caps.body_bytes:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=413,
            refused="body cap (declared)",
            client_id=client_id,
        )
        return json_error("request_too_large", status=413, headers=_NO_STORE)
    body_bytes = await request.content.read(caps.body_bytes + 1)
    if len(body_bytes) > caps.body_bytes:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=413,
            refused="body cap",
            client_id=client_id,
        )
        return json_error("request_too_large", status=413, headers=_NO_STORE)

    fenced = framing.fence_payload(
        body_bytes.decode("utf-8", errors="replace"),
        surface=_WEBHOOK_SURFACE,
        client_id=client_id,
        detail=raw,
        caps=caps,
    )
    payload = {"trigger_id": raw, "body": fenced, "source": "webhook.fire"}

    state: ConsoleState = request.app["state"]
    task = asyncio.create_task(
        _dispatch_store_action(row.trigger, payload, event="webhook.fire")
    )
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    audit_mod.audit(
        _WEBHOOK_SURFACE,
        route=route,
        status=202,
        bytes_in=len(body_bytes),
        client_id=client_id,
    )
    return web.json_response(
        {"ok": True, "accepted": True, "trigger": row.trigger.id},
        status=202,
        headers=_NO_STORE,
    )


async def _run_store(raw: str, request: web.Request) -> web.Response:
    """Fire one store-backed trigger (file/web_watch/idle/…) by hand.

    A `dry_run` reports S92's gate plan (which gates a manual fire enforces vs bypasses) without
    executing — that reuses `tools.run`, so the API and the chat tool answer identically. A real
    run dispatches the trigger's declared action through the SAME action-provider registry the
    live file-watch path (`_fire_file_trigger`) uses, so a Run button and an autonomous fire
    execute the same action the same way.

    Manual runs bypass quiet-hours + duty limits but never the injection screen, capability
    allowlist, or budget — the boundary `tools.MANUAL_NEVER_BYPASSES` pins.
    """
    from gideon.automation.triggers import tools as T

    store = _trigger_store()
    row = store.get(raw)
    if row is None:
        return web.json_response({"error": "not found"}, status=404)

    dry_run = request.query.get("dry_run", "") in ("1", "true", "yes")
    if not dry_run:
        try:
            body = await request.json()
            dry_run = (
                bool(body.get("dry_run", False)) if isinstance(body, dict) else False
            )
        except Exception:
            dry_run = False

    if dry_run:
        result = T.run(store, trigger_id=raw, dry_run=True)
        return web.json_response(
            {"ok": result.ok, "result": result.data, "text": result.text}
        )

    if row.errors:
        return web.json_response(
            {
                "error": f"{raw} has a parse error and cannot run ({row.errors[0].message})"
            },
            status=400,
        )
    refusal = T.manual_refusal()
    if refusal:
        return web.json_response(
            {"ok": False, "name": row.trigger.name, "refused": refusal}
        )
    ran, note = await _dispatch_store_action(
        row.trigger, {"trigger_id": raw, "manual": True}
    )
    paused_note = (
        "" if row.trigger.enabled else " (paused — this run does not re-enable it)"
    )
    return web.json_response(
        {"ok": ran, "name": row.trigger.name, "result": note + paused_note}
    )


async def _dispatch_store_action(
    trigger: Any, payload: dict[str, Any], *, event: str = "manual.run"
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

    from gideon.integrations.action_providers import ActionContext, get_action_provider
    from gideon.integrations.action_providers.registry import (
        _ensure_default_providers_registered,
    )

    workflow = trigger.workflow or {}
    inline = (
        workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    )
    action = inline or workflow
    provider_name = str(action.get("provider") or "")
    if not provider_name:
        return False, "no action provider configured"
    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    if provider is None:
        return False, f"unknown action provider {provider_name!r}"
    # recorded NOTHING — no `ExecutionJournal` row, no `last_run_ts` stamp. So the action ran while
    # `_record_manual_run` reuses the SAME `ExecutionJournal` ledger and the SAME
    ctx = ActionContext(event=event, context="", payload=payload)
    started = time.time()
    try:
        result = await provider.execute(action.get("config") or {}, ctx)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a failed manual run is RECORDED, not raised (#308)
        await _record_manual_run(trigger, started=started, exc=exc)
        return False, f"failed: {type(exc).__name__}: {exc}"
    await _record_manual_run(trigger, started=started, result=result)
    if result is not None and not bool(getattr(result, "success", True)):
        note = str(getattr(result, "error", "") or "") or "the action reported failure"
        return False, f"failed: {note}"
    return True, "ran"


async def _record_manual_run(
    trigger: Any,
    *,
    started: float,
    result: Any = None,
    exc: BaseException | None = None,
) -> None:
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
    try:
        import time
        from datetime import datetime, timezone

        from gideon.automation.schedule_history import ExecutionRecord

        trigger_id = str(getattr(trigger, "id", "") or "")
        if not trigger_id:
            return
        finished = time.time()

        if exc is not None:
            status = "failure"
            error = f"{type(exc).__name__}: {exc}"
            summary = error
        elif result is not None and not bool(getattr(result, "success", True)):
            status = "failure"
            error = (
                str(getattr(result, "error", "") or "") or "the action reported failure"
            )
            summary = error
        elif result is not None and str(getattr(result, "outcome", "") or "") in (
            "launched",
            "queued",
        ):
            status = str(getattr(result, "outcome", "") or "")
            error = ""
            summary = str(getattr(result, "stdout", "") or "")
        else:
            status = "success"
            error = ""
            summary = (
                str(getattr(result, "stdout", "") or "") if result is not None else ""
            )

        run_id = f"manual-{int(finished * 1000)}"
        await _runs_store().append(
            ExecutionRecord(
                run_id=run_id,
                job_id=trigger_id,
                trigger="manual",
                started_at=started,
                finished_at=finished,
                duration_ms=int(max(0.0, finished - started) * 1000),
                status=status,
                summary=summary,
                trace=summary,
                error=error,
            )
        )

        store = _trigger_store()
        row = store.get(trigger_id)
        if row is None:
            return
        live = row.trigger
        live.last_run_id = run_id
        stamp = datetime.now(timezone.utc).isoformat()
        if status == "failure":
            live.last_failure_at = stamp
            live.last_error_summary = (error or "manual run failed")[:200]
        else:
            live.last_success_at = stamp
        store.upsert(live)
    except (
        Exception
    ):  # noqa: BLE001 - see the docstring: recording must never fail the run
        logger.debug("could not record the manual run for %s", trigger, exc_info=True)


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

    state: ConsoleState = request.app["state"]
    try:
        body = await request.json()
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
    """Fire one event trigger by hand, through the SAME executor the live path uses.

    A manual fire does NOT call `record_fire`. The fire budget (`max_fires`) exists to bound
    UNATTENDED firing — spending it from a Run button would let a user exhaust and self-retire their
    own trigger by testing it, which is the same asymmetry S65 established for the hourly cap
    (`within_rate_window(manual=True)`). Debounce is skipped for the same reason: it protects
    against event storms, and a person clicking Run is not a storm.
    """
    from gideon.assurance.validation import sanitize_string
    from gideon.automation.event_triggers import execute_event_action

    store = _event_store()
    trigger = next((t for t in store.load() if t.id == raw), None)
    if trigger is None:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    key = sanitize_string(str(body.get("key", "") or "manual"))[:500]
    value = sanitize_string(str(body.get("value", "") or "manual fire"))[:10000]
    raw_meta = body.get("meta")
    meta = None
    if isinstance(raw_meta, dict):
        meta = {str(k): sanitize_string(str(v))[:500] for k, v in raw_meta.items()}

    outcome = await execute_event_action(
        trigger,
        source=trigger.source,
        event_type=str(body.get("event_type", "") or "MemoryUpdate"),
        key=key,
        value=value,
        meta=meta,
        test=bool(body.get("test")),
    )
    payload = outcome.to_dict()
    for field_name in ("stdout", "stderr", "error"):
        if payload.get(field_name):
            payload[field_name] = _redact(str(payload[field_name]))
    if payload.get("reason"):
        payload["reason"] = _redact(str(payload["reason"]))
    return web.json_response({"ok": outcome.ran, "result": payload})


async def api_trigger_test(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/test — execute a lifecycle or event trigger's action once."""
    from gideon.assurance.validation import sanitize_string
    from gideon.engine.hooks import run_script_hook

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
        body = await request.json()
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
    out = dict(run)
    for key in ("summary", "trace", "error"):
        if out.get(key):
            out[key] = _redact(out[key])
    if job_name is not None:
        out["job_name"] = _redact(job_name)
    return out


async def api_trigger_history(request: web.Request) -> web.Response:
    """GET /api/triggers/{id}/history — run records; other kinds answer `supported: false`.

    No longer touches `state` (S105): the run records come straight from `ExecutionJournal`, so this
    handler is fully decoupled from `ScheduleService`.
    """
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

    state: ConsoleState = request.app["state"]
    raw_start = (request.query.get("start") or "").strip()
    try:
        start = datetime.fromisoformat(raw_start) if raw_start else datetime.now()
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
            until = datetime.fromisoformat(raw_until)
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

    from gideon.automation.schedule import get_local_tz

    tz_name, _ = get_local_tz()
    return web.json_response(
        {
            "start": start.isoformat(),
            "end": (until or (start + timedelta(days=days))).isoformat(),
            "server_tz": tz_name,
            "occurrences": occurrences,
            "truncated": truncated,
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
    store_rows = [row for row in store.load() if row.trigger.kind == "clock"]
    if store_rows:
        for row in store_rows:
            rows.append(
                {
                    "id": f"{_SCHEDULE}:{row.trigger.id}",
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
                "workflow": {},
                "spec": {"glob": trigger.key_glob or ""},
            }
        )

    report = diagnose(rows, known_workflows=known_workflows)
    from gideon.automation.triggers.arm import semantic_spec_issues
    from gideon.automation.triggers.calendar import Finding

    for row in store_rows:
        for issue in semantic_spec_issues(row.trigger.kind, row.trigger.spec):
            is_error = issue.severity == "error"
            report.findings.append(
                Finding(
                    trigger_id=f"{_SCHEDULE}:{row.trigger.id}",
                    code="unfireable_spec" if is_error else "inert_spec_entry",
                    detail=f"{issue.path}: {issue.message}",
                    fix=(
                        "correct the expression or date — as authored, this part of the "
                        "trigger cannot do what it says"
                        if is_error
                        else "confirm this is intended, or adjust the schedule/skip date"
                    ),
                )
            )
    return web.json_response(report.to_dict())


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
    runs, total = await _runs_store().list_all(offset, limit, raw_filter)
    names = _trigger_names(state)
    enriched = [
        _redact_run(r, job_name=names.get(r.get("job_id", ""), "")) for r in runs
    ]

    if (request.query.get("shape") or "").lower() == "legacy":
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
        limit=limit,
    )
    payload = H.feed_response(records)
    payload["schedule_total"] = total
    payload["outcomes"] = H.outcome_counts(records)
    return web.json_response(payload)


def register_trigger_routes(app: web.Application) -> None:
    """Register /api/triggers/* — the unified Trigger surface."""
    app.router.add_get("/api/triggers", api_triggers)
    app.router.add_post("/api/triggers", api_trigger_create)
    app.router.add_get("/api/triggers/variables", api_trigger_variables)
    app.router.add_get("/api/triggers/history", api_trigger_history_all)
    app.router.add_get("/api/triggers/week", api_triggers_week)
    app.router.add_get("/api/triggers/doctor", api_triggers_doctor)
    app.router.add_post("/api/triggers/view/render", api_trigger_view_render)
    app.router.add_put("/api/triggers/{id}", api_trigger_detail)
    app.router.add_delete("/api/triggers/{id}", api_trigger_detail)
    app.router.add_post("/api/triggers/{id}/toggle", api_trigger_toggle)
    app.router.add_post("/api/triggers/{id}/run", api_trigger_run)
    app.router.add_post("/api/triggers/{id}/fire", api_trigger_fire)
    app.router.add_post("/api/triggers/{id}/test", api_trigger_test)
    app.router.add_post("/api/triggers/{id}/to-chat", api_trigger_to_chat)
    app.router.add_get("/api/triggers/{id}/history", api_trigger_history)
    app.router.add_get(
        "/api/triggers/{id}/history/{run_id}", api_trigger_history_detail
    )
