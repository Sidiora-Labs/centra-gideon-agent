"""ACP permission policy and measured provider coverage."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import reduce

HOST_AUTHORITY_MODE = "default"
AUTO_APPROVE_MODES: frozenset[str] = frozenset(
    {
        "acceptedits",
        "acceptall",
        "acceptalledits",
        "dontask",
        "neverask",
        "bypasspermissions",
        "bypass",
        "yolo",
        "auto",
        "autoapprove",
        "fullauto",
        "dangerfullaccess",
        "allowall",
    }
)
PASSTHROUGH_MODES: frozenset[str] = frozenset({"default", "plan"})


def canonical_mode(mode: str) -> str:
    return "".join(filter(str.isalnum, str(mode or "").lower()))


@dataclass(frozen=True)
class ModeDecision:
    mode: str
    requested: str
    downgraded: bool = False
    reason: str = ""


def sanitize_mode(requested: str | None, *, unattended: bool = False) -> ModeDecision:
    original = str(requested or "").strip()
    key = canonical_mode(original)
    allowed = key in PASSTHROUGH_MODES
    elevated = key in AUTO_APPROVE_MODES
    if allowed or (elevated and unattended):
        reason = (
            "unattended session — auto-approve mode allowed by §2.3" if elevated else ""
        )
        return ModeDecision(original, original, reason=reason)
    if not key:
        return ModeDecision(
            HOST_AUTHORITY_MODE,
            original,
            reason="no mode requested — host asserts the restrictive mode",
        )
    if elevated:
        reason = (
            f"{original!r} makes the CLI its own permission authority; the host "
            f"forwards {HOST_AUTHORITY_MODE!r} so every tool reaches the host gate"
        )
    else:
        reason = (
            f"unrecognized permission mode {original!r} — clamped to "
            f"{HOST_AUTHORITY_MODE!r} rather than assumed safe"
        )
    return ModeDecision(HOST_AUTHORITY_MODE, original, True, reason)


def command_probe(title: str, command: str) -> str:
    normalized = " ".join(str(command or "").split())
    needs_probe = normalized and normalized not in str(title or "")
    return "Running: " + normalized if needs_probe else ""


class ResidualState(str, Enum):
    ACCEPTED = "measured, accepted — a documented limitation; the host labels it and stays quiet"
    UNACCEPTED = "measured, NOT accepted — the host cannot gate it and nobody blessed it, so it stays loud"


@dataclass(frozen=True)
class NotGateable:
    tool: str
    reason: str
    observation: str
    title_patterns: tuple[str, ...] = ()
    state: ResidualState = ResidualState.UNACCEPTED

    @property
    def accepted(self) -> bool:
        return self.state is ResidualState.ACCEPTED

    def matches(self, title: str) -> bool:
        value = str(title or "").lower()
        needles = (self.tool.lower(), *self.title_patterns)
        return bool(value) and any(needle in value for needle in needles)


@dataclass(frozen=True)
class ProviderCoverage:
    provider: str
    measurement: str
    entries: tuple[NotGateable, ...] = field(default_factory=tuple)

    @property
    def gated_universally(self) -> bool:
        return len(self.entries) == 0

    @property
    def unaccepted_residual(self) -> tuple[NotGateable, ...]:
        return tuple(filter(lambda entry: not entry.accepted, self.entries))


NOT_GATEABLE: dict[str, ProviderCoverage] = {
    "kiro-cli": ProviderCoverage(
        provider="kiro-cli",
        measurement="Recorded kiro-cli coverage includes a 2026-08-18 turn with six tool calls: one permission request and five calls without a permission request (four task-list calls and one file read).",
        entries=(
            NotGateable(
                tool="todo_list",
                reason="The task-list tool emits a tool-call event without a session/request_permission event. Host controls that depend on a permission request cannot gate this operation.",
                observation="In a recorded thirteen-call turn, seven task-list calls executed without permission requests and were classified as destructive by the host. File-read, file-write and deletion operations in those turns did request permission.",
                title_patterns=("creating task list", "completing #", "task list"),
                state=ResidualState.ACCEPTED,
            ),
            NotGateable(
                tool="fs_read",
                reason="Some file reads execute without a session/request_permission event, even when a file write in the same turn requests permission. The host cannot present a decision for those reads.",
                observation="A recorded 2026-08-18 turn requested permission for a file write but not for a file read. This accepted read limitation is labelled without aborting the turn.",
                title_patterns=("reading ",),
                state=ResidualState.ACCEPTED,
            ),
        ),
    ),
    "claude-code": ProviderCoverage(
        provider="claude-code",
        measurement="Recorded claude-code coverage contains seven persisted ungated events across four sessions and two tool titles. These events do not support a claim of universal host permission coverage.",
        entries=(
            NotGateable(
                tool="Terminal",
                reason="Some shell calls execute without a session/request_permission event. Host deny-list, task-mode and blocking pre-tool controls that depend on that event cannot gate these calls. This remains an unaccepted limitation.",
                observation="Recorded execute-kind ungated events include the Terminal title and report that no permission request was received for the tool call.",
            ),
            NotGateable(
                tool="Read File",
                reason="Some file reads execute without a session/request_permission event. The host cannot present a decision for those reads. This remains an unaccepted limitation; effective SAFE risk does not abort the turn.",
                observation="Read File is one of the two tool titles in the seven recorded ungated claude-code events.",
            ),
        ),
    ),
    "codex": ProviderCoverage(
        provider="codex",
        measurement="Recorded codex coverage includes four ungated events: a file read, a workspace write, a write outside the workspace and a network call. These events do not support a claim of universal host permission coverage.",
        entries=(
            NotGateable(
                tool="codex-native",
                reason="In default permission mode, native file, shell and network operations can execute before the host receives a permission request. A completed write outside the workspace without a host decision remains an unaccepted limitation.",
                observation="Recorded operations include a read, a workspace write, a completed write outside the workspace and a network call without host permission requests. Other calls in the same recording did request permission, including a push operation that the deny-list refused.",
                title_patterns=(),
            ),
        ),
    ),
}

_PROVIDER_ALIASES: dict[str, str] = {
    "kiro": "kiro-cli",
    "kiro-cli": "kiro-cli",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude-code-agent": "claude-code",
    "claude-agent": "claude-code",
    "codex": "codex",
    "codex-cli": "codex",
    "codex-agent": "codex",
}


def normalize_provider(provider: str) -> str:
    name = str(provider or "").strip().lower().removeprefix("acp:")
    name = reduce(
        lambda value, suffix: value.removesuffix(suffix), ("-acp", "_acp", ".acp"), name
    )
    name = name.strip("-_ ")
    return _PROVIDER_ALIASES.get(name, name)


def not_gateable_entry(provider: str, title: str) -> NotGateable | None:
    coverage = coverage_for(provider)
    candidates = coverage.entries if coverage is not None else ()
    return next((entry for entry in candidates if entry.matches(title)), None)


def coverage_for(provider: str) -> ProviderCoverage | None:
    return NOT_GATEABLE.get(normalize_provider(provider))
