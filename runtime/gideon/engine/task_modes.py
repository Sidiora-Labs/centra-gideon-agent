"""Shared invocation classification, risk selection and task-mode admission."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from gideon.security.command_effects import CommandEffects, command_effects

UNCHECKED = "unchecked"
MAY_DESTROY = frozenset({"destructive", UNCHECKED})

VALID_TASK_MODES: tuple[str, ...] = ("agent", "ask", "plan", "build")

_MUTATING_TOOL_KINDS = {"edit", "delete", "move"}

_READONLY_TOOL_KINDS = {"read", "fetch", "search", "think"}

_COMMAND_TOOL_KINDS = {"command", "execute"}

SHELL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "bash",
        "shell",
        "execute_bash",
        "run-script",
        "run_script",
        "terminal",
    }
)

SHELL_TITLE_PREFIXES: tuple[str, ...] = ("running: ",)

_DESTRUCTIVE_NAME_HINTS = ("delete", "remove", "destroy", "drop_", "purge", "forget")

_NON_DESTRUCTIVE_MUTATING_NAME_HINTS = (
    "write",
    "edit",
    "create",
    "save",
    "update",
    "move",
    "rename",
    "append",
    "set_",
    "put_",
    "install",
    "deploy",
    "run",
    "exec",
    "spawn",
    "subagent",
    "schedule",
    "notify",
    "post_",
    "send",
    "commit",
    "push",
    "generate",
)

_MUTATING_NAME_HINTS = _NON_DESTRUCTIVE_MUTATING_NAME_HINTS + _DESTRUCTIVE_NAME_HINTS

_NETWORK_NAME_HINTS = (
    "web_",
    "http",
    "fetch",
    "browse",
    "download",
    "upload",
    "crawl",
    "scrape",
    "url",
)

_BUILD_NAME_HINTS = (
    "artifact",
    "widget",
    "skill",
    "prompt",
    "document",
    "infographic",
    "image",
)

_READ_VERB_HINTS = (
    "list",
    "get",
    "search",
    "read",
    "status",
    "info",
    "find",
    "inspect",
    "show",
    "view",
)

READ_ONLY = "read_only"

MUTATING = "mutating"

UNCLASSIFIED = "unclassified"

_RISK_ORDER = {"safe": 0, "caution": 1, "destructive": 2}


def is_read_only_bash(cmd: str) -> bool:
    return command_effects(cmd).reads_only


def declared_level(declared: object) -> str:
    value = getattr(declared, "value", declared)
    text = str(value).strip().lower() if value else ""
    return text if text in _RISK_ORDER else ""


def _has_hint(name: str, hints: tuple[str, ...]) -> bool:
    words = {word.lower() for word in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", name)}
    return any(hint.strip("_") in words for hint in hints)


@dataclass(frozen=True)
class CallReading:
    risk: str
    effects: CommandEffects | None


def read_call(declared: object, title: str, tool_kind: str, tool_input: object) -> CallReading:
    command = shell_command(title, tool_kind, tool_input, declared=declared)
    if not command:
        return CallReading(declared_level(declared) or "caution", None)
    effects = command_effects(command)
    risk = ("safe" if effects.reads_only else "destructive" if effects.deletes
            else UNCHECKED if effects.unread else "caution")
    return CallReading(risk, effects)


def extract_bash_command(tool_input: object) -> str:
    if isinstance(tool_input, str):
        try:
            parsed = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return tool_input
        if not isinstance(parsed, dict):
            return tool_input
    elif isinstance(tool_input, dict):
        parsed = tool_input
    else:
        return ""
    command = parsed.get("command", "")
    return command if isinstance(command, str) else ""


@dataclass(frozen=True)
class _Invocation:
    name: str
    kind: str
    arguments: object

    @classmethod
    def read(cls, title: str, kind: str, arguments: object) -> "_Invocation":
        return cls(title or "", (kind or "").lower(), arguments)

    @property
    def runs_shell(self) -> bool:
        return (
            self.kind in _COMMAND_TOOL_KINDS
            or self.name.lower() in SHELL_TOOL_NAMES
            or self.name.lower().startswith(SHELL_TITLE_PREFIXES)
        )

    def command(self) -> str:
        return shell_command(self.name, self.kind, self.arguments)

    def classification(self, declared: object = None) -> str:
        if is_shell_invocation(self.name, self.kind, declared=declared):
            command = shell_command(self.name, self.kind, self.arguments, declared=declared)
            if not command:
                return UNCLASSIFIED
            return READ_ONLY if is_read_only_bash(command) else MUTATING
        if declared is not None:
            return READ_ONLY if declared_level(declared) == "safe" else MUTATING
        if self.kind in _MUTATING_TOOL_KINDS:
            return MUTATING
        # Tool names describe intent, not effects. Only the registry's explicit
        # read-like kind is a declaration; otherwise deny by default.
        declared_read = self.kind in _READONLY_TOOL_KINDS
        if _has_hint(self.name, (*_MUTATING_NAME_HINTS, *_NETWORK_NAME_HINTS)):
            return MUTATING
        # A command argument on a non-shell tool never grants read authority.
        if extract_bash_command(self.arguments):
            return MUTATING
        return READ_ONLY if declared_read else MUTATING

    def effective_risk(self, declared: object) -> str:
        return read_call(declared, self.name, self.kind, self.arguments).risk

    def produces_deliverable(self) -> bool:
        destructive = _has_hint(self.name, _DESTRUCTIVE_NAME_HINTS)
        return not destructive and any(
            fragment in self.name for fragment in _BUILD_NAME_HINTS
        )


def is_shell_invocation(title: str, tool_kind: str, *, declared: object = "") -> bool:
    if declared_level(declared):
        return (title or "").lower() == "bash"
    return _Invocation.read(title, tool_kind, None).runs_shell


def shell_command(title: str, tool_kind: str, tool_input: object, *, declared: object = "") -> str:
    if not is_shell_invocation(title, tool_kind, declared=declared):
        return ""
    command = extract_bash_command(tool_input)
    if command:
        return command
    if (title or "").lower().startswith(SHELL_TITLE_PREFIXES):
        return title[len("running: "):]
    return ""


def classify_invocation(title: str, tool_kind: str, tool_input: object, *, declared: object = None) -> str:
    return _Invocation.read(title, tool_kind, tool_input).classification(declared)


def _is_read_only_tool(title: str, tool_kind: str, tool_input: object, *, declared: object = "") -> bool:
    return classify_invocation(title, tool_kind, tool_input, declared=declared) == READ_ONLY


def resolve_effective_risk(declared: object, title: str, tool_kind: str, tool_input: object) -> str:
    return read_call(declared, title, tool_kind, tool_input).risk


def infer_risk_from_name(name: str) -> str:
    bare = (name or "").strip()
    if bare.lower().startswith("mcp/"):
        bare = bare.rsplit("/", 1)[-1]
    priorities = (
        (_DESTRUCTIVE_NAME_HINTS, "destructive"),
        (_MUTATING_NAME_HINTS, "caution"),
    )
    return next(
        (
            risk
            for fragments, risk in priorities
            if _has_hint(bare, fragments)
        ),
        "caution",
    )


_MODE_REFUSALS = {
    "ask": "Ask mode — only read-only tools run (switch to Agent to make changes)",
    "plan": "Plan mode — inspection only, nothing is executed (switch to Agent to run it)",
    "build": "Build mode — only read-only + artifact-producing tools run (switch to Agent for the rest)",
}


def task_mode_denies(
    task_mode: str, title: str, tool_kind: str, tool_input: object, *, declared: object = None
) -> str:
    if task_mode == "agent" or task_mode not in VALID_TASK_MODES:
        return ""
    call = _Invocation.read(title, tool_kind, tool_input)
    if call.classification(declared) == READ_ONLY:
        return ""
    if task_mode == "build" and declared_level(declared) != "destructive" and call.produces_deliverable():
        return ""
    return _MODE_REFUSALS[task_mode]


def tool_input_to_str(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else ""
