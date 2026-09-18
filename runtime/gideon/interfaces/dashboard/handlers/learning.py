"""Learning routes — the Proposal Inbox and the staging week panel (§6.1 / §7 — S78).

GET    /api/learning/proposals            the inbox: six kinds, ordered, filterable
GET    /api/learning/proposals/{id}       one proposal, full record
POST   /api/learning/proposals/{id}/accept   install (human reviewers only)
DELETE /api/learning/proposals/{id}       reject (human reviewers only)
GET    /api/learning/staging/week         the week-at-a-glance capture panel
GET    /api/learning/health               the flywheel observability panel (LEARN-R14b)
GET    /api/learning/summary              the learning summary block (LV-3)
GET    /api/learning/identity-report      the periodic identity report, deterministic (LV-4)
POST   /api/learning/identity-report      compose + narrate + persist the artifact + surface it

The plan's success criterion 1 says "One Proposal Inbox SHOWS all six proposal kinds with
provenance,
evidence manifests, and risk-tier metadata; accept installs, reject dismisses — and the model cannot
accept its own proposals under any trust mode". Everything behind that sentence shipped in S75/S76
and
had no HTTP surface, so the criterion was unmet for want of a route: `inbox.build_view` and
`StagingStore.week` both return fully-serialized shapes, and this module wires them.

**The actor is load-bearing.** S75 measured that `proposals.accept()` knew nothing about who
was calling it, and put `require_human` inside it. A route that omitted the actor would default to
`user` and hand every caller — including an app-scoped token — the reviewer's authority. So the
actor is
DERIVED from the request rather than accepted from the body: a caller that could name itself `user`
would make the gate decorative.
"""

from __future__ import annotations

import logging

from aiohttp import web

from gideon.http_errors import json_error

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    """The `learning.enabled` kill-switch. Every route 404s when learning is off.

    404 rather than 403: with learning disabled there is no inbox, and reporting "forbidden" would
    imply one exists behind a permission wall. Matches the feedback module's rail.
    """
    try:
        from gideon.core.config.loader import AppConfig

        return bool(AppConfig.load().learning.enabled)
    except Exception:
        logger.debug(
            "learning config unreadable; treating the surface as enabled", exc_info=True
        )
        return True


def _actor(request: web.Request) -> str:
    """Who is making this request, for S75's accept gate.

    DERIVED, never read from the body. An app-scoped token is an `agent`: an installed app acting
    through the API is exactly the "worker whose self-report needs checking" case §7 names, and
    letting
    one accept its own proposals would reproduce the hole S75 closed one layer up.

    A dashboard session is `user` — that is a human at the UI, which is the only actor the gate
    permits. Anything unrecognized returns `""`, which `require_human` denies rather than assuming
    human.
    """
    if request.get("app"):
        return "agent"
    if request.get("user"):
        return "user"
    return ""


def _memory_service(request: web.Request):
    """The self-model store, or ``None`` when none is reachable."""
    try:
        from gideon.interfaces.dashboard.handlers.memory import _get_service

        return _get_service(request.app["state"])
    except Exception:
        logger.debug("accept installer: no memory service", exc_info=True)
        return None


def _install_context(request: web.Request):
    """The context the registry's installers run against, for ONE accept.

    Carries the memory service (the ``lesson_batch`` row projects a self-model
    principle into it) and collects what each installer wrote, which is how this route
    still reports an applied ``template_diff`` version now that the apply runs INSIDE
    the accept instead of after it.
    """
    from gideon.cognition.learning.installers import InstallContext

    return InstallContext(memory_service=_memory_service(request))


def _installer_for(request: web.Request, context=None):
    """The accept-time installer: the ONE registry row for the proposal's kind.

    `proposals.accept` runs it AFTER `require_human` — so this is the human
    installing, the one path §2.6 permits to write a self-model principle or a
    project-context change live. What runs for each kind is declared in
    :mod:`gideon.cognition.learning.installers`, not dispatched here: this module used
    to hold an ``elif`` chain whose final fall-through installed NOTHING and still let
    the queue record ``accepted``, so a kind no branch claimed (``tier_migration``)
    looked accepted from every surface while nothing happened. A kind the registry
    declares unsupported now refuses, and the row stays pending and retryable.
    """
    from gideon.cognition.learning.installers import installer_for

    return installer_for(context if context is not None else _install_context(request))


