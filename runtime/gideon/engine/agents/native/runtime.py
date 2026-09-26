"""The native in-process agent loop — ``NativeAgentRuntime`` (E2-P4).

A ReAct-style tool-use loop that runs entirely inside the Gideon process:

    user turn → INFERENCE (ModelProvider.complete) → if tool calls: execute
    (approval-gated) → feed results back → repeat; stop when a model turn makes
    no tool calls (or max_turns / cancel).

It emits the neutral :class:`~gideon.integrations.llm.events.AgentEvent` stream the chat
runner already consumes from ACP (text/thinking chunks, tool-call + tool-result
cards, a terminal ``EVENT_COMPLETE`` carrying *aggregated* usage), so the runner
needs no per-backend branching. History is owned **here** (``self._messages``) —
``ModelProvider.complete`` is stateless (E2-P2).

Decoupling: this module depends only on the ``ModelProvider`` /
``ToolProvider`` / ``AgentEvent`` contracts plus low-level ``security``. Hook
firing is an injected callable so the package stays free of any
``dashboard``/``chat_runner`` import.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gideon.core import cancellation
from gideon.core.cancellation import (
    CANCEL_INTERNAL,
    CANCEL_USER,
    REQUEST_NO_TURN,
    REQUEST_REPEAT,
    CancelScope,
)
from gideon.core.token_estimate import CONSERVATIVE_CHARS_PER_TOKEN
from gideon.engine.agents.native import dispatch_plan
from gideon.engine.agents.native.approval import REJECT, REVISE, ApprovalGate
from gideon.engine.agents.native.tools import (
    ARGUMENTS_UNREADABLE,
    format_tool_result,
    read_tool_arguments,
    tool_definitions_to_openai_schema,
)
from gideon.engine.agents.provider import AgentProvider
from gideon.integrations.acp.types import (
    STOP_REASON_CANCELLED,
    STOP_REASON_STOPPED_BY_USER,
)
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
    ContextUsage,
)
from gideon.integrations.llm.prompt_cache import (
    PromptCache,
    effective_cache_mode,
    mark_cacheable_prefix,
)
from gideon.integrations.tool_providers.base import RiskLevel
from gideon.security.guardrails.audit import AttemptRecord, now_ms, record_attempt
from gideon.security.guardrails.failure import (
    FailureMode,
    GuardError,
    correction_note,
    is_retryable,
)
from gideon.security.guardrails.loop_breaker import (
    BLOCK_THRESHOLD,
    WARN_THRESHOLD,
    LoopBreaker,
    blocked_message,
    circuit_message,
    params_key,
    result_digest,
    structural_note,
    warn_note,
)

if TYPE_CHECKING:
    from gideon.engine.agents.native.tool_retrieval import ToolRetriever
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.base import ModelProvider
    from gideon.integrations.tool_providers.base import ToolDefinition, ToolProvider

logger = logging.getLogger(__name__)

_TOOL_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_TOOL_NAME_SANITIZE_MAX = 64


_INFERENCE_RETRY_BACKOFF_SECS = 0.5
_MAX_INFERENCE_RECOVERIES_PER_TURN = 3


def _inference_failure_mode(exc: BaseException) -> FailureMode:
    match exc:
        case GuardError():
            return exc.mode
        case TimeoutError() | asyncio.TimeoutError():
            return FailureMode.TIMEOUT
        case _:
            return FailureMode.PROVIDER_ERROR


def _sanitized_tool_key(name: str) -> str:
    encoded = []
    for character in (name or "")[:_TOOL_NAME_SANITIZE_MAX]:
        allowed = (
            "a" <= character <= "z"
            or "A" <= character <= "Z"
            or "0" <= character <= "9"
            or character in "_-"
        )
        encoded.append(character if allowed else "_")
    return "".join(encoded) or "tool"


def build_sanitized_index(
    names: Iterable[str],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    exact = set(names)
    buckets: dict[str, list[str]] = {}
    for name in exact:
        alias = _sanitized_tool_key(name)
        if alias not in exact and alias != name:
            buckets.setdefault(alias, []).append(name)
    return (
        {
            alias: candidates[0]
            for alias, candidates in buckets.items()
            if len(candidates) == 1
        },
        {
            alias: sorted(candidates)
            for alias, candidates in buckets.items()
            if len(candidates) > 1
        },
    )


HookFire = Callable[[str, str | None], Awaitable[list[str]]]

_MAX_STEERS_PER_TURN = 4

_NEEDS_APPROVAL: Any = object()

CANCELLED_BEFORE_RUN = "Error: cancelled before this tool ran"

_DROPPED: Any = object()


@dataclass(frozen=True, slots=True)
class _PreparedCall:
    call: AgentEvent
    tool_name: str
    args: dict
    card: AgentEvent
    reservations: tuple[dispatch_plan.Reservation, ...]
    bkey: str
    arg_error: str = ""

    def result_event(
        self, observation: str, metadata: dict | None = None
    ) -> AgentEvent:
        return AgentEvent(
            kind=EVENT_TOOL_RESULT,
            tool_call_id=self.call.tool_call_id,
            title=self.tool_name,
            tool_output=observation,
            tool_meta=metadata or {},
        )


@dataclass
class _ToolInventory:
    definitions: list[Any]
    providers: dict[str, Any]
    owners: dict[str, str]

    @classmethod
    async def discover(cls, providers: list[Any], unattended: bool) -> "_ToolInventory":
        from gideon.integrations.tool_providers import tool_prefs
        from gideon.integrations.tool_providers.base import is_interactive_tool

        disabled = tool_prefs.load_disabled()
        disabled_providers = tool_prefs.load_disabled_providers()
        inventory = cls([], {}, {})
        for provider in providers:
            owner = getattr(provider, "name", "") or ""
            if owner in disabled_providers:
                logger.info("native provider disabled: %s", owner)
                continue
            try:
                definitions = await provider.list_tools()
            except Exception:
                logger.debug("Cannot discover native provider %s", owner, exc_info=True)
                continue
            for definition in definitions:
                tagged = getattr(definition, "provider", "") or owner
                if tool_prefs.is_disabled(
                    tagged, definition.name, disabled, disabled_providers
                ):
                    logger.info("native tool disabled: %s", definition.name)
                    continue
                inventory.definitions.append(definition)
                inventory.providers[definition.name] = provider
                inventory.owners[definition.name] = tagged
        if unattended:
            omitted = {
                entry.name
                for entry in inventory.definitions
                if is_interactive_tool(entry)
            }
            inventory.definitions = [
                entry
                for entry in inventory.definitions
                if not is_interactive_tool(entry)
            ]
            for name in omitted:
                inventory.providers.pop(name, None)
            if omitted:
                logger.info("native unattended tools omitted: %s", sorted(omitted))
        return inventory


def _discovery_tools() -> tuple[ToolDefinition, ...]:
    from gideon.integrations.tool_providers.base import ToolDefinition

    specifications = (
        (
            "tool_search",
            "query",
            {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "Search the complete tool catalog by capability, including inactive groups and tools "
            "whose schemas are deferred. Supply query and optionally limit. Results contain names "
            "and descriptions; use tool_schema for inputs, then invoke the exact tool name.",
        ),
        (
            "tool_schema",
            "tool_name",
            {"tool_name": {"type": "string"}},
            "Read a tool's full input schema and description by exact tool_name. This works for "
            "every catalog tool, including inactive groups. Invoke the tool after checking its inputs.",
        ),
        (
            "reset_tools",
            "groups",
            {
                "groups": {
                    "type": "object",
                    "description": "Group names mapped to active booleans; omitted groups deactivate.",
                    "additionalProperties": {"type": "boolean"},
                }
            },
            "Replace the active tool groups with the complete requested state. Supply groups as "
            "an object mapping names to true or false; omitted groups deactivate and core remains "
            "active. Batch changes into one call. Schemas change on your NEXT turn and require a "
            "cache re-read; newly activated groups return instructions. All tools remain callable "
            "by exact name while inactive and tool_search still discovers them.",
        ),
    )
    return tuple(
        ToolDefinition(
            name=name,
            provider="native",
            requires_approval=False,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": [required],
            },
        )
        for name, required, properties, description in specifications
    )


@dataclass(slots=True)
class _DispatchTicket:
    prepared: _PreparedCall
    outcome: Any = None
    invoke: bool = False


@dataclass(slots=True)
class _TurnTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    cycles: int = 0
    events: int = 0
    calls: int = 0
    recoveries: int = 0
    context_recovered: bool = False
    model_calls: int = 0
    measured_calls: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    last_context_usage: ContextUsage | None = None

    def account(self, response: "_ModelExchange") -> None:
        self.calls += len(response.calls)
        self.model_calls += response.attempts
        self.last_context_usage = (
            response.usage.context_usage if response.usage is not None else None
        )
        for usage in response.usages:
            self.measured_calls += 1
            self.input_tokens += usage.input_tokens or 0
            self.output_tokens += usage.output_tokens or 0
            self.cache_creation_tokens += usage.cache_creation_tokens or 0
            self.cache_read_tokens += usage.cache_read_tokens or 0
            self.cost += usage.cost_usd or 0.0
            if usage.context_usage_pct is not None:
                response.runtime._last_context_pct = usage.context_usage_pct

    def finish(self, reason: str, context_pct: float | None) -> AgentEvent:
        return AgentEvent(
            kind=EVENT_COMPLETE,
            stop_reason=reason,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost,
            num_turns=self.cycles,
            context_usage_pct=context_pct,
            context_usage=self.last_context_usage,
            event_count=self.events,
            tool_call_count=self.calls,
            cache_creation_tokens=self.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens,
            tool_meta={
                "model_calls": self.model_calls,
                "usage_status": (
                    "no_model_calls" if not self.model_calls else
                    "measured" if self.measured_calls == self.model_calls else
                    "partial" if self.measured_calls else "absent"
                ),
                "measured_calls": self.measured_calls,
            },
        )


class _ModelExchange:
    def __init__(self, runtime: "NativeAgentRuntime", totals: _TurnTotals):
        self.runtime = runtime
        self.totals = totals
        self.fragments: list[str] = []
        self.calls: list[AgentEvent] = []
        self.usage: AgentEvent | None = None
        self.usages: list[AgentEvent] = []
        self.attempts = 0
        self.visible = False
        self.retried = False
        self.thinking: list[str] = []

    def accept(self, event: AgentEvent) -> bool:
        self.totals.events += 1
        if event.kind == EVENT_TOOL_CALL:
            self.calls.append(event)
        elif event.kind == EVENT_COMPLETE:
            self.usage = event
            self.usages.append(event)
        elif event.kind in (EVENT_TEXT_CHUNK, EVENT_THINKING_CHUNK):
            self.visible = True
            if event.kind == EVENT_TEXT_CHUNK:
                self.fragments.append(event.text)
            else:
                self.thinking.append(event.text)
            return True
        return False

    def retry_allowed(self, failure: FailureMode) -> bool:
        return not (
            self.retried
            or self.totals.context_recovered
            or self.totals.recoveries >= _MAX_INFERENCE_RECOVERIES_PER_TURN
            or self.visible
            or self.calls
            or self.runtime._cancelled
            or not is_retryable(failure)
        )

    async def _retry_messages(
        self, error: Exception, messages: list[dict], cache_mode: PromptCache
    ) -> list[dict]:
        from gideon.automation.workflows.compaction import is_context_overflow

        failure = _inference_failure_mode(error)
        if not self.retry_allowed(failure):
            raise error
        if is_context_overflow(error):
            if self.totals.recoveries:
                raise error
            await self.runtime.compact()
            if self.runtime._compaction_result["type"] != "completed":
                raise error
            self.totals.context_recovered = True
            messages = mark_cacheable_prefix(
                self.runtime._messages,
                cache_mode,
                generation=self.runtime._cache_generation,
                max_markers=3,
            )
        else:
            instruction = correction_note(failure)
            if instruction:
                messages = [
                    *messages,
                    {"role": "user", "content": instruction, "_volatile": True},
                ]
        self.retried = True
        self.totals.recoveries += 1
        return messages

    async def events(self, tools: list[dict] | None) -> AsyncIterator[AgentEvent]:
        runtime = self.runtime
        cache_mode = effective_cache_mode(
            getattr(runtime._model, "prompt_cache", PromptCache.NONE),
            enabled=runtime._prompt_cache_enabled(),
        )
        messages = mark_cacheable_prefix(
            runtime._messages, cache_mode, generation=runtime._cache_generation,
            max_markers=3,
        )
        while True:
            if runtime._cancelled:
                return
            self.visible = False
            started = now_ms()
            self.attempts += 1
            try:
                async for event in runtime._model.complete(
                    messages,
                    tools=tools,
                    model=(runtime._definition.model or None) if runtime._active_fallback is None else None,
                    reasoning_effort=runtime._reasoning_effort,
                ):
                    if runtime._cancelled:
                        break
                    if self.accept(event):
                        yield event
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failure = _inference_failure_mode(error)
                runtime._audit_inference_attempt(
                    failure,
                    attempt=2 if self.retried else 1,
                    started_ms=started,
                    passed=False,
                )
                try:
                    messages = await self._retry_messages(error, messages, cache_mode)
                except Exception as retry_error:
                    if retry_error is not error:
                        raise
                    if (
                        not self.visible and not self.calls
                        and self.totals.recoveries < _MAX_INFERENCE_RECOVERIES_PER_TURN
                        and await runtime._advance_model_fallback(failure)
                    ):
                        self.retried = True
                        self.totals.recoveries += 1
                        self.usage = None
                        cache_mode = effective_cache_mode(
                            getattr(runtime._model, "prompt_cache", PromptCache.NONE),
                            enabled=runtime._prompt_cache_enabled(),
                        )
                        messages = mark_cacheable_prefix(
                            runtime._messages, cache_mode,
                            generation=runtime._cache_generation, max_markers=3,
                        )
                        continue
                    raise
                logger.warning(
                    "native inference failed (%s); retrying exchange %d "
                    "(turn recovery %d/%d): %s",
                    failure.value,
                    self.totals.cycles,
                    self.totals.recoveries,
                    _MAX_INFERENCE_RECOVERIES_PER_TURN,
                    error,
                )
                await asyncio.sleep(_INFERENCE_RETRY_BACKOFF_SECS)
                self.fragments.clear()
                self.usage = None
            else:
                if not runtime._cancelled and not self.calls and not "".join(self.fragments).strip():
                    if self.totals.recoveries >= _MAX_INFERENCE_RECOVERIES_PER_TURN:
                        raise RuntimeError("Model returned no answer after bounded recovery")
                    self.totals.recoveries += 1
                    if self.thinking:
                        note = "Your previous response contained reasoning but no answer. Provide the answer now."
                    elif any(message.get("role") == "tool" for message in messages):
                        note = "The tool results are available. Provide a substantive answer to the user."
                    else:
                        note = "Your response was empty. Provide a substantive answer to the user."
                    messages = [*messages, {"role": "user", "content": note, "_volatile": True}]
                    self.thinking.clear()
                    self.usage = None
                    continue
                if self.retried:
                    runtime._audit_inference_attempt(
                        FailureMode.NONE, attempt=2, started_ms=started, passed=True
                    )
                return


class NativeAgentRuntime(AgentProvider):
    """In-process agent runtime for one session."""

    def __init__(
        self,
        *,
        definition: "AgentRuntimeDefinition",
        model_provider: "ModelProvider",
        tool_providers: list["ToolProvider"] | None = None,
        cwd: Path | None = None,
        session_key: str = "",
        max_turns: int = 100,
        hook_fire: HookFire | None = None,
        extra_deny_patterns: list[str] | None = None,
        unattended: bool = False,
        dry_run: bool = False,
        reasoning_effort: str = "",
        project_id: str = "",
        tool_groups: list[str] | None = None,
        surface: str = "",
        max_tool_concurrency: int = dispatch_plan.MAX_CONCURRENT_CALLS,
    ) -> None:
        self._definition, self._model = definition, model_provider
        self._active_fallback: str | None = None
        self._agent_id = getattr(definition, "name", "") or ""
        self._project_id, self._reasoning_effort = (
            project_id or "",
            reasoning_effort or "",
        )
        self._next_reasoning_effort: str | None = None
        self._cwd = Path(cwd) if cwd else None
        self._session_key = session_key
        self._max_turns = max_turns
        self._tool_providers = list(tool_providers or ())
        self._max_tool_concurrency = max(1, int(max_tool_concurrency or 1))
        self._hook_fire = hook_fire
        self._extra_deny = list(extra_deny_patterns or ())
        self._dry_run = bool(dry_run)
        self._unattended = bool(unattended) or self._dry_run
        self._approval = ApprovalGate()
        self._revisions: dict[str, str] = {}
        self._pending_revisions: list[tuple[str, str]] = []
        self._approval_policy, self._task_mode = "", "agent"
        self._cancel, self._breaker = CancelScope(), LoopBreaker()
        self._messages: list[dict] = []
        self._tool_outcomes: list[tuple[str, str]] = []
        self._last_context_pct: float | None = None
        self._compaction_saves: list[float] = []
        self._compaction_result: dict = {"type": "timeout"}
        self._cache_generation = 0
        self._pull_steer: Callable[[], list[str]] | None = None
        self._steers_injected = 0
        self._steer_pending: list[str] = []
        self._surface = surface or ""
        self._group_seed = None if tool_groups is None else list(tool_groups)
        self._groups: list[Any] = []
        self._active_groups: set[str] | None = None
        self._pending_group_note = ""
        self._unofferable: set[str] = set()
        self._provider_of: dict[str, str] = {}
        self._group_of_name: dict[str, str] = {}
        self._active_defs: list[Any] = []
        self._tool_defs: list[Any] = []
        self._tool_schema: list[dict] = []
        self._tool_index: dict[str, ToolProvider] = {}
        self._tool_sanitized_index: dict[str, str] = {}
        self._tool_retriever: ToolRetriever | None = None
        self._tool_search_def: ToolDefinition | None = None
        self._tool_schema_def: ToolDefinition | None = None
        self._reset_tools_def: ToolDefinition | None = None

    @property
    def provider_id(self) -> str:
        return "native"

    async def start(self) -> None:
        if not getattr(self._model, "supports_tools", False):
            self._tool_defs = []
            self._tool_schema = []
            self._tool_index = {}
            self._tool_sanitized_index = {}
            self._groups = []
            self._active_defs = []
            self._group_of_name = {}
            logger.info("native model does not accept tools")
            return
        from gideon.engine.agents.native.tool_retrieval import ToolRetriever
        from gideon.integrations.tool_providers import groups

        inventory = await _ToolInventory.discover(
            self._tool_providers, self._unattended
        )
        self._tool_defs = inventory.definitions
        self._tool_index = inventory.providers
        self._provider_of = inventory.owners
        self._groups = groups.partition(self._tool_defs, provider_of=self._provider_of)
        self._active_groups = (
            groups.resolve_default_groups(self._surface)
            if self._group_seed is None
            else {groups.CORE_GROUP, *self._group_seed}
        )
        self._assemble_schema()
        self._tool_sanitized_index, collisions = build_sanitized_index(self._tool_index)
        for alias, names in collisions.items():
            logger.warning("native tool alias %r is ambiguous: %s", alias, names)
        self._tool_risk = {
            definition.name: getattr(definition, "risk_level", RiskLevel.SAFE)
            for definition in self._tool_defs
        }
        self._tool_retriever = ToolRetriever(self._tool_defs)
        self._tool_search_def, self._tool_schema_def, self._reset_tools_def = (
            _discovery_tools()
        )
        logger.info(
            "native catalog: %d tools from %d providers",
            len(self._tool_defs),
            len(self._tool_providers),
        )

    def _assemble_schema(self) -> None:
        from gideon.integrations.tool_providers import groups

        membership = {}
        for definition in self._tool_defs:
            name = getattr(definition, "name", "") or ""
            membership[name] = groups.group_of_tool(
                definition, provider=self._provider_of.get(name, "")
            )
        self._group_of_name = membership
        self._unofferable = set()
        visible = self._tool_defs
        if self._active_groups is not None:
            self._unofferable.update(
                group.name
                for group in self._groups
                if group.capability
                and not group.always_on
                and not groups.offerable(group)
            )
            self._active_groups.difference_update(self._unofferable)
            visible = [
                definition
                for definition in visible
                if membership.get(getattr(definition, "name", "") or "")
                in self._active_groups
            ]
        self._active_defs = list(visible)
        self._tool_schema = tool_definitions_to_openai_schema(self._active_defs)

    def refresh_toolset(self) -> None:
        self._assemble_schema()

    def _group_stub_lines(self) -> list[str]:
        if self._active_groups is None:
            return []
        excluded = self._active_groups | self._unofferable
        lines = []
        for group in self._groups:
            if group.name not in excluded:
                example = ", ".join(group.tools[:4])
                count = len(group.tools)
                suffix = f", +{count - 4} more" if count > 4 else ""
                lines.append(
                    f"- {group.name} ({_n_tools(count)}, INACTIVE): {example}{suffix} "
                    f'— reset_tools({{"{group.name}": true}}) to activate'
                )
        return lines

    def _reset_tools(self, args: dict) -> str:
        requested = args.get("groups")
        if not isinstance(requested, dict):
            return (
                "Error: `groups` must be an object mapping group name → true/false, "
                'e.g. {"groups": {"schedule": true, "memory": true}}.'
            )
        catalog = {group.name: group for group in self._groups}
        enabled = {str(name) for name, value in requested.items() if bool(value)}
        unknown = sorted(enabled - catalog.keys())
        blocked = sorted(enabled & catalog.keys() & self._unofferable)
        chosen = (enabled & catalog.keys()) - self._unofferable
        chosen.update(group.name for group in self._groups if group.always_on)
        previous = set(catalog) if self._active_groups is None else self._active_groups
        newly = sorted(chosen - previous)
        self._active_groups = chosen
        self.refresh_toolset()
        notices = []
        if unknown:
            notices.append(
                f"Unknown group(s) ignored: {', '.join(unknown)}. Available: {', '.join(sorted(catalog))}."
            )
        if blocked:
            notices.append(
                f"Unavailable in this install (not activated): {', '.join(blocked)} — "
                "the capability these tools need isn't configured, so they would fail."
            )
        description = ", ".join(
            f"{name} ({_n_tools(len(catalog[name].tools))})"
            for name in sorted(chosen)
            if name in catalog
        )
        notices.append(f"Active tool groups: {description}.")
        inactive = sorted(catalog.keys() - chosen - self._unofferable)
        if inactive:
            notices.append(f"Inactive: {', '.join(inactive)}.")
        notices.extend(
            f"[{name}] {catalog[name].instructions}"
            for name in newly
            if catalog[name].instructions
        )
        notices.append(
            "This takes effect on your NEXT turn — the tools you just activated "
            "carry their schemas from then on. (Any tool remains callable by name "
            "even while its group is inactive.)"
        )
        summary = " ".join(filter(None, notices))
        self._pending_group_note = (
            f"[tool groups] {summary}" if self._active_groups != previous else ""
        )
        logger.info("native active groups: %s", sorted(chosen))
        return summary

    @property
    def _cancelled(self) -> bool:
        return self._cancel.cancelled

    def _stop_reason_for_cancel(self) -> str:
        reasons = {True: STOP_REASON_STOPPED_BY_USER, False: STOP_REASON_CANCELLED}
        return reasons[bool(self._cancel.stopped_by_user)]

    def _audit_inference_attempt(
        self, mode: FailureMode, *, attempt: int, started_ms: float, passed: bool
    ) -> None:
        try:
            timestamp = now_ms()
            entry = AttemptRecord(
                audit_id=f"native-{id(self):x}-{time.time_ns():x}",
                ts=timestamp,
                use_case="native_loop",
                provider=self._model.__class__.__name__,
                model=self._definition.model or "",
                attempt=attempt,
                failure_mode=mode.value,
                latency_ms=max(now_ms() - started_ms, 0.0),
                passed=passed,
                strategy="direct" if attempt <= 1 else "retry",
            )
            record_attempt(entry)
        except Exception:
            logger.debug("Could not record native inference attempt", exc_info=True)

    async def _advance_model_fallback(self, failure: FailureMode) -> bool:
        if self._cancelled or not is_retryable(failure):
            return False
        try:
            from gideon.extensions.providers.provider_bridge import resolve_provider_for_use_case
            from gideon.extensions.providers.use_cases import resolution_chain

            chain = resolution_chain("chat")
            current = self._active_fallback or (self._definition.model or getattr(self._model, "_model", ""))
            current_id = str(current).split(":", 1)[-1]
            position = next(
                (index for index, ref in enumerate(chain)
                 if ref == current or ref.split(":", 1)[-1] == current_id),
                None,
            )
            if position is None or position + 1 >= len(chain):
                return False
            for ref in chain[position + 1:]:
                try:
                    candidate = resolve_provider_for_use_case("chat", model_override=ref, _force_model_axis=True)
                    await candidate.start()
                except Exception:
                    logger.warning("Native fallback candidate %s unavailable", ref, exc_info=True)
                    continue
                previous = self._model
                self._model = candidate
                self._active_fallback = ref
                await self._close_replaced_model(previous)
                logger.warning("Native inference switched to configured fallback %s", ref)
                return True
        except Exception:
            logger.warning("Native provider fallback failed", exc_info=True)
        return False

    @staticmethod
    async def _close_replaced_model(provider: "ModelProvider") -> None:
        try:
            await provider.shutdown()
        except Exception:
            logger.debug("Replaced model shutdown failed", exc_info=True)

    def last_stop_report(self) -> dict:
        report = self._cancel.report
        return report.to_dict()

    def note_subagents_stopped(self, count: int) -> None:
        scope = self._cancel
        scope.note_subagents_stopped(count)

    async def shutdown(self) -> None:
        scope, approvals = self._cancel, self._approval
        scope.request(reason=CANCEL_INTERNAL)
        approvals.cancel_all()

    def _prompt_cache_enabled(self) -> bool:
        from gideon.core.config.loader import AppConfig

        enabled = True
        try:
            enabled = bool(AppConfig.load().agent.prompt_cache_enabled)
        except Exception:
            logger.debug(
                "Prompt-cache configuration unavailable; using enabled default",
                exc_info=True,
            )
        return enabled

    async def stream(self, message: str) -> AsyncIterator[AgentEvent]:
        self._cancel.begin_turn()
        if self._next_reasoning_effort is not None:
            self._reasoning_effort = self._next_reasoning_effort
            self._next_reasoning_effort = None
        self._breaker.reset()
        self._steers_injected = 0
        self._steer_pending.clear()
        self._messages.append({"role": "user", "content": message})
        tools, annotation = self._prepare_turn_tools(message)
        if annotation:
            self._messages.append(
                {"role": "system", "content": annotation, "_volatile": True}
            )
        totals = _TurnTotals()
        try:
            for _ in range(self._max_turns):
                if self._cancelled:
                    yield totals.finish(
                        self._stop_reason_for_cancel(), self._last_context_pct
                    )
                    return
                totals.cycles += 1
                self._maybe_compact()
                exchange = _ModelExchange(self, totals)
                async for event in exchange.events(tools):
                    yield event
                totals.account(exchange)
                tool_calls = exchange.calls
                self._messages.append(
                    self._assistant_msg("".join(exchange.fragments), tool_calls)
                )
                if not tool_calls and not self._cancelled:
                    if self._drain_steers_into_history():
                        continue
                if not tool_calls or self._cancelled:
                    self._messages.extend(
                        self._tool_result_msg(call, CANCELLED_BEFORE_RUN)
                        for call in tool_calls
                    )
                    reason = (
                        self._stop_reason_for_cancel()
                        if self._cancelled
                        else "end_turn"
                    )
                    yield totals.finish(reason, self._last_context_pct)
                    return
                async for event in self._execute_tool_batch(tool_calls):
                    totals.events += 1
                    yield event
                if self._pending_revisions:
                    corrections = self._pending_revisions[:]
                    self._pending_revisions.clear()
                    self._messages.append({
                        "role": "user",
                        "content": "\n".join(
                            f"Revise the proposed {name} action: {instruction}. "
                            "Propose the updated action and wait for normal approval before execution."
                            for name, instruction in corrections
                        ),
                        "_volatile": True,
                    })
                if self._drain_steers_into_history():
                    yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="")
            yield totals.finish("max_turns", self._last_context_pct)
        finally:
            self._cancel.end_turn()

    def _prepare_turn_tools(self, message: str) -> tuple[list[dict] | None, str]:
        grouped = self._active_groups is not None
        available = self._active_defs if grouped else self._tool_defs
        restrict = (
            {getattr(entry, "name", "") for entry in available} if grouped else None
        )
        selected = (
            self._tool_retriever.select(message, restrict=restrict)
            if self._tool_retriever
            else available
        )
        reduced = bool(self._tool_retriever) and len(selected) < len(available)
        pending = self._pending_group_note
        self._pending_group_note = ""
        notes = [pending] if pending else []
        if not grouped and not reduced:
            schema = self._tool_schema or None
        else:
            definitions = list(selected if reduced else available)
            if reduced:
                definitions.extend((self._tool_search_def, self._tool_schema_def))
            if grouped:
                definitions.append(self._reset_tools_def)
            schema = tool_definitions_to_openai_schema(definitions) or None
            if reduced:
                retriever = self._tool_retriever
                if retriever is None:
                    raise RuntimeError("tool retrieval index is unavailable")
                exclude = {getattr(entry, "name", "") for entry in definitions}
                if grouped:
                    exclude.update(set(self._group_of_name) - (restrict or set()))
                catalog = retriever.catalog(exclude=exclude)
                notes.append(
                    "[tool catalog] Full schemas are provided for the most relevant tools. "
                    "The remaining available tools are listed below with descriptions. "
                    'Use tool_schema("name") to inspect inputs or tool_search("capability") '
                    "to find a tool, then invoke its exact name. Every listed tool is available; "
                    "schema selection does not disable tools.\n" + catalog
                )
        stubs = self._group_stub_lines()
        if stubs:
            notes.append(
                "[inactive tool groups] These capabilities remain available by tool name. "
                "To load their schemas next turn, call reset_tools once with the complete "
                "set of groups you want active.\n" + "\n".join(stubs)
            )
        return schema, "\n\n".join(notes)

    def _prepare_call(self, call: AgentEvent) -> "_PreparedCall":
        name = self._resolve_name(call.title or "")
        parsed = read_tool_arguments(call.tool_input)
        problem = ""
        if parsed is ARGUMENTS_UNREADABLE:
            parsed = {}
            stop = str(getattr(call, "stop_reason", "") or "").lower()
            problem = (
                correction_note(FailureMode.TOKEN_OVERFLOW)
                if stop in ("length", "max_tokens")
                else "Your tool call's arguments were not valid JSON, so the call could not be "
                "made. Re-send the call with a complete, valid JSON arguments object."
            )
            logger.warning(
                "native tool arguments unreadable: tool=%s stop=%r", name, stop
            )
        resources = (
            (dispatch_plan.EVERYTHING,)
            if self._requires_approval(name)
            else dispatch_plan.reservations_for(
                name, parsed, cwd=str(self._cwd) if self._cwd else None
            )
        )
        return _PreparedCall(
            call=call,
            tool_name=name,
            args=parsed,
            card=AgentEvent(
                kind=EVENT_TOOL_CALL,
                title=name,
                tool_call_id=call.tool_call_id,
                tool_input=parsed,
                risk_level=self._tool_risk.get(name, RiskLevel.SAFE).value,
            ),
            reservations=resources,
            bkey=params_key(name, parsed),
            arg_error=problem,
        )

    async def _execute_tool_batch(
        self, tool_calls: list[AgentEvent]
    ) -> AsyncIterator[AgentEvent]:
        started = time.perf_counter()
        calls = tuple(map(self._prepare_call, tool_calls))
        schedule = dispatch_plan.plan(
            [call.reservations for call in calls], max_width=self._max_tool_concurrency
        )
        poisoned: list[tuple[dispatch_plan.Reservation, ...]] = []
        try:
            for indices in schedule.waves:
                wave = [calls[index] for index in indices]
                async for event in self._execute_wave(wave, poisoned):
                    yield event
        finally:
            logger.info(
                "%s mode=%s calls=%d waves=%d widest=%d ms=%d",
                dispatch_plan.TIMING_LOG_PREFIX,
                schedule.mode,
                schedule.call_count,
                len(schedule.waves),
                schedule.widest,
                round((time.perf_counter() - started) * 1000),
            )

    async def _execute_wave(
        self,
        wave: list["_PreparedCall"],
        poisoned: list[tuple[dispatch_plan.Reservation, ...]],
    ) -> AsyncIterator[AgentEvent]:
        if len(wave) == 1:
            prepared = wave[0]
            if self._cancelled:
                execution = self._drop_queued_call(prepared)
            else:
                execution = self._run_tool(
                    prepared, prefetched=self._unrunnable_result(prepared, poisoned)
                )
            async for event in execution:
                yield event
            return
        tickets = [_DispatchTicket(prepared) for prepared in wave]
        for ticket in tickets:
            if self._cancelled:
                ticket.outcome = _DROPPED
            else:
                ticket.outcome = self._unrunnable_result(ticket.prepared, poisoned)
                ticket.invoke = (
                    ticket.outcome is None
                    and self._breaker.count(ticket.prepared.bkey) < BLOCK_THRESHOLD
                )
        for ticket in tickets:
            if ticket.outcome is not _DROPPED:
                yield ticket.prepared.card
        running = [ticket for ticket in tickets if ticket.invoke]
        if running:
            outcomes = await asyncio.gather(
                *(self._prefetch(ticket.prepared) for ticket in running),
                return_exceptions=True,
            )
            for ticket, outcome in zip(running, outcomes):
                if isinstance(outcome, BaseException):
                    poisoned.append(ticket.prepared.reservations)
                    ticket.outcome = (
                        f"Error: {ticket.prepared.tool_name} raised {type(outcome).__name__}: {outcome}",
                        {},
                    )
                else:
                    ticket.outcome = outcome
        for ticket in tickets:
            if ticket.outcome is _DROPPED or (
                ticket.outcome is None and self._cancelled
            ):
                execution = self._drop_queued_call(ticket.prepared)
            else:
                execution = self._run_tool(
                    ticket.prepared, prefetched=ticket.outcome, card_emitted=True
                )
            async for event in execution:
                yield event

    async def _drop_queued_call(
        self, prep: "_PreparedCall"
    ) -> AsyncIterator[AgentEvent]:
        self._cancel.note_tool_call_dropped()
        yield prep.result_event(CANCELLED_BEFORE_RUN)
        self._messages.append(self._tool_result_msg(prep.call, CANCELLED_BEFORE_RUN))

    @staticmethod
    def _unrunnable_result(
        prep: "_PreparedCall", poisoned: list[tuple[dispatch_plan.Reservation, ...]]
    ) -> tuple[str, dict] | None:
        if prep.arg_error:
            detail = prep.arg_error
        else:
            conflict = next(
                (
                    resources
                    for resources in poisoned
                    if dispatch_plan.conflicts(prep.reservations, resources)
                ),
                None,
            )
            if conflict is None:
                return None
            return (
                f"Error: {prep.tool_name} was not run — an earlier call in this turn that "
                "touches the same resource failed, so the resource's state is unknown. "
                "Re-check that state before retrying.",
                {},
            )
        return f"Error: {prep.tool_name} was not run. {detail}", {}

    async def _prefetch(self, prep: "_PreparedCall") -> tuple[Any, dict]:
        metadata: dict = {}
        observation = await self._guard_and_invoke(
            prep.call, prep.tool_name, prep.args, meta=metadata
        )
        return observation, metadata

    @staticmethod
    def _denial(kind: str, reason: str, tool: str) -> str:
        from gideon.security.security import classify_denial

        return classify_denial(kind, reason, tool)[1]

    async def _run_tool(
        self,
        prep: "_PreparedCall",
        *,
        prefetched: tuple[Any, dict] | None,
        card_emitted: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        from gideon.security import security

        if not card_emitted:
            yield prep.card
        if prefetched is None and self._breaker.count(prep.bkey) >= BLOCK_THRESHOLD:
            observation = blocked_message(
                prep.tool_name, self._breaker.count(prep.bkey)
            )
            yield prep.result_event(observation)
            self._messages.append(self._tool_result_msg(prep.call, observation))
            return
        observation, metadata = (
            await self._prefetch(prep) if prefetched is None else prefetched
        )
        revision_instruction = ""
        if observation is _NEEDS_APPROVAL:
            if self._unattended:
                observation = self._denial(
                    security.DENY_KIND_USER,
                    "this tool needs approval but the run is unattended (no human to approve) — it was auto-declined",
                    prep.tool_name,
                )
            else:
                request = prep.call.tool_call_id or prep.tool_name
                future = self._approval.register(request)
                yield AgentEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id=request,
                    tool_call_id=prep.call.tool_call_id,
                    title=prep.tool_name,
                    tool_input=prep.args,
                    risk_level=self._tool_risk.get(
                        prep.tool_name, RiskLevel.SAFE
                    ).value,
                )
                decision = await self._approval.wait(request, future)
                if self._cancelled:
                    observation = "Error: cancelled"
                elif decision == REVISE:
                    revision_instruction = self._revisions.pop(str(request), "")
                    observation = "Error: the user requested a revision; this action was not run."
                elif decision == REJECT:
                    observation = self._denial(
                        security.DENY_KIND_USER,
                        "the user declined this tool call",
                        prep.tool_name,
                    )
                else:
                    observation = await self._invoke(
                        prep.tool_name, prep.args, meta_sink=metadata
                    )
        observation = self._observe_tool_result(prep, observation)
        yield prep.result_event(observation, metadata)
        self._messages.append(self._tool_result_msg(prep.call, observation))
        if revision_instruction:
            self._pending_revisions.append((prep.tool_name, revision_instruction))
        if self._breaker.circuit_tripped():
            logger.warning("native: %s", circuit_message(self._breaker.total_failures))
            self._cancel.request(reason=CANCEL_INTERNAL)

    def _observe_tool_result(self, prep: "_PreparedCall", observation: str) -> str:
        from gideon.security.security import is_denial_observation

        failed = observation.startswith("Error:")
        if len(self._tool_outcomes) < 200:
            outcome = (
                "success"
                if not failed
                else "denied" if is_denial_observation(observation) else "failed"
            )
            self._tool_outcomes.append((prep.tool_name, outcome))
        streak = self._breaker.record(prep.bkey, failed)
        if failed:
            return (
                observation + warn_note(prep.tool_name, streak)
                if streak >= WARN_THRESHOLD
                else observation
            )
        signature = f"{prep.bkey}\x1f{result_digest(observation)}"
        repeated = self._breaker.record_structural(signature)
        if repeated:
            logger.info("native structural loop for %s: %s", prep.tool_name, repeated)
            return observation + structural_note(repeated)
        return observation

    async def _policy_refusal(
        self, call: AgentEvent, name: str, arguments: dict
    ) -> str | None:
        from gideon.engine.task_modes import task_mode_denies
        from gideon.security import security

        reason = security.is_denied(name, self._extra_deny)
        if not reason:
            reason = task_mode_denies(self._task_mode, name, "", call.tool_input)
        if reason:
            return self._denial(security.DENY_KIND_POLICY, reason, name)
        if self._hook_fire is not None:
            try:
                responses = await self._hook_fire(name, _short_json(arguments))
            except Exception:
                responses = []
            blocked = [
                value
                for value in (responses or [])
                if str(value).startswith("BLOCKED:")
            ]
            if blocked:
                reason = blocked[0].removeprefix("BLOCKED:").strip() or "policy hook"
                return self._denial(security.DENY_KIND_HOOK, reason, name)
        return None

    async def _guard_and_invoke(
        self, call: AgentEvent, tool_name: str, args: dict, *, meta: dict
    ):
        if (
            self._dry_run
            and self._tool_risk.get(tool_name, RiskLevel.SAFE) != RiskLevel.SAFE
        ):
            return (
                f"[DRY RUN — observe mode] `{tool_name}` is a write-capable tool; "
                f"it was NOT executed. With args {_short_json(args)} it would have "
                "run here. Continue reasoning about what this run would do; do not "
                "retry it expecting a real effect."
            )
        refusal = await self._policy_refusal(call, tool_name, args)
        if refusal is not None:
            return refusal
        if self._requires_approval(tool_name):
            return _NEEDS_APPROVAL
        return await self._invoke(tool_name, args, meta_sink=meta)

    def _resolve_name(self, name: str) -> str:
        return (
            self._tool_sanitized_index.get(name, name)
            if name not in self._tool_index and name not in self._META_TOOLS
            else name
        )

    def _search_tools(self, args: dict) -> str:
        retriever = self._tool_retriever
        if retriever is None:
            return "Tool search is unavailable because the tool catalog is not initialized."
        retriever.mark_used("tool_search")
        matches = retriever.search(
            str(args.get("query", "")), int(args.get("limit", 20) or 20)
        )
        if not matches:
            return "No tools matched. Try broader terms; all tools remain callable by name."
        lines = []
        for match in matches:
            group = self._group_of_name.get(match["name"], "")
            inactive = (
                self._active_groups is not None
                and group
                and group not in self._active_groups
            )
            hint = (
                (
                    f" [in INACTIVE group '{group}' — still callable by name, or "
                    f'reset_tools({{"{group}": true}}) to load its schemas]'
                )
                if inactive
                else ""
            )
            lines.append(f"- {match['name']}: {match['description']}{hint}")
        return (
            "Matching tools (call tool_schema(name) for inputs, or call by name):\n"
            + "\n".join(lines)
        )

    def _describe_tool(self, args: dict) -> str:
        import json

        requested = str(args.get("tool_name", "")).strip()
        for entry in self._tool_defs:
            if getattr(entry, "name", "") == requested:
                return json.dumps(
                    {
                        "name": entry.name,
                        "description": getattr(entry, "description", "") or "",
                        "parameters": getattr(entry, "parameters", {})
                        or {"type": "object", "properties": {}},
                        "provider": getattr(entry, "provider", ""),
                        "requires_approval": getattr(entry, "requires_approval", True),
                    },
                    indent=2,
                )
        return (
            f"No tool named {requested!r}. Use tool_search(query) to find the right name "
            "(names are case-sensitive and exact)."
        )

    @staticmethod
    def _result_metadata(result: Any) -> dict:
        metadata = dict(getattr(result, "metadata", {}) or {})
        if getattr(result, "truncated", False):
            metadata["truncated"] = True
            length = getattr(result, "original_length", None)
            if length is not None:
                metadata["original_length"] = length
        if not getattr(result, "success", True):
            metadata["ok"] = False
            hints = getattr(result, "recovery_hints", None)
            if hints:
                metadata["recovery_hints"] = list(hints)
            error = getattr(result, "agent_error", None)
            if error is not None:
                metadata["agent_error"] = error.to_dict()
        return metadata

    async def _invoke(self, tool_name: str, args: dict, *, meta_sink: dict) -> str:
        if self._session_key.removeprefix("dashboard_").startswith("room:"):
            from gideon.security.guardrails.policy import (
                profile_for_session,
                tool_grant_denial,
            )

            posture = profile_for_session(self._session_key)
            denial = tool_grant_denial(
                tool_name, posture.tool_grants, posture.tool_allowlist
            )
            if denial:
                meta_sink["ok"] = False
                return f"Error: {denial}"
        handlers = {
            "tool_schema": self._describe_tool,
            "reset_tools": self._reset_tools,
        }
        if self._tool_retriever is not None:
            handlers["tool_search"] = self._search_tools
        if tool_name in handlers:
            return handlers[tool_name](args)
        provider = self._tool_index.get(tool_name)
        if provider is None:
            return f"Error: unknown tool {tool_name!r}"
        if self._tool_retriever is not None:
            self._tool_retriever.mark_used(tool_name)
        from contextlib import ExitStack

        from gideon.engine.agents.native import builtin_tools
        from gideon.integrations import mcp_core

        session = mcp_core.set_current_session_key(self._session_key)
        agent = mcp_core.set_current_agent_id(self._agent_id)
        workspace = builtin_tools.bind_tool_context(
            cwd=self._cwd, agent=self._agent_id, project_id=self._project_id
        )
        scope = cancellation.bind_scope(self._cancel)
        with ExitStack() as release:
            release.callback(builtin_tools.reset_tool_context, workspace)
            release.callback(mcp_core.reset_current_agent_id, agent)
            release.callback(mcp_core.reset_current_session_key, session)
            release.callback(cancellation.reset_scope, scope)
            result = await provider.invoke(tool_name, args)
        meta_sink.update(self._result_metadata(result))
        return format_tool_result(result)

    _META_TOOLS = frozenset(("tool_search", "tool_schema", "reset_tools"))

    def _requires_approval(self, tool_name: str) -> bool:
        if tool_name in self._META_TOOLS or self._approval_policy in (
            "auto",
            "yolo",
            "acceptEdits",
        ):
            return False
        definition = next(
            (entry for entry in self._tool_defs if entry.name == tool_name), None
        )
        return (
            True
            if definition is None
            else bool(getattr(definition, "requires_approval", True))
        )

    _COMPACT_THRESHOLD_PCT = 70.0

    def _estimated_context_pct(self) -> float | None:
        from gideon.cognition.context_compaction import total_chars
        from gideon.integrations.model_windows import model_context_window

        characters = total_chars(self._messages)
        if characters <= 0:
            return None
        capacity = model_context_window(
            self.agent_model or None,
            override=getattr(self._model, "context_window", None),
            local=bool(getattr(self._model, "is_local", False)),
        )
        return (
            100.0 * (characters / CONSERVATIVE_CHARS_PER_TOKEN) / capacity
            if capacity > 0
            else None
        )

    def _maybe_compact(self) -> None:
        from gideon.cognition import context_compaction

        occupancy = self._last_context_pct
        if occupancy is None:
            occupancy = self._estimated_context_pct()
        if occupancy is None or occupancy < self._COMPACT_THRESHOLD_PCT:
            return
        if not context_compaction.should_compact(self._compaction_saves):
            return
        initial_size = context_compaction.total_chars(self._messages)
        if initial_size <= 0:
            return
        replacement = context_compaction.compact(self._messages)
        remaining_size = context_compaction.total_chars(replacement)
        saved = (initial_size - remaining_size) / initial_size
        if remaining_size < initial_size:
            self._messages = replacement
            self._cache_generation += 1
            if self._last_context_pct is not None:
                self._last_context_pct = occupancy * (remaining_size / initial_size)
            self._breaker.reset_structural()
            logger.info(
                "native history compacted: %d to %d characters",
                initial_size,
                remaining_size,
            )
        self._compaction_saves.append(saved)

    @staticmethod
    def _assistant_msg(text: str, tool_calls: list[AgentEvent]) -> dict:
        def wire_call(call: AgentEvent) -> dict:
            arguments = call.tool_input
            entry = {
                "id": call.tool_call_id,
                "type": "function",
                "function": {
                    "name": call.title,
                    "arguments": (
                        arguments
                        if isinstance(arguments, str)
                        else _short_json(arguments)
                    ),
                },
            }
            extra = (call.tool_meta or {}).get("extra_content")
            if extra:
                entry["extra_content"] = extra
            return entry

        message: dict[str, Any] = dict(role="assistant", content=text or "")
        if tool_calls:
            message["tool_calls"] = [wire_call(call) for call in tool_calls]
        return message

    @staticmethod
    def _tool_result_msg(call: AgentEvent, result_str: str) -> dict:
        return dict(role="tool", tool_call_id=call.tool_call_id, content=result_str)

    async def approve_tool(self, request_id: str | int) -> None:
        gate = self._approval
        gate.approve(str(request_id))

    async def reject_tool(self, request_id: str | int) -> None:
        gate = self._approval
        gate.reject(str(request_id))

    async def revise_tool(self, request_id: str | int, instruction: str) -> bool:
        key = str(request_id)
        if not instruction.strip() or len(instruction) > 4000:
            return False
        self._revisions[key] = instruction.strip()
        if self._approval.revise(key):
            return True
        self._revisions.pop(key, None)
        return False

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    @property
    def compacts_in_process(self) -> bool:
        return True

    async def compact(self, context: str = "") -> None:
        from gideon.cognition import context_compaction

        before = context_compaction.total_chars(self._messages)
        replacement = context_compaction.compact(self._messages)
        after = context_compaction.total_chars(replacement)
        status = "completed" if after < before else "noop"
        if status == "completed":
            self._messages = replacement
            self._cache_generation += 1
            if self._last_context_pct is not None and before > 0:
                self._last_context_pct *= after / before
            self._breaker.reset_structural()
        self._compaction_result = {
            "type": status,
            "before": before,
            "after": after,
            "summary": f"{before:,} → {after:,} characters",
        }

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return dict(self._compaction_result)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        scope = self._cancel
        disposition = scope.request(reason=CANCEL_USER)
        immediate = {REQUEST_NO_TURN: "no_turn", REQUEST_REPEAT: "acked"}
        if disposition in immediate:
            return immediate[disposition]
        self._approval.cancel_all()
        abort = getattr(self._model, "cancel", None)
        if abort is not None:
            try:
                await abort(wait_ack_timeout=wait_ack_timeout)
            except Exception:
                logger.warning("Could not abort native model request", exc_info=True)
            else:
                scope.note_model_request_aborted()
        try:
            await scope.reap_children()
        except Exception:
            logger.warning("Could not reap native tool children", exc_info=True)
        return "acked"

    def is_alive(self) -> bool:
        return True

    def stage_image_part(self, data_url: str) -> bool:
        receiver = getattr(self._model, "stage_image_part", None)
        return bool(receiver(data_url)) if callable(receiver) else False

    def drain_tool_outcomes(self) -> list[tuple[str, str]]:
        outcomes = self._tool_outcomes[:]
        del self._tool_outcomes[:]
        return outcomes

    def set_workspace(self, path: Path) -> None:
        self._cwd = Path(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._session_key = session_key

    def export_turn_state(self) -> dict:
        if self._cancel.active:
            raise RuntimeError("Cannot export native state during an active turn")
        return {
            "messages": copy.deepcopy(self._messages),
            "cache_generation": self._cache_generation,
            "last_context_pct": self._last_context_pct,
        }

    def restore_turn_state(self, state: dict) -> None:
        if self._cancel.active or self._messages:
            raise RuntimeError("Native state can only be restored before its first turn")
        self._messages = copy.deepcopy(state["messages"])
        self._cache_generation = int(state.get("cache_generation", 0)) + 1
        self._last_context_pct = state.get("last_context_pct")

    async def set_reasoning_effort(self, effort: str) -> None:
        if effort not in {"", "low", "medium", "high", "max"}:
            raise ValueError("unsupported reasoning effort")
        self._next_reasoning_effort = effort

    def set_steer_source(self, pull: "Callable[[], list[str]] | None") -> bool:
        self._pull_steer = pull
        return self._pull_steer is not None

    def _drain_steers_into_history(self) -> bool:
        if self._pull_steer is not None:
            try:
                arrivals = self._pull_steer() or []
            except Exception:
                logger.debug("Steering source failed", exc_info=True)
            else:
                self._steer_pending.extend(
                    text for text in arrivals if text and text.strip()
                )
        capacity = max(0, _MAX_STEERS_PER_TURN - self._steers_injected)
        delivery = self._steer_pending[:capacity]
        del self._steer_pending[: len(delivery)]
        self._messages.extend(
            {
                "role": "user",
                "content": f"[Steering — the user added this mid-task]\n{text}",
            }
            for text in delivery
        )
        self._steers_injected += len(delivery)
        return bool(delivery)

    def undelivered_steers(self) -> list[str]:
        return self._steer_pending[:]

    def set_approval_policy(self, policy: str) -> None:
        self._approval_policy = policy or ""

    def set_task_mode(self, mode: str) -> None:
        self._task_mode = mode or "agent"

    @property
    def agent_model(self) -> str:
        configured = self._definition.model
        if configured:
            return configured
        return getattr(self._model, "_model", "") or ""

    @property
    def agent_name(self) -> str:
        return self._definition.name or ""


def _n_tools(n: int) -> str:
    return f"{n} tool{'' if n == 1 else 's'}"


def _short_json(value: Any) -> str:
    import json

    try:
        return json.JSONEncoder(default=str).encode(value)
    except (TypeError, ValueError):
        return str(value)
