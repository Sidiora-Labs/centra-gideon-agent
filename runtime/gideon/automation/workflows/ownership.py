"""Workflow session identities and inherited memory restrictions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

OWNED_PREFIX = "workflow:"
OWNED_APP = "workflow"
SEL_SOURCE = "workflow"
_KEY_RE = re.compile(
    r"^workflow:(?P<run>[A-Za-z0-9_.-]+):(?P<node>[A-Za-z0-9_.\[\]-]+)$"
)


class MemoryMode(str, Enum):
    NORMAL = "normal"
    TEMPORARY = "temporary"
    INCOGNITO = "incognito"


WRITE_SUPPRESSED = frozenset({MemoryMode.TEMPORARY, MemoryMode.INCOGNITO})
READ_SUPPRESSED = frozenset({MemoryMode.TEMPORARY})
RUN_MODE_KEY = "memory_mode"
LEARNING_PROVIDERS = frozenset(
    {
        "knowledge-persist",
        "memory-write",
        "memory-persist",
        "lesson-write",
        "learning-capture",
        "feedback-capture",
    }
)
_KEY_RUN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)
_KEY_NODE_CHARS = _KEY_RUN_CHARS | frozenset("[]")
_MODE_LABELS = {mode.value: mode for mode in MemoryMode}
_MODE_LABELS.update({"": MemoryMode.NORMAL, "persistent": MemoryMode.NORMAL})


def _safe(part: str) -> str:
    source = str(part or "")
    return (
        "".join(char if char in _KEY_NODE_CHARS else "-" for char in source)
        or "unknown"
    )


@dataclass(frozen=True)
class _SessionKey:
    run: str
    node: str

    def encode(self) -> str:
        return OWNED_PREFIX + ":".join((self.run, self.node))

    @classmethod
    def read(cls, source: str) -> _SessionKey | None:
        raw = source or ""
        if not isinstance(raw, str):
            raise TypeError("expected string or bytes-like object")
        text = raw[:-1] if raw.endswith("\n") else raw
        if not text.startswith("workflow:"):
            return None
        run, separator, node = text[len("workflow:") :].partition(":")
        if not separator:
            return None
        for value, alphabet in ((run, _KEY_RUN_CHARS), (node, _KEY_NODE_CHARS)):
            if not value or any(char not in alphabet for char in value):
                return None
        return cls(run, node)


def owned_key(run_id: str, node_id: str) -> str:
    return _SessionKey(_safe(run_id), _safe(node_id)).encode()


def parse_owned(session_key: str) -> tuple[str, str] | None:
    key = _SessionKey.read(session_key)
    return None if key is None else (key.run, key.node)


def is_owned(session_key: str) -> bool:
    return parse_owned(session_key) is not None


def sel_source(session_key: str) -> str:
    if parse_owned(session_key) is None:
        from gideon.security.sel import _infer_source

        return _infer_source(session_key or "")
    return SEL_SOURCE


def parse_mode(raw: Any) -> MemoryMode:
    return _MODE_LABELS.get(str(raw or "").strip().lower(), MemoryMode.INCOGNITO)


def run_mode(run: Any) -> MemoryMode:
    extra = getattr(run, "extra", None) or {}
    return parse_mode(extra.get(RUN_MODE_KEY, ""))


def durable_metadata(mode: MemoryMode) -> dict[str, Any]:
    return {"memory_mode": mode.value}


def stamp_run_mode(extra: dict[str, Any], mode: MemoryMode) -> dict[str, Any]:
    stamped = dict(extra or {})
    stamped.update(durable_metadata(mode))
    return stamped


def inherit_mode(
    origin_key: str, *, origin_metadata: dict[str, Any] | None = None
) -> MemoryMode:
    stored = parse_mode((origin_metadata or {}).get("memory_mode"))
    if stored is MemoryMode.NORMAL:
        try:
            from gideon.engine import session_restrictions

            rules = (
                ("is_temporary", MemoryMode.TEMPORARY),
                ("is_incognito", MemoryMode.INCOGNITO),
            )
            for name, mode in rules:
                if getattr(session_restrictions, name)(origin_key):
                    return mode
        except Exception:
            return MemoryMode.NORMAL
    return stored


def _project(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(record, name) for name in fields}


@dataclass
class Ownership:
    key: str
    run_id: str
    node_id: str
    app: str = OWNED_APP
    source: str = SEL_SOURCE
    memory_mode: MemoryMode = MemoryMode.NORMAL

    @property
    def suppresses_writes(self) -> bool:
        return self.memory_mode in WRITE_SUPPRESSED

    @property
    def suppresses_reads(self) -> bool:
        return self.memory_mode in READ_SUPPRESSED

    def to_dict(self) -> dict[str, Any]:
        payload = _project(self, ("key", "run_id", "node_id", "app", "source"))
        payload["memory_mode"] = self.memory_mode.value
        payload.update(_project(self, ("suppresses_writes", "suppresses_reads")))
        return payload


def own_session(
    run_id: str, node_id: str, *, inherited_mode: MemoryMode = MemoryMode.NORMAL
) -> Ownership:
    return Ownership(
        owned_key(run_id, node_id),
        _safe(run_id),
        _safe(node_id),
        memory_mode=inherited_mode,
    )


def skips_node(node_config: dict[str, Any], mode: MemoryMode) -> tuple[bool, str]:
    if mode in WRITE_SUPPRESSED:
        config = node_config or {}
        provider = str(config.get("provider", "") or "").strip().lower()
        decisions = (
            (
                lambda: provider in LEARNING_PROVIDERS,
                lambda: f"{mode.value} run: skipping `{provider}` (memory writes are suppressed)",
            ),
            (
                lambda: config.get("persists_memory") is True,
                lambda: f"{mode.value} run: node declares persists_memory",
            ),
        )
        for applies, describe in decisions:
            if applies():
                return True, describe()
    return False, ""


@dataclass
class Announcement:
    origin_key: str
    text: str
    indexable: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _project(self, ("origin_key", "text", "indexable", "reason"))


def announcement(origin_key: str, text: str, mode: MemoryMode) -> Announcement:
    indexable = mode not in WRITE_SUPPRESSED
    return Announcement(
        origin_key,
        text,
        indexable,
        "" if indexable else f"origin session is {mode.value}",
    )


def restriction_calls(ownership: Ownership) -> list[str]:
    rules = (
        (MemoryMode.TEMPORARY, ("mark_temporary", "mark_incognito")),
        (MemoryMode.INCOGNITO, ("mark_incognito",)),
    )
    return next(
        (list(marks) for mode, marks in rules if ownership.memory_mode is mode), []
    )


def audit_fields(ownership: Ownership) -> dict[str, str]:
    return {
        wire: getattr(ownership, field)
        for wire, field in (
            ("source", "source"),
            ("session_key", "key"),
            ("run_id", "run_id"),
            ("node_id", "node_id"),
        )
    }


@dataclass
class OwnedSessions:
    run_id: str
    keys: list[str] = field(default_factory=list)

    def add(self, node_id: str) -> str:
        key = owned_key(self.run_id, node_id)
        self.keys.extend(() if key in self.keys else (key,))
        return key

    def cleanup_plan(self) -> list[str]:
        return list(self.keys)

    def to_dict(self) -> dict[str, Any]:
        return dict(run_id=self.run_id, keys=list(self.keys), count=len(self.keys))
