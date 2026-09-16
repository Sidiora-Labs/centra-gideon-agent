"""Shared invocation classification, risk selection and task-mode admission."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_READ_ONLY_BASH_PREFIXES: tuple[str, ...] = (
    "ls",
    "cat",
    "head",
    "tail",
    "find",
    "grep",
    "egrep",
    "fgrep",
    "wc",
    "which",
    "file",
    "stat",
    "du",
    "df",
    "tree",
    "diff",
    "pwd",
    "echo",
    "date",
    "whoami",
    "hostname",
    "uname",
    "readlink",
    "realpath",
    "basename",
    "dirname",
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git tag",
    "git remote",
    "git rev-parse",
    "git describe",
    "git ls-files",
    "git ls-tree",
    "git cat-file",
    "git blame",
    "python --version",
    "python3 --version",
    "node --version",
    "java -version",
    "javac -version",
)

_READ_ONLY_PIPE_RE = re.compile(
    r"^\s*(grep|egrep|fgrep|head|tail|wc|sort|uniq|cut|less|more|cat)\b"
)

_UNSAFE_SHELL_RE = re.compile(r">|`|\$\(|<\(|(?<!&)&(?!&)")

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


@dataclass(frozen=True)
class _CommandPipeline:
    commands: tuple[str, ...]

    def allows_read(self) -> bool:
        if not self.commands:
            return False
        head, *filters = self.commands
        executable = head.lower()
        known = executable.endswith(("--help", "--version")) or any(
            executable == prefix or executable.startswith(prefix + " ")
            for prefix in _READ_ONLY_BASH_PREFIXES
        )
        return known and all(
            _READ_ONLY_PIPE_RE.match(command) is not None for command in filters
        )


def is_read_only_bash(cmd: str) -> bool:
    source = cmd.strip()
    if not source or _UNSAFE_SHELL_RE.search(cmd):
        return False
    chains = re.split(r"\s*(?:&&|\|\||;|\n)\s*", source)
    pipelines = (
        _CommandPipeline(
            tuple(piece.strip() for piece in chain.split("|") if piece.strip())
        )
        for chain in chains
        if chain.strip()
    )
    return all(pipeline.allows_read() for pipeline in pipelines)


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
        return cls((title or "").lower(), (kind or "").lower(), arguments)

    @property
    def runs_shell(self) -> bool:
        return (
            self.kind in _COMMAND_TOOL_KINDS
            or self.name in SHELL_TOOL_NAMES
            or self.name.startswith(SHELL_TITLE_PREFIXES)
        )

    def command(self) -> str:
        return shell_command(self.name, self.kind, self.arguments)

    def classification(self) -> str:
        if self.runs_shell:
            command = self.command()
            if not command:
                return UNCLASSIFIED
            return READ_ONLY if is_read_only_bash(command) else MUTATING
        if self.kind in _MUTATING_TOOL_KINDS:
            return MUTATING
        declared_read = self.kind in _READONLY_TOOL_KINDS
        if not declared_read and any(
            fragment in self.name for fragment in _MUTATING_NAME_HINTS
        ):
            return MUTATING
        # A command argument on a non-shell tool must never grant read authority.
        return MUTATING if extract_bash_command(self.arguments) else READ_ONLY

    def effective_risk(self, declared: object) -> str:
        raw = getattr(declared, "value", declared)
        risk = str(raw).lower() if raw else ""
        classification = self.classification()
        if self.command():
            return "safe" if classification == READ_ONLY else (risk or "destructive")
        if risk in _RISK_ORDER:
            return risk
        if classification == UNCLASSIFIED:
            return "caution"
        if self.kind in _READONLY_TOOL_KINDS:
            return "safe"
        inferred = infer_risk_from_name(self.name)
        return "caution" if inferred == "safe" else inferred

    def produces_deliverable(self) -> bool:
        destructive = any(fragment in self.name for fragment in _DESTRUCTIVE_NAME_HINTS)
        return not destructive and any(
            fragment in self.name for fragment in _BUILD_NAME_HINTS
        )


def is_shell_invocation(title: str, tool_kind: str) -> bool:
    return _Invocation.read(title, tool_kind, None).runs_shell


def shell_command(title: str, tool_kind: str, tool_input: object) -> str:
    if tool_input and is_shell_invocation(title, tool_kind):
        return extract_bash_command(tool_input)
    return ""


def classify_invocation(title: str, tool_kind: str, tool_input: object) -> str:
    return _Invocation.read(title, tool_kind, tool_input).classification()


def _is_read_only_tool(title: str, tool_kind: str, tool_input: object) -> bool:
    return classify_invocation(title, tool_kind, tool_input) == READ_ONLY


def resolve_effective_risk(
    declared: object, title: str, tool_kind: str, tool_input: object
) -> str:
    return _Invocation.read(title, tool_kind, tool_input).effective_risk(declared)


def infer_risk_from_name(name: str) -> str:
    bare = (name or "").lower()
    if bare.startswith("mcp/"):
        bare = bare.rsplit("/", 1)[-1]
    priorities = (
        (_DESTRUCTIVE_NAME_HINTS, "destructive"),
        (_READ_VERB_HINTS, "safe"),
        (_MUTATING_NAME_HINTS, "caution"),
    )
    return next(
        (
            risk
            for fragments, risk in priorities
            if any(fragment in bare for fragment in fragments)
        ),
        "safe",
    )


_MODE_REFUSALS = {
    "ask": "Ask mode — only read-only tools run (switch to Agent to make changes)",
    "plan": "Plan mode — inspection only, nothing is executed (switch to Agent to run it)",
    "build": "Build mode — only read-only + artifact-producing tools run (switch to Agent for the rest)",
}


def task_mode_denies(
    task_mode: str, title: str, tool_kind: str, tool_input: object
) -> str:
    if task_mode == "agent" or task_mode not in VALID_TASK_MODES:
        return ""
    call = _Invocation.read(title, tool_kind, tool_input)
    if call.classification() == READ_ONLY:
        return ""
    if task_mode == "build" and call.produces_deliverable():
        return ""
    return _MODE_REFUSALS[task_mode]
