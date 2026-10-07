from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, cast

from .foundation import Cursor, Digest, Error, Id, Scope
from .identity import IdentityRelation, RelationKind

MAX_PARTS = 4_096
MAX_RELATIONS = 64


class HistoryError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.error = Error(code=code, message=message, retryable=retryable)
        super().__init__(message)

    @property
    def code(self) -> str:
        return self.error.code


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class PartKind(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    IMAGE = "image"
    FILE = "file"
    CONTEXT_MARKER = "context_marker"


def _structured_digest(fields: Iterable[bytes]) -> Digest:
    material = bytearray()
    for field in fields:
        material.extend(len(field).to_bytes(8, "big"))
        material.extend(field)
    return Digest.sha256(bytes(material))


@dataclass(frozen=True, slots=True)
class ContextPart:
    part_id: Id
    kind: PartKind
    content_digest: Digest
    text: str | None = None
    call_id: Id | None = None
    tool_name: str | None = None
    arguments_json: str | None = None
    result_json: str | None = None
    media_type: str | None = None
    source_uri: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "part_id", Id(self.part_id))
        object.__setattr__(self, "content_digest", Digest(self.content_digest))
        if self.call_id is not None:
            object.__setattr__(self, "call_id", Id(self.call_id))
        self.validate()

    def validate(self) -> None:
        if self.kind in (PartKind.TEXT, PartKind.REASONING, PartKind.CONTEXT_MARKER):
            if not isinstance(self.text, str) or len(self.text.encode()) > 1_048_576:
                raise HistoryError("INVALID_PART", "text part is absent or oversized")
            actual = Digest.sha256(self.text.encode())
        elif self.kind is PartKind.TOOL_CALL:
            if self.call_id is None or not self.tool_name or len(self.tool_name) > 256:
                raise HistoryError("INVALID_PART", "tool call identity is incomplete")
            if not isinstance(self.arguments_json, str):
                raise HistoryError("INVALID_PART", "tool arguments are absent")
            _validated_json(self.arguments_json)
            actual = _structured_digest(
                (
                    str(self.call_id).encode(),
                    self.tool_name.encode(),
                    self.arguments_json.encode(),
                )
            )
        elif self.kind is PartKind.TOOL_RESULT:
            if self.call_id is None or not isinstance(self.result_json, str):
                raise HistoryError("INVALID_PART", "tool result identity is incomplete")
            _validated_json(self.result_json)
            actual = _structured_digest(
                (str(self.call_id).encode(), self.result_json.encode())
            )
        else:
            if (
                not self.media_type
                or len(self.media_type) > 128
                or not isinstance(self.source_uri, str)
                or len(self.source_uri) > 4_096
            ):
                raise HistoryError("INVALID_PART", "media part is incomplete")
            for value in (self.width, self.height):
                if value is not None and (
                    isinstance(value, bool) or not 1 <= value <= 100_000
                ):
                    raise HistoryError("INVALID_PART", "media dimensions are invalid")
            actual = _structured_digest(
                (
                    self.media_type.encode(),
                    self.source_uri.encode(),
                    (self.width or 0).to_bytes(8, "big"),
                    (self.height or 0).to_bytes(8, "big"),
                )
            )
        if actual != self.content_digest:
            raise HistoryError(
                "PART_DIGEST_MISMATCH", "part digest does not match content"
            )

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "part_id": str(self.part_id),
            "kind": self.kind.value,
            "content_digest": str(self.content_digest),
        }
        for field in (
            "text",
            "call_id",
            "tool_name",
            "arguments_json",
            "result_json",
            "media_type",
            "source_uri",
            "width",
            "height",
            "metadata",
        ):
            value = getattr(self, field)
            if value is not None:
                result[field] = str(value) if isinstance(value, Id) else value
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ContextPart:
        return cls(
            part_id=Id(value["part_id"]),
            kind=PartKind(value["kind"]),
            content_digest=Digest(value["content_digest"]),
            text=value.get("text"),
            call_id=Id(value["call_id"]) if value.get("call_id") is not None else None,
            tool_name=value.get("tool_name"),
            arguments_json=value.get("arguments_json"),
            result_json=value.get("result_json"),
            media_type=value.get("media_type"),
            source_uri=value.get("source_uri"),
            width=value.get("width"),
            height=value.get("height"),
            metadata=value.get("metadata"),
        )


def _validated_json(value: str) -> None:
    if len(value.encode()) > 4_194_304:
        raise HistoryError("INVALID_PART", "JSON part is oversized")
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        raise HistoryError("INVALID_PART", "JSON part is malformed") from exc


@dataclass(frozen=True, slots=True)
class PendingContextItem:
    item_id: Id
    source_event_id: Id
    source_digest: Digest
    scope: Scope
    session_id: Id
    role: Role
    parts: tuple[ContextPart, ...]
    relations: tuple[IdentityRelation, ...]
    created_at: str
    recoverable: bool
    tombstone: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", Id(self.item_id))
        object.__setattr__(self, "source_event_id", Id(self.source_event_id))
        object.__setattr__(self, "source_digest", Digest(self.source_digest))
        object.__setattr__(self, "session_id", Id(self.session_id))
        object.__setattr__(self, "role", Role(self.role))
        object.__setattr__(self, "parts", tuple(self.parts))
        object.__setattr__(self, "relations", tuple(self.relations))
        if (
            not 1 <= len(self.parts) <= MAX_PARTS
            or len(self.relations) > MAX_RELATIONS
            or not self.created_at
            or not isinstance(self.recoverable, bool)
            or not isinstance(self.tombstone, bool)
        ):
            raise HistoryError("INVALID_ITEM", "context item is structurally invalid")
        if self.tombstone and not self.relations:
            raise HistoryError(
                "UNRELATED_TOMBSTONE", "tombstone must identify a prior item"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "source_event_id": str(self.source_event_id),
            "source_digest": str(self.source_digest),
            "scope": self.scope.to_wire(),
            "session_id": str(self.session_id),
            "role": self.role.value,
            "parts": [part.to_mapping() for part in self.parts],
            "relations": [relation.to_mapping() for relation in self.relations],
            "created_at": self.created_at,
            "recoverable": self.recoverable,
            "tombstone": self.tombstone,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PendingContextItem:
        return cls(
            item_id=Id(value["item_id"]),
            source_event_id=Id(value["source_event_id"]),
            source_digest=Digest(value["source_digest"]),
            scope=Scope.from_wire(value["scope"]),
            session_id=Id(value["session_id"]),
            role=Role(value["role"]),
            parts=tuple(ContextPart.from_mapping(part) for part in value["parts"]),
            relations=tuple(
                IdentityRelation.from_mapping(relation)
                for relation in value.get("relations", ())
            ),
            created_at=value["created_at"],
            recoverable=value["recoverable"],
            tombstone=value.get("tombstone", False),
        )


@dataclass(frozen=True, slots=True)
class ContextItem(PendingContextItem):
    cursor: Cursor = Cursor(1, 0)

    @property
    def reclaim_tag(self) -> int:
        return self.cursor.sequence

    def to_mapping(self) -> dict[str, Any]:
        result = PendingContextItem.to_mapping(self)
        result["cursor"] = self.cursor.to_wire()
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ContextItem:
        pending = PendingContextItem.from_mapping(value)
        return cls(
            item_id=pending.item_id,
            source_event_id=pending.source_event_id,
            source_digest=pending.source_digest,
            scope=pending.scope,
            session_id=pending.session_id,
            role=pending.role,
            parts=pending.parts,
            relations=pending.relations,
            created_at=pending.created_at,
            recoverable=pending.recoverable,
            tombstone=pending.tombstone,
            cursor=Cursor.from_wire(value["cursor"]),
        )


@dataclass(frozen=True, slots=True)
class JournalRange:
    start: Cursor
    end: Cursor

    def __post_init__(self) -> None:
        if (
            self.start.epoch != self.end.epoch
            or self.start.sequence == 0
            or self.start.sequence > self.end.sequence
        ):
            raise HistoryError("INVALID_RANGE", "journal range is invalid")


@dataclass(frozen=True, slots=True)
class RecoveredItem:
    item: ContextItem
    source_bytes: bytes


class SourceAdapter(Protocol):
    def resolve(self, scope: Scope, session_id: Id, source_event_id: Id) -> bytes: ...


class RawSourceJournal:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS raw_source_events (
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                source_bytes BLOB NOT NULL,
                PRIMARY KEY (owner_id, project_id, workspace_id, session_id, source_event_id)
            );
            CREATE TRIGGER IF NOT EXISTS raw_source_events_no_update
            BEFORE UPDATE ON raw_source_events BEGIN SELECT RAISE(ABORT, 'raw source journal is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS raw_source_events_no_delete
            BEFORE DELETE ON raw_source_events BEGIN SELECT RAISE(ABORT, 'raw source journal is immutable'); END;
            """)
        self._lock = threading.RLock()

    def append(
        self, scope: Scope, session_id: Id, source_event_id: Id, source_bytes: bytes
    ) -> Digest:
        session_id = Id(session_id)
        source_event_id = Id(source_event_id)
        if not isinstance(source_bytes, bytes):
            raise HistoryError("INVALID_SOURCE", "source content must be bytes")
        digest = Digest.sha256(source_bytes)
        key = _scope_key(scope) + (str(session_id), str(source_event_id))
        with self._lock:
            existing = self._connection.execute(
                "SELECT source_digest, source_bytes FROM raw_source_events "
                "WHERE owner_id=? AND project_id=? AND workspace_id=? AND session_id=? AND source_event_id=?",
                key,
            ).fetchone()
            if existing is not None:
                if existing[0] != digest or bytes(existing[1]) != source_bytes:
                    raise HistoryError(
                        "SOURCE_IDENTITY_CONFLICT",
                        "source event identity was reused for different content",
                    )
                return digest
            self._connection.execute(
                "INSERT INTO raw_source_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                key + (str(digest), source_bytes),
            )
        return digest

    def resolve(self, scope: Scope, session_id: Id, source_event_id: Id) -> bytes:
        key = _scope_key(scope) + (str(Id(session_id)), str(Id(source_event_id)))
        with self._lock:
            row = self._connection.execute(
                "SELECT source_digest, source_bytes FROM raw_source_events "
                "WHERE owner_id=? AND project_id=? AND workspace_id=? AND session_id=? AND source_event_id=?",
                key,
            ).fetchone()
        if row is None:
            raise HistoryError(
                "SOURCE_UNAVAILABLE", "authoritative source event was not found"
            )
        source_bytes = bytes(row[1])
        if Digest.sha256(source_bytes) != row[0]:
            raise HistoryError(
                "SOURCE_DIGEST_MISMATCH", "authoritative source content is corrupt"
            )
        return source_bytes


class HistoryJournal:
    def __init__(
        self,
        path: str | Path,
        *,
        scope: Scope,
        session_id: Id,
        epoch: int = 1,
    ) -> None:
        self.scope = scope
        self.session_id = Id(session_id)
        self.epoch = Cursor(epoch, 0).epoch
        self._connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS history_binding (
                singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                epoch INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history_entries (
                sequence INTEGER PRIMARY KEY,
                item_id TEXT NOT NULL UNIQUE,
                source_event_id TEXT NOT NULL UNIQUE,
                source_digest TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                item_json BLOB NOT NULL,
                source_snapshot BLOB
            );
            CREATE TRIGGER IF NOT EXISTS history_entries_no_update
            BEFORE UPDATE ON history_entries BEGIN SELECT RAISE(ABORT, 'history journal is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS history_entries_no_delete
            BEFORE DELETE ON history_entries BEGIN SELECT RAISE(ABORT, 'history journal is immutable'); END;
            """)
        expected = _scope_key(self.scope) + (str(self.session_id), self.epoch)
        row = self._connection.execute(
            "SELECT owner_id, project_id, workspace_id, session_id, epoch FROM history_binding WHERE singleton=1"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO history_binding VALUES (1, ?, ?, ?, ?, ?)", expected
            )
        elif tuple(row) != expected:
            raise HistoryError("SCOPE_MISMATCH", "journal binding does not match")
        self._validate_committed_state()

    @property
    def cursor(self) -> Cursor:
        with self._lock:
            sequence = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM history_entries"
            ).fetchone()[0]
        return Cursor(self.epoch, sequence)

    def append(
        self,
        *,
        expected_cursor: Cursor,
        idempotency_key: Id,
        item: PendingContextItem,
        source_snapshot: bytes | None = None,
    ) -> ContextItem:
        idempotency_key = Id(idempotency_key)
        if source_snapshot is not None and not isinstance(source_snapshot, bytes):
            raise HistoryError("INVALID_SOURCE", "source snapshot must be bytes")
        request_payload = {
            "expected_cursor": expected_cursor.to_wire(),
            "idempotency_key": str(idempotency_key),
            "item": item.to_mapping(),
            "source_snapshot": (
                source_snapshot.hex() if source_snapshot is not None else None
            ),
        }
        request_digest = Digest.sha256(_canonical_json(request_payload))
        source_fingerprint = Digest.sha256(
            _canonical_json(
                {
                    "item": item.to_mapping(),
                    "source_snapshot": (
                        source_snapshot.hex() if source_snapshot is not None else None
                    ),
                }
            )
        )
        if (
            source_snapshot is not None
            and Digest.sha256(source_snapshot) != item.source_digest
        ):
            raise HistoryError(
                "SOURCE_DIGEST_MISMATCH", "source snapshot digest does not match"
            )

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._connection.execute(
                    "SELECT request_digest, item_json FROM history_entries WHERE idempotency_key=?",
                    (str(idempotency_key),),
                ).fetchone()
                if replay is not None:
                    if replay[0] != request_digest:
                        raise HistoryError(
                            "IDEMPOTENCY_CONFLICT",
                            "idempotency key was reused for different work",
                        )
                    self._connection.execute("COMMIT")
                    return ContextItem.from_mapping(json.loads(replay[1]))

                replay = self._connection.execute(
                    "SELECT source_fingerprint, item_json FROM history_entries WHERE source_event_id=?",
                    (str(item.source_event_id),),
                ).fetchone()
                if replay is not None:
                    if replay[0] != source_fingerprint:
                        raise HistoryError(
                            "SOURCE_IDENTITY_CONFLICT",
                            "source event identity was reused for different work",
                        )
                    self._connection.execute("COMMIT")
                    return ContextItem.from_mapping(json.loads(replay[1]))

                current = self._cursor_locked()
                if expected_cursor != current:
                    raise HistoryError(
                        "STALE_CURSOR", "expected cursor is stale", retryable=True
                    )
                if item.scope != self.scope or item.session_id != self.session_id:
                    raise HistoryError(
                        "SCOPE_MISMATCH", "item does not match journal binding"
                    )
                if self._connection.execute(
                    "SELECT 1 FROM history_entries WHERE item_id=?",
                    (str(item.item_id),),
                ).fetchone():
                    raise HistoryError(
                        "ITEM_IDENTITY_CONFLICT", "item id is already committed"
                    )
                self._validate_relations(item)
                self._validate_tool_linkage(item)
                cursor = current.next()
                committed = ContextItem(
                    item_id=item.item_id,
                    source_event_id=item.source_event_id,
                    source_digest=item.source_digest,
                    scope=item.scope,
                    session_id=item.session_id,
                    role=item.role,
                    parts=item.parts,
                    relations=item.relations,
                    created_at=item.created_at,
                    recoverable=item.recoverable,
                    tombstone=item.tombstone,
                    cursor=cursor,
                )
                item_json = _canonical_json(committed.to_mapping())
                self._connection.execute(
                    "INSERT INTO history_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cursor.sequence,
                        str(item.item_id),
                        str(item.source_event_id),
                        str(item.source_digest),
                        str(idempotency_key),
                        str(request_digest),
                        str(source_fingerprint),
                        item_json,
                        source_snapshot,
                    ),
                )
                self._connection.execute("COMMIT")
                return committed
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def all_items(self) -> tuple[ContextItem, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT item_json FROM history_entries ORDER BY sequence"
            ).fetchall()
        return tuple(ContextItem.from_mapping(json.loads(row[0])) for row in rows)

    def items_through(self, cursor: Cursor) -> tuple[ContextItem, ...]:
        if cursor.epoch != self.epoch or cursor.sequence > self.cursor.sequence:
            raise HistoryError("INVALID_RANGE", "cursor is outside the journal")
        with self._lock:
            rows = self._connection.execute(
                "SELECT item_json FROM history_entries WHERE sequence<=? ORDER BY sequence",
                (cursor.sequence,),
            ).fetchall()
        return tuple(ContextItem.from_mapping(json.loads(row[0])) for row in rows)

    def items_in_range(self, journal_range: JournalRange) -> tuple[ContextItem, ...]:
        self._validate_range(journal_range)
        with self._lock:
            rows = self._connection.execute(
                "SELECT item_json FROM history_entries WHERE sequence BETWEEN ? AND ? ORDER BY sequence",
                (journal_range.start.sequence, journal_range.end.sequence),
            ).fetchall()
        return tuple(ContextItem.from_mapping(json.loads(row[0])) for row in rows)

    def source_digest(self, journal_range: JournalRange) -> Digest:
        material = bytearray()
        for item in self.items_in_range(journal_range):
            material.extend(item.cursor.epoch.to_bytes(8, "big"))
            material.extend(item.cursor.sequence.to_bytes(8, "big"))
            material.extend(str(item.item_id).encode())
            material.append(0)
            material.extend(str(item.source_event_id).encode())
            material.append(0)
            material.extend(bytes.fromhex(item.source_digest))
        return Digest.sha256(bytes(material))

    def recover(
        self,
        adapter: SourceAdapter,
        *,
        item_ids: Sequence[Id] | None = None,
        tags: Sequence[int] | None = None,
        cursor_range: JournalRange | None = None,
    ) -> tuple[RecoveredItem, ...]:
        selector_count = sum(
            value is not None for value in (item_ids, tags, cursor_range)
        )
        if selector_count != 1:
            raise HistoryError(
                "INVALID_RECOVERY", "exactly one recovery selector is required"
            )
        if item_ids is not None:
            unique = tuple(dict.fromkeys(str(Id(item_id)) for item_id in item_ids))
            if not unique:
                return ()
            placeholders = ",".join("?" for _ in unique)
            rows = self._connection.execute(
                f"SELECT item_json FROM history_entries WHERE item_id IN ({placeholders}) ORDER BY sequence",
                unique,
            ).fetchall()
            if len(rows) != len(unique):
                raise HistoryError("NOT_FOUND", "requested history item was not found")
            items = tuple(ContextItem.from_mapping(json.loads(row[0])) for row in rows)
        elif tags is not None:
            unique_tags = sorted(set(tags))
            if any(isinstance(tag, bool) or tag < 1 for tag in unique_tags):
                raise HistoryError("INVALID_RECOVERY", "reclaim tag is invalid")
            if not unique_tags:
                return ()
            placeholders = ",".join("?" for _ in unique_tags)
            rows = self._connection.execute(
                f"SELECT item_json FROM history_entries WHERE sequence IN ({placeholders}) ORDER BY sequence",
                unique_tags,
            ).fetchall()
            if len(rows) != len(unique_tags):
                raise HistoryError("NOT_FOUND", "requested reclaim tag was not found")
            items = tuple(ContextItem.from_mapping(json.loads(row[0])) for row in rows)
        else:
            items = self.items_in_range(cast(JournalRange, cursor_range))

        recovered = []
        for item in items:
            source_bytes = adapter.resolve(
                self.scope, self.session_id, item.source_event_id
            )
            if Digest.sha256(source_bytes) != item.source_digest:
                raise HistoryError(
                    "SOURCE_DIGEST_MISMATCH",
                    "authoritative source content does not match the committed digest",
                )
            recovered.append(RecoveredItem(item=item, source_bytes=source_bytes))
        return tuple(recovered)

    def _cursor_locked(self) -> Cursor:
        sequence = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM history_entries"
        ).fetchone()[0]
        return Cursor(self.epoch, sequence)

    def _validate_range(self, journal_range: JournalRange) -> None:
        if (
            journal_range.start.epoch != self.epoch
            or journal_range.end.sequence > self.cursor.sequence
        ):
            raise HistoryError(
                "INVALID_RANGE", "journal range is outside committed history"
            )

    def _validate_relations(self, item: PendingContextItem) -> None:
        for relation in item.relations:
            row = self._connection.execute(
                "SELECT source_digest FROM history_entries WHERE item_id=?",
                (str(relation.item_id),),
            ).fetchone()
            if row is None or (
                relation.source_digest is not None and row[0] != relation.source_digest
            ):
                raise HistoryError(
                    "INVALID_RELATION", "relation target is not committed"
                )

    def _validate_tool_linkage(self, item: PendingContextItem) -> None:
        states: dict[str, bool] = {}
        rows = self._connection.execute(
            "SELECT item_json FROM history_entries ORDER BY sequence"
        ).fetchall()
        prior_items = (ContextItem.from_mapping(json.loads(row[0])) for row in rows)
        for prior in prior_items:
            _apply_tool_parts(states, prior.parts)
        _apply_tool_parts(states, item.parts)

    def _validate_committed_state(self) -> None:
        rows = self._connection.execute(
            "SELECT sequence, source_digest, item_json, source_snapshot FROM history_entries ORDER BY sequence"
        ).fetchall()
        states: dict[str, bool] = {}
        items: dict[str, ContextItem] = {}
        for expected, row in enumerate(rows, start=1):
            if row[0] != expected:
                raise HistoryError(
                    "CORRUPT_JOURNAL", "journal ordinals are not contiguous"
                )
            item = ContextItem.from_mapping(json.loads(row[2]))
            if item.cursor != Cursor(self.epoch, expected) or item.scope != self.scope:
                raise HistoryError(
                    "CORRUPT_JOURNAL", "journal binding or cursor is corrupt"
                )
            if row[3] is not None and Digest.sha256(bytes(row[3])) != row[1]:
                raise HistoryError(
                    "CORRUPT_JOURNAL", "stored source snapshot is corrupt"
                )
            for relation in item.relations:
                target = items.get(str(relation.item_id))
                if target is None or (
                    relation.source_digest is not None
                    and relation.source_digest != target.source_digest
                ):
                    raise HistoryError("CORRUPT_JOURNAL", "journal relation is corrupt")
            _apply_tool_parts(states, item.parts)
            items[str(item.item_id)] = item


