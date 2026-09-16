"""Recognize system scheduler writes and offer managed automation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WRITE_PATTERNS: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    (
        "cron",
        re.compile(
            "(?:^|[;&|(\\n]|&&|\\|\\|)\\s*(?:sudo\\s+)?crontab\\s+(?:-[er]\\b|-\\s*$|-\\s*[<]|[^\\s-][^\\s]*)|[|]\\s*(?:sudo\\s+)?crontab\\s+-\\s*$"
        ),
        "crontab",
    ),
    (
        "launchd",
        re.compile(
            "\\blaunchctl\\s+(?:load|bootstrap|enable|submit)\\b|\\blaunchctl\\s+(?:unload|bootout)\\b|>\\s*[^\\s]*Library/LaunchAgents/"
        ),
        "launchd",
    ),
    (
        "systemd",
        re.compile(
            "\\bsystemctl\\b[^\\n]*\\b(?:enable|start|--now)\\b[^\\n]*\\.timer\\b|\\bsystemd-run\\b[^\\n]*--on-(?:calendar|active|boot)\\b|>\\s*[^\\s]*/systemd/user/[^\\s]*\\.timer\\b"
        ),
        "systemd timer",
    ),
)

_READ_PATTERNS: tuple["re.Pattern[str]", ...] = (
    re.compile("\\bcrontab\\s+-[lu]\\b"),
    re.compile("\\blaunchctl\\s+(?:list|print|dumpstate)\\b"),
    re.compile(
        "\\bsystemctl\\b[^\\n]*\\b(?:list-timers|list-units|status|show|cat)\\b"
    ),
)

HANDOFF_HINT = "Use Gideon's automation substrate instead of the system scheduler: it gives this job a run history, failure autopause, quiet hours, a capability fence and the kill switch, none of which a system cron has. Create it with the `automation_create` tool (or ask the user to add it on the Automations page). If you are MIGRATING existing system crons, read them with `crontab -l` — that is not intercepted — and create one automation per entry."


@dataclass(frozen=True)
class Handoff:
    scheduler: str
    pattern: str
    command: str

    @property
    def reason(self) -> str:
        prefix = f"This writes to the system {self.scheduler}, which puts the job outside Gideon "
        return prefix + (
            "entirely — no run history, no autopause, no quiet hours, no kill switch, and it "
            "survives uninstall."
        )

    @property
    def observation(self) -> str:
        return " ".join((self.reason, HANDOFF_HINT))

    def to_dict(self) -> dict[str, Any]:
        return dict(
            scheduler=self.scheduler,
            pattern=self.pattern,
            reason=self.reason,
            hint=HANDOFF_HINT,
        )


@dataclass(frozen=True)
class SchedulerCommand:
    text: str

    def proposal(self) -> Handoff | None:
        readonly = next(
            (rule for rule in _READ_PATTERNS if rule.search(self.text)), None
        )
        if readonly is not None:
            return None
        matches = (
            Handoff(label, key, self.text[:200])
            for key, pattern, label in _WRITE_PATTERNS
            if pattern.search(self.text)
        )
        return next(matches, None)


def detect(command: str) -> Handoff | None:
    if isinstance(command, str) and command:
        try:
            return SchedulerCommand(command.strip()).proposal()
        except Exception:
            pass
    return None


def needs_prompt(command: str) -> bool:
    proposal = detect(command)
    return proposal is not None
