"""Host-bound app execution scope, intersected with current manifest permissions."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from gideon.extensions.apps.agent_tiers import AGENT_READ, AGENT_TIERS, AGENT_TOOLS

_BOUND: dict[str, AppWork] = {}
_HELD: contextvars.ContextVar[AppWork | None] = contextvars.ContextVar(
    "gideon_app_work", default=None
)


@dataclass(frozen=True)
class AppWork:
    app: str
    tier: str

    @classmethod
    def for_app(cls, app: str, tier: str | None = None) -> AppWork:
        from gideon.extensions.apps.permissions import agent_tier_now

        if not app:
            return cls("", "")
        current = agent_tier_now(app)
        if tier is None:
            return cls(app, current)
        return cls(app, intersect(current, tier))

    def current_tier(self) -> str:
        from gideon.extensions.apps.permissions import agent_tier_now

        return intersect(self.tier, agent_tier_now(self.app)) if self.app else ""

    def child(self, requested: str | None = None) -> AppWork:
        current = self.current_tier()
        return AppWork(
            self.app, current if requested is None else intersect(current, requested)
        )

    def refusal(
        self, name: str, *, declared: Any = None, kind: str = "", arguments: Any = None
    ) -> str:
        tier = self.current_tier()
        if not tier:
            return f"app {self.app!r} may run no agent work now"
        if tier == "text":
            return f"app {self.app!r} text work may call no tool"
        if name in {"workflow_start", "workflow_start_draft"}:
            from gideon.security.session_credentials import current_work

            proof = current_work()
            bound = from_bound(proof)
            if bound is None or bound.app != self.app or not bound.current_tier():
                return (
                    "app workflow starts require the verified current app work origin"
                )
            # The service resolves the native private receipt before accepting Temporary work.
            return ""
        if name in {
            "workflow_run",
            "run_workflow",
            "trigger_create",
            "schedule_create",
        }:
            return "app work cannot start an unscoped persistent run"
        if tier == AGENT_READ:
            from gideon.security.guardrails.policy import TOOL_READ, tool_grant_denial

            return tool_grant_denial(
                name, TOOL_READ, declared=declared, tool_kind=kind, tool_input=arguments
            )
        return ""


def intersect(left: str, right: str) -> str:
    if left not in AGENT_TIERS or right not in AGENT_TIERS:
        return ""
    return AGENT_TIERS[min(AGENT_TIERS.index(left), AGENT_TIERS.index(right))]


def bind_session(key: str, work: AppWork) -> None:
    if not key or not isinstance(work, AppWork):
        raise ValueError("app execution requires a host-bound session and scope")
    prior = _BOUND.get(key)
    if prior is not None:
        work = (
            AppWork(prior.app, intersect(prior.tier, work.tier))
            if prior.app == work.app
            else AppWork(prior.app, "")
        )
    _BOUND[key] = work


def of_session(key: str) -> AppWork | None:
    return _BOUND.get(key)


def release_session(key: str) -> None:
    _BOUND.pop(key, None)


def held() -> AppWork | None:
    return _HELD.get()


def hold(work: AppWork | None):
    return _HELD.set(work)


def let_go(token) -> None:
    _HELD.reset(token)


def for_job(trigger_id: str) -> AppWork | None:
    """Resolve a canonically approved app job against its current installed declaration."""
    if not trigger_id.startswith("app:"):
        return None
    pieces = trigger_id.split(":")
    app = pieces[1] if len(pieces) > 1 else ""
    from gideon.extensions.apps.app_manager import _manifest_of
    from gideon.extensions.apps.permissions import checker_for

    manifest = _manifest_of(app) if app else None
    checker = checker_for(app) if app else None
    from gideon.automation.triggers.grants import _action, _loaded_trigger, is_granted
    from gideon.extensions.apps.app_crons import posture_refusal

    try:
        row = _loaded_trigger(trigger_id)
        trigger = row.trigger if row is not None and row.ok else None
        provider, action = _action(trigger) if trigger is not None else ("", {})
        canonical = bool(
            trigger is not None
            and trigger.id == trigger_id
            and trigger.kind == "clock"
            and trigger.enabled
            and trigger.created_by == f"app:{app}"
            and provider == "invoke-agent"
            and is_granted(trigger)
            and not posture_refusal(trigger_id, trigger.workflow)
        )
    except Exception:
        canonical = False
    if (
        not canonical
        or len(pieces) != 3
        or manifest is None
        or checker is None
        or not checker.can_use_cron()
        or not any(cron.name == pieces[2] for cron in manifest.crons)
    ):
        return AppWork(app, "")
    return AppWork.for_app(app)


RUN_KEY = "app_work"
STARTS_AGENTS = frozenset({"invoke-agent", "run-prompt", "run-workflow"})


def from_record(extra: object) -> AppWork | None:
    if not isinstance(extra, dict) or RUN_KEY not in extra:
        return None
    raw = extra[RUN_KEY]
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("app"), str)
        or raw.get("tier") not in AGENT_TIERS
    ):
        return AppWork("", "")
    return AppWork(raw["app"], raw["tier"])


def stamp(extra: dict | None, work: AppWork | None) -> dict:
    result = dict(extra or {})
    if work is not None:
        if not isinstance(work, AppWork):
            raise TypeError("workflow app scope requires host AppWork")
        prior = from_record(result)
        if prior is not None:
            work = prior.child(work.tier) if prior.app == work.app else prior.child("")
        result[RUN_KEY] = {"app": work.app, "tier": work.tier}
    return result


def of_run_id(run_id: str) -> AppWork | None:
    if not run_id:
        return None
    from gideon.automation.workflows import store

    try:
        run = store.get(run_id)
    except Exception:
        return AppWork("", "")
    if run is None:
        return AppWork("", "")
    return from_record(run.extra)


def from_bound(work) -> AppWork | None:
    if work is None:
        return None
    from gideon.security.approval_answer import APP

    actor = work.work_actor or work.initiator
    app = work.created_by_app or (actor.name if actor.kind == APP else "")
    if not app:
        return None
    scope = AppWork.for_app(app)
    prior = of_session(work.session_key)
    return (
        prior.child(scope.tier)
        if prior is not None and prior.app == app
        else (AppWork(app, "") if prior is not None else scope)
    )


def from_request(request) -> AppWork | None:
    from gideon.security.session_credentials import work_of_request

    app = request.get("app", "")
    proof_scope = from_bound(work_of_request(request))
    scope = AppWork.for_app(app) if app else proof_scope
    if app and proof_scope is not None and scope is not None:
        scope = (
            proof_scope.child(scope.tier)
            if proof_scope.app == app
            else proof_scope.child("")
        )
    return scope


def active_for(session_key: str) -> AppWork | None:
    from gideon.security.session_credentials import current_work

    proof = current_work()
    return (
        from_bound(proof)
        if proof is not None and proof.session_key == session_key
        else of_session(session_key)
    )


def run_refusal(work: AppWork | None, *, capability: str = "research") -> str:
    if work is None:
        return ""
    tier = work.current_tier()
    needed = "tools" if capability == "mutating" else "read"
    if intersect(tier, needed) != needed:
        return f"app {work.app!r} agent work cannot run a {capability} workflow step at tier {tier or 'none'}"
    return ""