def _tier_for(prop) -> str:
    """The risk tier for one proposal, or `""`.

    Only a `template_diff` carries typed ops to derive a tier from, so anything else is left
    unscored
    rather than stamped with a meaningless one — S75's projection defaults those to `review`, and a
    fabricated `low` would hand a lesson bulk-accept eligibility nobody computed.
    """
    from gideon.cognition.learning.refiner import risk_tier

    if str(getattr(prop, "kind", "")) != "template_diff":
        return ""
    manifest = getattr(prop, "change_manifest", None)
    ops = getattr(manifest, "targeted_fix", None) if manifest is not None else None
    if isinstance(ops, list):
        return risk_tier([o for o in ops if isinstance(o, dict)])
    return ""


async def api_learning_proposals(request: web.Request) -> web.Response:
    """GET /api/learning/proposals — the inbox across all six kinds.

    `?kind=` / `?tier=` / `?flagged=1` map onto `inbox.filter_rows`. Counts ride the response so a
    filter chip can render its number without a second request — a chip a user must click to
    discover
    is empty is worse than no chip.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning import proposals as store
    from gideon.cognition.learning.inbox import build_view

    kind = (request.query.get("kind") or "").strip()
    tier = (request.query.get("tier") or "").strip()
    flagged = request.query.get("flagged", "") in ("1", "true", "yes")

    try:
        pending = store.list_pending()
    except Exception:
        logger.warning("proposal listing failed", exc_info=True)
        pending = []

    tiers = {str(getattr(p, "id", "")): _tier_for(p) for p in pending}
    view = build_view(pending, tiers=tiers, kind=kind, tier=tier, flagged_only=flagged)
    return web.json_response(view.to_dict())


async def api_learning_proposal(request: web.Request) -> web.Response:
    """GET /api/learning/proposals/{id} — one full record.

    The inbox row is a summary; the detail view needs the body and the change manifest a reviewer
    actually reads before accepting.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning import proposals as store

    prop = store.get(request.match_info.get("id", ""))
    if prop is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(prop.to_dict())


async def api_learning_proposal_accept(request: web.Request) -> web.Response:
    """POST /api/learning/proposals/{id}/accept — install it.

    The actor is derived from the request and passed through to `proposals.accept`, which runs S75's
    `require_human`. A refusal is **403 with the gate's reason**, not a generic error: a reviewer
    who
    hits this needs to know an agent-scoped token cannot accept, and a bare 500 would send them
    looking for a bug.

    There is deliberately no `?force=` or trust override. §7: "under ANY trust mode".

    An accept the INSTALL step refuses — a kind the installer registry declares
    unsupported, or a writer that failed — is **409 with the structured `refusal`**,
    and the row stays pending: nothing was installed, so nothing is recorded, and the
    reviewer can accept it again once the writer exists or the failure is fixed.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning import proposals as store

    pid = request.match_info.get("id", "")
    actor = _actor(request)
    context = _install_context(request)
    try:
        prop = store.accept(
            pid, actor=actor, installer=_installer_for(request, context)
        )
    except store.AcceptError as exc:
        message = str(exc)
        refusal = dict(getattr(exc, "refusal", {}) or {})
        status = 409 if refusal else (404 if message.startswith("no proposal") else 403)
        _audit(request, "learning.proposal_accept", "rejected", f"{pid}:{message}")
        body: dict[str, object] = {"error": message}
        if refusal:
            body["refusal"] = refusal
        return web.json_response(body, status=status)
    applied = context.result.get("applied")
    _audit(request, "learning.proposal_accept", "ok", f"{pid}:{prop.kind}")
    return web.json_response(
        {"ok": True, "proposal": prop.to_dict(), "applied": applied}
    )


async def api_learning_proposal_reject(request: web.Request) -> web.Response:
    """DELETE /api/learning/proposals/{id} — dismiss it, and REMEMBER the decision.

    Gated on the same actor for the reason S75 recorded: an agent that could reject would clear its
    own
    bad proposals before a human read them, and the rejection exemplars the flywheel learns from
    would
    stop accumulating.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning import proposals as store

    pid = request.match_info.get("id", "")
    actor = _actor(request)
    if store.get(pid) is None:
        return web.json_response({"error": "not found"}, status=404)
    ok = store.reject(pid, actor=actor)
    _audit(request, "learning.proposal_reject", "ok" if ok else "rejected", pid)
    if not ok:
        return web.json_response(
            {"error": "only a human reviewer may reject proposals"}, status=403
        )
    return web.json_response({"ok": True})


