"""Explicitly enabled, isolated-home playback of validated JSON turns."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from gideon.integrations.llm.base import ModelProvider, _last_user_text
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
)
from gideon.integrations.llm.events import AgentEvent as LLMEvent

SCRIPT_ENV_VAR = "GIDEON_SCRIPTED_MODEL_SCRIPT"

HOME_ENV_VAR = "GIDEON_HOME"

SCRIPT_VERSION = 1

_TOP_LEVEL_KEYS = frozenset({"version", "context_usage_pct", "on_exhausted", "turns"})

_TURN_KEYS = frozenset(
    {
        "expect_prompt",
        "text",
        "chunks",
        "thinking",
        "tool_calls",
        "stop_reason",
        "usage",
        "context_usage_pct",
        "duration_ms",
    }
)

_TOOL_CALL_KEYS = frozenset(
    {"id", "name", "input", "risk_level", "requires_approval", "options"}
)

_USAGE_KEYS = frozenset(
    {"input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"}
)

_ON_EXHAUSTED = frozenset({"repeat_last", "error"})


class ScriptedProviderError(RuntimeError):
    """Playback could not be enabled or completed."""


class ScriptedProviderNotEnabled(ScriptedProviderError):
    """No script was explicitly selected."""


class ScriptedProviderRefused(ScriptedProviderError):
    """Playback would use the real assistant home."""


class ScriptedScriptError(ScriptedProviderError):
    """The selected script or requested turn is invalid."""


def _real_home() -> Path:
    from gideon.core.config.loader import CONFIG_DIR_NAME

    return Path.home().joinpath(CONFIG_DIR_NAME).resolve()


def resolve_script_path() -> Path:
    selection = os.environ.get(SCRIPT_ENV_VAR, "").strip()
    if not selection:
        raise ScriptedProviderNotEnabled(
            f"ScriptedProvider is disabled; {SCRIPT_ENV_VAR} must name a script file."
        )
    override = os.environ.get(HOME_ENV_VAR, "").strip()
    real = _real_home()
    if not override or Path(override).expanduser().resolve() == real:
        raise ScriptedProviderRefused(
            f"ScriptedProvider refuses the real assistant home ({real}); "
            f"set {HOME_ENV_VAR} to an isolated directory."
        )
    selected = Path(selection).expanduser()
    if selected.is_file():
        return selected
    raise ScriptedScriptError(
        f"{SCRIPT_ENV_VAR}={selection!r} does not name a readable file."
    )


class _ScriptRecord:
    def __init__(self, raw: Any, allowed: frozenset[str], location: str) -> None:
        self.location = location
        if not isinstance(raw, dict):
            raise ScriptedScriptError(
                f"{location} must be a JSON object, got {type(raw).__name__}"
            )
        unexpected = sorted(raw.keys() - allowed)
        if unexpected:
            raise ScriptedScriptError(
                f"{location} has unknown key(s) {unexpected}; allowed: {sorted(allowed)}"
            )
        self.data = raw

    def typed(self, key: str, expected: type, default: Any) -> Any:
        value = self.data.get(key, default)
        if not isinstance(value, expected) or (
            expected is int and isinstance(value, bool)
        ):
            label = {
                str: "a string",
                int: "an integer",
                bool: "a boolean",
                list: "a list",
            }[expected]
            raise ScriptedScriptError(f"{self.location}.{key} must be {label}")
        return value

    def required_text(self, key: str) -> str:
        value = self.data.get(key)
        if isinstance(value, str) and value:
            return value
        raise ScriptedScriptError(
            f"{self.location} requires a non-empty string {key!r}"
        )

    def strings(self, key: str) -> list[str]:
        values = self.typed(key, list, [])
        if any(not isinstance(value, str) for value in values):
            raise ScriptedScriptError(
                f"{self.location}.{key} must be a list of strings"
            )
        return [*values]

    def percentage(self, fallback: float | None = None) -> float | None:
        value = self.data.get("context_usage_pct", fallback)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScriptedScriptError(
                f"{self.location}.context_usage_pct must be a number or null"
            )
        return float(value)


def _tool_record(raw: Any, location: str) -> dict[str, Any]:
    record = _ScriptRecord(raw, _TOOL_CALL_KEYS, location)
    approval = record.typed("requires_approval", bool, False)
    options = record.typed("options", list, [])
    if options and not approval:
        raise ScriptedScriptError(
            f"{location} sets 'options' without 'requires_approval'; options are only emitted on a permission request"
        )
    return {
        "id": record.required_text("id"),
        "name": record.required_text("name"),
        "input": record.data.get("input", ""),
        "risk_level": record.typed("risk_level", str, ""),
        "requires_approval": approval,
        "options": [*options],
    }


def _turn_record(raw: Any, index: int, percentage: float | None) -> dict[str, Any]:
    record = _ScriptRecord(raw, _TURN_KEYS, f"turns[{index}]")
    if {"text", "chunks"}.issubset(record.data):
        raise ScriptedScriptError(
            f"{record.location} sets both 'text' and 'chunks'; pick one"
        )
    if "text" in record.data:
        text = record.typed("text", str, "")
        chunks = [text] if text else []
    else:
        chunks = record.strings("chunks")
    raw_usage = record.data.get("usage")
    usage = _ScriptRecord(
        {} if raw_usage is None else raw_usage, _USAGE_KEYS, f"{record.location}.usage"
    )
    calls = record.typed("tool_calls", list, [])
    return {
        "expect_prompt": record.typed("expect_prompt", str, ""),
        "thinking": record.strings("thinking"),
        "chunks": chunks,
        "tool_calls": [
            _tool_record(call, f"{record.location}.tool_calls[{offset}]")
            for offset, call in enumerate(calls)
        ],
        "stop_reason": record.typed("stop_reason", str, ""),
        "usage": {key: usage.typed(key, int, 0) for key in sorted(_USAGE_KEYS)},
        "context_usage_pct": record.percentage(percentage),
        "duration_ms": record.typed("duration_ms", int, 0),
    }


def load_script(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ScriptedScriptError(f"cannot read script {path}: {error}") from error
    try:
        raw = json.loads(text)
    except ValueError as error:
        raise ScriptedScriptError(
            f"script {path} is not valid JSON: {error}"
        ) from error
    script = _ScriptRecord(raw, _TOP_LEVEL_KEYS, f"script {path}")
    if script.data.get("version") != SCRIPT_VERSION:
        raise ScriptedScriptError(
            f"script {path} has version {script.data.get('version')!r}; this build understands version {SCRIPT_VERSION}"
        )
    exhausted = script.data.get("on_exhausted", "repeat_last")
    if not isinstance(exhausted, str) or exhausted not in _ON_EXHAUSTED:
        raise ScriptedScriptError(
            f"script {path}.on_exhausted must be one of {sorted(_ON_EXHAUSTED)}"
        )
    turns = script.data.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ScriptedScriptError(f"script {path} requires a non-empty 'turns' list")
    percentage = script.percentage()
    return {
        "on_exhausted": exhausted,
        "turns": [
            _turn_record(turn, index, percentage) for index, turn in enumerate(turns)
        ],
    }


class _PlaybackCursor:
    def __init__(self, script: dict[str, Any], path: Path) -> None:
        self.script, self.path, self.position = script, path, 0

    def claim(self, prompt: str) -> dict[str, Any]:
        turns = self.script["turns"]
        if self.position >= len(turns) and self.script["on_exhausted"] == "error":
            raise ScriptedScriptError(
                f"script {self.path} has {len(turns)} turn(s) but prompt {self.position + 1} was sent (on_exhausted='error')"
            )
        selected = min(self.position, len(turns) - 1)
        turn = turns[selected]
        expected = turn["expect_prompt"]
        if expected and expected not in prompt:
            raise ScriptedScriptError(
                f"script {self.path} turns[{selected}] expects a prompt containing {expected!r} but got {prompt!r}"
            )
        self.position += 1
        return turn


def _turn_events(turn: dict[str, Any]) -> Iterator[LLMEvent]:
    for field, kind in (
        ("thinking", EVENT_THINKING_CHUNK),
        ("chunks", EVENT_TEXT_CHUNK),
    ):
        for text in turn[field]:
            yield LLMEvent(kind=kind, text=text)
    for call in turn["tool_calls"]:
        details = dict(
            tool_call_id=call["id"],
            title=call["name"],
            risk_level=call["risk_level"],
            tool_input=call["input"],
        )
        if call["requires_approval"]:
            yield LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                request_id=call["id"],
                options=[*call["options"]],
                **details,
            )
        yield LLMEvent(kind=EVENT_TOOL_CALL, **details)
    yield LLMEvent(
        kind=EVENT_COMPLETE,
        stop_reason=turn["stop_reason"],
        context_usage_pct=turn["context_usage_pct"],
        duration_ms=turn["duration_ms"],
        **turn["usage"],
    )


class ScriptedProvider(ModelProvider):
    supports_tools: bool = True

    def __init__(self) -> None:
        self._script_path = resolve_script_path()
        self._script = load_script(self._script_path)
        self._cursor = _PlaybackCursor(self._script, self._script_path)
        self._pending: dict[str, str] = {}
        self._decisions: list[tuple[str, str]] = []
        self._last_context_pct: float | None = None
        self._last_complete_call: dict[str, Any] = {}

    @property
    def script_path(self) -> Path:
        return self._script_path

    @property
    def turn_index(self) -> int:
        return self._cursor.position

    @property
    def decisions(self) -> list[tuple[str, str]]:
        return [*self._decisions]

    @property
    def pending_tool_calls(self) -> dict[str, str]:
        return dict(self._pending)

    @property
    def last_complete_call(self) -> dict[str, Any]:
        return dict(self._last_complete_call)

    async def start(self) -> None:
        resolve_script_path()

    async def shutdown(self) -> None:
        return None

    def _next_turn(self, prompt: str) -> dict[str, Any]:
        return self._cursor.claim(prompt)

    async def _emit(self, prompt: str) -> AsyncIterator[LLMEvent]:
        for event in _turn_events(self._next_turn(prompt)):
            if event.kind == EVENT_PERMISSION_REQUEST:
                self._pending[event.tool_call_id] = event.title
            elif event.kind == EVENT_COMPLETE:
                self._last_context_pct = event.context_usage_pct
            yield event

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for event in self._emit(message):
            yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        self._last_complete_call = dict(
            message_count=len(messages),
            tool_count=len(tools or []),
            model=model or "",
            reasoning_effort=reasoning_effort,
        )
        async for event in self._emit(_last_user_text(messages)):
            yield event

    async def approve_tool(self, request_id: str | int) -> None:
        self._resolve(request_id, "approved")

    async def reject_tool(self, request_id: str | int) -> None:
        self._resolve(request_id, "rejected")

    def _resolve(self, request_id: str | int, decision: str) -> None:
        identity = str(request_id)
        self._pending.pop(identity, None)
        self._decisions += [(identity, decision)]

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct
