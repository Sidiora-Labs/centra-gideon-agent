"""Admission, execution, retirement, and delivery of delegated work."""

import asyncio
import io
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from gideon.assurance.validation import _AGENT_NAME_RE
from gideon.cognition.context import PromptAssembler
from gideon.core.config.loader import AppConfig
from gideon.core.textfmt import extract_options
from gideon.engine.hooks import (
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    fire_tool_hooks,
    safe_read_file,
)
from gideon.engine.session import ConversationDirectory
from gideon.engine.session_workspace import result_path as _ws_result_path
from gideon.engine.subagent_persistence import (
    _agent_dir,
    _cleanup_session_files_sync,
    create_agent_folder,
    delete_agent_folder,
    list_orphans,
    prune_stale_tombstones,
    update_state,
    write_result_chunk,
    write_tombstone,
)
from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
)
from gideon.operations.stats import Stats
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel

logger = logging.getLogger(__name__)
_MAX_CONCURRENT = 3
_AUTO_FLOOR = 2
_AUTO_CEILING = 8
_CPU_HEADROOM = 2
_MAX_DONE_RESULT_LEN = 50_000
_TIMEOUT_SECS = 1800
_TURN_LIMIT = 100
_REAPER_INTERVAL = 60
_RESET_TIMEOUT = 30.0
_ON_DONE_TIMEOUT = 1200.0
INJECTION_TIMEOUT = 300.0
_CIRCUIT_BREAKER_THRESHOLD = 5
CAPABILITY_RESEARCH = "research"
CAPABILITY_MUTATING = "mutating"
_SYSTEM_PREFIX = (
    "You are a focused sub-agent. Complete the following task concisely. "
    "Do NOT create other agents. Report your result directly.\n"
    "IMPORTANT: Do NOT narrate your own process, failures, retries, or "
    "orchestration decisions. The user does not care how you got the answer. "
    "Only output meaningful, actionable results. Never output greetings or filler.\n\n"
)


def _read_memory_value(text: str, field_name: str) -> float:
    for row in text.splitlines():
        if row.startswith(field_name + ":"):
            return int(row.split()[1]) / (1024**2)
    return -1.0


def _total_memory_gb() -> float:
    try:
        if sys.platform == "linux":
            return max(
                0.0, _read_memory_value(safe_read_file("/proc/meminfo"), "MemTotal")
            )
        if sys.platform == "darwin":
            raw = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2)
            return int(raw.decode().strip()) / (1024**3)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return 0.0