async def api_learning_staging_week(request: web.Request) -> web.Response:
    """GET /api/learning/staging/week — the week-at-a-glance capture panel.

    `?days=` bounds the window (1-31). The panel's point is that an EMPTY day renders as a gap:
    `health()` aggregates and cannot see a day where capture never ran, which is the failure the
    staging tier exists to expose.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning.staging import StagingStore

    try:
        days = max(1, min(int(request.query.get("days", "7")), 31))
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)

    store = StagingStore()
    try:
        return web.json_response(store.week(days=days))
    finally:
        store.close()


_CALIBRATION_RUNS = 50


def _precision_from_events(days: int) -> tuple[float | None, int, int]:
    """Surfaced-vs-used precision over `surfacing_events`. `(precision, surfaced, used)`.

    **This read used to come from `usage.UsageStore`**, on the stated grounds that the per-arm
    report "needs a `surfacing_events` table that nothing writes yet" while "the surfaced/used
    counters, by contrast, have a live writer per session flush". The first half was true and is
    now fixed — LEARN-R4's table exists and `allocate_skills` writes it. The second half was
    NOT true: `UsageStore.record` has no production caller at all, so this function returned
    `(None, 0, 0)` on every box, and the panel's surfacing row rendered the empty state the old
    docstring was written to avoid. Reading the table that is actually written is the fix.

    Aggregated across arms because the response's three fields are totals; the per-`(kind, arm)`
    breakdown is `measure.per_arm_precision`'s return value, which this discards deliberately
    rather than publishing a field no surface reads yet.

    Windowed on the panel's own `days` so the number answers the question the page asks. A
    lifetime ratio beside windowed capture/utilization figures would invite a comparison across
    two different periods.
    """
    from gideon.cognition.learning import measure
    from gideon.cognition.learning.surfacing_events import SurfacingEventStore

    store = SurfacingEventStore()
    try:
        events = [e.to_dict() for e in store.read(days=days)]
    finally:
        store.close()
    arms = measure.per_arm_precision(events)
    surfaced = sum(a.surfaced for a in arms)
    used = sum(a.used for a in arms)
    return ((used / surfaced) if surfaced else None), surfaced, used


def _judge_calibration() -> dict:
    """MAE buckets + false-pass rate over recent run ledgers (LEARN-R10d)."""
    from gideon.automation.workflows import journal as journal_mod
    from gideon.automation.workflows import judge_calibration as jc
    from gideon.automation.workflows import store as run_store

    verdicts: list = []
    divergences: list = []
    runs, _total = run_store.list_runs(limit=_CALIBRATION_RUNS)
    for run in runs:
        run_id = getattr(run, "id", "")
        if not run_id:
            continue
        entries = journal_mod.ledger(
            run_id, kinds={journal_mod.JUDGE_VERDICT, journal_mod.JUDGE_DIVERGENCE}
        )
        for rec in jc.verdicts_from_journal(entries):
            rec.run_id = rec.run_id or run_id
            verdicts.append(rec)
        for div in jc.divergences_from_journal(entries):
            div.run_id = div.run_id or run_id
            divergences.append(div)

    summary = jc.calibration_summary(verdicts, divergences)
    return {
        "runs_scanned": len(runs),
        "verdicts": len(verdicts),
        "divergences": len(divergences),
        "false_pass_rate": summary.get("false_pass_rate"),
        "nodding_gates": summary.get("nodding_gates", []),
        "mae": jc.mae_buckets(verdicts, divergences),
    }


async def api_learning_health(request: web.Request) -> web.Response:
    """GET /api/learning/health — the flywheel observability panel (LEARN-R14b).

    Everything here already had a live writer and no reader. The four additions §6.2
    names — the 0-100 composite with the 50-80% utilization band, R10d's judge MAE
    buckets, R16's attribution verdict history, R19e's per-op cost aggregates — were
    each computed or recorded somewhere and rendered nowhere.

    Every section degrades to a stated "unmeasured" rather than a zero. A panel that
    reports an un-instrumented subsystem as 0% is worse than one that says nothing:
    the user cannot tell it apart from a broken one, and the only apparent fix is to
    generate traffic.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning import measure
    from gideon.cognition.learning.staging import StagingStore

    try:
        days = max(1, min(int(request.query.get("days", "7")), 31))
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)

    store = StagingStore()
    try:
        capture = store.health(days=days)
        utilization = store.utilization(days=days)
        cost_by_op = store.cost_by_op(days=days)
        ablation = store.latest_ablation()
    finally:
        store.close()

    precision, surfaced, used = _precision_from_events(days)

    try:
        judge = _judge_calibration()
    except Exception:
        logger.warning("judge calibration read failed", exc_info=True)
        judge = {
            "runs_scanned": 0,
            "verdicts": 0,
            "divergences": 0,
            "false_pass_rate": None,
            "nodding_gates": [],
            "mae": {"buckets": [], "labelled": 0, "unlabelled": 0, "no_confidence": 0},
        }

    try:
        from gideon.cognition.learning import attribution

        attribution_rows = attribution.proposer_trust_report()
        verdict_history = [
            {"source": source, "verdict": verdict}
            for source, verdict in attribution.verdict_history()
        ]
    except Exception:
        logger.warning("attribution history read failed", exc_info=True)
        attribution_rows, verdict_history = [], []

    composite = measure.health_composite(
        precision=precision,
        capture_passes=int(capture.get("passes", 0) or 0),
        capture_errors=int(capture.get("errors", 0) or 0),
        utilization=utilization.get("mean"),
        judge_false_pass_rate=judge.get("false_pass_rate"),
    )

    return web.json_response(
        {
            "days": days,
            "composite": composite,
            "utilization": {**utilization, "ideal_band": composite["ideal_band"]},
            "capture": capture,
            "surfacing": {"surfaced": surfaced, "used": used, "precision": precision},
            "cost_by_op": cost_by_op,
            "judge": judge,
            "attribution": {"proposers": attribution_rows, "history": verdict_history},
            "ablation": ablation,
        }
    )


