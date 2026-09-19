"""Declarative pipeline rules and persisted, scoped lifecycle actions."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from gideon.core.atomic_write import atomic_write
from gideon.security.safety_flags import strict_bool
from gideon.security.security import (
    is_denied,
    is_sensitive_bash_command,
    is_sensitive_path,
)

logger = logging.getLogger(__name__)

HOOK_PASSTHROUGH = "passthrough"

HOOK_REPLY = "reply"

HOOK_MODIFY = "modify"

HOOK_INJECT_CONTEXT = "inject_context"

TOOL_ALLOW = "allow"

TOOL_AUTO_APPROVE = "auto_approve"

TOOL_DENY = "deny"

HOOK_EVENT_AGENT_SPAWN = "AgentSpawn"

HOOK_EVENT_USER_PROMPT_SUBMIT = "UserPromptSubmit"

HOOK_EVENT_PRE_TOOL_USE = "PreToolUse"

HOOK_EVENT_POST_TOOL_USE = "PostToolUse"

HOOK_EVENT_STOP = "Stop"

HOOK_EVENT_PRE_RESPONSE = "PreResponse"

HOOK_EVENT_POST_RESPONSE = "PostResponse"

HOOK_EVENT_SESSION_START = "SessionStart"

HOOK_EVENT_SESSION_END = "SessionEnd"

HOOK_EVENT_MEMORY_WRITE = "MemoryWrite"

HOOK_EVENT_ERROR = "Error"

HOOK_EVENT_CONTEXT_COMPACT = "ContextCompact"

HOOK_EVENT_SUBAGENT_SPAWN = "SubagentSpawn"

HOOK_EVENT_TASK_COMPLETE = "TaskComplete"

HOOK_EVENT_APPROVAL_REQUEST = "ApprovalRequest"

HOOK_EVENTS = (
    HOOK_EVENT_AGENT_SPAWN,
    HOOK_EVENT_SESSION_START,
    HOOK_EVENT_USER_PROMPT_SUBMIT,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_POST_TOOL_USE,
    HOOK_EVENT_PRE_RESPONSE,
    HOOK_EVENT_POST_RESPONSE,
    HOOK_EVENT_MEMORY_WRITE,
    HOOK_EVENT_CONTEXT_COMPACT,
    HOOK_EVENT_SUBAGENT_SPAWN,
    HOOK_EVENT_TASK_COMPLETE,
    HOOK_EVENT_APPROVAL_REQUEST,
    HOOK_EVENT_ERROR,
    HOOK_EVENT_SESSION_END,
    HOOK_EVENT_STOP,
)

_LIFECYCLE_BASE_VARS = ("$EVENT", "$CONTEXT", "$cwd")

LIFECYCLE_EVENT_CATALOG: tuple[dict, ...] = (
    {
        "event": HOOK_EVENT_AGENT_SPAWN,
        "label": "Agent spawn",
        "desc": "A new agent session is created.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_SESSION_START,
        "label": "Session start",
        "desc": "A chat/agent session begins.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
        "label": "User prompt submit",
        "desc": "The user submits a turn — before the agent runs.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$prompt"),
    },
    {
        "event": HOOK_EVENT_PRE_TOOL_USE,
        "label": "Pre tool use",
        "desc": "Before a tool runs — can block it.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name", "$tool_input"),
        "blocking": True,
    },
    {
        "event": HOOK_EVENT_POST_TOOL_USE,
        "label": "Post tool use",
        "desc": "After a tool runs.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name", "$tool_input", "$tool_response"),
    },
    {
        "event": HOOK_EVENT_PRE_RESPONSE,
        "label": "Pre response",
        "desc": "Before the agent streams its reply.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_POST_RESPONSE,
        "label": "Post response",
        "desc": "After the agent finishes its reply.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_MEMORY_WRITE,
        "label": "Memory write",
        "desc": "The agent writes a memory/lesson.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_CONTEXT_COMPACT,
        "label": "Context compact",
        "desc": "The conversation context is summarized.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_SUBAGENT_SPAWN,
        "label": "Subagent spawn",
        "desc": "A subagent is spawned.",
        "vars": (
            *_LIFECYCLE_BASE_VARS,
            "$subagent_id",
            "$parent_session_key",
            "$agent_role",
        ),
    },
    {
        "event": HOOK_EVENT_TASK_COMPLETE,
        "label": "Task complete",
        "desc": "A task finishes.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_APPROVAL_REQUEST,
        "label": "Approval request",
        "desc": "A tool needs approval.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name"),
    },
    {
        "event": HOOK_EVENT_ERROR,
        "label": "Error",
        "desc": "An error occurs in the loop.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_SESSION_END,
        "label": "Session end",
        "desc": "A session ends.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_STOP,
        "label": "Stop",
        "desc": "The agent loop stops.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
)

BLOCKING_EVENTS: frozenset[str] = frozenset(
    str(e["event"]) for e in LIFECYCLE_EVENT_CATALOG if e.get("blocking")
)

ENFORCEMENT_ENFORCING = "enforcing"

ENFORCEMENT_NOT_ENFORCING = "not_enforcing"

ENFORCEMENT_ADVISORY = "advisory"

ENFORCEMENT_STATES: frozenset[str] = frozenset(
    {ENFORCEMENT_ENFORCING, ENFORCEMENT_NOT_ENFORCING, ENFORCEMENT_ADVISORY}
)

_TOOL_TITLE_PREFIXES = ("Running: ", "Reading ")

MAX_FILE_BYTES = 50 * 1024 * 1024

_HOOKS_FILE = "hooks.json"


def hook_enforcement(event: str, *, enabled: bool, bound: bool) -> str:
    if event in BLOCKING_EVENTS:
        return ENFORCEMENT_ENFORCING if enabled and bound else ENFORCEMENT_NOT_ENFORCING
    return ENFORCEMENT_ADVISORY


@dataclass
class HookResult:
    action: str
    text: str = ""

    @staticmethod
    def passthrough() -> "HookResult":
        return HookResult(HOOK_PASSTHROUGH)

    @staticmethod
    def reply(text: str) -> "HookResult":
        return HookResult(HOOK_REPLY, text)

    @staticmethod
    def modify(text: str) -> "HookResult":
        return HookResult(HOOK_MODIFY, text)

    @staticmethod
    def inject_context(text: str) -> "HookResult":
        return HookResult(HOOK_INJECT_CONTEXT, text)


@dataclass
class ToolHookResult:
    action: str
    reason: str = ""

    @staticmethod
    def allow() -> "ToolHookResult":
        return ToolHookResult(TOOL_ALLOW)

    @staticmethod
    def auto_approve() -> "ToolHookResult":
        return ToolHookResult(TOOL_AUTO_APPROVE)

    @staticmethod
    def deny(reason: str) -> "ToolHookResult":
        return ToolHookResult(TOOL_DENY, reason)


@dataclass
class ContextRule:
    triggers: list[str] = field(default_factory=list)
    context: str = ""


@dataclass
class AutoReplyHook:
    pattern: str = ""
    reply: str = ""
    exact: bool = False


@dataclass
class TransformHook:
    pattern: str = ""
    prefix: str = ""
    suffix: str = ""


def _declared_fields(record_type, data: dict) -> dict:
    defaults = record_type()
    return {
        item.name: data.get(item.name, getattr(defaults, item.name))
        for item in fields(record_type)
    }


@dataclass
class HooksConfig:
    auto_approve_tools: list[str] = field(default_factory=list)
    auto_approve_sources: list[str] = field(default_factory=list)
    auto_approve_subagent_spawn: bool = False
    auto_approve_subagent_tools: bool = False
    auto_deny_tools: list[str] = field(default_factory=list)
    auto_replies: list[AutoReplyHook] = field(default_factory=list)
    transforms: list[TransformHook] = field(default_factory=list)
    context_rules: list[ContextRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "HooksConfig":
        values = _declared_fields(cls, data)
        for name, record in (
            ("auto_replies", AutoReplyHook),
            ("transforms", TransformHook),
            ("context_rules", ContextRule),
        ):
            values[name] = [
                record(**_declared_fields(record, row)) for row in values[name]
            ]
        for name in ("auto_approve_subagent_spawn", "auto_approve_subagent_tools"):
            values[name] = strict_bool(data.get(name), field=f"hooks.{name}")
        return cls(**values)


@dataclass(frozen=True)
class _MessageRules:
    config: HooksConfig
    text: str

    def evaluate(self) -> HookResult:
        folded = self.text.lower()
        reply = next(
            (
                rule
                for rule in self.config.auto_replies
                if (
                    folded == rule.pattern.lower()
                    if rule.exact
                    else rule.pattern.lower() in folded
                )
            ),
            None,
        )
        if reply is not None:
            return HookResult.reply(reply.reply)
        transform = next(
            (rule for rule in self.config.transforms if rule.pattern.lower() in folded),
            None,
        )
        if transform is not None:
            parts = [self.text]
            if transform.prefix:
                parts.insert(0, str(transform.prefix))
            if transform.suffix:
                parts.append(str(transform.suffix))
            return HookResult.modify("\n".join(parts))
        context = [
            rule.context
            for rule in self.config.context_rules
            if any(word.lower() in folded for word in rule.triggers)
        ]
        return (
            HookResult.inject_context("\n\n".join(context))
            if context
            else HookResult.passthrough()
        )


@dataclass(frozen=True)
class _ToolAdmission:
    original: str
    normalized: str
    config: HooksConfig

    def refusal(self) -> str:
        if self.original.startswith("Reading "):
            if is_sensitive_path(self.normalized):
                return f"Blocked: access to sensitive path: {self.normalized}"
        elif self.original.startswith("Running: "):
            reason = is_sensitive_bash_command(self.normalized)
            if reason:
                return reason
            from gideon.automation.triggers.handoff import detect

            proposal = detect(self.normalized)
            if proposal is not None:
                return proposal.observation
        return (
            is_denied(self.normalized, self.config.auto_deny_tools)
            or is_denied(self.original, self.config.auto_deny_tools)
            or ""
        )

    def evaluate(self) -> ToolHookResult:
        refusal = self.refusal()
        if refusal:
            return ToolHookResult.deny(refusal)
        for pattern in self.config.auto_approve_tools:
            if not any(
                _tool_matches(pattern, name)
                for name in (self.original, self.normalized)
            ):
                continue
            if not _chains_beyond_pattern(pattern, self.normalized):
                return ToolHookResult.auto_approve()
            logger.info(
                "not auto-approving a chained command on pattern %r: %s",
                pattern,
                self.normalized[:120],
            )
        return ToolHookResult.allow()


class HookManager:
    def __init__(self, config: HooksConfig | None = None):
        self._config = config or HooksConfig()

    def reload(self, config: HooksConfig) -> None:
        self._config = config

    @property
    def auto_approve_subagent_spawn(self) -> bool:
        return self._config.auto_approve_subagent_spawn

    @property
    def auto_approve_subagent_tools(self) -> bool:
        return self._config.auto_approve_subagent_tools

    def on_message(self, text: str) -> HookResult:
        return _MessageRules(self._config, text).evaluate()

    def on_tool_call(self, tool_name: str) -> ToolHookResult:
        return _ToolAdmission(
            tool_name, _normalize_tool_name(tool_name), self._config
        ).evaluate()


def _normalize_tool_name(tool_name: str) -> str:
    prefix = next(
        (value for value in _TOOL_TITLE_PREFIXES if tool_name.startswith(value)), ""
    )
    return tool_name[len(prefix) :]


def _chains_beyond_pattern(pattern: str, command: str) -> bool:
    from gideon.security.security import _CMD_SEPARATOR_RE

    bare = _normalize_tool_name(pattern.strip()).strip()
    authorizes_chain = (
        bare in ("*", "") or _CMD_SEPARATOR_RE.search(pattern) is not None
    )
    return not authorizes_chain and _CMD_SEPARATOR_RE.search(command) is not None


def _tool_matches(pattern: str, tool_name: str) -> bool:
    return pattern == "*" or fnmatch.fnmatch(tool_name.lower(), pattern.lower())


def validate_file_path(raw: str) -> str | None:
    if raw:
        try:
            canonical = os.path.realpath(os.path.expanduser(raw))
        except (ValueError, OSError):
            logger.debug(
                "validate_file_path: uncanonicalizable path rejected", exc_info=True
            )
        else:
            if not is_sensitive_path(canonical):
                return canonical
    return None


def safe_read_file(path: str) -> str:
    try:
        canonical = Path(path).expanduser().resolve()
    except (ValueError, OSError) as exc:
        raise PermissionError(f"Blocked: unusable path: {exc}") from exc
    if is_sensitive_path(str(canonical)):
        raise PermissionError(f"Blocked: access to sensitive path: {canonical}")
    return canonical.read_text(encoding="utf-8")


class FileTooLargeError(Exception):
    """A requested file exceeds the configured byte boundary."""


def safe_read_file_bytes(raw: str) -> bytes | None:
    resolved = validate_file_path(raw)
    if resolved is None:
        return None
    try:
        with Path(resolved).open("rb") as source:
            payload = source.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if len(payload) <= MAX_FILE_BYTES:
        return payload
    raise FileTooLargeError(
        f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB safety cap"
    )


@dataclass
class ScriptHook:
    id: str = ""
    name: str = ""
    event: str = HOOK_EVENT_USER_PROMPT_SUBMIT
    matcher: str = ""
    provider: str = "bash"
    provider_config: dict = field(default_factory=dict)
    timeout: int = 30
    enabled: bool = True
    last_run: float = 0.0
    last_status: str = ""
    run_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptHook":
        values = _declared_fields(cls, data)
        values["id"] = data.get("id", str(uuid.uuid4())[:8])
        values["provider_config"] = dict(values["provider_config"] or {})
        return cls(**values)


@dataclass
class ScriptHookResult:
    hook_id: str
    hook_name: str
    event: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    error: str = ""
    duration_ms: int = 0

    @property
    def blocked(self) -> bool:
        return self.exit_code == 2

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class _HookDispatch:
    hook: ScriptHook
    context: str
    payload: dict
    enforced: bool
    record: Callable[[str], None]

    def failure(self, status: str, error: str) -> ScriptHookResult:
        self.record(status)
        return ScriptHookResult(
            self.hook.id, self.hook.name, self.hook.event, error=error
        )

    def admit(self, context):
        from gideon.security.guardrails.denylist import enforce_action
        from gideon.security.guardrails.incident import incident_active
        from gideon.security.guardrails.policy import unattended_dispatch_key
        from gideon.security.guardrails.rungs import (
            announce_withheld,
            route_provider_action,
        )

        if incident_active():
            return None, self.failure(
                "skipped_incident", "skipped: incident mode active"
            )
        hook = self.hook
        session = str(
            self.payload.get("parent_session_key", "")
        ) or unattended_dispatch_key(f"hook:{hook.id}")
        verdict = enforce_action(
            hook.provider, hook.provider_config, context, session_key=session
        )
        if verdict.blocked:
            return None, self.failure(
                "blocked", f"blocked by guardrails denylist: {verdict.reason}"
            )
        route = route_provider_action(hook.provider, session_key=session)
        if route.executes:
            return route, None
        announce_withheld(
            route,
            title=f"{hook.name or hook.provider} is waiting for you",
            body=f"The {hook.provider!r} action on the {hook.event} event did not run: {route.reason}.",
            refs={"hook": hook.id, "provider": hook.provider},
            dedup_key=f"autonomy_hold:{route.key}:hook:{hook.id}",
        )
        return None, self.failure(
            "held_for_rung", f"held for your approval: {route.reason}"
        )

    def status(self, result) -> str:
        if result.blocked:
            return "blocked" if self.enforced else "advisory"
        if result.success:
            return result.outcome if result.outcome in ("launched", "queued") else "ok"
        return "timeout" if result.error and "Timed out" in result.error else "error"

    def finish(self, result, route) -> ScriptHookResult:
        from gideon.security.guardrails.rungs import record_reversal

        hook = self.hook
        self.record(self.status(result))
        if route.records_reversal and result.success:
            record_reversal(
                route,
                result,
                label=hook.name or hook.provider,
                refs={"hook": hook.id, "provider": hook.provider},
            )
        return ScriptHookResult(
            hook.id,
            hook.name,
            hook.event,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=-1 if result.exit_code is None else result.exit_code,
            error=(
                result.error
                if result.agent_error is None
                else result.agent_error.render()
            ),
            duration_ms=result.duration_ms,
        )

    async def execute(self) -> ScriptHookResult:
        from gideon.integrations.action_providers import (
            get_action_provider,
            provider_failure,
        )
        from gideon.integrations.action_providers.base import ActionContext

        provider = get_action_provider(self.hook.provider)
        if provider is None:
            return self.failure(
                "error", f"Unknown action provider {self.hook.provider!r}"
            )
        context = ActionContext(
            event=self.hook.event, context=self.context, payload=self.payload
        )
        route, refusal = self.admit(context)
        if refusal is not None:
            return refusal
        try:
            result = await provider.execute(
                self.hook.provider_config, context, timeout=self.hook.timeout
            )
        except Exception as exc:
            logger.warning(
                "Action provider %r raised for hook %s",
                self.hook.provider,
                self.hook.id,
            )
            self.record("error")
            envelope = provider_failure(self.hook.provider, exc)
            return ScriptHookResult(
                self.hook.id, self.hook.name, self.hook.event, error=envelope.render()
            )
        return self.finish(result, route)


async def run_script_hook(
    hook: ScriptHook,
    context: str = "",
    hook_event: dict | None = None,
    *,
    enforced: bool = False,
    test: bool = False,
) -> ScriptHookResult:
    from gideon.integrations.action_providers.registry import (
        _ensure_default_providers_registered,
    )

    _ensure_default_providers_registered()
    payload = (
        {"hook_event_name": hook.event, "cwd": os.getcwd()}
        if hook_event is None
        else hook_event
    )
    if test:
        payload = {**payload, "test": True}

    def _record(status: str) -> None:
        if test:
            return
        hook.last_run = time.time()
        hook.last_status = (
            status
            if status
            in (
                "ok",
                "error",
                "timeout",
                "launched",
                "queued",
                "blocked",
                "advisory",
                "held_for_rung",
                "skipped_incident",
            )
            else "error"
        )
        hook.run_count += 1

    return await _HookDispatch(hook, context, payload, enforced, _record).execute()


@dataclass(frozen=True)
class _HookSelection:
    event: str
    context: str
    tool: str
    identifiers: set[str] | None

    def accepts(self, hook: ScriptHook) -> bool:
        if not hook.enabled or hook.event != self.event:
            return False
        if self.identifiers is not None and hook.id not in self.identifiers:
            return False
        if not hook.matcher:
            return True
        if self.event in (HOOK_EVENT_PRE_TOOL_USE, HOOK_EVENT_POST_TOOL_USE):
            return _tool_matches(hook.matcher, self.tool)
        return not self.context or fnmatch.fnmatch(
            self.context.lower(), hook.matcher.lower()
        )

    def payload(
        self,
        depth,
        tool_input,
        tool_response,
        subagent_id,
        parent_session_key,
        agent_role,
    ) -> dict:
        payload = {
            "hook_event_name": self.event,
            "cwd": os.getcwd(),
            "__hook_depth": depth,
        }
        optional = {
            "subagent_id": subagent_id,
            "parent_session_key": parent_session_key,
            "agent_role": agent_role,
            "tool_name": self.tool,
        }
        payload.update({key: value for key, value in optional.items() if value})
        if self.event == HOOK_EVENT_USER_PROMPT_SUBMIT and self.context:
            payload["prompt"] = self.context
        for key, value in (
            ("tool_input", tool_input),
            ("tool_response", tool_response),
        ):
            if value is not None:
                payload[key] = value
        return payload


def _validate_hook_patch(data: dict) -> None:
    if "event" in data and data["event"] not in HOOK_EVENTS:
        raise ValueError(f"invalid event: {data['event']}")
    if "timeout" in data and (
        not isinstance(data["timeout"], int) or not 1 <= data["timeout"] <= 300
    ):
        raise ValueError("timeout must be an integer between 1 and 300")
    if "provider_config" in data and not isinstance(data["provider_config"], dict):
        raise ValueError("provider_config must be an object")


class ScriptHookStore:
    def __init__(self, config_dir: Path | None = None):
        from gideon.core.config.loader import config_dir as active_home

        self._dir = config_dir or active_home()
        self._path = self._dir / _HOOKS_FILE
        self._hooks: dict[str, ScriptHook] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                document = json.loads(self._path.read_text(encoding="utf-8"))
                for entry in document.get("hooks", []):
                    restored = ScriptHook.from_dict(entry)
                    self._hooks[restored.id] = restored
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load hooks: %s", exc)

    def _save(self) -> None:
        self._save_snapshot([record.to_dict() for record in self._hooks.values()])

    def _save_snapshot(self, hooks_data: list[dict]) -> None:
        atomic_write(self._path, json.dumps({"hooks": hooks_data}, indent=2))

    def list_all(self) -> list[ScriptHook]:
        return [*self._hooks.values()]

    def get(self, hook_id: str) -> ScriptHook | None:
        return self._hooks.get(hook_id)

    def create(self, data: dict) -> ScriptHook:
        record = ScriptHook.from_dict(data)
        record.id = record.id or str(uuid.uuid4())[:8]
        self._hooks[record.id] = record
        self._save()
        return record

    def update(self, hook_id: str, data: dict) -> ScriptHook | None:
        record = self.get(hook_id)
        if record is None:
            return None
        _validate_hook_patch(data)
        editable = (
            "name",
            "event",
            "matcher",
            "provider",
            "provider_config",
            "timeout",
            "enabled",
        )
        record.__dict__.update({key: data[key] for key in editable if key in data})
        self._save()
        return record

    def delete(self, hook_id: str) -> bool:
        if self._hooks.pop(hook_id, None) is None:
            return False
        self._save()
        return True

    def toggle(self, hook_id: str) -> ScriptHook | None:
        record = self.get(hook_id)
        return (
            self.update(hook_id, {"enabled": not record.enabled})
            if record is not None
            else None
        )

    async def fire(
        self,
        event: str,
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        return await self._fire(
            event,
            context=context,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            hook_ids=None,
            subagent_id=subagent_id,
            parent_session_key=parent_session_key,
            agent_role=agent_role,
        )

    async def fire_for_ids(
        self,
        event: str,
        hook_ids: "set[str] | list[str] | None",
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
        depth: int = 0,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        if not hook_ids:
            return []
        return await self._fire(
            event,
            context=context,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            hook_ids=set(hook_ids),
            depth=depth,
            enforced=True,
            subagent_id=subagent_id,
            parent_session_key=parent_session_key,
            agent_role=agent_role,
        )

    async def _fire(
        self,
        event: str,
        *,
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
        hook_ids: "set[str] | None" = None,
        depth: int = 0,
        enforced: bool = False,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        selection = _HookSelection(event, context, tool_name, hook_ids)
        payload = selection.payload(
            depth,
            tool_input,
            tool_response,
            subagent_id,
            parent_session_key,
            agent_role,
        )
        results = []
        for hook in list(self._hooks.values()):
            if selection.accepts(hook):
                result = await run_script_hook(
                    hook,
                    context,
                    payload,
                    enforced=enforced and event in BLOCKING_EVENTS,
                )
                results.append(result)
                logger.info(
                    "Hook %s (%s): %s in %dms (exit=%d)",
                    hook.name,
                    event,
                    hook.last_status,
                    result.duration_ms,
                    result.exit_code,
                )
        snapshot = [record.to_dict() for record in self._hooks.values()]
        await asyncio.to_thread(self._save_snapshot, snapshot)
        return results


_global_script_hook_store: ScriptHookStore | None = None


def set_global_hook_store(store: ScriptHookStore) -> None:
    global _global_script_hook_store
    _global_script_hook_store = store


def get_global_hook_store() -> ScriptHookStore | None:
    return _global_script_hook_store


async def fire_tool_hooks(
    hook_store: ScriptHookStore | None,
    event_title: str,
    event_tool_input: str | None = None,
    *,
    subagent_id: str = "",
    parent_session_key: str = "",
    agent_role: str = "",
) -> None:
    if hook_store is None:
        return
    title = (event_title or "").removeprefix("Running: ")
    arguments = None
    if event_tool_input:
        try:
            arguments = json.loads(event_tool_input)
        except Exception:
            pass
    try:
        await hook_store.fire(
            HOOK_EVENT_PRE_TOOL_USE,
            tool_name=title,
            tool_input=arguments,
            subagent_id=subagent_id,
            parent_session_key=parent_session_key,
            agent_role=agent_role,
        )
    except Exception:
        logger.debug("PreToolUse hook error", exc_info=True)