def resolve_max_subagents(configured: int, per_agent_gb: float = 4.0) -> int:
    if configured > 0:
        return configured
    processors, memory = os.cpu_count() or 0, _total_memory_gb()
    if processors <= 0 or memory <= 0:
        logger.info("Subagent capacity uses fixed fallback %d", _MAX_CONCURRENT)
        return _MAX_CONCURRENT
    capacity = min(
        max(1, processors - _CPU_HEADROOM),
        max(1, int(memory // max(0.5, per_agent_gb))),
    )
    return min(_AUTO_CEILING, max(_AUTO_FLOOR, capacity))


def _validate_agent(requested: str) -> tuple[str, str]:
    if not requested:
        return "", ""
    names = AppConfig.load().agents
    if requested in names:
        return requested, ""
    alternatives = sorted(set(names) - {"gideon", "gideon-orchestrator"})
    return (
        "",
        f"unknown agent {requested!r}; valid agents: {', '.join(alternatives) or '(none configured)'}",
    )


def _redact(text: str) -> str:
    for scrub in (redact_exfiltration_urls, redact_credentials):
        text, _ = scrub(text)
    return text


def _done_result(text: str) -> str:
    safe = _redact(text) if text else ""
    return (
        "…(truncated)\n" + safe[-_MAX_DONE_RESULT_LEN:]
        if len(safe) > _MAX_DONE_RESULT_LEN
        else safe
    )


def _fanout_key(info: "SubagentInfo") -> str:
    return next((key for key in (info.parent_run, info.parent_session_key) if key), "")


def _timeout_context(info: "SubagentInfo", *, include_elapsed: bool = True) -> str:
    details = [f"turn {info.turns}/{info.max_turns}"]
    if info.last_tool:
        details += [f"last tool: {_redact(info.last_tool)}"]
    if include_elapsed:
        duration = info.elapsed if info.elapsed > 0 else time.time() - info.started
        details += [f"elapsed: {int(duration)}s"]
    return " | ".join(details)


def check_memory_available(
    min_gb: float = 4.0, path: str = "/proc/meminfo"
) -> tuple[bool, float]:
    try:
        available = _read_memory_value(safe_read_file(path), "MemAvailable")
    except (OSError, ValueError, IndexError):
        available = -1.0
    return (True, -1.0) if available < 0 else (available >= min_gb, round(available, 2))


def validate_cwd(cwd: str, allowed_roots: list[str]) -> tuple[str, str]:
    if not cwd:
        return "", ""
    if not allowed_roots:
        return "", "cwd override is disabled (subagent_cwd_allowed_roots is empty)"
    try:
        expanded = os.path.expanduser(cwd)
        if not os.path.isabs(expanded):
            return "", "cwd must be an absolute path"
        resolved = os.path.realpath(expanded)
    except (OSError, ValueError) as error:
        return "", f"cwd resolution failed: {error}"
    if not os.path.isdir(resolved):
        return "", "cwd does not exist or is not a directory"
    for configured in allowed_roots:
        root = os.path.realpath(os.path.expanduser(configured))
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved, ""
    return "", f"cwd is not under any allowed root: {allowed_roots}"


def resolve_capability_class(*, capability_class: str, approval_mode: str) -> str:
    named = (capability_class or "").strip().lower()
    if named not in {CAPABILITY_RESEARCH, CAPABILITY_MUTATING}:
        named = CAPABILITY_RESEARCH if approval_mode == "auto" else CAPABILITY_MUTATING
    return named


@dataclass
class SubagentInfo:
    id: str
    task: str
    started: float = field(default_factory=time.time)
    done: bool = False
    result: str = ""
    result_path: str = ""
    error: str = ""
    parent_session_key: str = ""
    agent: str = ""
    approval_mode: str = ""
    capability_class: str = ""
    dry_run: bool = False
    silent: bool = False
    turns: int = 0
    last_tool: str = ""
    max_turns: int = 0
    reaped: bool = False
    streaming_text: str = ""
    elapsed: float = 0.0
    _raw_task: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cwd: str = ""
    sandbox: str = "none"
    extra_env: dict[str, str] = field(default_factory=dict)
    _pid: int | None = None
    parent_run: str = ""
    queued: bool = False
    cancelled: bool = False
    _outcome_noted: bool = False


@dataclass
class _ExecutionPass:
    info: SubagentInfo
    client: Any
    session_key: str
    policy: str
    turn_limit: int
    research: bool
    output: io.StringIO = field(default_factory=io.StringIO)
    turns: int = 0


AnnounceCallback = Callable[[list[SubagentInfo]], Awaitable[None]]
SubagentEventCallback = Callable[[str, SubagentInfo, dict], Awaitable[None]]


class ToolApprovalCallback(Protocol):
    async def __call__(self, event: LLMEvent, parent_session_key: str = "") -> bool: ...


class SpawnApprovalCallback(Protocol):
    async def __call__(
        self, request_id: str, description: str, parent_session_key: str = ""
    ) -> bool: ...


class DelegationSupervisor:
    """Keep admission, owned execution, and parent delivery independently bounded."""

    def __init__(
        self,
        sessions: ConversationDirectory,
        ctx_builder: PromptAssembler,
        on_done: AnnounceCallback | None = None,
        max_concurrent: int = _MAX_CONCURRENT,
        default_turn_limit: int = _TURN_LIMIT,
        default_timeout: int = _TIMEOUT_SECS,
        on_tool_approval: ToolApprovalCallback | None = None,
        on_tool_approval_factory: (
            Callable[[SubagentInfo], Callable[[LLMEvent], Awaitable[bool]]] | None
        ) = None,
        on_spawn_approval: SpawnApprovalCallback | None = None,
        is_yolo: Callable[[], bool] | None = None,
        on_event: SubagentEventCallback | None = None,
        run_lane_cap: int = 0,
        delivery_coalesce_secs: float = 0.05,
    ):
        self._sessions, self._ctx_builder = sessions, ctx_builder
        self._on_done, self._on_event = on_done, on_event
        self._max_concurrent = max_concurrent
        self._run_lane_cap = run_lane_cap if run_lane_cap > 0 else max_concurrent
        self._default_turn_limit = default_turn_limit
        self._default_timeout = (
            default_timeout if default_timeout > 0 else _TIMEOUT_SECS
        )
        self._delivery_coalesce_secs = max(0.0, delivery_coalesce_secs)
        self._on_tool_approval = on_tool_approval
        self._on_tool_approval_factory = on_tool_approval_factory
        self._on_spawn_approval, self._is_yolo = on_spawn_approval, is_yolo
        self._running_count = 0
        self._running_by_fanout: dict[str, int] = {}
        self._fanout_failures: dict[str, int] = {}
        self._fanout_stops: dict[str, str] = {}
        self._agents: dict[str, SubagentInfo] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._queue: list[SubagentInfo] = []
        self._pending_delivery: dict[str, list[SubagentInfo]] = {}
        self._delivery_tasks: dict[str, asyncio.Task] = {}
        self._reaper_task: asyncio.Task | None = None
        self.hook_store: Any = None
        if hasattr(sessions, "register_child_stopper"):
            sessions.register_child_stopper(self.stop_children_of)
        try:
            self._global_approval_mode = AppConfig.load().agent.approval_mode
        except Exception:
            logger.warning("Could not read default approval policy", exc_info=True)
            self._global_approval_mode = ""

    @staticmethod
    async def _approve_and_log(
        client,
        request_id: str | int,
        session_key: str,
        event: LLMEvent,
        *,
        metadata: dict | None = None,
    ) -> None:
        await client.approve_tool(request_id)
        outcome = "auto_approved" if metadata and metadata.get("reason") else "approved"
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome=outcome,
            request_id=request_id,
            metadata=metadata,
        )

    @staticmethod
    async def _reject_and_log(
        client,
        request_id: str | int,
        session_key: str,
        event: LLMEvent,
        *,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        await client.reject_tool(request_id)
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome="denied" if error else "rejected",
            request_id=request_id,
            error=error or "",
            metadata=metadata,
        )

    @staticmethod
    def _spawn_audit(parent: str, outcome: str, **metadata) -> None:
        sel().log_tool_invocation(
            session_key=parent or "",
            source="subagent",
            tool_name="subagent_run",
            outcome=outcome,
            metadata=metadata,
        )

    @staticmethod
    def _refused(task: str, agent: str, error: str) -> SubagentInfo:
        return SubagentInfo(
            id=uuid.uuid4().hex[:8], task=task, agent=agent, done=True, error=error
        )

    def _host_admission(
        self, task: str, agent: str, parent: str
    ) -> SubagentInfo | None:
        try:
            minimum = AppConfig.load().agent.spawn_min_memory_gb
        except Exception:
            minimum = 4.0
        enough, available = check_memory_available(min_gb=minimum)
        if not enough:
            self._spawn_audit(
                parent,
                "refused_low_memory",
                available_gb=available,
                min_gb=minimum,
                task=task[:120],
            )
            return self._refused(
                task,
                agent,
                f"spawn refused: only {available:.1f} GB memory available (need {minimum:.0f} GB)",
            )
        try:
            from gideon.security.guardrails.incident import incident_active

            if incident_active():
                self._spawn_audit(parent, "refused_incident", task=task[:120])
                return self._refused(
                    task,
                    agent,
                    "spawn refused: incident mode active (resume with `gideon incident off`)",
                )
        except Exception:
            logger.debug(
                "subagent spawn incident check failed (fail-open)", exc_info=True
            )
        try:
            from gideon.security.guardrails.budgets import (
                BudgetVerdict,
                budget_from_config,
                get_meter,
            )

            budget = budget_from_config()
            if not budget.is_unlimited:
                verdict, reason = get_meter().check_day(budget)
                if verdict is BudgetVerdict.EXCEEDED:
                    self._spawn_audit(
                        parent,
                        "refused_budget_exceeded",
                        reason=reason,
                        task=task[:120],
                    )
                    return self._refused(
                        task,
                        agent,
                        f"spawn refused: {reason} (resets next day, or raise the budget in Settings → Guardrails)",
                    )
        except Exception:
            logger.debug(
                "subagent spawn budget check failed (fail-open)", exc_info=True
            )
        return None

    def spawn(
        self,
        task: str,
        parent_session_key: str = "",
        agent: str = "",
        max_turns: int = 0,
        model: str | None = None,
        cwd: str = "",
        approval_mode: str | None = None,
        capability_class: str | None = None,
        silent: bool = False,
        dry_run: bool = False,
        parent_run: str = "",
        sandbox: str = "none",
        extra_env: dict[str, str] | None = None,
    ) -> SubagentInfo | None:
        public_task = _redact(task)
        rejected = self._host_admission(public_task, agent, parent_session_key)
        if rejected is not None:
            return rejected
        directory = ""
        if cwd:
            try:
                roots = AppConfig.load().agent.subagent_cwd_allowed_roots
            except Exception:
                roots = []
            directory, error = validate_cwd(cwd, roots)
            if error:
                self._spawn_audit(
                    parent_session_key,
                    "rejected_invalid_cwd",
                    cwd=cwd[:200],
                    reason=error,
                    task=public_task[:120],
                )
                return self._refused(public_task, agent, f"spawn refused: {error}")
        if agent:
            agent, error = _validate_agent(agent)
            if error:
                return self._refused(public_task, "", error)
        info = SubagentInfo(
            id=uuid.uuid4().hex[:8],
            task=public_task,
            parent_session_key=parent_session_key,
            agent=agent,
            max_turns=max_turns,
            model=model or "",
            cwd=directory,
            approval_mode=approval_mode or "",
            capability_class=capability_class or "",
            silent=silent,
            dry_run=dry_run,
            parent_run=parent_run,
            sandbox=sandbox or "none",
            extra_env=dict(extra_env or {}),
            _raw_task=task,
        )
        stopped = self._fanout_stops.get(_fanout_key(info))
        if stopped:
            info.done, info.error = True, f"spawn refused: {stopped}"
            self._spawn_audit(
                parent_session_key,
                "refused_fanout_stopped",
                subagent_id=info.id,
                reason=stopped,
            )
            return info
        self._agents[info.id] = info
        if self._has_capacity(info):
            self._dispatch_run(info)
        else:
            info.queued = True
            self._queue.append(info)
        return info

    def _has_capacity(self, info: SubagentInfo) -> bool:
        return (
            self._running_count < self._max_concurrent
            and self._lane_count(_fanout_key(info)) < self._run_lane_cap
        )

    def _spawn_permission(self, info: SubagentInfo) -> str | None:
        trusted = bool(
            info.parent_session_key
            and self._sessions.get_approval_policy(info.parent_session_key) == "auto"
        )
        if self._is_yolo and self._is_yolo():
            return ""
        if info.approval_mode == "auto":
            return "approval_mode_auto"
        if trusted:
            return "parent_trusted"
        hooks = self._ctx_builder.hooks if self._ctx_builder else None
        if hooks and hooks.auto_approve_subagent_spawn is True:
            return "tool_calls_gated"
        return None

    def _dispatch_run(self, info: SubagentInfo) -> None:
        info.queued = False
        self._inc_running(info)
        reason = self._spawn_permission(info)
        if reason is not None:
            self._tasks[info.id] = asyncio.create_task(self._run(info))
            self._log_spawned(info)
            if reason:
                self._spawn_audit(
                    info.parent_session_key,
                    "auto_approved_spawn",
                    subagent_id=info.id,
                    reason=reason,
                )
        elif self._on_spawn_approval:
            self._tasks[info.id] = asyncio.create_task(self._spawn_with_approval(info))
        else:
            info.done, info.error = (
                True,
                "spawn rejected: no approval mechanism configured",
            )
            self._dec_running(info)
            self._drain_queue()
            has_hooks = bool(self._ctx_builder and self._ctx_builder.hooks)
            self._spawn_audit(
                info.parent_session_key,
                "rejected_spawn" if has_hooks else "rejected",
                subagent_id=info.id,
                reason=(
                    "no_approval_mechanism" if has_hooks else "no approval mechanism"
                ),
            )
            if not has_hooks and self._on_done:
                self._tasks[info.id] = asyncio.ensure_future(self._safe_announce(info))
            return
        from gideon.automation.triggers.lifecycle_fire import (
            fire_sync,
            subagent_spawn_payload,
        )

        identity: dict = dict(
            subagent_id=info.id,
            parent_session_key=info.parent_session_key,
            agent_role=info.agent or "",
        )
        fire_sync(subagent_spawn_payload(**identity), **identity)

    async def _spawn_with_approval(self, info: SubagentInfo) -> None:
        assert self._on_spawn_approval is not None
        try:
            approved = await self._on_spawn_approval(
                f"spawn:{info.id}",
                f"subagent_run({_redact(info.task)[:80]})",
                info.parent_session_key,
            )
        except Exception:
            logger.exception("Spawn approval failed for %s", info.id)
            approved = False
        if approved:
            self._log_spawned(info)
            await self._run(info)
            return
        info.done, info.error = True, "spawn rejected"
        self._dec_running(info)
        self._drain_queue()
        self._tasks.pop(info.id, None)
        self._spawn_audit(info.parent_session_key, "rejected", subagent_id=info.id)
        if self._on_done:
            await self._safe_announce(info)

    def _log_spawned(self, info: SubagentInfo) -> None:
        try:
            create_agent_folder(
                info.id,
                task=info.task,
                agent=info.agent,
                parent_session=info.parent_session_key,
                max_turns=info.max_turns,
            )
        except Exception:
            logger.warning(
                "Failed to create agent folder for %s", info.id, exc_info=True
            )
        Stats().inc_subagent_spawned()
        self._spawn_audit(
            info.parent_session_key,
            "spawned",
            subagent_id=info.id,
            agent=info.agent or "gideon",
            cwd=info.cwd,
        )

    def _lane_count(self, fkey: str) -> int:
        return self._running_by_fanout.get(fkey, 0)

    def _inc_running(self, info: SubagentInfo) -> None:
        lane = _fanout_key(info)
        self._running_count += 1
        self._running_by_fanout[lane] = self._lane_count(lane) + 1

    def _dec_running(self, info: SubagentInfo) -> None:
        lane = _fanout_key(info)
        self._running_count = max(0, self._running_count - 1)
        remaining = self._lane_count(lane) - 1
        if remaining <= 0:
            self._running_by_fanout.pop(lane, None)
        else:
            self._running_by_fanout[lane] = remaining

    def _drain_queue(self) -> None:
        while self._queue and self._running_count < self._max_concurrent:
            eligible = next(
                (i for i, info in enumerate(self._queue) if self._has_capacity(info)),
                None,
            )
            if eligible is None:
                return
            info = self._queue.pop(eligible)
            if info.done or info.cancelled:
                continue
            refusal = self._fanout_stops.get(_fanout_key(info))
            if refusal:
                info.done, info.error = True, f"spawn refused: {refusal}"
                continue
            self._dispatch_run(info)
            if self._queue and self._running_count < self._max_concurrent:
                asyncio.get_event_loop().call_later(2.0, self._drain_queue)
            return

    def _note_child_outcome(self, info: SubagentInfo) -> None:
        if info._outcome_noted:
            return
        info._outcome_noted = True
        lane = _fanout_key(info)
        if info.cancelled:
            return
        if not info.error:
            self._fanout_failures.pop(lane, None)
            return
        failures = self._fanout_failures.get(lane, 0) + 1
        self._fanout_failures[lane] = failures
        if failures < _CIRCUIT_BREAKER_THRESHOLD or lane in self._fanout_stops:
            return
        self._fanout_stops[lane] = (
            f"fan-out breaker tripped after {failures} consecutive child failures"
        )
        try:
            self._spawn_audit(
                info.parent_session_key,
                "fanout_breaker_tripped",
                fanout=lane,
                failures=failures,
            )
        except Exception:
            logger.debug("SEL audit failed for breaker trip %s", lane, exc_info=True)

    def _charge_child_and_check_budget(self, info: SubagentInfo) -> None:
        try:
            from gideon.security.guardrails.budgets import (
                BudgetVerdict,
                get_meter,
                run_budget_from_config,
            )

            budget = run_budget_from_config()
            if budget.is_unlimited:
                return
            lane, meter = _fanout_key(info), get_meter()
            meter.charge(
                info.input_tokens + info.output_tokens, info.cost_usd, run_key=lane
            )
            verdict, reason = meter.check_run(lane, budget)
            if verdict is not BudgetVerdict.EXCEEDED or lane in self._fanout_stops:
                return
            self._fanout_stops[lane] = stop_reason = f"run budget exceeded ({reason})"
            try:
                self._spawn_audit(
                    info.parent_session_key,
                    "fanout_budget_exceeded",
                    fanout=lane,
                    reason=reason,
                )
            except Exception:
                logger.debug("SEL audit failed for budget stop %s", lane, exc_info=True)
            asyncio.ensure_future(self.cancel_fanout(lane, reason=stop_reason))
        except Exception:
            logger.debug("fan-out budget check failed (fail-open)", exc_info=True)

    def _maybe_clear_fanout(self, fkey: str) -> None:
        occupied = self._lane_count(fkey) > 0 or any(
            _fanout_key(info) == fkey for info in self._queue
        )
        if occupied:
            return
        for table in (self._fanout_failures, self._fanout_stops):
            table.pop(fkey, None)
        try:
            from gideon.security.guardrails.budgets import get_meter

            get_meter().end_run(fkey)
        except Exception:
            logger.debug("fan-out meter end_run failed for %s", fkey, exc_info=True)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def running_count(self) -> int:
        return self._running_count

    @property
    def running(self) -> list[SubagentInfo]:
        return [info for info in self._agents.values() if not info.done]

    @property
    def all_agents(self) -> list[SubagentInfo]:
        return [*self._agents.values()]

    def get(self, agent_id: str) -> SubagentInfo | None:
        return self._agents.get(agent_id)

    def live_controls(self, agent_id: str) -> dict[str, Any] | None:
        info = self.get(agent_id)
        if info is None:
            return None
        empty = {"model": info.model, "effort": "", "models": [], "efforts": []}
        if info.done or info.queued:
            return empty
        provider = self._sessions.get_provider(f"subagent:{agent_id}")
        controls = getattr(provider, "live_controls", None)
        if not callable(controls):
            return empty
        offered = controls()
        if os.environ.get("GIDEON_HOSTED", "").strip() == "1":
            offered["models"] = []
        return offered

    async def set_live_control(
        self, agent_id: str, axis: str, value: str
    ) -> dict[str, Any]:
        from gideon.integrations.acp.live_controls import LiveControlUnavailable

        offered = self.live_controls(agent_id)
        if offered is None:
            raise KeyError(agent_id)
        choices = (
            offered["models"]
            if axis == "model"
            else [row["value"] for row in offered["efforts"]]
            if axis == "effort"
            else []
        )
        if value not in choices:
            raise LiveControlUnavailable(
                f"This agent does not offer live {axis} selection"
            )
        provider = self._sessions.get_provider(f"subagent:{agent_id}")
        change = getattr(provider, "set_live_control", None)
        if change is None:
            raise LiveControlUnavailable("This agent has no live control contract")
        await change(axis, value)
        if axis == "model":
            info = self._agents[agent_id]
            info.model = value
        return self.live_controls(agent_id) or offered

    @property
    def count(self) -> int:
        return sum(not info.done for info in self._agents.values())

    def running_agents_for(self, parent_key: str) -> list[dict]:
        summaries = []
        for info in self.running:
            if info.parent_session_key == parent_key:
                summaries.append(
                    dict(
                        id=info.id,
                        task=_redact(info.task[:80]),
                        agent=_redact(info.agent),
                        turns=info.turns,
                        last_tool=_redact(info.last_tool),
                        startedAt=info.started,
                    )
                )
        return summaries

    def _execution_policy(self, info: SubagentInfo) -> str:
        policy = self._sessions.get_approval_policy(info.parent_session_key)
        operation = ""
        caller = info.parent_session_key or f"subagent:{info.id}"
        if not policy:
            if info.approval_mode == "auto":
                operation = "subagent.approval_mode_auto_policy"
            elif self._is_yolo and self._is_yolo():
                operation, caller = (
                    "subagent.yolo_policy_fallback",
                    info.parent_session_key,
                )
            elif not info.parent_session_key and self._global_approval_mode == "auto":
                operation = "subagent.config_policy_fallback"
            elif (
                self._ctx_builder
                and self._ctx_builder.hooks
                and self._ctx_builder.hooks.auto_approve_subagent_tools is True
            ):
                operation = "subagent.auto_approve_subagent_tools_policy"
            if operation:
                policy = "auto"
                sel().log_api_access(
                    caller=caller,
                    operation=operation,
                    outcome="ok",
                    source="subagent",
                    resources=f"subagent_id={info.id}",
                )
        if policy == "auto":
            from gideon.security.guardrails.policy import ceiling_permits_approval

            if not ceiling_permits_approval("auto"):
                policy = ""
                sel().log_api_access(
                    caller=info.parent_session_key or f"subagent:{info.id}",
                    operation="subagent.approval_grant_refused",
                    outcome="blocked",
                    source="guardrails",
                    resources=f"subagent_id={info.id},grant=auto,refused_by=governance_ceiling",
                )
        return policy

    def _runner_arguments(self, info: SubagentInfo, policy: str) -> dict[str, Any]:
        options: dict[str, Any] = (
            {"model": info.model} if info.model else {"model_axis": "orchestration"}
        )
        if info.cwd:
            options["cwd"] = info.cwd
        if info.sandbox and info.sandbox != "none":
            options["sandbox"] = info.sandbox
        attended = bool(
            info.parent_session_key
            and self._sessions.has_session(info.parent_session_key)
        )
        if info.approval_mode == "auto" or (policy == "auto" and not attended):
            options["unattended"] = True
        if info.dry_run:
            options["dry_run"] = True
        if info.extra_env:
            options["extra_env"] = dict(info.extra_env)
        return options

    def _record_execution_identity(self, info: SubagentInfo, client, key: str) -> None:
        try:
            pid = self._sessions.get_pid(key)
            if pid:
                info._pid = pid
                update_state(info.id, pid=pid, pid_recorded_at=time.time())
        except Exception:
            logger.debug("Failed to record PID for %s", info.id, exc_info=True)
        try:
            update_state(
                info.id, session_id=getattr(client, "session_id", ""), provider="acp"
            )
        except Exception:
            logger.debug("Failed to record session_id for %s", info.id, exc_info=True)

    async def _run_inner(self, info: SubagentInfo, session_key: str) -> None:
        policy = self._execution_policy(info)
        agent = info.agent or self._sessions.get_agent(info.parent_session_key)
        if not info.agent and agent:
            sel().log_api_access(
                caller=f"subagent:{info.id}",
                operation="subagent.agent_inheritance",
                outcome="ok",
                source="subagent",
                resources=f"subagent_id={info.id},inherited_agent={agent}",
            )
        client, initial, _ = await self._sessions.get_or_create(
            session_key,
            agent=agent or None,
            approval_policy=policy,
            **self._runner_arguments(info, policy),
        )
        message = info._raw_task or info.task
        if not (info.agent and _AGENT_NAME_RE.fullmatch(info.agent)):
            from gideon.integrations.prompt_providers.runtime import (
                render_snippet_block,
            )

            prefix = render_snippet_block("subagent-system-prefix")
            message = (prefix + "\n\n" if prefix else _SYSTEM_PREFIX) + message
        prompt, _ = self._ctx_builder.build_message(message, initial, session_key)
        await self._fire_event(
            "subagent_spawn", info, {"task": _redact(info.task), "agent": agent or ""}
        )
        self._record_execution_identity(info, client, session_key)
        info.result_path = str(_agent_dir(info.id) / "result.txt")
        run = _ExecutionPass(
            info=info,
            client=client,
            session_key=session_key,
            policy=policy,
            turn_limit=info.max_turns or self._default_turn_limit or _TURN_LIMIT,
            research=resolve_capability_class(
                capability_class=info.capability_class, approval_mode=info.approval_mode
            )
            == CAPABILITY_RESEARCH,
        )
        handlers = {
            EVENT_TEXT_CHUNK: self._consume_text,
            EVENT_PERMISSION_REQUEST: self._consume_permission,
            EVENT_TOOL_CALL: self._consume_tool,
            EVENT_COMPLETE: self._consume_completion,
        }
        async for event in client.stream(prompt):
            consume = handlers.get(event.kind)
            if consume is not None and not await consume(run, event):
                break
        if not info.done:
            self._finish_output(run)

    async def _consume_text(self, run: _ExecutionPass, event: LLMEvent) -> bool:
        run.output.write(event.text)
        write_result_chunk(run.info.id, event.text)
        safe = _redact(event.text)
        run.info.streaming_text += safe
        if len(run.info.streaming_text) > 50_000:
            run.info.streaming_text = (
                "…(truncated)\n" + run.info.streaming_text[-40_000:]
            )
        await self._fire_event("subagent_chunk", run.info, {"text": safe})
        return True

    async def _consume_permission(self, run: _ExecutionPass, event: LLMEvent) -> bool:
        info = run.info
        run.turns += 1
        info.turns, info.last_tool = run.turns, event.title or ""
        try:
            update_state(info.id, turns=run.turns, last_tool=info.last_tool)
        except Exception:
            pass
        await self._fire_event(
            "subagent_tool",
            info,
            {"tool": _redact(info.last_tool), "tool_kind": event.tool_kind},
        )
        if run.turns > run.turn_limit:
            info.result = run.output.getvalue() or "_Partial output._"
            self._abnormal_exit(info, "turn_limit", f"turn_limit:{run.turn_limit}")
            return False
        approved, error, metadata = await self._permission_decision(run, event)
        if approved:
            await self._approve_and_log(
                run.client, event.request_id, run.session_key, event, metadata=metadata
            )
        else:
            await self._reject_and_log(
                run.client,
                event.request_id,
                run.session_key,
                event,
                error=error,
                metadata=metadata,
            )
        return True

    async def _permission_decision(
        self, run: _ExecutionPass, event: LLMEvent
    ) -> tuple[bool, str | None, dict | None]:
        info = run.info
        from gideon.security.guardrails.policy import (
            TOOL_READ_WRITE,
            profile_for_session,
            tool_grant_denial,
            tool_grant_posture,
        )

        profile = profile_for_session(run.session_key)
        if info.capability_class == CAPABILITY_MUTATING and not run.research:
            profile = tool_grant_posture(TOOL_READ_WRITE)
        grant_denial = tool_grant_denial(
            event.title or "", profile.tool_grants, profile.tool_allowlist
        )
        if grant_denial:
            return (
                False,
                grant_denial,
                {"subagent_id": info.id, "reason": "tool_grant_deny"},
            )
        if run.research:
            from gideon.automation.workflows.batch_compile import is_write_tool

            if is_write_tool(event.title or ""):
                return (
                    False,
                    "research_capability_deny",
                    dict(
                        subagent_id=info.id,
                        capability_class=CAPABILITY_RESEARCH,
                        tool=event.title or "",
                    ),
                )
        hook = self._ctx_builder.hooks.on_tool_call(event.title)
        if hook.action == TOOL_DENY:
            return False, "hook_deny", None
        if hook.action == TOOL_AUTO_APPROVE:
            return True, None, {"subagent_id": info.id, "reason": "hook_auto_approve"}
        if run.policy == "auto":
            return True, None, {"subagent_id": info.id, "reason": "parent_policy_auto"}
        if self._on_tool_approval_factory:
            approved = await self._on_tool_approval_factory(info)(event)
            metadata = {"subagent_id": info.id}
            if not approved:
                metadata["reason"] = "factory_rejected"
            return bool(approved), None, metadata
        if self._on_tool_approval:
            approved = await self._on_tool_approval(event, info.parent_session_key)
            return bool(approved), None, {"subagent_id": info.id} if approved else None
        return False, None, {"subagent_id": info.id, "reason": "no_policy_deny_default"}

    async def _consume_tool(self, run: _ExecutionPass, event: LLMEvent) -> bool:
        info = run.info
        sel().log_tool_invocation(
            session_key=run.session_key,
            source="subagent",
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome="auto_approved",
            metadata={"subagent_id": info.id},
        )
        await fire_tool_hooks(
            self.hook_store,
            event.title,
            event.tool_input,
            subagent_id=info.id,
            parent_session_key=info.parent_session_key,
            agent_role=info.agent,
        )
        return True

    async def _consume_completion(self, run: _ExecutionPass, event: LLMEvent) -> bool:
        info = run.info
        info.input_tokens = int(getattr(event, "input_tokens", 0) or 0)
        info.output_tokens = int(getattr(event, "output_tokens", 0) or 0)
        price = float(getattr(event, "cost_usd", 0.0) or 0.0)
        if not price and info.model:
            from gideon.operations.pricing import estimate_cost

            price = estimate_cost(
                info.model,
                input_tokens=info.input_tokens,
                output_tokens=info.output_tokens,
                cache_read_tokens=int(getattr(event, "cache_read_tokens", 0) or 0),
                cache_creation_tokens=int(
                    getattr(event, "cache_creation_tokens", 0) or 0
                ),
            )
        info.cost_usd = price
        try:
            event.cost_usd = price
        except (AttributeError, TypeError):
            pass
        self._record_subagent_usage(info, run.session_key, event)
        return False

    def _finish_output(self, run: _ExecutionPass) -> None:
        info = run.info
        raw = run.output.getvalue()
        content, _ = extract_options(raw) if raw else (raw, [])
        info.result = (_redact(content) if content else "") or "_No response._"
        if info.result_path:
            from pathlib import Path

            from gideon.cognition.context_management import (
                cap_result_file,
                evict_completed_agents,
            )

            cap_result_file(Path(info.result_path))
            info.result = info.result[:3000]
            evict_completed_agents(self._agents)
        info.done = True
        self._sessions.record_success(run.session_key)
        self._charge_child_and_check_budget(info)
        Stats().inc_subagent_completed()

    @staticmethod
    def _record_subagent_usage(
        info: SubagentInfo, session_key: str, event: object
    ) -> None:
        from gideon.operations.usage_ledger import record_from_event

        record_from_event(
            event,
            source="subagent",
            session_key=info.parent_session_key or session_key,
            agent=info.agent or "",
            provider="acp",
            model=info.model or "",
        )

    def _abnormal_exit(self, info: SubagentInfo, cause: str, error: str) -> None:
        if info.reaped:
            return
        info.done, info.error = True, error
        Stats().inc_subagent_failed()
        self._write_tombstone(info, cause)

    @staticmethod
    def _completion_payload(info: SubagentInfo, *, usage: bool) -> dict:
        result = dict(
            elapsed=info.elapsed,
            error=_redact(info.error) if info.error else None,
            task=_redact(info.task),
            agent=_redact(info.agent),
            result=_done_result(info.result),
        )
        if usage:
            result.update(
                cost_usd=round(info.cost_usd, 6),
                tokens=info.input_tokens + info.output_tokens,
            )
        return result

    async def _retire_session(
        self, info: SubagentInfo, session_key: str, *, reaper: bool
    ) -> None:
        try:
            await asyncio.wait_for(
                self._sessions.reset(session_key), timeout=_RESET_TIMEOUT
            )
        except asyncio.TimeoutError:
            self._sigkill_session(session_key)
            if not reaper:
                try:
                    sel().log_tool_invocation(
                        session_key=session_key,
                        source="subagent",
                        tool_name="run_finally_force_kill",
                        outcome="sigkill",
                        metadata={"subagent_id": info.id},
                    )
                except Exception:
                    logger.exception("Subagent %s: SEL audit failed", info.id)
        except Exception:
            logger.exception("Subagent %s: reset failed", info.id)

    def _release_session(self, session_key: str) -> None:
        try:
            self._sessions.release(session_key, cleanup=True)
        except Exception:
            logger.warning("Subagent release failed for %s", session_key, exc_info=True)

    async def _run(self, info: SubagentInfo) -> None:
        key = f"subagent:{info.id}"
        try:
            await asyncio.wait_for(
                self._run_inner(info, key), timeout=self._default_timeout
            )
        except asyncio.TimeoutError:
            self._abnormal_exit(
                info,
                "timeout",
                f"Timed out after {self._default_timeout // 60} minutes [{_timeout_context(info)}]",
            )
        except asyncio.CancelledError:
            self._abnormal_exit(info, "cancelled", "cancelled")
        except Exception as error:
            self._abnormal_exit(info, "error", str(error))
            logger.exception("Subagent %s failed", info.id)
        finally:
            if not info.reaped:
                info.elapsed = time.time() - info.started
                await self._fire_event(
                    "subagent_done", info, self._completion_payload(info, usage=True)
                )
                self._release_session(key)
                self._dec_running(info)
                self._note_child_outcome(info)
                self._drain_queue()
                self._maybe_clear_fanout(_fanout_key(info))
                await self._retire_session(info, key, reaper=False)
            self._tasks.pop(info.id, None)
        if self._on_done and not info.reaped:
            self._enqueue_delivery(info)

    async def _force_reap(
        self, agent_id: str, info: SubagentInfo, elapsed: float
    ) -> None:
        key = f"subagent:{agent_id}"
        await self._retire_session(info, key, reaper=True)
        task = self._tasks.pop(agent_id, None)
        if task is not None and not task.done():
            task.cancel()
        if not info.done:
            self._abnormal_exit(
                info,
                "reaped",
                f"Reaped after {int(elapsed)}s (exceeded {self._default_timeout}s deadline) [{_timeout_context(info, include_elapsed=False)}]",
            )
            self._dec_running(info)
            self._note_child_outcome(info)
        info.reaped = True
        self._maybe_clear_fanout(_fanout_key(info))
        sel().log_tool_invocation(
            session_key=key,
            source="subagent",
            tool_name="reaper_force_kill",
            outcome="reaped",
            metadata={
                "subagent_id": agent_id,
                "session_key": key,
                "elapsed": int(elapsed),
            },
        )
        self._release_session(key)
        info.elapsed = elapsed
        await self._fire_event(
            "subagent_done", info, self._completion_payload(info, usage=False)
        )
        if self._on_done:
            self._enqueue_delivery(info)
        if len(info.streaming_text) > 10_000:
            info.streaming_text = info.streaming_text[:10_000] + "\n…(truncated)"

    def _sigkill_session(self, session_key: str) -> None:
        try:
            from gideon.integrations.acp.client import (
                _get_child_pids,
                _get_start_time,
                _is_our_child,
                _kill_escaped_children,
            )

            entry = self._sessions._sessions.get(session_key)
            if entry is None:
                return
            client = getattr(entry.provider, "_client", None)
            pid = getattr(client, "_pid", None) if client else None
            if not isinstance(pid, int) or not pid:
                return
            recorded = getattr(client, "_child_pids", None)
            stored = dict(recorded) if isinstance(recorded, dict) else {}
            captured = dict(stored)
            for child in _get_child_pids(pid):
                if child not in captured:
                    captured[child] = _get_start_time(child)
            birth = getattr(client, "_start_time", None)
            if birth is None:
                _kill_escaped_children(captured)
                return
            if not _is_our_child(pid, expected_start=birth):
                _kill_escaped_children(stored)
                return
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            _kill_escaped_children(captured)
        except Exception:
            logger.exception("Reaper: SIGKILL failed for %s", session_key)

    @staticmethod
    def _write_tombstone(info: SubagentInfo, cause: str) -> None:
        try:
            write_tombstone(
                info.id,
                cause=cause,
                recovery_action="pending",
                pid=info._pid,
                turns=info.turns,
                last_tool=info.last_tool,
            )
        except Exception:
            logger.debug("Failed to write tombstone for %s", info.id, exc_info=True)

    async def _safe_announce(self, info: SubagentInfo) -> None:
        assert self._on_done is not None
        try:
            await self._on_done([info])
        except Exception:
            logger.exception("Subagent announce failed for %s", info.id)

    def _enqueue_delivery(self, info: SubagentInfo) -> None:
        parent = info.parent_session_key
        self._pending_delivery.setdefault(parent, []).append(info)
        owner = self._delivery_tasks.get(parent)
        if owner is None or owner.done():
            self._delivery_tasks[parent] = asyncio.create_task(
                self._deliver_after_delay(parent)
            )

    async def _deliver_after_delay(self, parent_key: str) -> None:
        cancelled = False
        try:
            if self._delivery_coalesce_secs > 0:
                await asyncio.sleep(self._delivery_coalesce_secs)
            await self._flush_delivery(parent_key)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            logger.exception("Subagent delivery flush failed for %s", parent_key)
        finally:
            self._delivery_tasks.pop(parent_key, None)
        if not cancelled and self._pending_delivery.get(parent_key):
            self._delivery_tasks[parent_key] = asyncio.create_task(
                self._deliver_after_delay(parent_key)
            )

    async def _flush_delivery(self, parent_key: str) -> None:
        ready = self._pending_delivery.pop(parent_key, [])
        if not ready or not self._on_done:
            return
        try:
            await asyncio.wait_for(self._on_done(ready), timeout=_ON_DONE_TIMEOUT)
        except asyncio.TimeoutError:
            reason = f"batch delivery timed out after {int(_ON_DONE_TIMEOUT)}s"
        except Exception:
            logger.exception("Subagent batch delivery failed for %s", parent_key)
            reason = "batch delivery failed"
        else:
            for info in ready:
                if not info.error:
                    self._cleanup_delivered(info)
            return
        for info in ready:
            self.notify_injection_failed(info, reason=reason)

    async def flush_deliveries(self) -> None:
        remaining_rounds = 100
        while remaining_rounds:
            pending = [
                task for task in self._delivery_tasks.values() if not task.done()
            ]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)
            remaining_rounds -= 1

    def _cleanup_delivered(self, info: SubagentInfo) -> None:
        actions = [lambda: delete_agent_folder(info.id)]
        if info.parent_session_key.startswith("dashboard:"):
            actions.append(
                lambda: _ws_result_path(
                    info.parent_session_key.removeprefix("dashboard:"), info.id
                ).unlink(missing_ok=True)
            )
        for cleanup in actions:
            try:
                cleanup()
            except Exception:
                logger.debug(
                    "Failed to clean delivered artifacts for %s", info.id, exc_info=True
                )

    async def _fire_event(
        self, etype: str, info: SubagentInfo, extra: dict | None = None
    ) -> None:
        callback = self._on_event
        if callback is not None:
            try:
                await callback(etype, info, extra or {})
            except Exception:
                logger.warning(
                    "on_event failed for %s/%s", etype, info.id, exc_info=True
                )

    def notify_injection_failed(
        self, info: SubagentInfo, reason: str = "delivery timed out"
    ) -> None:
        try:
            if not info.parent_session_key.startswith("dashboard:"):
                return
            hint = ""
            if info.result_path:
                try:
                    size = f" ({os.path.getsize(info.result_path):,} bytes)"
                except OSError:
                    size = ""
                hint = f"\nResult saved at: {info.result_path}{size}\nUse the read tool to retrieve it if needed."
            message = (
                f"[Subagent completion event]\nAgent `{info.id}` ❌ {reason}\n"
                f"Task: {_redact((info.task or '')[:100])}\n"
                f"The agent finished but result delivery timed out.{hint}"
            )
            if self._on_event:
                notification = asyncio.ensure_future(
                    self._fire_event(
                        "subagent_injection_failed",
                        info,
                        {
                            "error": reason,
                            "session": info.parent_session_key.removeprefix(
                                "dashboard:"
                            ),
                            "failure_msg": message,
                        },
                    )
                )
                notification.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception()
                )
        except Exception:
            logger.debug(
                "notify_injection_failed failed for %s", info.id, exc_info=True
            )

    def start_reaper(self) -> None:
        if self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        self._reconcile_task = asyncio.create_task(self._reconcile_orphans())

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(_REAPER_INTERVAL)
            clock = time.time()
            overdue = [
                (key, info, clock - info.started)
                for key, info in list(self._agents.items())
                if not info.done and clock - info.started > self._default_timeout
            ]
            for key, info, elapsed in overdue:
                try:
                    await self._force_reap(key, info, elapsed)
                except Exception:
                    logger.exception("Reaper: failed to reap %s", key)
            try:
                prune_stale_tombstones(max_age_days=7)
            except Exception:
                logger.debug("Reaper: tombstone pruning failed", exc_info=True)

    async def _reconcile_orphans(self) -> None:
        try:
            states = list_orphans()
            processed = 0
            for state in states:
                agent_id = state.get("id", "")
                if not agent_id or agent_id in self._agents:
                    continue
                try:
                    await self._recover_one(agent_id, state)
                except Exception:
                    logger.warning(
                        "Failed to reconcile orphan %s", agent_id, exc_info=True
                    )
                processed += 1
                if processed % 50 == 0:
                    await asyncio.sleep(0)
        except Exception:
            logger.warning("Orphan reconciliation failed", exc_info=True)

    async def _recover_one(self, agent_id: str, state: dict) -> None:
        pid = state.get("pid")
        try:
            result = _agent_dir(agent_id) / "result.txt"
            has_result = result.exists() and result.stat().st_size > 0
        except OSError:
            has_result = False
        if pid and self._is_pid_alive(pid):
            recorded_at = state.get("pid_recorded_at", state.get("started", 0))
            if self._is_orphan_process(pid, recorded_at):
                self._kill_orphan_pid(pid)
                try:
                    sel().log_tool_invocation(
                        session_key=f"subagent:{agent_id}",
                        source="subagent",
                        tool_name="orphan_reconcile_kill",
                        outcome="killed",
                        metadata={"subagent_id": agent_id, "pid": pid},
                    )
                except Exception:
                    logger.debug("SEL audit failed for orphan %s", agent_id)
        recovery = "result_available" if has_result else "notification_pending"
        self._record_recovery(agent_id, state, recovery)
        if state.get("session_id"):
            try:
                _cleanup_session_files_sync(state["session_id"])
            except Exception:
                logger.debug(
                    "Session cleanup failed for orphan %s", agent_id, exc_info=True
                )
        try:
            await self._notify_orphan(agent_id, state, recovery, has_result)
        except Exception:
            logger.debug("Notification failed for orphan %s", agent_id, exc_info=True)

    @staticmethod
    def _record_recovery(agent_id: str, state: dict, recovery: str) -> None:
        try:
            write_tombstone(
                agent_id,
                cause="gateway_restart",
                recovery_action=recovery,
                pid=state.get("pid"),
                turns=state.get("turns", 0),
                last_tool=state.get("last_tool", ""),
            )
        except Exception:
            logger.debug("Failed to tombstone orphan %s", agent_id, exc_info=True)

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _is_orphan_process(pid: int, spawned_at: float) -> bool:
        try:
            birth = os.stat(f"/proc/{pid}").st_ctime
        except OSError:
            return False
        return birth <= spawned_at + 2.0

    @staticmethod
    def _kill_orphan_pid(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            logger.debug("Orphan PID %s is no longer signalable", pid)

    async def _notify_orphan(
        self, agent_id: str, state: dict, recovery: str, has_result: bool
    ) -> None:
        preview = (state.get("task", "") or "")[:100]
        parent = state.get("parent_session", "")
        status = (
            "⚠️ orphaned by gateway restart"
            if has_result
            else "❌ lost to gateway restart"
        )
        detail = (
            f"Result saved at: `{_agent_dir(agent_id) / 'result.txt'}`\nUse the read tool to retrieve it."
            if has_result
            else "No result was captured before the restart."
        )
        message = _redact(
            f"[Subagent completion event]\nAgent `{agent_id}` {status}\nTask: {preview}\n{detail}"
        )
        if parent.startswith("dashboard:"):
            try:
                if await self._try_inject_orphan_notification(parent, message):
                    self._record_recovery(agent_id, state, "delivered")
                    return
            except Exception:
                logger.debug("Injection failed for orphan %s", agent_id, exc_info=True)
        try:
            await self._send_orphan_channel_dm(message)
        except Exception:
            logger.debug(
                "Channel fallback failed for orphan %s", agent_id, exc_info=True
            )

    async def _try_inject_orphan_notification(
        self, parent_session: str, msg: str
    ) -> bool:
        """No dashboard injection adapter is supplied by this supervisor's constructor."""
        return False

    async def _send_orphan_channel_dm(self, msg: str) -> None:
        """The existing orphan fallback is a local warning; no channel client is bound."""
        logger.warning("Orphan notification (channel DM pending): %s", msg[:200])

    async def cancel(self, agent_id: str) -> bool:
        info = self._agents.get(agent_id)
        if info is None or info.done:
            return False
        info.cancelled, info.error = True, "Cancelled by user"
        if not info.queued:
            await self._force_reap(agent_id, info, time.time() - info.started)
        else:
            self._queue[:] = [
                pending for pending in self._queue if pending.id != agent_id
            ]
            info.queued, info.done = False, True
        return True

    async def stop_children_of(self, parent_key: str) -> int:
        if not parent_key:
            return 0
        victims = [
            info for info in self.running if info.parent_session_key == parent_key
        ]
        for info in victims:
            info.cancelled = True
            lane = _fanout_key(info)
            if lane:
                self._fanout_stops.setdefault(lane, "parent turn stopped by user")
        results = await asyncio.gather(
            *(self.cancel(info.id) for info in victims), return_exceptions=True
        )
        return sum(result is True for result in results)

    async def cancel_fanout(self, fanout_key: str, *, reason: str = "") -> int:
        if not fanout_key:
            return 0
        self._fanout_stops.setdefault(fanout_key, reason or "fan-out cancelled by user")
        victims = [info for info in self.running if _fanout_key(info) == fanout_key]
        for info in victims:
            info.cancelled = True
        await asyncio.gather(
            *(self.cancel(info.id) for info in victims), return_exceptions=True
        )
        return len(victims)

    async def cancel_all(self) -> None:
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()
            self._reaper_task = None
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
