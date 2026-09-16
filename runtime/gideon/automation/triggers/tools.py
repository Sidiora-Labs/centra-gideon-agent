"""Automation authoring plans, scoped mutations and manual-run presentation."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGENT_TRIGGERS = 20

AGENT_ONETIME_TTL_SECS = 7 * 86400

AGENT_RECURRING_TTL_SECS = 30 * 86400

MIN_AGENT_TTL_SECS = 60

MAX_AGENT_TTL_SECS = 90 * 86400

TOOL_NAMES: tuple[str, ...] = (
    "automation_create",
    "automation_list",
    "automation_update",
    "automation_pause",
    "automation_resume",
    "automation_run",
    "automation_history",
    "automation_delete",
    "automation_delete_all",
)

PATCHABLE: frozenset[str] = frozenset(
    {
        "name",
        "spec",
        "gates",
        "workflow",
        "enabled",
        "overlap",
        "session",
        "model_tier",
        "delivery",
        "failure_delivery",
        "yield_to_user",
        "catch_up",
        "expires_at",
    }
)

_SLUG_RE = re.compile("[^a-z0-9]+")

_BROWSE_PROVIDER = "browse"

MANUAL_BYPASSES: frozenset[str] = frozenset({"quiet", "duty"})

MANUAL_NEVER_BYPASSES: frozenset[str] = frozenset(
    {"incident", "screen", "capability", "budget", "claim"}
)


@dataclass
class ToolResult:
    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(ok=self.ok, text=self.text, data=dict(self.data))


def _agent_expiry_iso(resolved_spec: dict, ttl_secs: float, *, now: float = 0.0) -> str:
    import time
    from datetime import datetime, timezone

    base = float(now) if now else time.time()
    lifetime = (
        AGENT_ONETIME_TTL_SECS if resolved_spec.get("at") else AGENT_RECURRING_TTL_SECS
    )
    if ttl_secs and ttl_secs > 0:
        lifetime = min(
            float(MAX_AGENT_TTL_SECS), max(float(MIN_AGENT_TTL_SECS), float(ttl_secs))
        )
    return datetime.fromtimestamp(base + float(lifetime), timezone.utc).isoformat()


def max_agent_triggers() -> int:
    try:
        from gideon.core.config.loader import AppConfig

        configured = AppConfig.load().workflows.self_schedule_max_outstanding
        return int(configured)
    except Exception:
        return DEFAULT_MAX_AGENT_TRIGGERS


def unattended_action_refusal(workflow: Any) -> ToolResult | None:
    action = workflow if isinstance(workflow, dict) else {}
    nested = action.get("inline")
    if isinstance(nested, dict):
        action = nested
    if str(action.get("provider") or "").strip() != _BROWSE_PROVIDER:
        return None
    from gideon.integrations.browse.target import (
        UnknownBrowseTarget,
        permits_unattended,
        resolve_target,
        unattended_refusal,
        unknown_target_error,
    )

    raw = action.get("config")
    try:
        target = resolve_target(raw if isinstance(raw, dict) else {})
    except UnknownBrowseTarget as error:
        refusal = unknown_target_error(error.raw)
    else:
        if permits_unattended(target):
            return None
        refusal = unattended_refusal(target, origin="a scheduled automation")
    return ToolResult(
        False, f"Error: {refusal.what}. {refusal.fix}.", dict(error=refusal.to_dict())
    )


def slug_for(name: str, kind: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return (kind + ":" + (slug or "automation"))[:96]


def _unique_id(store: Any, base: str) -> str:
    occupied = {entry.trigger.id for entry in store.load()}
    candidates = (base, *(f"{base}-{suffix}" for suffix in range(2, 100)))
    return next(
        (candidate for candidate in candidates if candidate not in occupied),
        f"{base}-{len(occupied) + 1}",
    )


def _active_agent_count(store: Any) -> int:
    return sum(
        bool(entry.trigger.enabled)
        for entry in store.load()
        if entry.trigger.created_by == "agent"
    )


def _origin_harness_for(store: Any) -> str:
    try:
        from pathlib import Path

        from gideon.operations.durability.shards import machine_id

        directory = getattr(store, "base_dir", None)
        if directory is None:
            from gideon.core.config.loader import config_dir

            directory = config_dir()
        return machine_id(Path(directory))
    except Exception:
        return ""


@dataclass
class CreationPlan:
    store: Any
    name: str
    when: str
    kind: str
    spec: dict[str, Any]
    workflow: dict[str, Any] | None
    message: str
    created_by: str
    enabled: bool
    cadence_to_cron: Any
    resume: dict[str, Any] | None
    ttl_secs: float
    because: str = ""
    warnings: list[Any] = field(default_factory=list)

    def choose_schedule(self) -> ToolResult | None:
        if self.kind:
            return None
        from gideon.automation.triggers.nl_kind import route

        routed = route(self.when)
        if not routed.ok:
            return ToolResult(False, f"Error: {routed.error}", dict(when=self.when))
        self.kind, self.because = routed.kind, routed.because
        self.spec = {**routed.spec, **self.spec}
        if routed.cadence and "expr" not in self.spec and "at" not in self.spec:
            expression, error = (self.cadence_to_cron or _default_cadence_to_cron)(
                routed.cadence
            )
            if error:
                return ToolResult(
                    False, f"Error: {error}", dict(cadence=routed.cadence)
                )
            self.spec = {"kind": "cron", "expr": expression, **self.spec}
        return None

    def check_quota(self) -> ToolResult | None:
        if self.created_by != "agent":
            return None
        active, cap = _active_agent_count(self.store), max_agent_triggers()
        if active < cap:
            return None
        return ToolResult(
            False,
            f"Error: {active} agent-created automations are already active (cap {cap}). Pause or delete one first.",
            dict(active=active, cap=cap),
        )

    def choose_action(self) -> ToolResult | None:
        if self.resume is not None:
            if self.workflow:
                return ToolResult(
                    False,
                    "Error: give either a resume target or a workflow, not both — a trigger with a resume target wakes the named run instead of running an action.",
                )
            target = {
                key: value
                for key, value in dict(self.resume).items()
                if value not in (None, "")
            }
            if not str(target.get("run_id") or "").strip():
                return ToolResult(
                    False, "Error: a resume target needs a run_id.", dict(resume=target)
                )
            if self.message and "answer" not in target:
                target.update(answer=self.message)
            self.workflow = dict(resume=target)
        if self.message and not self.workflow:
            self.workflow = dict(
                provider="run-prompt", config=dict(message=self.message)
            )
        if not self.workflow:
            return ToolResult(
                False, "Error: give a message or a workflow for the automation to run."
            )
        return unattended_action_refusal(self.workflow)

    def check_schedule(self) -> ToolResult | None:
        from gideon.automation.triggers.arm import semantic_spec_issues
        from gideon.automation.triggers.models import validate_spec

        issues = [
            *validate_spec(self.kind, self.spec),
            *semantic_spec_issues(self.kind, self.spec),
        ]
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            detail = "; ".join(f"{issue.path}: {issue.message}" for issue in errors)
            return ToolResult(False, f"Error: {detail}", dict(spec=self.spec))
        self.warnings = [
            issue
            for issue in semantic_spec_issues(self.kind, self.spec)
            if issue.severity != "error"
        ]
        return None

    def check_provider(self) -> ToolResult | None:
        inline = self.workflow.get("inline")
        raw = (
            (inline or {}).get("provider")
            if isinstance(inline, dict)
            else self.workflow.get("provider") or ""
        )
        name = str(raw).strip()
        if not name or "resume" in self.workflow:
            return None
        from gideon.integrations.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
            list_action_providers,
        )

        _ensure_default_providers_registered()
        if get_action_provider(name) is not None:
            return None
        return ToolResult(
            False,
            f"Error: unknown action provider {name!r}. Registered providers: {sorted(list_action_providers())}.",
            dict(provider=name),
        )

    def persist(self) -> Any:
        from gideon.automation.triggers.arm import arm
        from gideon.automation.triggers.models import Trigger
        from gideon.automation.triggers.screen import capabilities_for_action

        trigger = Trigger(
            id=_unique_id(self.store, slug_for(self.name, self.kind)),
            name=self.name.strip(),
            kind=self.kind,
            enabled=bool(self.enabled),
            created_by=self.created_by,
            origin_harness=_origin_harness_for(self.store),
            spec=self.spec,
            workflow=dict(self.workflow),
        )
        trigger.capabilities = capabilities_for_action(trigger)
        if self.created_by == "agent" and not trigger.expires_at:
            trigger.expires_at = _agent_expiry_iso(self.spec, self.ttl_secs)
        following = arm(trigger) if trigger.enabled else ""
        if following:
            trigger.next_fire_at = following
        return self.store.upsert(trigger)

    def describe(self, saved: Any) -> ToolResult:
        lines = [f"Created automation '{saved.name}' ({saved.id}), kind {saved.kind}."]
        if self.because:
            lines.append(f"  {self.because}")
        lines.extend(
            f"  Warning ({issue.path}): {issue.message}" for issue in self.warnings
        )
        if self.spec.get("expr"):
            lines.append(f"  cron: {self.spec['expr']}")
        if self.spec.get("paths"):
            lines.append(f"  watching: {', '.join(self.spec['paths'])}")
        if self.created_by == "agent":
            state = (
                "active now" if saved.enabled else "switched off until you enable it"
            )
            lines.append(
                f"  I created this for you — it is {state} and visible on the Automations page ({_active_agent_count(self.store)}/{max_agent_triggers()} agent-created)."
            )
        return ToolResult(True, "\n".join(lines), dict(trigger=saved.to_dict()))

    def execute(self) -> ToolResult:
        if not (self.name or "").strip():
            return ToolResult(False, "Error: name is required.")
        for stage in (
            self.choose_schedule,
            self.check_quota,
            self.choose_action,
            self.check_schedule,
            self.check_provider,
        ):
            refusal = stage()
            if refusal is not None:
                return refusal
        return self.describe(self.persist())


def create(
    store: Any,
    *,
    name: str,
    when: str = "",
    kind: str = "",
    spec: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
    message: str = "",
    created_by: str = "agent",
    enabled: bool = True,
    cadence_to_cron: Any = None,
    resume: dict[str, Any] | None = None,
    ttl_secs: float = 0,
) -> ToolResult:
    if not (name or "").strip():
        return ToolResult(False, "Error: name is required.")
    return CreationPlan(
        store,
        name,
        when,
        kind,
        dict(spec or {}),
        workflow,
        message,
        created_by,
        enabled,
        cadence_to_cron,
        resume,
        ttl_secs,
    ).execute()


def _default_cadence_to_cron(cadence: str) -> tuple[str, str]:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from gideon.automation.nl_to_cron import nl_to_cron

    def convert():
        return asyncio.run(nl_to_cron(cadence))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return convert()
    with ThreadPoolExecutor() as workers:
        return workers.submit(convert).result(timeout=60)


@dataclass
class AutomationSummary:
    row: Any

    def to_dict(self) -> dict[str, Any]:
        trigger = self.row.trigger
        mapping = {
            "id": "id",
            "name": "name",
            "kind": "kind",
            "enabled": "enabled",
            "created_by": "created_by",
            "health": "health_status",
            "runs": "run_count",
            "next_fire_at": "next_fire_at",
            "last_error": "last_error_summary",
        }
        return {
            **{key: getattr(trigger, source) for key, source in mapping.items()},
            "broken": [issue.message for issue in self.row.errors],
        }

    @staticmethod
    def line(row: dict[str, Any]) -> str:
        suffix = []
        if not row["enabled"]:
            suffix.append(" [paused]")
        if row["health"]:
            suffix.append(f" health={row['health']}")
        if row["broken"]:
            suffix.append(f" ⚠ {row['broken'][0]}")
        return f"{row['id']} — {row['name']} ({row['kind']})" + "".join(suffix)


def list_automations(store: Any, *, kind: str = "", state: str = "") -> ToolResult:
    rows = []
    for row in store.load():
        trigger = row.trigger
        selected = (
            (not kind or trigger.kind == kind)
            and (state != "active" or trigger.enabled)
            and (state != "paused" or not trigger.enabled)
        )
        if selected:
            rows.append(AutomationSummary(row).to_dict())
    text = (
        "\n".join(map(AutomationSummary.line, rows))
        if rows
        else "No automations match."
    )
    return ToolResult(True, text, dict(automations=rows))


def _missing(trigger_id: str) -> ToolResult:
    return ToolResult(False, f"Error: no automation with id {trigger_id!r}.")


def update(store: Any, *, trigger_id: str, patch: dict[str, Any]) -> ToolResult:
    row = store.get(trigger_id)
    if row is None:
        return _missing(trigger_id)
    accepted = {key: value for key, value in patch.items() if key in PATCHABLE}
    rejected = sorted(set(patch).difference(PATCHABLE))
    if not accepted:
        return ToolResult(
            False,
            f"Error: nothing to update. Not settable here: {', '.join(rejected) or 'none given'}.",
            dict(rejected=rejected),
        )
    if "workflow" in accepted:
        refusal = unattended_action_refusal(accepted["workflow"])
        if refusal is not None:
            return refusal
    for key in accepted:
        setattr(row.trigger, key, accepted[key])
    saved = store.upsert(row.trigger)
    message = f"Updated {saved.id}: {', '.join(sorted(accepted))}."
    if rejected:
        message += f"\n  Ignored (not settable via this tool): {', '.join(rejected)}."
    return ToolResult(True, message, dict(trigger=saved.to_dict(), rejected=rejected))


def set_paused(store: Any, *, trigger_id: str, paused: bool) -> ToolResult:
    row = store.get(trigger_id)
    if row is None:
        return _missing(trigger_id)
    saved = store.set_enabled(trigger_id, not paused)
    if saved is not None:
        return ToolResult(
            True,
            f"{'Paused' if paused else 'Resumed'} {saved.id} ({saved.name}).",
            dict(trigger=saved.to_dict()),
        )
    if row.errors:
        errors = [issue.message for issue in row.errors]
        return ToolResult(
            False,
            f"Error: {trigger_id} could not be resumed — it has a parse error ({errors[0]}). Fix it first.",
            dict(errors=errors),
        )
    return ToolResult(False, f"Error: could not change {trigger_id!r}.")


def delete(store: Any, *, trigger_id: str, confirm: bool = False) -> ToolResult:
    if not confirm:
        return ToolResult(
            False,
            f"Error: deleting {trigger_id!r} needs confirm: true. Pause it instead if you might want it back.",
        )
    row = store.get(trigger_id)
    if row is None:
        return _missing(trigger_id)
    name = row.trigger.name
    store.delete(trigger_id)
    return ToolResult(True, f"Deleted {trigger_id} ({name}).", dict(deleted=trigger_id))


def delete_all(
    store: Any, *, created_by: str = "agent", confirm: bool = False
) -> ToolResult:
    if not confirm:
        return ToolResult(
            False,
            f"Error: deleting every {created_by}-created automation needs confirm: true. Pause them instead if you might want them back.",
        )
    owned = [
        entry.trigger.id
        for entry in store.load()
        if entry.trigger.created_by == created_by
    ]
    deleted = []
    for identity in owned:
        try:
            store.delete(identity)
        except Exception:
            logger.debug("could not delete %s", identity, exc_info=True)
        else:
            deleted.append(identity)
    message = f"No {created_by}-created automations to delete."
    if owned:
        message = f"Deleted {len(deleted)} {created_by}-created automation(s): {', '.join(deleted)}."
        if len(deleted) != len(owned):
            message += f"\n  ⚠️ {len(owned) - len(deleted)} could not be deleted."
    return ToolResult(True, message, dict(deleted=deleted, created_by=created_by))


def manual_gate_plan(dry_run: bool = False) -> dict[str, Any]:
    from gideon.automation.triggers.firepath import GATE_ORDER

    plan = {
        "bypassed": [],
        "enforced": [],
        "dry_run": bool(dry_run),
        "executes": not dry_run,
    }
    for gate in GATE_ORDER:
        plan["bypassed" if gate in MANUAL_BYPASSES else "enforced"].append(gate)
    return plan


def manual_refusal() -> str:
    from gideon.security.guardrails.incident import incident_active

    return (
        "incident mode is active: unattended fires are suspended (resume with `gideon incident off`)"
        if incident_active()
        else ""
    )


@dataclass
class ManualRun:
    trigger: Any
    dry_run: bool
    runner: Any
    lines: list[str] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)

    def result(self, ok: bool, **data: Any) -> ToolResult:
        return ToolResult(ok, "\n".join(self.lines), dict(plan=self.plan, **data))

    def execute(self) -> ToolResult:
        self.plan = manual_gate_plan(self.dry_run)
        trigger = self.trigger
        self.lines = [
            f"{'Dry run' if self.dry_run else 'Manual run'} of {trigger.id} ({trigger.name}).",
            f"  gates enforced: {', '.join(self.plan['enforced'])}",
            f"  bypassed (manual): {', '.join(self.plan['bypassed']) or 'none'}",
        ]
        if not trigger.enabled:
            self.lines.append(
                "  note: this automation is paused — running it here does not re-enable it."
            )
        if self.dry_run:
            self.lines.append("  nothing was executed.")
            return self.result(True, trigger=trigger.to_dict())
        refusal = manual_refusal()
        if refusal:
            self.lines.append(f"  refused: {refusal}")
            return self.result(False, refused=refusal)
        if self.runner is None:
            self.lines.append(
                "  no runner is wired in this context, so nothing was executed."
            )
            return self.result(False)
        result = self.runner(
            dict(trigger_id=trigger.id, workflow=dict(trigger.workflow))
        )
        self.lines.append(f"  result: {result}")
        return self.result(True, result=result)


def run(
    store: Any, *, trigger_id: str, dry_run: bool = False, runner: Any = None
) -> ToolResult:
    row = store.get(trigger_id)
    if row is None:
        return _missing(trigger_id)
    if row.errors:
        errors = [issue.message for issue in row.errors]
        return ToolResult(
            False,
            f"Error: {trigger_id} has a parse error and cannot run ({errors[0]}).",
            dict(errors=errors),
        )
    return ManualRun(row.trigger, dry_run, runner).execute()


def _same_trigger(record_id: str, wanted: str) -> bool:
    return bool(record_id and wanted) and (
        record_id == wanted or record_id.endswith(":" + wanted)
    )


def history(
    store: Any,
    *,
    trigger_id: str,
    n: int = 10,
    schedule_runs: list[dict[str, Any]] | None = None,
    hooks: list[Any] | None = None,
    event_triggers: list[Any] | None = None,
) -> ToolResult:
    if store.get(trigger_id) is None:
        return _missing(trigger_id)
    from gideon.automation.triggers.history import feed_response, unified_feed

    limit = max(1, n)
    feed = unified_feed(
        schedule_runs=schedule_runs,
        hooks=hooks,
        event_triggers=event_triggers,
        limit=limit * 10,
    )
    rows = [record for record in feed if _same_trigger(record.trigger_id, trigger_id)][
        :limit
    ]
    message = f"{trigger_id} has no recorded runs yet."
    if rows:
        lines = []
        for record in rows:
            line = (
                f"{record.started_at or record.scheduled_for or '?'} {record.outcome}"
            )
            lines.append(line + (f" — {record.reason}" if record.reason else ""))
        message = f"{trigger_id} — last {len(rows)} run(s):\n" + "\n".join(lines)
    return ToolResult(True, message, {"trigger_id": trigger_id, **feed_response(rows)})