def _audit(request: web.Request, operation: str, outcome: str, resources: str) -> None:
    """SEL-audit a review action.

    §3 requires an audit of accepts, and `proposals.accept` already logs one. This adds the HTTP
    caller's identity, which that layer cannot see — "who accepted this" is the question an audit
    trail
    exists to answer, and the store only knows the actor CLASS.
    """
    try:
        import gideon.interfaces.dashboard.handlers as _pkg

        _pkg.sel().log_api_access(
            caller=str(request.get("app") or request.get("user") or "unknown"),
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
        )
    except Exception:
        logger.debug("learning SEL audit failed", exc_info=True)


async def api_learning_summary(request: web.Request) -> web.Response:
    """GET /api/learning/summary — the learning summary block (LV-3).

    `?days=` bounds the window (1-90, default 7). Four groups — new skills, refined
    skills, pending proposals, facts — each an exact `count` plus a bounded `names`
    sample. Read-only: composing the block writes to no learning store.

    LV-3's task row asked for this to register with plan 42's digest builder. No such
    builder exists in the tree, so the same block renders on the skills page header —
    the fallback the task row and the atom's `done_when` both sanction. A digest
    builder, when it arrives, calls `compose_learning_summary` rather than
    reimplementing the gather.

    The facts group is memory content, so a temporary session (`blocks_reads`) gets the
    summary WITHOUT it — matching `/api/lessons`, which returns an empty list for the
    same caller. The skill and proposal groups are not memory and stay visible.
    """
    if not _enabled():
        return web.json_response({"error": "learning is disabled"}, status=404)

    from gideon.cognition.learning_summary import (
        MAX_WINDOW_DAYS,
        MIN_WINDOW_DAYS,
        compose_learning_summary,
    )
    from gideon.interfaces.dashboard.handlers._shared import (
        _blocks_reads_session,
        _get_memory,
    )

    state = request.app["state"]
    try:
        days = max(
            MIN_WINDOW_DAYS, min(int(request.query.get("days", "7")), MAX_WINDOW_DAYS)
        )
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)

    vs = None
    if _blocks_reads_session(state, request):
        _audit(request, "learning.summary", "denied", resources="facts")
    else:
        try:
            mem = _get_memory(state)
            vs = getattr(mem, "vector_store", None)
        except Exception:
            logger.debug("learning summary: memory store unavailable", exc_info=True)

    return web.json_response(
        compose_learning_summary(window_days=days, vs=vs).to_payload()
    )


def _report_window(request: web.Request) -> "int | None":
    """The clamped ``?days=`` window, or None when ``days`` does not parse.

    ``None`` rather than a message string: the caller owns the envelope, and a helper that
    returned prose would tempt a second error shape onto a surface the wire-envelope census
    is already holding at a fixed flat count.

    An ABSENT ``days`` resolves from the configured cadence rather than from a constant, so the
    preview a reader opens describes the same period the scheduled job will deliver. Hardcoding
    30 made a weekly install's panel say "last 30 days" about a document its own cron writes
    over 7 — a config that changed the product without changing anything the user could see.
    """
    from gideon.cognition.learning_report import (
        MAX_WINDOW_DAYS,
        MIN_WINDOW_DAYS,
        cadence_window_days,
        configured_cadence,
    )

    raw = request.query.get("days", "")
    if not raw:
        return cadence_window_days(configured_cadence())
    try:
        return max(MIN_WINDOW_DAYS, min(int(raw), MAX_WINDOW_DAYS))
    except ValueError:
        return None


