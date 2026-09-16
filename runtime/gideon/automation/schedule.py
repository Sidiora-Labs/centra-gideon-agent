"""Schedule records, canonical actions and the clock/display policies they share."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo

from croniter import croniter

from gideon.cognition.autonomous_framing import with_autonomous_framing
from gideon.core.timezones import resolve_zone, resolve_zone_name

try:
    from cron_descriptor import Options, get_description
except ImportError:
    Options = get_description = None

logger = logging.getLogger(__name__)
_CRONS_FILE = "crons.json"
_STORE_VERSION = 2
_MIN_INTERVAL_SECS = 60
_JOB_TIMEOUT_SECS = 1800
_STATUS_PENDING = "_pending"
_TIMER_POLL_SECS = 30
_JITTER_HOURLY_MAX = 20 * 60
_JITTER_DAILY_MAX = 2 * 3600


@dataclass
class ScheduleDefinition:
    kind: str
    every_secs: int | None = None
    at_ts: float | None = None
    cron_expr: str | None = None


@dataclass
class ScheduleJob:
    id: str
    name: str
    schedule: ScheduleDefinition = field(
        default_factory=lambda: ScheduleDefinition(kind="every")
    )
    channel: str | None = None
    thread_ts: str | None = None
    enabled: bool = True
    last_run_ts: float | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_outcome: str = ""
    dry_run: bool = False
    created_ts: float = 0.0
    delete_after_run: bool = False
    last_result: str | None = None
    context_enabled: bool = False
    acked_items: list[str] = field(default_factory=list)
    created_by: str = ""
    silent: bool = False
    session_key: str = ""
    last_posted_hash: str = ""
    consecutive_dupes: int = 0
    last_posted_at: float = 0.0
    last_failure_hash: str = ""
    last_failure_at: float = 0.0
    consecutive_failures: int = 0
    skip_dates: list[str] = field(default_factory=list)
    timezone: str = ""
    persistent_session: bool = True
    agent_sequence: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_secs: int = _JOB_TIMEOUT_SECS
    strict_schedule: bool = False
    action: dict = field(default_factory=dict)

    @property
    def _config(self) -> dict:
        action = self.action if isinstance(self.action, dict) else {}
        return action.get("config") or {}

    @property
    def provider(self) -> str:
        action = self.action if isinstance(self.action, dict) else {}
        return str(action.get("provider") or "")

    def _action_text(self, owner: str, field_name: str) -> str:
        if owner != self.provider:
            return ""
        return str(self._config.get(field_name) or "")

    @property
    def exec_mode(self) -> str:
        return {"run-script": "script", "bash": "command"}.get(self.provider, "agent")

    @property
    def message(self) -> str:
        return self._action_text("invoke-agent", "task_template")

    @property
    def agent_id(self) -> str:
        return self._action_text("invoke-agent", "agent")

    @property
    def model(self) -> str:
        return self._action_text("invoke-agent", "model")

    @property
    def approval_mode(self) -> str:
        return self._action_text("invoke-agent", "approval_mode")

    @property
    def script(self) -> str:
        return self._action_text("run-script", "script")

    @property
    def command(self) -> str:
        return self._action_text("bash", "command")

    @property
    def zt_timeout(self) -> int:
        if self.exec_mode == "agent":
            return 0
        try:
            return int(self._config.get("timeout") or 0)
        except (TypeError, ValueError):
            return 0


SCHEDULE_VARS = (
    "$EVENT",
    "$CONTEXT",
    "$last_result",
    "$now",
    "$timezone",
    "$job_id",
    "$job_name",
)


class RunContext:
    def __init__(self, job: ScheduleJob):
        self.job = job

    def prior_context(self) -> list[str]:
        sections = []
        if self.job.acked_items:
            lines = ["[Iteration context queue — messages from earlier runs]"]
            lines.extend(f"- {item}" for item in self.job.acked_items)
            sections.append("\n".join([*lines, "[End iteration context queue]"]))
        if self.job.last_result:
            sections.append(
                "\n".join(
                    [
                        "[Previous run result — do NOT repeat the same content]",
                        self.job.last_result,
                        "[End of previous run result]",
                    ]
                )
            )
        return sections

    def assemble(self) -> tuple[str, str]:
        key = "cron:" + self.job.id
        sections = self.prior_context() if self.job.persistent_session else []
        if not self.job.persistent_session:
            key += ":" + uuid.uuid4().hex[:8]
        return key, with_autonomous_framing("\n\n".join([*sections, self.job.message]))


def build_schedule_session_context(job: ScheduleJob) -> tuple[str, str]:
    return RunContext(job).assemble()


def _action(provider: str, **values) -> dict:
    return dict(provider=provider, config=values)


def make_agent_action(
    message: str = "", agent: str = "", model: str = "", approval_mode: str = ""
) -> dict:
    values = dict(
        task_template=message, agent=agent, model=model, approval_mode=approval_mode
    )
    return _action(
        "invoke-agent", **{key: value or "" for key, value in values.items()}
    )


def make_command_action(command: str, timeout: int = 0) -> dict:
    return _action("bash", command=command or "", timeout=int(timeout or 0))


def make_script_action(script: str, timeout: int = 0) -> dict:
    return _action("run-script", script=script or "", timeout=int(timeout or 0))


def _action_from_record(j: dict) -> dict:
    stored = j.get("action")
    if isinstance(stored, dict) and stored.get("provider"):
        return stored
    modes = (("script", make_script_action), ("command", make_command_action))
    for field_name, build in modes:
        if value := j.get(field_name):
            return build(value, int(j.get("zt_timeout") or 0))
    aliases = {
        "message": "message",
        "agent": "agent_id",
        "model": "model",
        "approval_mode": "approval_mode",
    }
    return make_agent_action(
        **{key: j.get(source) or "" for key, source in aliases.items()}
    )


class ActionPolicy:
    @staticmethod
    def script(config: dict) -> None:
        location = str(config.get("script") or "").strip()
        if location:
            from gideon.automation.schedule_script import resolve_script_path

            resolve_script_path(location)

    @staticmethod
    def agent(config: dict) -> None:
        permission = str(config.get("approval_mode") or "")
        if permission not in ("", "auto"):
            raise ValueError(f"Invalid approval_mode: {permission!r}")

    @classmethod
    def normalize(cls, source) -> dict:
        if not isinstance(source, dict) or not source.get("provider"):
            raise ValueError("action must be an object with a 'provider'")
        provider = str(source["provider"]).strip()
        configuration = source.get("config") or {}
        if not isinstance(configuration, dict):
            raise ValueError("action.config must be an object")
        validate = {"run-script": cls.script, "invoke-agent": cls.agent}.get(provider)
        if validate:
            validate(configuration)
        return dict(provider=provider, config=configuration)


def normalize_action(action: dict | None) -> dict:
    return ActionPolicy.normalize(action)


def cron_expr_matches(expr: str, dt: datetime) -> bool:
    try:
        matches = croniter.match(expr, dt)
    except (KeyError, ValueError):
        matches = False
    return matches


def validate_cron_expr(expr: str) -> bool:
    valid = croniter.is_valid
    return valid(expr)


class CronDescription:
    def __init__(self, expression: str):
        self.expression = expression

    def fixed_time(self, description: str, name: str) -> str:
        fields = self.expression.split()
        if not (
            name and len(fields) == 5 and all(value.isdigit() for value in fields[:2])
        ):
            return description
        try:
            zone = resolve_zone(name)
            local = (
                croniter(self.expression, datetime.now(zone))
                .get_next(datetime)
                .astimezone(zone)
            )
            reference = croniter(self.expression, datetime.now(timezone.utc)).get_next(
                datetime
            )
            label = local.strftime("%-I:%M %p %Z")
            for pattern in ("%-I:%M %p", "%I:%M %p"):
                updated = description.replace(
                    "At " + reference.strftime(pattern), "At " + label
                )
                if updated != description:
                    return updated
            return f"At {label}, {description.removeprefix('At ')}"
        except Exception:
            return description

    def render(self, zone: str) -> str:
        if get_description is None:
            return self.expression
        options = Options()
        options.use_24hour_time_format = False
        try:
            description = get_description(self.expression, options)
        except Exception:
            return self.expression
        return self.fixed_time(description, zone)


def _humanize_cron(expr: str, tz_name: str = "") -> str:
    return CronDescription(expr).render(tz_name)


def format_schedule(schedule: ScheduleDefinition, tz_name: str = "") -> str:
    match schedule.kind:
        case "cron" if schedule.cron_expr:
            return _humanize_cron(schedule.cron_expr, tz_name)
        case "every" if schedule.every_secs:
            amount = schedule.every_secs
            value, unit = (amount // 3600, "h") if amount >= 3600 else (amount, "s")
            return f"every {value}{unit}"
        case "at" if schedule.at_ts:
            zone = resolve_zone(tz_name)
            fire = datetime.fromtimestamp(schedule.at_ts, zone)
            pattern = "at %I:%M %p %Z"
            if fire.date() != datetime.now(zone).date():
                pattern += ", %b %-d"
            return fire.strftime(pattern)
    return schedule.kind


def get_local_tz() -> tuple[str, tzinfo]:
    name, _ = resolve_zone_name()
    return name, resolve_zone()


def _job_tz(job: ScheduleJob) -> tzinfo:
    name = job.timezone
    return resolve_zone(name)


class ScheduleClock:
    def __init__(self, job: ScheduleJob, now: float):
        self.job, self.now = job, now

    def interval(self) -> float | None:
        period = self.job.schedule.every_secs
        previous = self.job.last_run_ts
        if previous is None:
            previous = self.job.created_ts
        if period is None or previous is None:
            return None
        candidate = previous + period
        return candidate if candidate > self.now else self.now

    def once(self) -> float | None:
        due = self.job.schedule.at_ts
        return due if due is not None and due > self.now else None

    def calendar(self) -> float | None:
        expression = self.job.schedule.cron_expr
        if expression is None:
            return None
        origin = datetime.fromtimestamp(self.now, tz=_job_tz(self.job))
        return croniter(expression, origin).get_next(float)

    def next(self) -> float | None:
        policies = {"every": self.interval, "at": self.once, "cron": self.calendar}
        compute = policies.get(self.job.schedule.kind)
        return compute() if compute else None


def compute_next_run_ts(job: ScheduleJob, now: float | None = None) -> float | None:
    try:
        if job.enabled:
            instant = time.time() if now is None else now
            return ScheduleClock(job, instant).next()
    except Exception:
        logger.warning("Failed to compute next run for job %s", job.id, exc_info=True)
    return None
