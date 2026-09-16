"""Trigger schemas, ordered authoring diagnostics and fire-record policies."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any

KINDS: tuple[str, ...] = (
    "clock",
    "event",
    "run_completed",
    "idle",
    "file",
    "webhook",
    "view",
    "web_watch",
    "manual",
)


class TriggerState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    AUTOPAUSED = "autopaused"
    PARKED = "parked"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class TriggerHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    PARKED = "parked"
    FAILING = "failing"


class RunWeight(str, Enum):
    LEDGER = "ledger"
    FULL = "full"


class Outcome(str, Enum):
    RAN = "ran"
    RAN_LATE = "ran_late"
    SKIPPED_OVERLAP = "skipped_overlap"
    SKIPPED_BUDGET = "skipped_budget"
    SKIPPED_GATE = "skipped_gate"
    SKIPPED_NOOP = "skipped_noop"
    SKIPPED_TRIAGE = "skipped_triage"
    SKIPPED_MISSED = "skipped_missed"
    DEFERRED = "deferred"
    REFUSED = "refused"
    BLOCKED_INJECTION = "blocked_injection"
    FAILED = "failed"


FIRE_OUTCOMES: tuple[str, ...] = tuple(o.value for o in Outcome)

TRUE_FAILURE_OUTCOMES: frozenset[str] = frozenset({Outcome.FAILED.value})

INERT_OUTCOMES: frozenset[str] = frozenset(
    {
        Outcome.SKIPPED_OVERLAP.value,
        Outcome.SKIPPED_BUDGET.value,
        Outcome.SKIPPED_GATE.value,
        Outcome.SKIPPED_NOOP.value,
        Outcome.SKIPPED_TRIAGE.value,
        Outcome.SKIPPED_MISSED.value,
    }
)

SPEC_KEYS: dict[str, frozenset[str]] = {
    "clock": frozenset(
        {
            "kind",
            "expr",
            "at",
            "interval_secs",
            "interval_secs_healthy",
            "interval_secs_degraded",
            "health_state",
            "timezone",
            "jitter_secs",
            "strict",
            "skip_dates",
            "delete_after_run",
        }
    ),
    "event": frozenset({"source", "pattern", "blocking", "agent_scope"}),
    "run_completed": frozenset({"source_trigger", "source_def"}),
    "idle": frozenset(
        {
            "scope",
            "idle_secs",
            "first_idle_secs",
            "message",
            "max_cycles",
            "stop_sentinel_path",
        }
    ),
    "file": frozenset({"paths", "dedup"}),
    "webhook": frozenset({"token_ref"}),
    "view": frozenset({"surface_binding", "ttl_secs"}),
    "web_watch": frozenset(
        {
            "url",
            "poll_interval",
            "extraction",
            "novelty_key",
            "escalate_headless",
            "max_headless_requests",
        }
    ),
    "manual": frozenset(),
}

CLOCK_KINDS: frozenset[str] = frozenset(
    {"cron", "at", "sequence", "interval", "adaptive"}
)

MIN_CLOCK_INTERVAL_SECS = 900

GATE_KEYS: frozenset[str] = frozenset(
    {
        "debounce_secs",
        "rate_cap",
        "max_fires",
        "skip_dates",
        "quiet_hours",
        "cost_cap",
        "max_cost_usd_per_run",
        "max_actions_per_hour",
        "cooldown_secs",
        "idempotency",
        "threshold",
        "condition",
        "max_runs_per_hour",
        "duty_gate",
    }
)

FAIL_OPEN_GATES: frozenset[str] = frozenset(
    {
        "cost_cap",
        "max_cost_usd_per_run",
        "max_actions_per_hour",
        "max_runs_per_hour",
        "rate_cap",
        "condition",
        "duty_gate",
        "duty",
        "slot",
        "active",
        "incident",
        "spacing",
        "rate",
        "debounce_secs",
        "cooldown_secs",
    }
)

FAIL_CLOSED_GATES: frozenset[str] = frozenset(
    {"screen", "quiet", "budget", "claim", "yield", "capability", "idempotency"}
)

_RESUME_TARGET_FIELDS: tuple[str, ...] = (
    "run_id",
    "project_id",
    "resume_token",
    "answer",
)

_ACTION_KEYS: tuple[str, ...] = ("inline", "provider", "ref")

LEGACY_FIELD_MAP: dict[str, dict[str, str | None]] = {
    "ScheduleJob": {
        "id": "id",
        "name": "name",
        "enabled": "enabled",
        "created_by": "created_by",
        "created_ts": None,
        "schedule": "spec (clock: kind/expr/at)",
        "timezone": "spec.timezone",
        "skip_dates": "spec.skip_dates",
        "strict_schedule": "spec.strict",
        "delete_after_run": "spec.delete_after_run",
        "action": "workflow.inline",
        "agent_sequence": "workflow.ref (a sequence becomes a def, not a list on the trigger)",
        "env": "capabilities.env",
        "timeout_secs": "gates.cooldown_secs is NOT this — timeout is per-run, so it rides the run",
        "dry_run": "spec (manual dry-run is a fire MODE, not trigger state)",
        "channel": "delivery",
        "thread_ts": "delivery (channel target carries the thread)",
        "silent": "delivery == none",
        "session_key": "session",
        "persistent_session": "session (pinned:<key>)",
        "context_enabled": "spec.context_enabled is NOT a gate — it shapes the run's prompt",
        "last_run_ts": "last_success_at / last_failure_at",
        "last_status": "health_status",
        "last_error": "last_error_summary",
        "last_outcome": "the fire record's typed outcome",
        "last_result": None,
        "consecutive_failures": "failure_policy (autopause counter is derived from fire records)",
        "last_failure_hash": "failure_policy.dedupe_hash",
        "last_failure_at": "last_failure_at",
        "last_posted_hash": "gates.idempotency",
        "last_posted_at": None,
        "consecutive_dupes": None,
        "acked_items": None,
    },
    "EventTrigger": {
        "id": "id",
        "enabled": "enabled",
        "pattern": "spec.source",
        "source": "spec.source",
        "key_glob": "spec.pattern.glob",
        "content_re": "spec.pattern.regex",
        "sender_glob": "spec.pattern.sender_glob",
        "address_glob": "spec.pattern.address_glob",
        "event_glob": "spec.pattern.event_glob",
        "state": "state",
        "park_reason": "last_error_summary",
        "park_retry_after": "park_retry_after",
        "action_provider": "workflow.inline.provider",
        "action_config": "workflow.inline",
        "max_fires": "gates.max_fires",
        "debounce_secs": "gates.debounce_secs",
        "fire_count": "run_count",
        "last_fired_at": "last_success_at",
    },
}


@dataclass
class Issue:
    path: str
    message: str
    severity: str = "warning"
    closest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _record_projection(self, ("path", "message", "severity", "closest"))


@dataclass
class Trigger:
    id: str
    name: str
    kind: str
    enabled: bool = True
    created_by: str = "user"
    author: str = ""
    origin_harness: str = ""
    spec: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    overlap: str = "skip"
    session: str = "fresh"
    model_tier: str = "background"
    delivery: str = "none"
    failure_delivery: str = "inbox"
    retry: dict[str, Any] = field(default_factory=dict)
    failure_policy: dict[str, Any] = field(default_factory=dict)
    yield_to_user: bool = False
    resource_slots: list[str] = field(default_factory=list)
    skip_if_active: dict[str, Any] = field(default_factory=dict)
    catch_up: bool = False
    expires_at: str = ""
    next_fire_at: str = ""
    last_run_id: str = ""
    run_count: int = 0
    last_success_at: str = ""
    last_failure_at: str = ""
    last_fired_at: str = ""
    health_status: str = TriggerHealth.OK.value
    last_error_summary: str = ""
    state: str = TriggerState.ACTIVE.value
    park_retry_after: float = 0.0
    last_alert_hash: str = ""
    last_alert_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return _record_projection(
            self, _TRIGGER_EXPORT_ORDER, _TRIGGER_MAPPING_FIELDS, {"resource_slots"}
        )

    @property
    def fires_automatically(self) -> bool:
        if self.enabled:
            return self.state == TriggerState.ACTIVE.value and self.kind != "manual"
        return self.enabled


@dataclass
class FireRecord:
    id: str
    trigger_id: str
    outcome: str
    reason: str = ""
    weight: str = RunWeight.LEDGER.value
    scheduled_for: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_secs: float = 0.0
    run_id: str = ""
    mutated: bool = False
    counters: dict[str, Any] = field(default_factory=dict)
    incomplete: bool = False
    acted_on: bool = False
    dismissed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _record_projection(
            self, (item.name for item in fields(FireRecord)), {"counters"}
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FireRecord:
        source = _RecordInput(d or {})
        values = source.strings(
            (
                "id",
                "trigger_id",
                "outcome",
                "reason",
                "scheduled_for",
                "started_at",
                "finished_at",
                "run_id",
            )
        )
        values.update(source.flags(("mutated", "incomplete", "acted_on", "dismissed")))
        values["outcome"] = _member_or(
            values["outcome"], FIRE_OUTCOMES, Outcome.FAILED.value
        )
        values["weight"] = _member_or(
            source.text("weight", RunWeight.LEDGER.value),
            tuple(weight.value for weight in RunWeight),
            RunWeight.LEDGER.value,
        )
        values["counters"] = source.mapping("counters")
        values["duration_secs"] = _float(source.data.get("duration_secs"), 0.0)
        return cls(**values)

    @property
    def productive(self) -> bool:
        if self.mutated:
            return not require_reason(self.outcome)
        return self.mutated

    @property
    def counts_toward_autopause(self) -> bool:
        return self.outcome in TRUE_FAILURE_OUTCOMES


_TRIGGER_MAPPING_FIELDS = frozenset(
    (
        "spec",
        "gates",
        "capabilities",
        "workflow",
        "retry",
        "failure_policy",
        "skip_if_active",
    )
)
_TRIGGER_EXPORT_ORDER = (
    "id",
    "name",
    "kind",
    "enabled",
    "created_by",
    "author",
    "origin_harness",
    "spec",
    "gates",
    "capabilities",
    "workflow",
    "overlap",
    "session",
    "model_tier",
    "delivery",
    "failure_delivery",
    "retry",
    "failure_policy",
    "yield_to_user",
    "resource_slots",
    "skip_if_active",
    "catch_up",
    "expires_at",
    "next_fire_at",
    "last_run_id",
    "run_count",
    "last_success_at",
    "last_failure_at",
    "last_fired_at",
    "park_retry_after",
    "last_alert_hash",
    "last_alert_at",
    "health_status",
    "last_error_summary",
    "state",
)


def _record_projection(record, names, mappings=(), sequences=()):
    result = {}
    for name in names:
        value = getattr(record, name)
        if name in mappings:
            value = dict(value)
        elif name in sequences:
            value = list(value)
        result[name] = value
    return result


class _RecordInput:
    def __init__(self, data):
        self.data = data

    def text(self, name, fallback=""):
        return str(self.data.get(name, fallback) or fallback)

    def strings(self, names):
        return {name: self.text(name) for name in names}

    def flags(self, names):
        return {name: self.data.get(name) is True for name in names}

    def mapping(self, name):
        value = self.data.get(name)
        return dict(value) if isinstance(value, dict) else {}


def _closest(name: str, known: tuple[str, ...] | frozenset[str]) -> str:
    candidates = iter(difflib.get_close_matches(name, list(known), n=1, cutoff=0.7))
    return next(candidates, "")


def _member_or(value, options, fallback):
    return value if value in options else fallback


class _Diagnostics:
    def __init__(self):
        self.items: list[Issue] = []

    def add(self, path, message, severity="warning", closest=""):
        self.items.append(Issue(path, message, severity, closest))

    def extend(self, issues):
        self.items.extend(issues)

    def unknown(self, keys, known, path, message):
        for key in keys:
            if key not in known:
                self.add(path(key), message(key), closest=_closest(key, known))

    def required(self, valid, path, message):
        if not valid:
            self.add(path, message, "error")

    def choice(self, value, options, fallback, path, label):
        if value in options:
            return value
        self.add(path, f"unknown {label} {value!r}", closest=_closest(value, options))
        return fallback

    @property
    def fatal(self):
        return any(item.severity == "error" for item in self.items)


class _SpecContract:
    def __init__(self, kind, spec):
        self.kind = kind
        self.spec = spec or {}
        self.report = _Diagnostics()

    def clock(self):
        spec, report = self.spec, self.report
        variant = str(spec.get("kind", "") or "")
        if variant not in CLOCK_KINDS:
            message = (
                f"unknown clock kind {variant!r}"
                if variant
                else f"a clock trigger needs one of {sorted(CLOCK_KINDS)}"
            )
            report.add(
                "spec.kind",
                message,
                "error",
                _closest(variant, CLOCK_KINDS) if variant else "",
            )
            return
        if variant == "cron":
            report.required(
                str(spec.get("expr", "") or "").strip(),
                "spec.expr",
                "a cron clock needs an expression",
            )
        elif variant in ("at", "sequence"):
            report.required(spec.get("at"), "spec.at", f"an {variant} clock needs `at`")
        elif variant == "adaptive":
            for field_name in ("interval_secs_healthy", "interval_secs_degraded"):
                cadence = _float(spec.get(field_name) or 0, 0.0)
                if cadence <= 0:
                    report.add(
                        f"spec.{field_name}",
                        f"an adaptive clock needs a positive `{field_name}`",
                        "error",
                    )
        elif variant == "interval":
            cadence = _int(spec.get("interval_secs") or 0, 0)
            if 0 < cadence < MIN_CLOCK_INTERVAL_SECS:
                report.add(
                    "spec.interval_secs",
                    f"{cadence}s is below the {MIN_CLOCK_INTERVAL_SECS}s floor for an LLM-invoking trigger; it will still run, but confirm this is intended",
                )

    def validate(self):
        kind, spec, report = self.kind, self.spec, self.report
        known = SPEC_KEYS.get(kind)
        if known is None:
            report.add(
                "kind",
                f"unknown trigger kind {kind!r}; expected one of {list(KINDS)}",
                "error",
                _closest(kind, KINDS),
            )
            return report.items
        report.unknown(
            sorted(spec),
            known,
            lambda key: f"spec.{key}",
            lambda key: f"{kind} triggers do not use {key!r}",
        )
        if kind == "clock":
            self.clock()
        elif kind in ("event", "webhook", "web_watch"):
            field_name, message = {
                "event": ("source", "an event trigger needs a source"),
                "webhook": (
                    "token_ref",
                    "a webhook trigger needs a token_ref; an unauthenticated fire endpoint is refused rather than defaulted",
                ),
                "web_watch": ("url", "a web_watch trigger needs a url"),
            }[kind]
            report.required(
                str(spec.get(field_name, "") or "").strip(),
                f"spec.{field_name}",
                message,
            )
        if kind == "event":
            report.extend(_agent_scope_issues(self.spec))
        return report.items


def _agent_scope_issues(spec: dict[str, Any] | None) -> list[Issue]:
    scope = (spec or {}).get("agent_scope")
    report = _Diagnostics()
    if scope is None:
        return report.items
    if not isinstance(scope, (list, tuple)):
        message = f"agent_scope must be a list of agent ids; a {type(scope).__name__} is refused rather than coerced, because a scope that silently read as one agent would fence the wrong thing"
    elif not scope:
        message = "agent_scope is empty, so this trigger can never fire for any agent — remove the key to leave it unscoped, or name the agents it belongs to"
    else:
        invalid = [
            value for value in scope if not isinstance(value, str) or not value.strip()
        ]
        if not invalid:
            return report.items
        message = f"agent_scope entries must be non-empty agent ids; got {invalid!r}"
    report.add("spec.agent_scope", message, "error")
    return report.items


def validate_spec(kind: str, spec: dict[str, Any]) -> list[Issue]:
    return _SpecContract(kind, spec).validate()


def gate_failure_mode(gate: str) -> str:
    return "open" if gate in FAIL_OPEN_GATES else "closed"


def validate_gates(gates: dict[str, Any]) -> list[Issue]:
    report = _Diagnostics()
    report.unknown(
        sorted(gates or {}),
        GATE_KEYS,
        lambda key: f"gates.{key}",
        lambda key: f"unknown gate {key!r}; it would be stored and never enforced",
    )
    return report.items


def _known_fields() -> frozenset[str]:
    return frozenset(Trigger.__dataclass_fields__)


def _token_ref_issues(spec: Any) -> list[Issue]:
    if isinstance(spec, dict):
        token = spec.get("token_ref")
        if isinstance(token, str) and token.strip():
            from gideon.automation.triggers.secrets import SECRET_REF_RE

            if SECRET_REF_RE.fullmatch(token.strip()) is None:
                return [
                    Issue(
                        "spec.token_ref",
                        "token_ref holds the token itself rather than a reference — store it with `gideon auth` and reference it as {{secret:KEY}}, which is resolved at dispatch and never written to triggers.json (which is snapshotted and rendered in the UI)",
                    )
                ]
    return []


def _inline_credential_issues(workflow: Any) -> list[Issue]:
    if not isinstance(workflow, dict):
        return []
    try:
        from gideon.automation.workflows.secrets import find_inline_secrets

        findings = find_inline_secrets(workflow)
    except Exception:
        return []
    report = _Diagnostics()
    for finding in findings:
        report.add(
            "workflow" + (f".{finding.key}" if finding.key else ""),
            f"{finding.key or 'the action'} looks like an inline credential ({finding.hint}) — reference it as {{{{secret:KEY}}}} instead, which is resolved at dispatch and never stored",
        )
    return report.items


class _ResumeContract:
    def __init__(self, workflow, name):
        self.workflow = workflow
        self.name = name
        self.path = f"workflow.{name}"
        self.report = _Diagnostics()

    def validate(self):
        raw, report, path = self.workflow.get(self.name), self.report, self.path
        if not isinstance(raw, dict):
            report.add(
                path,
                f"a resume target must be an object with a run_id, not {type(raw).__name__}",
                "error",
            )
            return report.items
        report.required(
            str(raw.get("run_id", "") or "").strip(),
            f"{path}.run_id",
            "a resume target needs the run_id of the parked run to resume; without one this trigger starts a new run instead",
        )
        for key in raw:
            if key not in _RESUME_TARGET_FIELDS:
                report.add(
                    f"{path}.{key}",
                    f"unknown resume-target field {key!r}",
                    closest=_closest(str(key), _RESUME_TARGET_FIELDS),
                )
        actions = sorted(key for key in _ACTION_KEYS if self.workflow.get(key))
        if actions:
            report.add(
                path,
                f"this trigger declares both a resume target and an action ({', '.join(actions)}); a resume target REPLACES the action, so the action would never run — keep whichever you meant",
                "error",
            )
        return report.items


def _resume_target_issues(workflow: Any) -> list[Issue]:
    from gideon.automation.triggers.wakeup import RESUME_TARGET_KEY

    if isinstance(workflow, dict) and RESUME_TARGET_KEY in workflow:
        return _ResumeContract(workflow, RESUME_TARGET_KEY).validate()
    return []


class _TriggerDecoder:
    def __init__(self, raw):
        self.source = _RecordInput(raw if isinstance(raw, dict) else {})
        self.report = _Diagnostics()
        if not isinstance(raw, dict):
            self.report.add("", "a trigger must be an object", "error")
        self.kind = self.source.text("kind").strip().lower()
        self.spec = self.source.mapping("spec")
        self.gates = self.source.mapping("gates")

    def validate(self):
        source, report = self.source, self.report
        report.unknown(
            sorted(source.data),
            _known_fields(),
            lambda key: key,
            lambda key: f"unknown trigger field {key!r}",
        )
        report.extend(validate_spec(self.kind, self.spec))
        report.extend(validate_gates(self.gates))
        report.extend(_inline_credential_issues(source.data.get("workflow")))
        report.extend(_resume_target_issues(source.data.get("workflow")))
        report.extend(_token_ref_issues(source.data.get("spec")))
        for name, message in (
            ("id", "a trigger needs an id"),
            ("name", "a trigger needs a name"),
        ):
            report.required(source.text(name).strip(), name, message)
        overlap = report.choice(
            source.text("overlap", "skip"),
            ("skip", "queue", "parallel"),
            "skip",
            "overlap",
            "overlap policy",
        )
        state = report.choice(
            source.text("state", TriggerState.ACTIVE.value),
            tuple(state.value for state in TriggerState),
            TriggerState.ACTIVE.value,
            "state",
            "state",
        )
        return overlap, state

    def decode(self):
        overlap, state = self.validate()
        source = self.source
        values = source.strings(
            (
                "id",
                "name",
                "origin_harness",
                "expires_at",
                "next_fire_at",
                "last_run_id",
                "last_success_at",
                "last_failure_at",
                "last_fired_at",
                "last_alert_hash",
                "last_error_summary",
            )
        )
        defaults = {
            "created_by": "user",
            "session": "fresh",
            "model_tier": "background",
            "delivery": "none",
            "failure_delivery": "inbox",
            "health_status": TriggerHealth.OK.value,
        }
        values.update(
            {name: source.text(name, fallback) for name, fallback in defaults.items()}
        )
        values.update({name: source.mapping(name) for name in _TRIGGER_MAPPING_FIELDS})
        values.update(source.flags(("yield_to_user", "catch_up")))
        values.update(
            kind=self.kind,
            spec=dict(self.spec),
            gates=dict(self.gates),
            overlap=overlap,
            state=state,
            author=source.text("author").strip().lower(),
            enabled=bool(source.data.get("enabled", True)) and not self.report.fatal,
            resource_slots=list(map(str, source.data.get("resource_slots") or [])),
            run_count=_int(source.data.get("run_count"), 0),
            park_retry_after=_float(source.data.get("park_retry_after"), 0.0),
            last_alert_at=_float(source.data.get("last_alert_at"), 0.0),
        )
        return Trigger(**values), self.report.items


def parse_trigger(raw: dict[str, Any]) -> tuple[Trigger, list[Issue]]:
    return _TriggerDecoder(raw).decode()


def _number(value, default, conversion):
    try:
        return conversion(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    return _number(value, default, int)


def _float(value: Any, default: float) -> float:
    return _number(value, default, float)


def classify_weight(*, node_count: int, has_llm: bool, resumable: bool) -> str:
    requires_journal = node_count >= 2 or has_llm or resumable
    return RunWeight.FULL.value if requires_journal else RunWeight.LEDGER.value


def require_reason(outcome: str) -> bool:
    return outcome not in {Outcome.RAN.value, Outcome.RAN_LATE.value}


def fire_issues(record: FireRecord) -> list[Issue]:
    report = _Diagnostics()
    report.required(
        record.outcome in FIRE_OUTCOMES,
        "outcome",
        f"unknown outcome {record.outcome!r}",
    )
    if require_reason(record.outcome):
        report.required(
            record.reason.strip(),
            "reason",
            f"{record.outcome} must say why in one line; a suppression with no reason reads as the automation being broken",
        )
    if record.outcome == Outcome.RAN_LATE.value:
        report.required(
            record.scheduled_for,
            "scheduled_for",
            "ran_late is only meaningful next to the slot it missed",
        )
    return report.items


def unmapped_legacy_fields(legacy: str, field_names: list[str]) -> list[str]:
    accounted_for = LEGACY_FIELD_MAP.get(legacy, {})
    return list(filter(lambda name: name not in accounted_for, field_names))