def _apply_tool_parts(states: dict[str, bool], parts: Sequence[ContextPart]) -> None:
    for part in parts:
        if part.kind is PartKind.TOOL_CALL:
            call_id = str(part.call_id)
            if call_id in states:
                raise HistoryError(
                    "DUPLICATE_TOOL_CALL", "tool call id is already committed"
                )
            states[call_id] = False
        elif part.kind is PartKind.TOOL_RESULT:
            call_id = str(part.call_id)
            if call_id not in states:
                raise HistoryError(
                    "ORPHAN_TOOL_RESULT", "tool result has no prior call"
                )
            if states[call_id]:
                raise HistoryError(
                    "DUPLICATE_TOOL_RESULT", "tool call already has a result"
                )
            states[call_id] = True


def _scope_key(scope: Scope) -> tuple[str, str, str]:
    return (
        str(scope.owner_id),
        str(scope.project_id),
        str(scope.workspace_id) if scope.workspace_id is not None else "",
    )


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


__all__ = [
    "ContextItem",
    "ContextPart",
    "HistoryError",
    "HistoryJournal",
    "JournalRange",
    "PartKind",
    "PendingContextItem",
    "RawSourceJournal",
    "RecoveredItem",
    "RelationKind",
    "Role",
    "SourceAdapter",
]
