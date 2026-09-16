"""The typed memory record — one shape the whole memory subsystem speaks.

Today memory is several ad-hoc shapes: semantic key/value rows, episodic rows,
lessons (semantic rows with a ``lesson.*`` key), preference facets, and the
markdown prefs/projects/history files. This module introduces **one**
``MemoryRecord`` with a ``kind`` discriminator so the provider stores rows and
the service reasons over them uniformly (memory-architecture.md §3.1).

M0 scope: this is a typed view + (de)serialization helpers ONLY. The SQLite
schema does not change here — ``MemoryRecord`` maps onto the existing
``semantic_memory`` and ``episodic_memories`` rows. The new durability/reach
axes (tier/scope/category/…) are carried on the dataclass with safe defaults so
M5+ can populate them once the backing columns exist; until then they round-trip
through defaults and never alter behavior.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


def _now_iso() -> str:
    stamp = datetime.now(timezone.utc)
    return stamp.isoformat()


class MemoryKind(str, Enum):
    """What KIND of thing a record is (memory-architecture.md §3.1/§3.7).

    The first four are the user/world-facing classes that exist today; the last
    three (``procedural``/``commitment``/``self_persona``) are the M5+ classes
    that turn memory from "facts about the user" into "memory that acts".
    """

    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    LESSON = "lesson"
    PREFERENCE = "preference"
    NOTE = "note"
    PROCEDURAL = "procedural"
    COMMITMENT = "commitment"
    SELF_PERSONA = "self_persona"
    APPROVAL = "approval"
    SLOT = "slot"


_DECAY_PROFILES: dict["MemoryKind", str] = {
    MemoryKind.SEMANTIC: "semantic",
    MemoryKind.EPISODIC: "episodic",
    MemoryKind.LESSON: "lesson",
    MemoryKind.PREFERENCE: "preference",
    MemoryKind.NOTE: "note",
    MemoryKind.PROCEDURAL: "procedural",
    MemoryKind.COMMITMENT: "commitment",
    MemoryKind.SELF_PERSONA: "self_persona",
    MemoryKind.APPROVAL: "approval",
    MemoryKind.SLOT: "slot",
}


def decay_profile(kind: "MemoryKind") -> str:
    """The decay profile for a memory kind. Raises for an unmapped kind.

    Raising is the whole contract: a memory class added without a decay decision has
    no defensible rate, and inventing one for it would make the omission invisible.
    """
    try:
        discriminator = MemoryKind(kind)
        profile = _DECAY_PROFILES[discriminator]
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"no decay profile for memory kind {kind!r} — add it to "
            "memory_record._DECAY_PROFILES and learning.decay.KIND_MULTIPLIERS"
        ) from exc
    return profile


class MemoryTier(str, Enum):
    """DURABILITY axis — deepens via SEALING (§3.5). Independent of SCOPE."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEGMENT = "segment"
    SEMANTIC = "semantic"


class MemoryScope(str, Enum):
    """REACH axis — widens via heat-gated PROMOTION (§3.5). Independent of TIER."""

    SESSION = "session"
    WORKSPACE = "workspace"
    AGENT = "agent"
    GLOBAL = "global"


_DEFAULT_TIER: dict[str, MemoryTier] = {
    MemoryKind.SEMANTIC: MemoryTier.SEMANTIC,
    MemoryKind.LESSON: MemoryTier.SEMANTIC,
    MemoryKind.PREFERENCE: MemoryTier.SEMANTIC,
    MemoryKind.NOTE: MemoryTier.SEMANTIC,
    MemoryKind.EPISODIC: MemoryTier.EPISODIC,
    MemoryKind.PROCEDURAL: MemoryTier.SEMANTIC,
    MemoryKind.COMMITMENT: MemoryTier.EPISODIC,
    MemoryKind.SELF_PERSONA: MemoryTier.SEMANTIC,
    MemoryKind.APPROVAL: MemoryTier.SEMANTIC,
    MemoryKind.SLOT: MemoryTier.SEMANTIC,
}


def _pack_portable(embedding: list[float]) -> bytes:
    magnitude = math.sqrt(sum(value * value for value in embedding))
    values = [value / magnitude for value in embedding] if magnitude > 0 else embedding
    encoding = struct.Struct(f"{len(values)}f")
    return encoding.pack(*values)


def _unpack_portable(blob: bytes) -> list[float]:
    encoding = struct.Struct(f"{len(blob) // 4}f")
    return list(encoding.unpack(blob))


def embedding_to_blob(embedding: list[float] | None) -> bytes | None:
    if embedding is None:
        return None
    if not _HAS_NUMPY:
        return _pack_portable(embedding)
    vector = np.array(embedding, dtype=np.float32)
    magnitude = float(np.linalg.norm(vector))
    normalized = vector / magnitude if magnitude > 0 else vector
    return normalized.tobytes()


def blob_to_embedding(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    if not _HAS_NUMPY:
        return _unpack_portable(blob)
    values = np.frombuffer(blob, dtype=np.float32)
    return values.tolist()


@dataclass
class MemoryRecord:
    """One row the provider stores and the service reasons over.

    Subsumes the legacy semantic_memory + episodic_memories shapes. Fields with
    NEW-axis semantics (tier/scope/category/visit_count/…) carry safe defaults
    until the M5+ migration adds their backing columns; they round-trip through
    ``extra`` so a forward-written record never loses data on an older store.
    """

    id: str
    kind: MemoryKind
    text: str = ""
    value: Any = None
    embedding: list[float] | None = None
    importance: float = 0.5
    confidence: float = 0.5
    source: str = ""
    recall_count: int = 0
    visit_count: int = 0
    last_accessed_at: str | None = None
    tier: MemoryTier | None = None
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_ref: str | None = None
    category: str | None = None
    superseded_by: str | None = None
    invalidated_at: str | None = None
    conversation_id: str = ""
    tags: list[str] = field(default_factory=list)
    is_deleted: bool = False
    created_at: str = ""
    updated_at: str = ""
    safe_to_act: dict | None = None
    due_window: str | None = None
    channel: str | None = None
    dismissed_at: str | None = None
    source_ref: dict | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, enum in (
            ("kind", MemoryKind),
            ("tier", MemoryTier),
            ("scope", MemoryScope),
        ):
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, enum(value))
        self.tier = (
            _DEFAULT_TIER.get(self.kind, MemoryTier.SEMANTIC)
            if self.tier is None
            else self.tier
        )
        self.created_at = self.created_at or _now_iso()
        self.updated_at = self.updated_at or self.created_at
        if not self.text and self.value is not None:
            self.text = _value_text(self.value)

    @classmethod
    def from_semantic_row(cls, row: Any) -> "MemoryRecord":
        return cls(**_RecordRow(row).semantic())

    @classmethod
    def from_episodic_row(cls, row: Any) -> "MemoryRecord":
        return cls(**_RecordRow(row).episodic())

    def embedding_blob(self) -> bytes | None:
        vector = self.embedding
        return embedding_to_blob(vector)

    def idle_days(self, *, now: datetime | None = None) -> float | None:
        stamp = next(
            (
                value
                for value in (self.last_accessed_at, self.updated_at, self.created_at)
                if value
            ),
            None,
        )
        if stamp is None:
            return None
        try:
            touched = datetime.fromisoformat(stamp)
        except (ValueError, TypeError):
            return None
        touched = (
            touched.replace(tzinfo=timezone.utc) if touched.tzinfo is None else touched
        )
        elapsed = ((now or datetime.now(tz=timezone.utc)) - touched).total_seconds()
        return max(0.0, elapsed / 86400.0)

    def heat(self, *, now: datetime | None = None) -> float:
        from gideon.cognition.learning.decay import strength

        visits = float(self.recall_count + self.visit_count)
        use = math.log1p(visits) / math.log(10)
        idle = self.idle_days(now=now)
        recency = 0.0
        if idle is not None:
            recency = strength(
                kind=decay_profile(self.kind),
                active_days_since_use=idle,
                importance=self.importance,
            )
        return 0.7 * use + 0.5 * recency

    def to_public_dict(self) -> dict:
        fields = (
            "id",
            "kind",
            "text",
            "value",
            "importance",
            "confidence",
            "source",
            "recall_count",
            "tier",
            "scope",
            "category",
            "superseded_by",
            "conversation_id",
            "tags",
            "is_deleted",
            "created_at",
            "updated_at",
        )
        payload = {name: getattr(self, name) for name in fields}
        payload.update(
            kind=self.kind.value,
            tier=self.tier.value if self.tier else None,
            scope=self.scope.value,
        )
        return payload


