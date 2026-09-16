from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CLOCK_CUES = (
    "every",
    "each",
    "daily",
    "weekly",
    "monthly",
    "hourly",
    "at ",
    "am",
    "pm",
    "weekday",
    "weekend",
    "midnight",
    "noon",
    "morning",
    "evening",
    "night",
    "minutes",
    "hours",
)

_FILE_CUES = ("file", "folder", "directory", "dir ", "path", "saved", "download")

_CHANGE_CUES = (
    "changes",
    "change",
    "changed",
    "modified",
    "edited",
    "updated",
    "appears",
    "added",
    "new file",
    "gets a",
)

_KIND_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "run_completed",
        (
            "run finishes",
            "run completes",
            "workflow finishes",
            "after the run",
            "run is done",
            "workflow completes",
        ),
    ),
    (
        "web_watch",
        (
            "web page",
            "webpage",
            "website",
            "url ",
            "http://",
            "https://",
            "page changes",
        ),
    ),
    ("idle", ("idle", "nothing happens", "no activity", "away from")),
    ("webhook", ("webhook", "incoming request", "posts to")),
    (
        "event",
        (
            "when i ",
            "whenever i ",
            "session",
            "memory",
            "subagent",
            "approval",
            "compact",
            "hook",
        ),
    ),
)

_PATH_RE = re.compile("(?:~|\\.{1,2})?/[^\\s,;'\\\"]+|~[^\\s,;'\\\"]*")

_URL_RE = re.compile("https?://[^\\s,;'\\\"]+")

_DIR_SUFFIX = "/**"


@dataclass
class Route:
    kind: str = ""
    spec: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    cadence: str = ""
    because: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.kind) and not self.error

    def to_dict(self) -> dict[str, Any]:
        fields = {
            key: getattr(self, key) for key in ("kind", "error", "cadence", "because")
        }
        return dict(fields, spec=dict(self.spec), ok=self.ok)


def _paths_in(text: str) -> list[str]:
    globs = {}
    for match in _PATH_RE.finditer(text):
        value = match.group().rstrip(".,;:!?)")
        if value in ("", "/", "~"):
            continue
        exact = any(character in value for character in "*?[") or re.search(
            r"\.[A-Za-z0-9]{1,6}$", value
        )
        globs[value if exact else value.rstrip("/") + _DIR_SUFFIX] = None
    return list(globs)


def _url_in(text: str) -> str:
    found = next(_URL_RE.finditer(text), None)
    return "" if found is None else found.group().rstrip(".,;:!?)")


def _has(text: str, cues: tuple[str, ...]) -> bool:
    return any(map(text.__contains__, cues))


@dataclass(frozen=True)
class WhenRequest:
    text: str

    @property
    def lower(self) -> str:
        return self.text.lower()

    def url(self) -> Route | None:
        if _has(self.lower, ("http://", "https://")):
            return Route(
                "web_watch",
                {"url": _url_in(self.text)},
                because="routed to the web_watch kind because you gave a URL",
            )
        return None

    def file(self) -> Route | None:
        paths = _paths_in(self.text)
        cue = _has(self.lower, _FILE_CUES)
        if paths and (cue or _has(self.lower, _CHANGE_CUES)):
            spec = {"paths": paths}
            if _has(self.lower, ("content", "text changes", "edited")):
                spec["dedup"] = "content"
            return Route(
                "file", spec, because="read as a file watch because you named a path"
            )
        if cue and not paths:
            return Route(
                error="Which path should I watch? Name a folder or glob (e.g. ~/notes or ~/notes/**/*.md) — I will not guess a root."
            )
        return None

    def named(self) -> Route | None:
        match = next(
            (kind for kind, cues in _KIND_CUES if _has(self.lower, cues)), None
        )
        if match is None:
            return None
        return Route(
            match,
            because=f"routed to the {match} kind from the wording of your request",
        )

    def clock(self) -> Route | None:
        if _has(self.lower, _CLOCK_CUES) or re.search(
            r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", self.lower
        ):
            return Route(
                "clock",
                cadence=self.text,
                because="read as a schedule; converting the cadence to a cron expression",
            )
        return None

    def resolve(self) -> Route:
        if not self.text:
            return Route(error="Say when the automation should run.")
        for rule in (self.url, self.file, self.named, self.clock):
            candidate = rule()
            if candidate is not None:
                return candidate
        return Route(
            error=f"I could not tell what should trigger this from {self.text!r}. Give a cadence ('every weekday at 9') or an event ('when a file in ~/notes changes')."
        )


def route(when: str) -> Route:
    return WhenRequest((when or "").strip()).resolve()
