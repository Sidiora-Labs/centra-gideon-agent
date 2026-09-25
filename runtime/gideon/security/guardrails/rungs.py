"""Rung ROUTING — what each earned-autonomy rung actually does at a dispatch seam
(AUTONOMY-GUARDRAILS §5.2).

:mod:`~gideon.security.guardrails.autonomy` is the decision layer: it holds the ladder, the
declarations, the grants and the derived track record. It shipped with **no call sites**,
which makes it a decision object nothing consults. This module is the other half — the
one the dispatch seams call — and it answers one question:

    given a provider-dispatched action, may it run right now, and what does the user see?

**The four rungs, as behaviour:**

======================  ==========================================================
``draft_only``          Do not execute. File a PROPOSAL inbox item describing what
                        the action would have done.
``one_tap``             Do not execute. File an agent-request inbox item so the
                        user can decide. (The one-tap CARD is AG-8's frontend; the
                        durable row it renders is raised here.)
``auto_with_undo``      Execute, then persist the provider's reversal handle (SEL +
                        the notification's ``meta``) and passively notify — but only
                        when there IS a handle. A notification offering an undo that
                        cannot happen is worse than silence.
``autonomous``          Execute. The SEL row the action already writes is the record.
======================  ==========================================================

**How a declaration reaches a seam with no per-action branching.** A seam holds a
provider NAME (``hook.provider`` / ``trigger.action_provider``) and nothing else. The
name→type mapping lives on the DECLARATION (``ActionTypeSpec.providers``), so
:func:`route_provider_action` is the same three lines for ``bash`` and for an
app-contributed provider nobody in core has heard of. There is no ``if key == …`` at
either seam, and adding a governed action is data.

**An UNDECLARED provider keeps its pre-ladder behaviour.** ``route`` is ``execute`` and
``governed`` is False. This is deliberate and is not a hole: the denylist, the incident
kill switch, the injection screen and the creation-time capability grant all still run —
they are the floor this ladder sits on top of. Treating every undeclared provider as
``draft_only`` would withhold every hook and trigger in the tree, which is an outage
wearing a safety control's clothes. What DOES fail closed is a *declared* key with no
registration and a *granted* rung that cannot be proven: ``resolve_rung`` answers
``draft_only`` for both.

**Two levels, tightest wins** (PLATFORM-HARDENING-FLOORS §5). Level one is the type's own
ceiling, enforced by ``resolve_rung``. Level two is
:func:`~gideon.security.guardrails.policy.rung_ceiling_for_profile`, which may only NARROW —
so an unattended run cannot reach ``autonomous`` and go silent, whatever the type's
declaration says. Neither level can widen the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from gideon.security.guardrails.autonomy import (
    RUNG_AUTO_WITH_UNDO,
    RUNG_AUTONOMOUS,
    RUNG_DRAFT_ONLY,
    RUNG_ONE_TAP,
    RUNGS,
    ActionTypeSpec,
    action_type_for_provider,
    register_action_type,
    resolve_rung,
    rung_rank,
)

logger = logging.getLogger(__name__)


ROUTE_EXECUTE = "execute"
ROUTE_EXECUTE_WITH_UNDO = "execute_with_undo"
ROUTE_ASK = "ask"
ROUTE_DRAFT = "draft"

_ROUTE_BY_RUNG: dict[str, str] = {
    RUNG_DRAFT_ONLY: ROUTE_DRAFT,
    RUNG_ONE_TAP: ROUTE_ASK,
    RUNG_AUTO_WITH_UNDO: ROUTE_EXECUTE_WITH_UNDO,
    RUNG_AUTONOMOUS: ROUTE_EXECUTE,
}

RUNG_LABELS: dict[str, str] = {
    RUNG_DRAFT_ONLY: "drafts only",
    RUNG_ONE_TAP: "asks first",
    RUNG_AUTO_WITH_UNDO: "runs with undo",
    RUNG_AUTONOMOUS: "runs on its own",
}


def rung_label(rung: str) -> str:
    """The rung in the words a USER reads, falling back to the key if it is unknown.

    The one accessor for ``RUNG_LABELS``, because "autonomous" is what the code calls a rung and
    "runs on its own" is what a person needs to know — and every sentence a user reads has to pick
    the second. Three copies of ``RUNG_LABELS.get(x, x)` had grown up in as many modules; prose
    composers call this instead. Machine-facing strings deliberately keep the key: an audit row, a
    dedup key, a ``ValueError`` for a developer, and the echo of a rung a caller supplied.
    """
    return RUNG_LABELS.get(rung, rung)


RUNG_HINTS: dict[str, str] = {
    RUNG_DRAFT_ONLY: "Never executes on its own. Files a proposal describing what it would do.",
    RUNG_ONE_TAP: "Never executes on its own. Raises a request for you to decide.",
    RUNG_AUTO_WITH_UNDO: "Executes, then tells you and keeps a handle so you can undo it.",
    RUNG_AUTONOMOUS: "Executes silently. The audit log is the record.",
}


@dataclass(frozen=True)
class RungRoute:
    """One routing decision: the rung that applies and what the seam should do with it."""

    route: str = ROUTE_EXECUTE
    key: str = ""
    rung: str = ""
    reason: str = ""

    @property
    def governed(self) -> bool:
        """Whether a declaration claimed this action at all."""
        return bool(self.key)

    @property
    def executes(self) -> bool:
        return self.route in (ROUTE_EXECUTE, ROUTE_EXECUTE_WITH_UNDO)

    @property
    def records_reversal(self) -> bool:
        return self.route == ROUTE_EXECUTE_WITH_UNDO


_PROVIDER_SPECS: tuple[ActionTypeSpec, ...] = (
    ActionTypeSpec(
        key="action.notify",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("notify", "wellbeing-reminder"),
    ),
    ActionTypeSpec(
        key="action.knowledge_read",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("knowledge-retrieve", "knowledge-health", "knowledge-gaps"),
    ),
    ActionTypeSpec(
        key="action.artifact_inspect",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("artifact_inspect",),
    ),
    ActionTypeSpec(
        key="action.selfqa_triage",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("selfqa-triage",),
    ),
    ActionTypeSpec(
        key="action.check_work",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("check-work",),
    ),
    ActionTypeSpec(
        key="action.best_of_n",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("best-of-n",),
    ),
    ActionTypeSpec(
        key="action.selfqa_evidence",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("selfqa-evidence",),
    ),
    ActionTypeSpec(
        key="action.create_task",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("create-task", "selfqa-file-finding"),
    ),
    ActionTypeSpec(
        key="action.knowledge_write",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=(
            "knowledge-persist",
            "knowledge-consolidate",
            "knowledge-propose",
            "knowledge-report",
            "knowledge-review",
            "knowledge-ideas-sync",
            "source-digest",
        ),
    ),
    ActionTypeSpec(
        key="action.artifact_write",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("artifact-update", "render-report", "identity-report"),
    ),
    ActionTypeSpec(
        key="action.digest",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("notification-digest", "usage-recap", "triage-digest"),
    ),
    ActionTypeSpec(
        key="action.self_remediation",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("self-remediation",),
    ),
    ActionTypeSpec(
        key="action.inbox_op",
        floor=RUNG_AUTO_WITH_UNDO,
        ceiling=RUNG_AUTO_WITH_UNDO,
        providers=("inbox-op",),
    ),
    ActionTypeSpec(
        key="action.spawn_turn",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=(
            "run-prompt",
            "invoke-agent",
            "run-workflow",
            "second-opinion",
            "selfqa-commit-watch",
        ),
    ),
    ActionTypeSpec(
        key="action.browse",
        floor=RUNG_ONE_TAP,
        ceiling=RUNG_ONE_TAP,
        leaves_machine=True,
        providers=("browse",),
    ),
    ActionTypeSpec(
        key="action.web_fetch",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("net-fetch",),
    ),
    ActionTypeSpec(
        key="action.execute_code",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("bash", "run-script"),
    ),
    ActionTypeSpec(
        key="action.send_message",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("send-message",),
    ),
    ActionTypeSpec(
        key="action.call_app_route",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("call-app-route",),
    ),
)

_AFFORDANCE_SPECS: tuple[ActionTypeSpec, ...] = (
    ActionTypeSpec(
        key="inbox.reply_draft",
        floor=RUNG_DRAFT_ONLY,
        ceiling=RUNG_ONE_TAP,
        leaves_machine=True,
    ),
    ActionTypeSpec(
        key="inbox.classify",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
    ),
    ActionTypeSpec(
        key="sessions.auto_tag",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
    ),
)

COMPUTER_USE_DRIVE = "computer_use.drive"

_CAPABILITY_SPECS: tuple[ActionTypeSpec, ...] = (
    ActionTypeSpec(
        key=COMPUTER_USE_DRIVE,
        floor=RUNG_ONE_TAP,
        ceiling=RUNG_ONE_TAP,
        leaves_machine=True,
    ),
)

CORE_ACTION_TYPES: tuple[ActionTypeSpec, ...] = (
    *_PROVIDER_SPECS,
    *_AFFORDANCE_SPECS,
    *_CAPABILITY_SPECS,
)


def ensure_core_action_types() -> None:
    """Register every core declaration. Idempotent — registration replaces by key.

    Called from the provider-registration seam
    (``action_providers.registry._ensure_default_providers_registered``) and from the
    inbox AI affordances, so the governed inventory is complete in any process that can
    dispatch an action, not only in one that already has.
    """
    for spec in CORE_ACTION_TYPES:
        register_action_type(spec)


def route_action_type(key: str, *, session_key: str = "") -> RungRoute:
    """The route for a declared action type, composed with the run's SafetyProfile.

    ``resolve_rung`` gives the type's own answer (floor + accepted grant, clamped to its
    ceiling, clamped again to ``one_tap`` during an incident). The profile then NARROWS it
    and can never widen it — the lower of the two wins.
    """
    from gideon.security.guardrails.policy import (
        profile_for_session,
        rung_ceiling_for_profile,
    )

    rung = resolve_rung(key)
    profile = profile_for_session(session_key)
    ceiling = rung_ceiling_for_profile(profile)
    effective = RUNGS[min(max(rung_rank(rung), 0), max(rung_rank(ceiling), 0))]
    reason = f"this action {rung_label(rung)}"
    if effective != rung:
        reason = (
            f"this action {rung_label(rung)}, narrowed so it {rung_label(effective)} "
            f"by the {profile.name} profile"
        )
    return RungRoute(
        route=_ROUTE_BY_RUNG.get(effective, ROUTE_DRAFT),
        key=key,
        rung=effective,
        reason=reason,
    )


def route_provider_action(provider_name: str, *, session_key: str = "") -> RungRoute:
    """The route for a provider-dispatched action — the seam-facing entry point.

    An UNDECLARED provider routes to ``execute`` with ``governed`` False: the ladder
    governs what a declaration claimed, and the pre-ladder floor (denylist, incident,
    capability fence, creation-time grant) governs the rest.

    Core declarations are (re)registered here rather than assumed. The seams reach this
    function from paths that do NOT all go through provider registration — an event
    trigger resolves an already-registered provider directly — and a missing declaration
    reads as "ungoverned", which is the one fail-OPEN this function could have. Making it
    self-sufficient costs a dozen dict writes per dispatch and removes that whole class.
    """
    ensure_core_action_types()
    spec = action_type_for_provider(provider_name)
    if spec is None:
        return RungRoute(
            route=ROUTE_EXECUTE,
            reason=f"no action type declares provider {provider_name!r}",
        )
    return route_action_type(spec.key, session_key=session_key)


_WITHHOLD_SURFACE: dict[str, tuple[str, str, str]] = {
    ROUTE_DRAFT: ("skills", "proposal", "proposal"),
    ROUTE_ASK: ("system", "agent_request", "agent_request"),
}


def announce_withheld(
    route: RungRoute,
    *,
    title: str,
    body: str = "",
    refs: dict | None = None,
    dedup_key: str = "",
) -> str:
    """Raise the durable row for an action the ladder withheld. Returns the item id.

    ``draft_only`` files a proposal ("here is what it would have done"); ``one_tap`` files
    an agent request ("decide"). Both carry the action-type key in ``refs`` so AG-8's card
    and the ladder panel can find every held action of one type.

    Deduped per action type by default: a trigger that matches every thirty seconds must
    not stack a hundred identical rows, and a held action is one standing request however
    many times it was attempted.

    Best-effort. A withheld action is already withheld — failing to *announce* it must not
    turn into an exception at the seam, which would be a strictly worse outcome than a
    missing row.
    """
    surface = _WITHHOLD_SURFACE.get(route.route)
    if surface is None:
        return ""
    source, kind, item_kind = surface
    try:
        from gideon.integrations.inbox import emit_attention_item
        from gideon.integrations.inbox_providers.native_source import (
            get_dashboard_state,
        )

        try:
            state = get_dashboard_state()
        except Exception:  # noqa: BLE001 - headless: the row still persists
            state = None
            logger.debug("withheld action: no dashboard state", exc_info=True)
        return emit_attention_item(
            state,
            source=source,
            kind=kind,
            item_kind=item_kind,
            title=title,
            body=body,
            refs={"action_type": route.key, "rung": route.rung, **dict(refs or {})},
            dedup_key=dedup_key or f"autonomy_hold:{route.key}",
        )
    except Exception:  # noqa: BLE001
        logger.warning("withheld action: could not raise an inbox row", exc_info=True)
        return ""


def record_reversal(
    route: RungRoute, result: Any, *, label: str, refs: dict | None = None
) -> str:
    """Persist the reversal handle an ``auto_with_undo`` action came back with.

    The handle itself is the PROVIDER's: only it knows what "undo" means for its own
    effect (``ActionResult.reversal``, e.g. the task row a ``create-task`` filed). This
    persists it in the three places the undo click needs — the SEL row (audit), the
    reversal record store (``guardrails.ladder``, which is what
    :func:`~gideon.security.guardrails.ladder.reverse_action` resolves an id against) and the
    notification's ``meta`` (which carries the record id, so the affordance is rendered
    from persisted state rather than from a handle sitting in a page) — and returns the
    handle it recorded.

    **No handle, no notification.** A provider that cannot reverse itself leaves
    ``reversal`` empty, and then the passive notify is skipped entirely: an "undo
    available" notice for an action that cannot be undone is a promise the product cannot
    keep, and it would also mean every unattended fire in the tree grew a notification
    overnight. The SEL row is still written, so the execution is auditable either way.
    """
    handle = str(getattr(result, "reversal", "") or "")
    meta = {"action_type": route.key, "rung": route.rung, **dict(refs or {})}
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=f"autonomy:{route.key}",
            operation="guardrails.autonomy_executed",
            outcome="ok",
            source="guardrails",
            resources=f"rung={route.rung} reversal={handle or 'none'}"[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("reversal SEL audit failed", exc_info=True)
    if not handle:
        return ""
    record_id = ""
    try:
        from gideon.security.guardrails.ladder import record_reversal_handle

        record_id = record_reversal_handle(
            action_type=route.key, rung=route.rung, handle=handle, label=label
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "could not record a reversal handle for %s", route.key, exc_info=True
        )
    try:
        from gideon.integrations.action_providers.services import get_action_services
        from gideon.workspace import notification_kinds

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is not None:
            state.notify(
                notification_kinds.INFO,
                "An automatic action ran",
                (
                    f"{label} ran on its own. You can still undo it."
                    if record_id
                    else f"{label} ran on its own."
                ),
                meta={**meta, "reversal": handle, "reversal_id": record_id},
            )
    except Exception:  # noqa: BLE001
        logger.debug("reversal notify failed", exc_info=True)
    return handle