def _report_vs(request: web.Request, action: str):
    """The vector store the report reads facets and lessons from, or None.

    A temporary session (`blocks_reads`) gets the report WITHOUT them — matching
    `/api/lessons` and `/api/learning/summary`, which withhold the same content from the
    same caller. The skills and proposals sections are not memory and stay visible.
    """
    from gideon.interfaces.dashboard.handlers._shared import (
        _blocks_reads_session,
        _get_memory,
    )

    state = request.app["state"]
    if _blocks_reads_session(state, request):
        _audit(request, action, "denied", resources="facets,lessons")
        return None
    try:
        return getattr(_get_memory(state), "vector_store", None)
    except Exception:
        logger.debug("identity report: memory store unavailable", exc_info=True)
        return None


async def api_learning_identity_report(request: web.Request) -> web.Response:
    """GET /api/learning/identity-report — the deterministic report, no model call.

    The preview the Learning page renders. `narrate=False` on purpose: a GET that a panel
    issues on mount must not spend a model call, and the narrative is the one part of the
    document that is not a function of the stores. `POST` is where a narrated document is
    composed and delivered.

    Read-only end to end — composing this writes to no learning store.

    The body carries the delivery ``cadence`` beside the report, so the panel renders its own
    control without the Learning page fetching the whole config — that page's rule is that the
    backend owns every judgement it renders. Composed by
    :func:`gideon.cognition.learning_report.identity_report_payload`, NOT assembled here: this handler
    owns nothing but the JSON, and a dict built in the route makes it a second author of a shape
    that module owns (measured — it reddens `test_wire_error_envelope_census`'s `Call` pin).
    """
    if not _enabled():
        return json_error("learning_disabled", status=404)

    from gideon.cognition.learning_report import identity_report_payload

    days = _report_window(request)
    if days is None:
        return json_error(
            "bad_request", message="`days` must be an integer.", status=400
        )
    vs = _report_vs(request, "learning.identity_report")
    return web.json_response(identity_report_payload(window_days=days, vs=vs))


async def api_learning_identity_report_deliver(request: web.Request) -> web.Response:
    """POST /api/learning/identity-report — compose, narrate, persist, surface.

    The delivery path a user can reach by hand, and the SAME function a scheduled clock
    job calls: one owner, one mechanism. It writes the versioned artifact first and then
    raises one attention item, so quiet hours can only drop the notification.

    `require_human` is not applied here. Unlike `accept`, this installs nothing and writes
    no learning state — it renders things the user already owns into a document — so the
    gate would be theatre. What it does write (an artifact, an inbox row) is audited.
    """
    if not _enabled():
        return json_error("learning_disabled", status=404)

    from gideon.cognition.learning_report import deliver_identity_report

    days = _report_window(request)
    if days is None:
        return json_error(
            "bad_request", message="`days` must be an integer.", status=400
        )
    vs = _report_vs(request, "learning.identity_report_deliver")
    delivery = await deliver_identity_report(
        request.app["state"], window_days=days, vs=vs, narrate=True
    )
    _audit(
        request,
        "learning.identity_report_deliver",
        "allowed",
        f"{delivery.artifact_slug}:{delivery.artifact_version}",
    )
    return web.json_response(delivery.to_payload())


def register_learning_routes(app: web.Application) -> None:
    """Register /api/learning/* — the Proposal Inbox and the staging panel.

    Literal paths register BEFORE `/{id}` so aiohttp does not capture `staging` as a proposal
    id — the ordering landmine S67 and S70 each paid for once.
    """
    app.router.add_get("/api/learning/staging/week", api_learning_staging_week)
    app.router.add_get("/api/learning/health", api_learning_health)
    app.router.add_get("/api/learning/summary", api_learning_summary)
    app.router.add_get("/api/learning/identity-report", api_learning_identity_report)
    app.router.add_post(
        "/api/learning/identity-report", api_learning_identity_report_deliver
    )
    app.router.add_get("/api/learning/proposals", api_learning_proposals)
    app.router.add_get("/api/learning/proposals/{id}", api_learning_proposal)
    app.router.add_post(
        "/api/learning/proposals/{id}/accept", api_learning_proposal_accept
    )
    app.router.add_delete("/api/learning/proposals/{id}", api_learning_proposal_reject)
