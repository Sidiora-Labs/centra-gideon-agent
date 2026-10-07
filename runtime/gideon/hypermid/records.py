from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .protocol import Cursor, Scope, Trace, _require_digest, _require_id

MAX_CONTENT_BYTES = 262_144
MAX_CATEGORY_BYTES = 128


class MemoryContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RecordKind(str, Enum):
    FACT = "fact"
    EPISODE = "episode"
    NOTE = "note"
    SMART_NOTE = "smart_note"
    ANCHOR = "anchor"
    SUMMARY = "summary"


class RecordStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    STALE = "stale"
    TOMBSTONED = "tombstoned"


class VerificationState(str, Enum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    REFUTED = "refuted"
    UNKNOWN = "unknown"


class SourceKind(str, Enum):
    MEMORY = "memory"
    MESSAGE = "message"
    FILE = "file"
    GIT_COMMIT = "git_commit"
    EXTERNAL = "external"


class LineageRelation(str, Enum):
    DERIVED_FROM = "derived_from"
    CITES = "cites"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    MERGED_FROM = "merged_from"
    SPLIT_FROM = "split_from"
    IMPORTED_FROM = "imported_from"
    VERIFIES = "verifies"

    @property
    def acyclic(self) -> bool:
        return self in {
            LineageRelation.DERIVED_FROM,
            LineageRelation.SUPERSEDES,
            LineageRelation.MERGED_FROM,
            LineageRelation.SPLIT_FROM,
        }


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def content_digest(content: str) -> str:
    return digest_bytes(content.encode("utf-8"))


def normalized_content_digest(content: str) -> str:
    normalized = " ".join(content.split()).casefold()
    return content_digest(normalized)


def canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return digest_bytes(encoded)


def _bounded_text(value: str, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise MemoryContractError("INVALID_MEMORY", f"{field_name} is required")
    if len(value.encode("utf-8")) > maximum:
        raise MemoryContractError("INVALID_MEMORY", f"{field_name} is too large")
    return value


def _probability(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemoryContractError("INVALID_MEMORY", f"{field_name} must be numeric")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise MemoryContractError("INVALID_MEMORY", f"{field_name} is outside 0..1")
    return result


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    owner_scope: Scope
    kind: SourceKind
    source_digest: str
    locator: str | None
    captured_content: str | None
    capture_method: str
    observed_at_ms: int

    def __post_init__(self) -> None:
        _require_id(self.source_id, "source_id")
        _require_digest(self.source_digest, "source_digest")
        _bounded_text(self.capture_method, "capture_method", 128)
        if self.locator is not None and len(self.locator.encode("utf-8")) > 4096:
            raise MemoryContractError("INVALID_PROVENANCE", "locator is too large")
        if isinstance(self.observed_at_ms, bool) or self.observed_at_ms < 0:
            raise MemoryContractError("INVALID_PROVENANCE", "observed_at_ms is invalid")
        if (
            self.captured_content is not None
            and content_digest(self.captured_content) != self.source_digest
        ):
            raise MemoryContractError(
                "SOURCE_DIGEST_MISMATCH",
                "captured source bytes do not match source_digest",
            )

    def recover(
        self, span_start: int | None = None, span_end: int | None = None
    ) -> str:
        if self.captured_content is None:
            raise MemoryContractError(
                "SOURCE_CONTENT_UNAVAILABLE", "source has only an external reference"
            )
        if span_start is None and span_end is None:
            return self.captured_content
        if span_start is None or span_end is None:
            raise MemoryContractError(
                "INVALID_SOURCE_SPAN", "source span is incomplete"
            )
        raw = self.captured_content.encode("utf-8")
        if span_start < 0 or span_end < span_start or span_end > len(raw):
            raise MemoryContractError(
                "INVALID_SOURCE_SPAN", "source span is outside content"
            )
        try:
            return raw[span_start:span_end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryContractError(
                "INVALID_SOURCE_SPAN", "source span splits a UTF-8 code point"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProvenanceSpan:
    source_id: str
    span_start: int | None = None
    span_end: int | None = None
    quoted_digest: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.source_id, "source_id")
        if (self.span_start is None) != (self.span_end is None):
            raise MemoryContractError(
                "INVALID_SOURCE_SPAN", "source span is incomplete"
            )
        if self.span_start is not None and (
            self.span_start < 0
            or self.span_end is None
            or self.span_end < self.span_start
        ):
            raise MemoryContractError("INVALID_SOURCE_SPAN", "source span is invalid")
        if self.quoted_digest is not None:
            _require_digest(self.quoted_digest, "quoted_digest")

    def recover(self, source: SourceSnapshot) -> str:
        if source.source_id != self.source_id:
            raise MemoryContractError(
                "SOURCE_MISMATCH", "provenance names another source"
            )
        recovered = source.recover(self.span_start, self.span_end)
        if (
            self.quoted_digest is not None
            and content_digest(recovered) != self.quoted_digest
        ):
            raise MemoryContractError(
                "QUOTED_DIGEST_MISMATCH",
                "recovered source span failed its digest guard",
            )
        return recovered


@dataclass(frozen=True, slots=True)
class LineageEdge:
    parent_id: str
    relation: LineageRelation
    parent_revision_digest: str

    def __post_init__(self) -> None:
        _require_id(self.parent_id, "parent_id")
        _require_digest(self.parent_revision_digest, "parent_revision_digest")


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    record_id: str
    revision: int
    revision_digest: str
    parent_revision_digest: str | None
    content: str
    content_digest: str
    metadata: Mapping[str, Any]
    author_scope: Scope
    authored_at_ms: int
    provenance: tuple[ProvenanceSpan, ...] = ()
    lineage: tuple[LineageEdge, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record_id")
        _require_digest(self.revision_digest, "revision_digest")
        if self.parent_revision_digest is not None:
            _require_digest(self.parent_revision_digest, "parent_revision_digest")
        _bounded_text(self.content, "content", MAX_CONTENT_BYTES)
        if content_digest(self.content) != self.content_digest:
            raise MemoryContractError(
                "CONTENT_DIGEST_MISMATCH", "content digest is stale"
            )
        if self.revision < 1 or (self.revision > 1) != (
            self.parent_revision_digest is not None
        ):
            raise MemoryContractError(
                "INVALID_REVISION", "revision parent is inconsistent"
            )
        if self.authored_at_ms < 0:
            raise MemoryContractError("INVALID_REVISION", "authored_at_ms is invalid")
        if len(self.provenance) > 128 or len(self.lineage) > 128:
            raise MemoryContractError(
                "INVALID_REVISION", "revision edges are unbounded"
            )


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    record_id: str
    scope: Scope
    kind: RecordKind
    category: str
    status: RecordStatus
    current: MemoryRevision
    importance: float = 0.5
    confidence: float = 0.5
    verification: VerificationState = VerificationState.UNVERIFIED
    expires_at_ms: int | None = None
    retention_until_ms: int | None = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    deleted_at_ms: int | None = None

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record_id")
        _bounded_text(self.category, "category", MAX_CATEGORY_BYTES)
        if self.current.record_id != self.record_id:
            raise MemoryContractError(
                "INVALID_REVISION", "revision belongs to another record"
            )
        _probability(self.importance, "importance")
        _probability(self.confidence, "confidence")
        if self.kind is RecordKind.ANCHOR and self.expires_at_ms is not None:
            raise MemoryContractError("IMMUTABLE_ANCHOR", "anchors cannot expire")
        if self.status is RecordStatus.TOMBSTONED and self.deleted_at_ms is None:
            raise MemoryContractError(
                "INVALID_TOMBSTONE", "tombstone requires deleted_at_ms"
            )
        if self.updated_at_ms < self.created_at_ms:
            raise MemoryContractError(
                "INVALID_MEMORY", "updated_at_ms predates creation"
            )


@dataclass(frozen=True, slots=True)
class MutationResult:
    record: MemoryRecord
    cursor: Cursor
    trace: Trace
    invalidated_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for record_id in self.invalidated_ids:
            _require_id(record_id, "invalidated_id")


def instruction_shaped(content: str) -> bool:
    normalized = " ".join(content.casefold().split())
    prefixes = (
        "system:",
        "assistant:",
        "developer:",
        "ignore previous",
        "ignore all previous",
        "you must",
        "do not ask",
    )
    return normalized.startswith(prefixes)


def render_as_untrusted_data(record: MemoryRecord) -> str:
    label = (
        " instruction-shaped=true" if instruction_shaped(record.current.content) else ""
    )
    return (
        f'<hypermid-memory id="{record.record_id}" kind="{record.kind.value}"'
        f' revision="{record.current.revision_digest}"{label}>'
        f"\n{record.current.content}\n</hypermid-memory>"
    )
