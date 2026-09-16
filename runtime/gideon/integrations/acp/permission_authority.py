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
        measurement="AAP-3 sweep (K13, K15) + AAP-5 live re-drive 2026-08-18: one turn, 6 tool calls, 1 gated, 5 ungated (4x todo_list + 1 file read)",
        entries=(
            NotGateable(
                tool="todo_list",
                reason="kiro's native task-list tool emits a tool_call frame and a SEL 'invoked' row but never a session/request_permission, so no host gate — deny-list, task-mode, PreToolUse — can run for it.",
                observation="G27: seven of thirteen tool calls in one turn ('Creating task list: …', 'Completing #1/#2/#3') executed with no permission request, each labelled risk='destructive' by the host, in the same turns where the read, the write and the rm each raised a card.",
                title_patterns=("creating task list", "completing #", "task list"),
                state=ResidualState.ACCEPTED,
            ),
            NotGateable(
                tool="fs_read",
                reason="kiro self-approves its OWN file reads: the read raises no session/request_permission even though the write in the same turn does, so a read of a path the host would have questioned is never offered for a decision.",
                observation="AAP-5 live re-drive 2026-08-18 against real kiro-cli: in one turn 'Creating todo_probe.txt' raised a card while 'Reading todo_probe.txt:1-10' (kind='read') did not — 6 tool calls, 1 gated, 5 ungated. Effective risk resolves to SAFE, so this residue is labelled, never turn-aborting.",
                title_patterns=("reading ",),
                state=ResidualState.ACCEPTED,
            ),
        ),
    ),
    "claude-code": ProviderCoverage(
        provider="claude-code",
        measurement="AAP-5 Phase-1 SEL re-read (O96): 7 persisted rows with outcome='ungated', provider='claude-code', across 4 sessions and 2 tool titles. RETRACTS the earlier AAP-1 zero-residual claim, which runtime disproved: chat_runner records 'ungated_declared' whenever not_gateable_entry() matched, so a plain 'ungated' row is proof the registry held nothing for that title.",
        entries=(
            NotGateable(
                tool="Terminal",
                reason="claude-code runs its shell tool without emitting a session/request_permission for it. The host's deny-list, task-mode gate and blocking PreToolUse hooks all hang off that frame, so none of them ran. NOT accepted: a shell command that reaches the OS with no host decision point is not a limitation we are willing to go quiet about.",
                observation="O97: the execute-kind share of O96's 7 'ungated' rows carries title='Terminal' and reason='no session/request_permission for this tool_call'.",
            ),
            NotGateable(
                tool="Read File",
                reason="claude-code self-approves its own file reads — the same missing frame — so a read of a path the host would have questioned is never offered for a decision. NOT accepted: effective risk resolves to SAFE so it never aborts a turn, but nobody ever blessed it, and an unblessed hole stays loud.",
                observation="O98: 'Read File' is the second of the two titles in O96's 7-row 'ungated' set for provider='claude-code'.",
            ),
        ),
    ),
    "codex": ProviderCoverage(
        provider="codex",
        measurement="AAP-5 Phase-1 live drive (O99-O102): 4 plain 'ungated' rows on provider='codex' — a read, an in-workspace write, an out-of-workspace write and a network call. RETRACTS the earlier AAP-2 zero-residual claim.",
        entries=(
            NotGateable(
                tool="codex-native",
                reason="codex is its own first-line permission authority: under HOST_AUTHORITY_MODE='default' it escalates almost nothing, so its whole native tool surface — reads, writes, shell, network — can execute before the host has a decision point. NOT accepted: an out-of-workspace write that completed with no card is the exact shape §2.2 exists to make loud.",
                observation="O99-O101: four plain 'ungated' rows in one AAP-5 Phase-1 drive — a read, an in-workspace write, an out-of-workspace write ('printf … > /private/tmp/aap2b-outside-probe.txt', which EXECUTED) and a network call ('curl https://example.com'). Vacuity floor for the same drive (O102): codex DOES escalate on retry, and 'git push' was escalated and correctly deny-listed — so 'escalates almost nothing' measures codex, not a dead harness.",
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