def _kind_from_key(key: str) -> MemoryKind:
    prefixes = (
        ("lesson.", MemoryKind.LESSON),
        ("user.procedural.", MemoryKind.PROCEDURAL),
        ("user.persona.", MemoryKind.SELF_PERSONA),
        ("user.commitment.", MemoryKind.COMMITMENT),
        ("user.approval.", MemoryKind.APPROVAL),
        ("slot.", MemoryKind.SLOT),
    )
    return next(
        (kind for prefix, kind in prefixes if key.startswith(prefix)),
        MemoryKind.SEMANTIC,
    )


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "keys"):
        try:
            available = row.keys()
            if key in available:
                return row[key]
        except (IndexError, KeyError):
            pass
        return default
    return row.get(key, default) if isinstance(row, dict) else default


def _value_text(value):
    return value if isinstance(value, str) else json.dumps(value)


class _RecordRow:
    def __init__(self, row):
        self.row = row

    def read(self, name, default=None):
        return _row_get(self.row, name, default)

    def defaults(self, fields):
        return {
            name: self.read(name, fallback) or fallback for name, fallback in fields
        }

    def axes(self):
        state = self.defaults(
            (("visit_count", 0), ("tier", None), ("scope", MemoryScope.GLOBAL))
        )
        state.update({name: self.read(name) for name in ("scope_ref", "category")})
        return state

    def lifecycle(self):
        return dict(
            is_deleted=bool(self.read("is_deleted", 0)),
            created_at=self.read("created_at", "") or "",
        )

    def semantic(self):
        required = self.row.__getitem__ if hasattr(self.row, "keys") else self.row.get
        key, encoded = required("key"), required("value_json")
        try:
            value = json.loads(encoded) if encoded is not None else None
        except (json.JSONDecodeError, TypeError):
            value = encoded
        result = dict(
            id=key, kind=_kind_from_key(str(key)), text=_value_text(value), value=value
        )
        result["embedding"] = blob_to_embedding(self.read("embedding"))
        result.update(
            self.defaults((("confidence", 0.5), ("source", ""), ("recall_count", 0)))
        )
        result.update(self.axes())
        result.update(
            {name: self.read(name) for name in ("superseded_by", "invalidated_at")}
        )
        result.update(self.lifecycle())
        result["updated_at"] = self.read("updated_at", "") or ""
        return result

    def episodic(self):
        encoded = self.read("tags", "[]")
        try:
            tags = json.loads(encoded) if isinstance(encoded, str) else (encoded or [])
        except (json.JSONDecodeError, TypeError):
            tags = []
        result = dict(
            id=self.read("id"),
            kind=MemoryKind.EPISODIC,
            text=self.read("text", "") or "",
        )
        result["embedding"] = blob_to_embedding(self.read("embedding"))
        result.update(self.defaults((("importance", 0.5), ("conversation_id", ""))))
        result["tags"] = tags
        result.update(self.axes())
        result["last_accessed_at"] = self.read("last_accessed_at")
        result.update(self.lifecycle())
        result["updated_at"] = (
            self.read("last_accessed_at") or self.read("created_at", "") or ""
        )
        return result


@dataclass(frozen=True)
class MemoryCapabilities:
    """What a memory provider can do, so the service degrades per-capability
    instead of all-or-nothing (memory-architecture.md §3.2/§3.4).

    Mirrors the Tool result-contract + Knowledge "degrade to FTS" discipline.
    """

    vector: bool = False
    transactional_batch: bool = False
    event_log: bool = False
    full_text_search: bool = True
    entity_graph: bool = False

    def to_dict(self) -> dict:
        fields = (
            "vector",
            "transactional_batch",
            "event_log",
            "full_text_search",
            "entity_graph",
        )
        return {name: getattr(self, name) for name in fields}
