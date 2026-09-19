"""Ordered deterministic rejection rules and tri-state fallback checks."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GIVEUP_PATTERNS = (
    r"\bi (?:could ?n[o']?t|was unable to|failed to)\b",
    r"\b(?:unable|failed) to (?:complete|finish|fix|resolve|implement)\b",
    r"\bgiv(?:e|ing) up\b",
    r"\bnot (?:possible|feasible) (?:to|for me)\b",
    r"\bneeds? (?:human|manual) (?:intervention|help)\b",
    r"\bi'?ll leave (?:this|that) (?:to you|for now)\b",
)

_TOOL_ERROR_PATTERNS = (
    r"\bcommand not found\b",
    r"\bpermission denied\b",
    r"\bno such file or directory\b",
    r"\bmodulenotfounderror\b",
    r"\bconnection (?:refused|reset|timed out)\b",
    r"\btraceback \(most recent call last\)",
)

_STUB_PATTERNS = (
    r"\braise NotImplementedError\b",
    r"\bTODO:? implement\b",
    r"\bFIXME\b",
    r"\bpass\s*#\s*stub\b",
    r"\breturn None\s*#\s*(?:stub|placeholder)\b",
)

_GIVEUP_RE = re.compile("|".join(_GIVEUP_PATTERNS), re.IGNORECASE)
_TOOL_ERROR_RE = re.compile("|".join(_TOOL_ERROR_PATTERNS), re.IGNORECASE)
_STUB_RE = re.compile("|".join(_STUB_PATTERNS), re.IGNORECASE)

MIN_SUBSTANCE_CHARS = 20


FAILURE_CLASSES = (
    "empty_output",
    "worker_gave_up",
    "tool_error",
    "stubbed_output",
    "missing_artifact",
    "no_output",
)


@dataclass
class PreTierResult:
    """What the free rules concluded."""

    rejected: bool = False
    failure_class: str = ""
    reason: str = ""
    checks_run: list[str] = field(default_factory=list)
    fallback_result: bool | None = None

    @property
    def should_invoke_judge(self) -> bool:
        """Only spend a model call on what the rules could not settle."""
        return not self.rejected


def check_mechanical(
    text: str, *, min_chars: int = MIN_SUBSTANCE_CHARS
) -> PreTierResult:
    refused = not text or len(text.strip()) < min_chars
    return _rule_result(
        "mechanical",
        "empty_output" if refused else "",
        f"output under {min_chars} chars — nothing to judge" if refused else "",
    )


def check_failure_patterns(text: str) -> PreTierResult:
    rules = (
        (_GIVEUP_RE, "worker_gave_up", "worker admitted failure"),
        (_TOOL_ERROR_RE, "tool_error", "tool/infrastructure error"),
    )
    return _match_failure("failure_patterns", text, rules)


def check_stubs(text: str) -> PreTierResult:
    return _match_failure(
        "stubs", text, ((_STUB_RE, "stubbed_output", "stub marker present"),)
    )


def check_structural(
    referenced_paths: list[str], *, root: Path | None = None
) -> PreTierResult:
    def location(raw: str) -> Path:
        path = Path(raw)
        return root / path if root is not None and not path.is_absolute() else path

    missing = [raw for raw in referenced_paths if raw and not location(raw).exists()]
    return _rule_result(
        "structural",
        "missing_artifact" if missing else "",
        f"referenced path(s) do not exist: {', '.join(missing[:3])}" if missing else "",
    )


def check_existence(
    *, artifacts: int = 0, commits: int = 0, changed_files: int = 0
) -> PreTierResult:
    absent = all(count <= 0 for count in (artifacts, commits, changed_files))
    return _rule_result(
        "existence",
        "no_output" if absent else "",
        "no artifacts, commits, or changed files" if absent else "",
    )


def run_pretier(
    *,
    worker_output: str = "",
    referenced_paths: list[str] | None = None,
    root: Path | None = None,
    artifacts: int = 0,
    commits: int = 0,
    changed_files: int = 0,
    min_chars: int = MIN_SUBSTANCE_CHARS,
    check_existence_gate: bool = True,
) -> PreTierResult:
    def stages():
        yield check_mechanical(worker_output, min_chars=min_chars)
        yield check_failure_patterns(worker_output)
        yield check_stubs(worker_output)
        yield check_structural(referenced_paths or [], root=root)
        if check_existence_gate:
            yield check_existence(
                artifacts=artifacts, commits=commits, changed_files=changed_files
            )

    completed = []
    for result in stages():
        completed.extend(result.checks_run)
        if result.rejected:
            result.checks_run = completed
            return result
    return PreTierResult(checks_run=completed)


async def run_fallback_check(
    check: Any,
    *,
    command: str = "",
    artifact_path: str = "",
    diff_lines: int = 0,
    cwd: str | None = None,
) -> bool | None:
    from gideon.automation.workflows.judge_contract import FallbackCheck

    label = str(getattr(check, "value", check))
    kind = next((member for member in FallbackCheck if member.value == label), None)
    if kind is FallbackCheck.COMMAND_EXIT_CODE and command:
        from gideon.automation.loop.gates import run_verify_command

        return await run_verify_command(command, cwd, label="judge fallback")
    decisions = {
        FallbackCheck.ARTIFACT_EXISTS: lambda: (
            Path(artifact_path).exists() if artifact_path else None
        ),
        FallbackCheck.DIFF_NONEMPTY: lambda: diff_lines > 0,
    }
    evaluate = decisions.get(kind) if kind is not None else None
    return evaluate() if evaluate is not None else None


def _rule_result(check: str, failure: str = "", reason: str = "") -> PreTierResult:
    return PreTierResult(
        rejected=bool(failure), failure_class=failure, reason=reason, checks_run=[check]
    )


def _match_failure(check: str, text: str, rules) -> PreTierResult:
    for pattern, failure, description in rules:
        match = pattern.search(text or "")
        if match:
            return _rule_result(check, failure, f"{description}: {match.group(0)!r}")
    return _rule_result(check)
