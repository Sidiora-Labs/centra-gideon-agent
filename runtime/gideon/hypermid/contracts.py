"""Typed contracts for the Hypermid memory service boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .foundation import Cursor, Digest, EffectState, Id, Scope, Trace
from .model_budget import ModelBudget
from .models import JsonValue


class MemoryContractError(ValueError):
    pass


class MemoryOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    RESTORE = "restore"
    MERGE = "merge"
    SPLIT = "split"
    RELOCATE = "relocate"
    DELETE = "delete"
    PURGE = "purge"
    VERIFY = "verify"
    EMBED = "embed"
    INDEX = "index"
    SUMMARIZE = "summarize"
    IMPORT = "import"
    EXPORT = "export"


class GrantOperation(str, Enum):
    READ = "read"
    SEARCH = "search"
    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    RESTORE = "restore"
    MERGE = "merge"
    SPLIT = "split"
    RELOCATE = "relocate"
    DELETE = "delete"
    PURGE = "purge"
    VERIFY = "verify"
    EMBED = "embed"
    INDEX = "index"
    SUMMARIZE = "summarize"
    IMPORT = "import"
    EXPORT = "export"


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


class SharingClassification(str, Enum):
    PRIVATE = "private"
    SHARED = "shared"


class TrustDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class SourceKind(str, Enum):
    MEMORY = "memory"
    MESSAGE = "message"
    FILE = "file"
    GIT_COMMIT = "git_commit"
    EXTERNAL = "external"


class SearchMode(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class PredicateOperator(str, Enum):
    ALL = "all"
    ANY = "any"
    NONE = "none"


class PredicateComparison(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    CONTAINS = "contains"
    GTE = "gte"
    LTE = "lte"


class PredicateField(str, Enum):
    EVENT_KIND = "event.kind"
    EVENT_LABEL = "event.label"
    RECORD_KIND = "record.kind"
    RECORD_CATEGORY = "record.category"
    RECORD_STATUS = "record.status"
    TIME_HOUR = "time.hour"
    TIME_WEEKDAY = "time.weekday"


class MaintenanceKind(str, Enum):
    EXTRACT_FACTS = "extract_facts"
    EXTRACT_EPISODES = "extract_episodes"
    VERIFY_CLAIMS = "verify_claims"
    EVALUATE_SMART_NOTES = "evaluate_smart_notes"
    REFRESH_SUMMARIES = "refresh_summaries"
    DECAY_SUMMARIES = "decay_summaries"
    EMBED_RECORDS = "embed_records"
    REEMBED_MODEL = "reembed_model"
    RECONCILE_FTS = "reconcile_fts"
    RECONCILE_SOURCES = "reconcile_sources"
    INDEX_MESSAGES = "index_messages"
    INDEX_GIT_COMMITS = "index_git_commits"
    INVALIDATE_LINEAGE = "invalidate_lineage"
    SWEEP_ORPHANS = "sweep_orphans"
    COMPACT_EVENTS = "compact_events"
    CHECK_INTEGRITY = "check_integrity"
    IMPORT_BATCH = "import_batch"
    EXPORT_BATCH = "export_batch"
    PURGE_TOMBSTONES = "purge_tombstones"


class MaintenanceTerminalState(str, Enum):
    FAILED = "failed"
    ABANDONED = "abandoned"


class LegacyKnowledgeCategory(str, Enum):
    PAGE = "page"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    LESSON = "lesson"
    SLOT = "slot"
    GRAPH_LINK = "graph_link"
    AUDIT_EVENT = "audit_event"
    SUMMARY = "summary"


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryContractError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str, *, maximum: int = 4096) -> Sequence[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise MemoryContractError(f"{name} must be a bounded array")
    return value


def _uint(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MemoryContractError(f"{name} must be an integer at least {minimum}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemoryContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MemoryContractError(f"{name} must be finite")
    return result


def _probability(value: object, name: str) -> float:
    result = _number(value, name)
    if not 0.0 <= result <= 1.0:
        raise MemoryContractError(f"{name} must be between zero and one")
    return result


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise MemoryContractError(f"{name} must be non-empty and at most {maximum} bytes")
    return value


def _json(value: object, name: str = "JSON value") -> JsonValue:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MemoryContractError(f"{name} contains a non-finite number")
        return value  # type: ignore[return-value]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MemoryContractError(f"{name} contains a non-string key")
            result[key] = _json(item, name)
        return result
    if isinstance(value, (list, tuple)):
        return [_json(item, name) for item in value]
    raise MemoryContractError(f"{name} is not JSON-compatible")


@dataclass(frozen=True, slots=True)
class RevisionPrecondition:
    digest: Digest | None = None

    @classmethod
    def must_not_exist(cls) -> RevisionPrecondition:
        return cls()

    @classmethod
    def match(cls, digest: str) -> RevisionPrecondition:
        return cls(Digest(digest))

    def to_wire(self) -> dict[str, JsonValue]:
        if self.digest is None:
            return {"kind": "must_not_exist"}
        return {"kind": "match", "digest": str(self.digest)}


@dataclass(frozen=True, slots=True)
class AccessRequest:
    operation: GrantOperation
    actor_scope: Scope
    target_scope: Scope
    resource_id: Id
    trace: Trace
    category: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", GrantOperation(self.operation))
        if self.operation not in (GrantOperation.READ, GrantOperation.SEARCH):
            raise MemoryContractError("access operation must be read or search")
        object.__setattr__(self, "resource_id", Id(self.resource_id))
        if self.category is not None:
            _text(self.category, "category", 128)

    def to_wire(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "operation": self.operation.value,
            "actor_scope": self.actor_scope.to_wire(),
            "target_scope": self.target_scope.to_wire(),
            "resource_id": str(self.resource_id),
            "trace": self.trace.to_wire(),
        }
        if self.category is not None:
            value["category"] = self.category
        return value


@dataclass(frozen=True, slots=True)
class MutationRequest:
    operation: MemoryOperation
    actor_scope: Scope
    target_scope: Scope
    revision: RevisionPrecondition
    trace: Trace
    record_id: Id | None = None
    category: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", MemoryOperation(self.operation))
        if self.record_id is not None:
            object.__setattr__(self, "record_id", Id(self.record_id))
        if self.category is not None:
            _text(self.category, "category", 128)

    def to_wire(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "operation": self.operation.value,
            "actor_scope": self.actor_scope.to_wire(),
            "target_scope": self.target_scope.to_wire(),
            "revision": self.revision.to_wire(),
            "trace": self.trace.to_wire(),
        }
        if self.record_id is not None:
            value["record_id"] = str(self.record_id)
        if self.category is not None:
            value["category"] = self.category
        return value


@dataclass(frozen=True, slots=True)
class ProvenanceSpan:
    source_id: Id
    span_start: int | None = None
    span_end: int | None = None
    quoted_digest: Digest | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", Id(self.source_id))
        if (self.span_start is None) != (self.span_end is None):
            raise MemoryContractError("source span must be complete")
        if self.span_start is not None:
            _uint(self.span_start, "span_start")
            _uint(self.span_end, "span_end")
            if self.span_end < self.span_start:  # type: ignore[operator]
                raise MemoryContractError("source span is reversed")
        if self.quoted_digest is not None:
            object.__setattr__(self, "quoted_digest", Digest(self.quoted_digest))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "source_id": str(self.source_id),
            "span_start": self.span_start,
            "span_end": self.span_end,
            "quoted_digest": str(self.quoted_digest) if self.quoted_digest else None,
        }


@dataclass(frozen=True, slots=True)
class LineageEdge:
    parent_id: Id
    relation: str
    parent_revision_digest: Digest

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_id", Id(self.parent_id))
        object.__setattr__(self, "parent_revision_digest", Digest(self.parent_revision_digest))
        if self.relation not in {
            "derived_from", "cites", "supersedes", "contradicts",
            "merged_from", "split_from", "imported_from", "verifies",
        }:
            raise MemoryContractError("lineage relation is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "parent_id": str(self.parent_id),
            "relation": self.relation,
            "parent_revision_digest": str(self.parent_revision_digest),
        }

    @classmethod
    def from_wire(cls, value: object) -> LineageEdge:
        raw = _object(value, "lineage edge")
        return cls(
            parent_id=Id(raw.get("parent_id")),
            relation=_text(raw.get("relation"), "lineage relation", 128),
            parent_revision_digest=Digest(raw.get("parent_revision_digest")),
        )


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: Id
    owner_scope_digest: Digest
    kind: SourceKind
    source_digest: Digest
    locator: str | None
    captured_content: str | None
    capture_method: str
    observed_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", Id(self.source_id))
        object.__setattr__(self, "owner_scope_digest", Digest(self.owner_scope_digest))
        object.__setattr__(self, "kind", SourceKind(self.kind))
        object.__setattr__(self, "source_digest", Digest(self.source_digest))
        _text(self.capture_method, "capture_method", 128)
        _uint(self.observed_at_ms, "observed_at_ms")
        if self.locator is not None and len(self.locator.encode("utf-8")) > 4096:
            raise MemoryContractError("source locator is too large")
        if self.captured_content is not None:
            encoded = self.captured_content.encode("utf-8")
            if len(encoded) > 4 * 1024 * 1024:
                raise MemoryContractError("captured source content is too large")
            if Digest.sha256(encoded) != self.source_digest:
                raise MemoryContractError("captured source content does not match its digest")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "source_id": str(self.source_id),
            "owner_scope_digest": str(self.owner_scope_digest),
            "kind": self.kind.value,
            "source_digest": str(self.source_digest),
            "locator": self.locator,
            "captured_content": self.captured_content,
            "capture_method": self.capture_method,
            "observed_at_ms": self.observed_at_ms,
        }


PredicateScalar = str | bool | int | float
PredicateValue = PredicateScalar | tuple[PredicateScalar, ...]


def _predicate_scalar(value: object, name: str) -> PredicateScalar:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 1024:
            raise MemoryContractError(f"{name} string is too large")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise MemoryContractError(f"{name} number must be finite")
        return value
    raise MemoryContractError(f"{name} must be a scalar")


@dataclass(frozen=True, slots=True)
class PredicateClause:
    field: PredicateField
    comparison: PredicateComparison
    value: PredicateValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", PredicateField(self.field))
        object.__setattr__(self, "comparison", PredicateComparison(self.comparison))
        comparison = self.comparison
        value = self.value
        if comparison is PredicateComparison.IN:
            if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 64:
                raise MemoryContractError("in predicate requires a bounded scalar array")
            object.__setattr__(
                self,
                "value",
                tuple(_predicate_scalar(item, "predicate value") for item in value),
            )
            return
        if isinstance(value, (list, tuple)):
            raise MemoryContractError("predicate comparison requires a scalar value")
        scalar = _predicate_scalar(value, "predicate value")
        if comparison is PredicateComparison.CONTAINS and not isinstance(scalar, str):
            raise MemoryContractError("contains predicate requires a string")
        if comparison in (PredicateComparison.GTE, PredicateComparison.LTE) and (
            isinstance(scalar, bool) or not isinstance(scalar, (int, float))
        ):
            raise MemoryContractError("ordered predicate requires a number")
        object.__setattr__(self, "value", scalar)

    def to_wire(self) -> dict[str, JsonValue]:
        value: JsonValue
        if isinstance(self.value, tuple):
            value = list(self.value)
        else:
            value = self.value
        return {
            "field": self.field.value,
            "comparison": self.comparison.value,
            "value": value,
        }

    @classmethod
    def from_wire(cls, value: object) -> PredicateClause:
        raw = _object(value, "predicate clause")
        clause_value = raw.get("value")
        if isinstance(clause_value, list):
            clause_value = tuple(clause_value)
        return cls(
            field=PredicateField(raw.get("field")),
            comparison=PredicateComparison(raw.get("comparison")),
            value=clause_value,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class SmartPredicate:
    operator: PredicateOperator
    clauses: tuple[PredicateClause, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator", PredicateOperator(self.operator))
        clauses = tuple(self.clauses)
        if not 1 <= len(clauses) <= 32:
            raise MemoryContractError(
                "smart-note predicate must contain between 1 and 32 clauses"
            )
        if not all(isinstance(clause, PredicateClause) for clause in clauses):
            raise MemoryContractError("smart-note predicate clauses are invalid")
        object.__setattr__(self, "clauses", clauses)

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "operator": self.operator.value,
            "clauses": [clause.to_wire() for clause in self.clauses],
        }

    @classmethod
    def from_wire(cls, value: object) -> SmartPredicate:
        raw = _object(value, "smart-note predicate")
        return cls(
            operator=PredicateOperator(raw.get("operator")),
            clauses=tuple(
                PredicateClause.from_wire(item)
                for item in _sequence(raw.get("clauses"), "predicate clauses", maximum=32)
            ),
        )


@dataclass(frozen=True, slots=True)
class SmartNoteCandidate:
    record_id: Id
    revision_digest: Digest
    category: str
    predicate: SmartPredicate
    predicate_digest: Digest
    last_evaluated_cursor: Cursor | None
    last_result: bool | None
    next_evaluation_at_ms: int | None

    @classmethod
    def from_wire(cls, value: object) -> SmartNoteCandidate:
        raw = _object(value, "smart-note candidate")
        last_cursor = raw.get("last_evaluated_cursor")
        last_result = raw.get("last_result")
        next_evaluation = raw.get("next_evaluation_at_ms")
        if last_result is not None and not isinstance(last_result, bool):
            raise MemoryContractError("smart-note last result must be boolean")
        if next_evaluation is not None:
            next_evaluation = _uint(
                next_evaluation, "smart-note next evaluation timestamp"
            )
        return cls(
            record_id=Id(raw.get("record_id")),
            revision_digest=Digest(raw.get("revision_digest")),
            category=_text(raw.get("category"), "smart-note category", 128),
            predicate=SmartPredicate.from_wire(raw.get("predicate")),
            predicate_digest=Digest(raw.get("predicate_digest")),
            last_evaluated_cursor=(
                Cursor.from_wire(_object(last_cursor, "smart-note cursor"))
                if last_cursor is not None
                else None
            ),
            last_result=last_result,
            next_evaluation_at_ms=next_evaluation,
        )


@dataclass(frozen=True, slots=True)
class SmartNoteCandidatePage:
    cursor: Cursor
    candidates: tuple[SmartNoteCandidate, ...]

    @classmethod
    def from_wire(cls, value: object) -> SmartNoteCandidatePage:
        raw = _object(value, "smart-note candidate page")
        result = cls(
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "candidate cursor")),
            candidates=tuple(
                SmartNoteCandidate.from_wire(item)
                for item in _sequence(
                    raw.get("candidates"), "smart-note candidates", maximum=1000
                )
            ),
        )
        ids = [str(candidate.record_id) for candidate in result.candidates]
        if len(ids) != len(set(ids)):
            raise MemoryContractError("smart-note candidate ids must be unique")
        return result


@dataclass(frozen=True, slots=True)
class RecordDraft:
    id: Id
    scope: Scope
    kind: RecordKind
    category: str
    content: str
    importance: float
    confidence: float
    expires_at_ms: int | None = None
    retention_until_ms: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    provenance: tuple[ProvenanceSpan, ...] = ()
    lineage: tuple[LineageEdge, ...] = ()
    smart_predicate: SmartPredicate | None = None
    summary: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", Id(self.id))
        object.__setattr__(self, "kind", RecordKind(self.kind))
        _text(self.category, "category", 128)
        _text(self.content, "content", 262_144)
        object.__setattr__(self, "importance", _probability(self.importance, "importance"))
        object.__setattr__(self, "confidence", _probability(self.confidence, "confidence"))
        for name in ("expires_at_ms", "retention_until_ms"):
            value = getattr(self, name)
            if value is not None:
                _uint(value, name)
        metadata = _json(self.metadata, "metadata")
        if not isinstance(metadata, dict) or len(metadata) > 64:
            raise MemoryContractError("metadata must be a bounded object")
        object.__setattr__(self, "metadata", MappingProxyType(metadata))
        if self.kind is RecordKind.SMART_NOTE:
            if self.smart_predicate is None:
                raise MemoryContractError("smart-note records require a predicate")
        elif self.smart_predicate is not None:
            raise MemoryContractError("only smart-note records may carry a predicate")
        if self.smart_predicate is not None and not isinstance(
            self.smart_predicate, SmartPredicate
        ):
            raise MemoryContractError("smart-note predicate is invalid")

    def to_wire(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "id": str(self.id),
            "scope": self.scope.to_wire(),
            "kind": self.kind.value,
            "category": self.category,
            "content": self.content,
            "metadata": dict(self.metadata),
            "importance": self.importance,  # type: ignore[dict-item]
            "confidence": self.confidence,  # type: ignore[dict-item]
            "expires_at_ms": self.expires_at_ms,
            "retention_until_ms": self.retention_until_ms,
            "provenance": [item.to_wire() for item in self.provenance],
            "lineage": [item.to_wire() for item in self.lineage],
            "summary": dict(self.summary) if self.summary is not None else None,
        }
        if self.smart_predicate is not None:
            value["smart_predicate"] = self.smart_predicate.to_wire()
        return value


@dataclass(frozen=True, slots=True)
class MaintenanceJobSpec:
    id: Id
    kind: MaintenanceKind
    target_scope: Scope
    actor_scope: Scope
    required_operation: MemoryOperation
    input_cursor: Cursor
    input_digest: Digest
    config_digest: Digest
    budget: ModelBudget
    available_at_ms: int
    created_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", Id(self.id))
        object.__setattr__(self, "kind", MaintenanceKind(self.kind))
        object.__setattr__(
            self, "required_operation", MemoryOperation(self.required_operation)
        )
        object.__setattr__(self, "input_digest", Digest(self.input_digest))
        object.__setattr__(self, "config_digest", Digest(self.config_digest))
        _uint(self.available_at_ms, "available_at_ms")
        _uint(self.created_at_ms, "created_at_ms")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "id": str(self.id),
            "kind": self.kind.value,
            "target_scope": self.target_scope.to_wire(),
            "actor_scope": self.actor_scope.to_wire(),
            "required_operation": self.required_operation.value,
            "input_cursor": self.input_cursor.to_wire(),
            "input_digest": str(self.input_digest),
            "config_digest": str(self.config_digest),
            "budget": self.budget.to_wire(),
            "available_at_ms": self.available_at_ms,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True, slots=True)
class SummaryPublication:
    job_id: Id
    expected_input_digest: Digest
    expected_config_digest: Digest
    draft: RecordDraft
    sources: tuple[SourceSnapshot, ...]
    now_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", Id(self.job_id))
        object.__setattr__(
            self, "expected_input_digest", Digest(self.expected_input_digest)
        )
        object.__setattr__(
            self, "expected_config_digest", Digest(self.expected_config_digest)
        )
        if self.draft.kind is not RecordKind.SUMMARY:
            raise MemoryContractError("summary publication draft must be a summary")
        _uint(self.now_ms, "now_ms")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "kind": "summary",
            "job_id": str(self.job_id),
            "expected_input_digest": str(self.expected_input_digest),
            "expected_config_digest": str(self.expected_config_digest),
            "draft": self.draft.to_wire(),
            "sources": [source.to_wire() for source in self.sources],
            "now_ms": self.now_ms,
        }


@dataclass(frozen=True, slots=True)
class SmartNoteEvaluation:
    record_id: Id
    expected_revision_digest: Digest
    expected_predicate_digest: Digest

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", Id(self.record_id))
        object.__setattr__(
            self, "expected_revision_digest", Digest(self.expected_revision_digest)
        )
        object.__setattr__(
            self, "expected_predicate_digest", Digest(self.expected_predicate_digest)
        )

    @classmethod
    def from_candidate(cls, candidate: SmartNoteCandidate) -> SmartNoteEvaluation:
        return cls(
            candidate.record_id,
            candidate.revision_digest,
            candidate.predicate_digest,
        )

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "record_id": str(self.record_id),
            "expected_revision_digest": str(self.expected_revision_digest),
            "expected_predicate_digest": str(self.expected_predicate_digest),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeVerification:
    record_id: Id
    expected_revision_digest: Digest
    state: VerificationState
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", Id(self.record_id))
        object.__setattr__(
            self, "expected_revision_digest", Digest(self.expected_revision_digest)
        )
        object.__setattr__(self, "state", VerificationState(self.state))
        object.__setattr__(
            self, "confidence", _probability(self.confidence, "verification confidence")
        )

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "record_id": str(self.record_id),
            "expected_revision_digest": str(self.expected_revision_digest),
            "state": self.state.value,
            "confidence": self.confidence,  # type: ignore[dict-item]
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSharingJudgment:
    record_id: Id
    expected_revision_digest: Digest
    classification: SharingClassification
    trust_decision: TrustDecision
    policy_id: str
    policy_version: int
    policy_digest: Digest
    provider_id: str | None
    model_id: str | None
    evidence_digest: Digest

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", Id(self.record_id))
        object.__setattr__(
            self, "expected_revision_digest", Digest(self.expected_revision_digest)
        )
        object.__setattr__(
            self, "classification", SharingClassification(self.classification)
        )
        object.__setattr__(self, "trust_decision", TrustDecision(self.trust_decision))
        _text(self.policy_id, "sharing policy id", 128)
        _uint(self.policy_version, "sharing policy version", minimum=1)
        object.__setattr__(self, "policy_digest", Digest(self.policy_digest))
        if (self.provider_id is None) != (self.model_id is None):
            raise MemoryContractError(
                "sharing provider and model identities must be supplied together"
            )
        if self.provider_id is not None:
            _text(self.provider_id, "sharing provider id", 256)
            _text(self.model_id, "sharing model id", 256)
        object.__setattr__(self, "evidence_digest", Digest(self.evidence_digest))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "record_id": str(self.record_id),
            "expected_revision_digest": str(self.expected_revision_digest),
            "classification": self.classification.value,
            "trust_decision": self.trust_decision.value,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_digest": str(self.policy_digest),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "evidence_digest": str(self.evidence_digest),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeRecall:
    draft: RecordDraft

    def __post_init__(self) -> None:
        if self.draft.kind not in (RecordKind.FACT, RecordKind.NOTE):
            raise MemoryContractError("knowledge recall must create a fact or note")

    def to_wire(self) -> dict[str, JsonValue]:
        return {"draft": self.draft.to_wire()}


@dataclass(frozen=True, slots=True)
class KnowledgePublication:
    job_id: Id
    expected_input_digest: Digest
    expected_config_digest: Digest
    source: SourceSnapshot
    evaluated_cursor: Cursor
    smart_notes: tuple[SmartNoteEvaluation, ...] = ()
    verifications: tuple[KnowledgeVerification, ...] = ()
    sharing_judgments: tuple[KnowledgeSharingJudgment, ...] = ()
    recall: KnowledgeRecall | None = None
    repository_identity_digest: Digest | None = None
    refs_digest: Digest | None = None
    next_evaluation_at_ms: int | None = None
    now_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "smart_notes", tuple(self.smart_notes))
        object.__setattr__(self, "verifications", tuple(self.verifications))
        object.__setattr__(self, "sharing_judgments", tuple(self.sharing_judgments))
        object.__setattr__(self, "job_id", Id(self.job_id))
        object.__setattr__(
            self, "expected_input_digest", Digest(self.expected_input_digest)
        )
        object.__setattr__(
            self, "expected_config_digest", Digest(self.expected_config_digest)
        )
        if self.source.source_digest != self.expected_input_digest:
            raise MemoryContractError(
                "knowledge source digest must match the expected job input"
            )
        if self.source.captured_content is None:
            raise MemoryContractError("knowledge publication requires captured source bytes")
        if self.source.kind is SourceKind.GIT_COMMIT:
            if self.repository_identity_digest is None or self.refs_digest is None:
                raise MemoryContractError("Git knowledge publication requires guard digests")
            expected_locator = f"git:{self.repository_identity_digest}"
            if (
                self.source.capture_method != "gideon_guarded_local_git"
                or self.source.locator != expected_locator
            ):
                raise MemoryContractError("Git knowledge source is not guard-captured")
        elif self.source.kind is SourceKind.FILE:
            if self.repository_identity_digest is not None or self.refs_digest is not None:
                raise MemoryContractError("file knowledge source cannot carry Git guards")
        else:
            raise MemoryContractError(
                "knowledge publication accepts only note or Git sources"
            )
        if self.next_evaluation_at_ms is not None:
            _uint(self.next_evaluation_at_ms, "next evaluation timestamp")
        _uint(self.now_ms, "knowledge publication timestamp")
        output_count = (
            len(self.smart_notes)
            + len(self.verifications)
            + len(self.sharing_judgments)
            + int(self.recall is not None)
        )
        if not 1 <= output_count <= 200:
            raise MemoryContractError(
                "knowledge publication must contain between 1 and 200 outputs"
            )
        for name, values in (
            ("smart-note", self.smart_notes),
            ("verification", self.verifications),
            ("sharing", self.sharing_judgments),
        ):
            record_ids = [str(item.record_id) for item in values]
            if len(record_ids) != len(set(record_ids)):
                raise MemoryContractError(f"knowledge {name} record ids must be unique")
        for judgment in self.sharing_judgments:
            if judgment.evidence_digest != self.source.source_digest:
                raise MemoryContractError(
                    "sharing judgment evidence must match the knowledge source"
                )
        if (self.verifications or self.recall is not None) and (
            self.source.kind is not SourceKind.GIT_COMMIT
        ):
            raise MemoryContractError(
                "verification and recall require a guarded Git source"
            )
        if self.recall is not None and not any(
            span.source_id == self.source.source_id
            and span.quoted_digest == self.source.source_digest
            for span in self.recall.draft.provenance
        ):
            raise MemoryContractError(
                "knowledge recall provenance must cite the guarded source"
            )

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "job_id": str(self.job_id),
            "expected_input_digest": str(self.expected_input_digest),
            "expected_config_digest": str(self.expected_config_digest),
            "source": self.source.to_wire(),
            "repository_identity_digest": (
                str(self.repository_identity_digest)
                if self.repository_identity_digest is not None
                else None
            ),
            "refs_digest": str(self.refs_digest) if self.refs_digest is not None else None,
            "evaluated_cursor": self.evaluated_cursor.to_wire(),
            "next_evaluation_at_ms": self.next_evaluation_at_ms,
            "smart_notes": [item.to_wire() for item in self.smart_notes],
            "verifications": [item.to_wire() for item in self.verifications],
            "sharing_judgments": [item.to_wire() for item in self.sharing_judgments],
            "recall": self.recall.to_wire() if self.recall is not None else None,
            "now_ms": self.now_ms,
        }


@dataclass(frozen=True, slots=True)
class KnowledgePublicationAuthority:
    capability_id: Id
    request: MutationRequest
    authority_resource: Id | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", Id(self.capability_id))
        if self.authority_resource is not None:
            object.__setattr__(
                self, "authority_resource", Id(self.authority_resource)
            )

    def to_wire(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "capability_id": str(self.capability_id),
            "request": self.request.to_wire(),
        }
        if self.authority_resource is not None:
            value["authority_resource"] = str(self.authority_resource)
        return value


@dataclass(frozen=True, slots=True)
class SmartNoteDecision:
    record_id: Id
    result: bool
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> SmartNoteDecision:
        raw = _object(value, "smart-note decision")
        result = raw.get("result")
        if not isinstance(result, bool):
            raise MemoryContractError("smart-note decision result must be boolean")
        return cls(
            record_id=Id(raw.get("record_id")),
            result=result,
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "decision cursor")),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeVerificationReceipt:
    record_id: Id
    state: VerificationState
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> KnowledgeVerificationReceipt:
        raw = _object(value, "knowledge verification receipt")
        return cls(
            record_id=Id(raw.get("record_id")),
            state=VerificationState(raw.get("state")),
            cursor=Cursor.from_wire(
                _object(raw.get("cursor"), "verification cursor")
            ),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRecallReceipt:
    record_id: Id
    revision_digest: Digest
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> KnowledgeRecallReceipt:
        raw = _object(value, "knowledge recall receipt")
        return cls(
            record_id=Id(raw.get("record_id")),
            revision_digest=Digest(raw.get("revision_digest")),
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "recall cursor")),
        )


@dataclass(frozen=True, slots=True)
class SharingJudgmentReceipt:
    judgment_id: Id
    record_id: Id
    revision_digest: Digest
    classification: SharingClassification
    trust_decision: TrustDecision
    policy_digest: Digest
    decided_at_ms: int

    @classmethod
    def from_wire(cls, value: object) -> SharingJudgmentReceipt:
        raw = _object(value, "sharing judgment receipt")
        return cls(
            judgment_id=Id(raw.get("judgment_id")),
            record_id=Id(raw.get("record_id")),
            revision_digest=Digest(raw.get("revision_digest")),
            classification=SharingClassification(raw.get("classification")),
            trust_decision=TrustDecision(raw.get("trust_decision")),
            policy_digest=Digest(raw.get("policy_digest")),
            decided_at_ms=_uint(raw.get("decided_at_ms"), "judgment timestamp"),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSharingReceipt:
    judgment: SharingJudgmentReceipt
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> KnowledgeSharingReceipt:
        raw = _object(value, "knowledge sharing receipt")
        return cls(
            judgment=SharingJudgmentReceipt.from_wire(raw.get("judgment")),
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "sharing cursor")),
        )


@dataclass(frozen=True, slots=True)
class KnowledgePublicationReceipt:
    job_id: Id
    kind: MaintenanceKind
    state: str
    output_cursor: Cursor
    output_digest: Digest
    source_digest: Digest
    smart_notes: tuple[SmartNoteDecision, ...]
    verifications: tuple[KnowledgeVerificationReceipt, ...]
    sharing_judgments: tuple[KnowledgeSharingReceipt, ...]
    recall: KnowledgeRecallReceipt | None
    invalidated_ids: tuple[Id, ...]
    finished_at_ms: int

    @classmethod
    def from_wire(cls, value: object) -> KnowledgePublicationReceipt:
        raw = _object(value, "knowledge publication receipt")
        state = raw.get("state")
        if state != "succeeded":
            raise MemoryContractError("knowledge publication did not succeed")
        recall = raw.get("recall")
        return cls(
            job_id=Id(raw.get("job_id")),
            kind=MaintenanceKind(raw.get("kind")),
            state=state,
            output_cursor=Cursor.from_wire(
                _object(raw.get("output_cursor"), "knowledge output cursor")
            ),
            output_digest=Digest(raw.get("output_digest")),
            source_digest=Digest(raw.get("source_digest")),
            smart_notes=tuple(
                SmartNoteDecision.from_wire(item)
                for item in _sequence(
                    raw.get("smart_notes"), "smart-note decisions", maximum=200
                )
            ),
            verifications=tuple(
                KnowledgeVerificationReceipt.from_wire(item)
                for item in _sequence(
                    raw.get("verifications"), "verification receipts", maximum=200
                )
            ),
            sharing_judgments=tuple(
                KnowledgeSharingReceipt.from_wire(item)
                for item in _sequence(
                    raw.get("sharing_judgments"), "sharing receipts", maximum=200
                )
            ),
            recall=(
                KnowledgeRecallReceipt.from_wire(recall)
                if recall is not None
                else None
            ),
            invalidated_ids=tuple(
                Id(item)
                for item in _sequence(
                    raw.get("invalidated_ids"), "invalidated ids", maximum=4096
                )
            ),
            finished_at_ms=_uint(raw.get("finished_at_ms"), "finished timestamp"),
        )


@dataclass(frozen=True, slots=True)
class ShareGrantDraft:
    id: Id
    owner_scope: Scope
    grantee_scope: Scope
    operations: tuple[GrantOperation, ...]
    categories: tuple[str, ...] | None
    expires_at_ms: int
    expected_revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", Id(self.id))
        operations = tuple(sorted({GrantOperation(item) for item in self.operations}, key=str))
        if not operations:
            raise MemoryContractError("share grant requires at least one operation")
        object.__setattr__(self, "operations", operations)
        if self.owner_scope == self.grantee_scope:
            raise MemoryContractError("share grant recipient must use a different scope")
        if self.categories is not None:
            categories = tuple(sorted({_text(item, "grant category", 128) for item in self.categories}))
            if not categories:
                raise MemoryContractError("share grant categories cannot be empty")
            object.__setattr__(self, "categories", categories)
        _uint(self.expires_at_ms, "expires_at_ms", minimum=1)
        _uint(self.expected_revision, "expected_revision")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "id": str(self.id),
            "owner_scope": self.owner_scope.to_wire(),
            "grantee_scope": self.grantee_scope.to_wire(),
            "operations": [operation.value for operation in self.operations],
            "categories": list(self.categories) if self.categories is not None else None,
            "expires_at_ms": self.expires_at_ms,
            "expected_revision": self.expected_revision,
        }


@dataclass(frozen=True, slots=True)
class ShareGrant:
    id: Id
    owner_scope: Scope
    grantee_scope: Scope
    operations: tuple[GrantOperation, ...]
    categories: tuple[str, ...] | None
    granted_at_ms: int
    expires_at_ms: int | None
    revoked_at_ms: int | None
    revision: int

    @classmethod
    def from_wire(cls, value: object) -> ShareGrant:
        raw = _object(value, "memory share grant")
        categories = raw.get("categories")
        return cls(
            id=Id(raw.get("id")),
            owner_scope=Scope.from_wire(_object(raw.get("owner_scope"), "owner scope")),
            grantee_scope=Scope.from_wire(_object(raw.get("grantee_scope"), "grantee scope")),
            operations=tuple(
                GrantOperation(item)
                for item in _sequence(raw.get("operations"), "grant operations", maximum=16)
            ),
            categories=(
                tuple(
                    _text(item, "grant category", 128)
                    for item in _sequence(categories, "grant categories", maximum=256)
                )
                if categories is not None
                else None
            ),
            granted_at_ms=_uint(raw.get("granted_at_ms"), "granted_at_ms"),
            expires_at_ms=(
                _uint(raw.get("expires_at_ms"), "expires_at_ms")
                if raw.get("expires_at_ms") is not None
                else None
            ),
            revoked_at_ms=(
                _uint(raw.get("revoked_at_ms"), "revoked_at_ms")
                if raw.get("revoked_at_ms") is not None
                else None
            ),
            revision=_uint(raw.get("revision"), "revision", minimum=1),
        )


@dataclass(frozen=True, slots=True)
class LegacySourceItem:
    item_key: Id
    category: LegacyKnowledgeCategory
    source_identity: str
    source_digest: Digest
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_key", Id(self.item_key))
        object.__setattr__(self, "category", LegacyKnowledgeCategory(self.category))
        _text(self.source_identity, "legacy source identity", 4_096)
        object.__setattr__(self, "source_digest", Digest(self.source_digest))
        payload = _json(self.payload, "legacy source payload")
        if not isinstance(payload, dict):
            raise MemoryContractError("legacy source payload must be an object")
        object.__setattr__(self, "payload", MappingProxyType(payload))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "item_key": str(self.item_key),
            "category": self.category.value,
            "source_identity": self.source_identity,
            "source_digest": str(self.source_digest),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class LegacyConversationLogEvidence:
    relative_path: str
    byte_length: int
    source_digest: Digest

    def __post_init__(self) -> None:
        _text(self.relative_path, "legacy conversation path", 4_096)
        if self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise MemoryContractError("legacy conversation path must be relative")
        _uint(self.byte_length, "legacy conversation byte_length")
        object.__setattr__(self, "source_digest", Digest(self.source_digest))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "relative_path": self.relative_path,
            "byte_length": self.byte_length,
            "source_digest": str(self.source_digest),
        }


@dataclass(frozen=True, slots=True)
class GideonLegacySnapshot:
    scope: Scope
    items: tuple[LegacySourceItem, ...]
    conversation_logs: tuple[LegacyConversationLogEvidence, ...]
    source_digest: Digest

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_digest", Digest(self.source_digest))
        identities = {(item.source_digest, item.item_key) for item in self.items}
        if len(identities) != len(self.items):
            raise MemoryContractError("legacy snapshot contains a duplicate source item")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "scope": self.scope.to_wire(),
            "items": [item.to_wire() for item in self.items],
            "conversation_logs": [item.to_wire() for item in self.conversation_logs],
            "source_digest": str(self.source_digest),
        }


@dataclass(frozen=True, slots=True)
class MaintenanceClaimProof:
    job_id: Id
    holder_id: str
    fencing_token: Digest

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", Id(self.job_id))
        _text(self.holder_id, "maintenance holder_id", 160)
        object.__setattr__(self, "fencing_token", Digest(self.fencing_token))

    def to_wire(self) -> dict[str, str]:
        return {
            "job_id": str(self.job_id),
            "holder_id": self.holder_id,
            "fencing_token": str(self.fencing_token),
        }


@dataclass(frozen=True, slots=True)
class MaintenanceClaimReceipt:
    job_id: Id
    kind: MaintenanceKind
    actor_scope: Scope
    target_scope: Scope
    required_operation: MemoryOperation
    input_cursor: Cursor
    input_digest: Digest
    config_digest: Digest
    budget: ModelBudget
    available_at_ms: int
    trace: Trace
    holder_id: str
    fencing_token: Digest
    expires_at_ms: int

    @property
    def proof(self) -> MaintenanceClaimProof:
        return MaintenanceClaimProof(self.job_id, self.holder_id, self.fencing_token)

    @classmethod
    def from_wire(cls, value: object) -> MaintenanceClaimReceipt:
        raw = _object(value, "maintenance claim")
        return cls(
            job_id=Id(raw.get("job_id")),
            kind=MaintenanceKind(raw.get("kind")),
            actor_scope=Scope.from_wire(_object(raw.get("actor_scope"), "actor scope")),
            target_scope=Scope.from_wire(_object(raw.get("target_scope"), "target scope")),
            required_operation=MemoryOperation(raw.get("required_operation")),
            input_cursor=Cursor.from_wire(_object(raw.get("input_cursor"), "input cursor")),
            input_digest=Digest(raw.get("input_digest")),
            config_digest=Digest(raw.get("config_digest")),
            budget=ModelBudget.from_wire(_object(raw.get("budget"), "maintenance budget")),
            available_at_ms=_uint(raw.get("available_at_ms"), "available_at_ms"),
            trace=Trace.from_wire(_object(raw.get("trace"), "maintenance trace")),
            holder_id=_text(raw.get("holder_id"), "maintenance holder_id", 160),
            fencing_token=Digest(raw.get("fencing_token")),
            expires_at_ms=_uint(raw.get("expires_at_ms"), "expires_at_ms"),
        )


@dataclass(frozen=True, slots=True)
class RecordRevision:
    number: int
    digest: Digest
    parent_digest: Digest | None
    content: str
    content_digest: Digest
    metadata: Mapping[str, JsonValue]
    author_scope_digest: Digest
    authored_at_ms: int

    @classmethod
    def from_wire(cls, value: object) -> RecordRevision:
        raw = _object(value, "record revision")
        metadata = _json(raw.get("metadata"), "revision metadata")
        if not isinstance(metadata, dict):
            raise MemoryContractError("revision metadata must be an object")
        parent = raw.get("parent_digest")
        return cls(
            number=_uint(raw.get("number"), "revision number", minimum=1),
            digest=Digest(raw.get("digest")),
            parent_digest=Digest(parent) if parent is not None else None,
            content=_text(raw.get("content"), "record content", 262_144),
            content_digest=Digest(raw.get("content_digest")),
            metadata=MappingProxyType(metadata),
            author_scope_digest=Digest(raw.get("author_scope_digest")),
            authored_at_ms=_uint(raw.get("authored_at_ms"), "authored_at_ms"),
        )


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: Id
    owner_scope_digest: Digest
    kind: RecordKind
    category: str
    status: RecordStatus
    current: RecordRevision
    normalized_content_digest: Digest
    importance: float
    confidence: float
    verification: VerificationState
    expires_at_ms: int | None
    retention_until_ms: int | None
    created_at_ms: int
    updated_at_ms: int
    deleted_at_ms: int | None

    @classmethod
    def from_wire(cls, value: object) -> MemoryRecord:
        raw = _object(value, "memory record")
        optional_times = []
        for name in ("expires_at_ms", "retention_until_ms", "deleted_at_ms"):
            item = raw.get(name)
            optional_times.append(_uint(item, name) if item is not None else None)
        return cls(
            id=Id(raw.get("id")),
            owner_scope_digest=Digest(raw.get("owner_scope_digest")),
            kind=RecordKind(raw.get("kind")),
            category=_text(raw.get("category"), "category", 128),
            status=RecordStatus(raw.get("status")),
            current=RecordRevision.from_wire(raw.get("current")),
            normalized_content_digest=Digest(raw.get("normalized_content_digest")),
            importance=_probability(raw.get("importance"), "importance"),
            confidence=_probability(raw.get("confidence"), "confidence"),
            verification=VerificationState(raw.get("verification")),
            expires_at_ms=optional_times[0],
            retention_until_ms=optional_times[1],
            created_at_ms=_uint(raw.get("created_at_ms"), "created_at_ms"),
            updated_at_ms=_uint(raw.get("updated_at_ms"), "updated_at_ms"),
            deleted_at_ms=optional_times[2],
        )


@dataclass(frozen=True, slots=True)
class RecordView:
    record: MemoryRecord | None
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> RecordView:
        raw = _object(value, "record view")
        record = raw.get("record")
        return cls(
            record=MemoryRecord.from_wire(record) if record is not None else None,
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "record cursor")),
        )


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    record: MemoryRecord | None
    records: tuple[MemoryRecord, ...]
    cursor: Cursor
    invalidated_ids: tuple[Id, ...]
    effect_state: EffectState
    result_digest: Digest | None

    @classmethod
    def from_wire(cls, value: object) -> MutationReceipt:
        raw = _object(value, "memory mutation receipt")
        record = raw.get("record")
        records = raw.get("records", [])
        state = EffectState(raw.get("effect_state", EffectState.COMMITTED.value))
        if state is not EffectState.COMMITTED:
            raise MemoryContractError("a successful mutation receipt must be committed")
        result_digest = raw.get("result_digest")
        return cls(
            record=MemoryRecord.from_wire(record) if record is not None else None,
            records=tuple(MemoryRecord.from_wire(item) for item in _sequence(records, "records", maximum=256)),
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "cursor")),
            invalidated_ids=tuple(Id(item) for item in _sequence(raw.get("invalidated_ids", []), "invalidated_ids", maximum=4096)),
            effect_state=state,
            result_digest=Digest(result_digest) if result_digest is not None else None,
        )


@dataclass(frozen=True, slots=True)
class SplitReceipt:
    source: MemoryRecord
    replacements: tuple[MutationReceipt, ...]
    cursor: Cursor
    invalidated_ids: tuple[Id, ...]

    @classmethod
    def from_wire(cls, value: object) -> SplitReceipt:
        raw = _object(value, "memory split receipt")
        return cls(
            source=MemoryRecord.from_wire(raw.get("source")),
            replacements=tuple(
                MutationReceipt.from_wire(item)
                for item in _sequence(raw.get("replacements"), "replacements", maximum=256)
            ),
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "cursor")),
            invalidated_ids=tuple(
                Id(item)
                for item in _sequence(
                    raw.get("invalidated_ids", []), "invalidated_ids", maximum=4096
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class RelocationReceipt:
    record: MemoryRecord
    source_cursor: Cursor
    destination_cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> RelocationReceipt:
        raw = _object(value, "memory relocation receipt")
        return cls(
            record=MemoryRecord.from_wire(raw.get("record")),
            source_cursor=Cursor.from_wire(
                _object(raw.get("source_cursor"), "source cursor")
            ),
            destination_cursor=Cursor.from_wire(
                _object(raw.get("destination_cursor"), "destination cursor")
            ),
        )


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[MemoryRecord, ...]
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> RecordPage:
        raw = _object(value, "record page")
        if "record" in raw:
            record = raw.get("record")
            records = [] if record is None else [record]
        else:
            records = _sequence(raw.get("records"), "records")
        return cls(
            records=tuple(MemoryRecord.from_wire(item) for item in records),
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "cursor")),
        )


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    mode: SearchMode
    limit: int
    trace: Trace
    now_ms: int
    semantic_required: bool = False
    candidate_limit_per_source: int = 100
    include_archived: bool = False
    visible_digests: tuple[Digest, ...] = ()
    query_vector: tuple[float, ...] | None = None
    vector_fingerprint: Digest | None = None
    from_ms: int | None = None
    to_ms: int | None = None
    sources: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    cursor: Cursor | None = None

    def __post_init__(self) -> None:
        _text(self.query, "query", 32_768)
        object.__setattr__(self, "mode", SearchMode(self.mode))
        _uint(self.now_ms, "now_ms")
        if not 1 <= self.limit <= 200:
            raise MemoryContractError("search limit must be between 1 and 200")
        if not 1 <= self.candidate_limit_per_source <= 1000:
            raise MemoryContractError("candidate limit must be between 1 and 1000")
        if self.query_vector is not None:
            if not self.query_vector or len(self.query_vector) > 65_536:
                raise MemoryContractError("query vector dimensions are invalid")
            for component in self.query_vector:
                _number(component, "query vector component")
        if (self.query_vector is None) != (self.vector_fingerprint is None):
            raise MemoryContractError("query vector and fingerprint must be supplied together")
        for name in ("from_ms", "to_ms"):
            value = getattr(self, name)
            if value is not None:
                _uint(value, name)

    def to_wire(self, scope: Scope) -> dict[str, JsonValue]:
        return {
            "query": self.query,
            "scope": scope.to_wire(),
            "mode": self.mode.value,
            "semantic_required": self.semantic_required,
            "limit": self.limit,
            "candidate_limit_per_source": self.candidate_limit_per_source,
            "include_archived": self.include_archived,
            "visible_digests": [str(item) for item in self.visible_digests],
            "query_vector": list(self.query_vector) if self.query_vector is not None else None,
            "vector_fingerprint": (
                str(self.vector_fingerprint) if self.vector_fingerprint is not None else None
            ),
            "now_ms": self.now_ms,
            "from_ms": self.from_ms,
            "to_ms": self.to_ms,
            "sources": list(self.sources),
            "kinds": list(self.kinds),
            "categories": list(self.categories),
            "cursor": self.cursor.to_wire() if self.cursor is not None else None,
            "trace": self.trace.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    lexical: float
    semantic: float
    fusion: float
    importance: float
    recency: float
    verification: float
    provenance: float
    stability: float
    usefulness: float
    decay: float
    contradiction: float
    total: float

    @classmethod
    def from_wire(cls, value: object) -> ScoreComponents:
        raw = _object(value, "search scores")
        return cls(
            **{
                name: _number(raw.get(name), f"score.{name}")
                for name in cls.__dataclass_fields__
            }
        )


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: Id
    scope: Scope
    readable_scopes: tuple[Scope, ...]
    content: str
    content_digest: Digest
    source: SourceKind
    kind: str
    category: str
    status: RecordStatus
    expires_at_ms: int | None
    source_time_ms: int | None
    importance: float
    verification: VerificationState
    provenance: tuple[Id, ...]
    contradiction_group: str | None
    decay: float
    useful_count: int
    not_useful_count: int
    scores: ScoreComponents

    @property
    def total_score(self) -> float:
        return self.scores.total

    @classmethod
    def from_wire(cls, value: object) -> SearchHit:
        raw = _object(value, "search hit")
        candidate = _object(raw.get("candidate", raw), "search candidate")
        expires_at_ms = candidate.get("expires_at_ms")
        source_time_ms = candidate.get("source_time_ms")
        contradiction_group = candidate.get("contradiction_group")
        if expires_at_ms is not None:
            expires_at_ms = _uint(expires_at_ms, "hit expires_at_ms")
        if source_time_ms is not None:
            source_time_ms = _uint(source_time_ms, "hit source_time_ms")
        if contradiction_group is not None:
            contradiction_group = _text(
                contradiction_group, "hit contradiction group", 160
            )
        return cls(
            id=Id(candidate.get("id")),
            scope=Scope.from_wire(_object(candidate.get("scope"), "hit scope")),
            readable_scopes=tuple(
                Scope.from_wire(_object(item, "readable scope"))
                for item in _sequence(candidate.get("readable_scopes"), "readable scopes")
            ),
            content=_text(candidate.get("content"), "hit content", 4 * 1024 * 1024),
            content_digest=Digest(candidate.get("content_digest")),
            source=SourceKind(candidate.get("source")),
            kind=_text(candidate.get("kind"), "hit kind", 128),
            category=_text(candidate.get("category"), "hit category", 128),
            status=RecordStatus(candidate.get("status")),
            expires_at_ms=expires_at_ms,
            source_time_ms=source_time_ms,
            importance=_probability(candidate.get("importance"), "hit importance"),
            verification=VerificationState(candidate.get("verification")),
            provenance=tuple(
                Id(item)
                for item in _sequence(candidate.get("provenance"), "hit provenance")
            ),
            contradiction_group=contradiction_group,
            decay=_probability(candidate.get("decay"), "hit decay"),
            useful_count=_uint(candidate.get("useful_count"), "hit useful_count"),
            not_useful_count=_uint(
                candidate.get("not_useful_count"), "hit not_useful_count"
            ),
            scores=ScoreComponents.from_wire(raw.get("scores")),
        )


@dataclass(frozen=True, slots=True)
class SearchResponse:
    hits: tuple[SearchHit, ...]
    cursor: Cursor
    suppressed: Mapping[str, int]
    degraded: bool
    degradation_reason: str | None
    trace: Trace

    @classmethod
    def from_wire(cls, value: object, expected_trace: Trace | None = None) -> SearchResponse:
        raw = _object(value, "search response")
        suppressed = _object(raw.get("suppressed"), "suppression counts")
        counts = {
            name: _uint(suppressed.get(name), f"suppressed.{name}")
            for name in ("unauthorized", "state", "stale", "visible", "duplicate")
        }
        degraded = raw.get("degraded")
        reason = raw.get("degradation_reason")
        if not isinstance(degraded, bool) or (reason is not None and not isinstance(reason, str)):
            raise MemoryContractError("search degradation state is invalid")
        trace = Trace.from_wire(_object(raw.get("trace"), "search trace"))
        if expected_trace is not None and trace != expected_trace:
            raise MemoryContractError("search response trace does not match the request")
        return cls(
            hits=tuple(SearchHit.from_wire(item) for item in _sequence(raw.get("hits"), "hits")),
            cursor=Cursor.from_wire(_object(raw.get("cursor"), "cursor")),
            suppressed=MappingProxyType(counts),
            degraded=degraded,
            degradation_reason=reason,
            trace=trace,
        )


@dataclass(frozen=True, slots=True)
class ScopedSearchResponse:
    """Presentation union retaining each independently authorized native result."""
    sources: tuple[tuple[Scope, SearchResponse], ...]
    limit: int

    def __post_init__(self) -> None:
        if not 1 <= len(self.sources) <= 2 or len({scope for scope, _ in self.sources}) != len(self.sources):
            raise MemoryContractError("search sources must be distinct authorized scopes")
        if any(hit.scope != scope for scope, response in self.sources for hit in response.hits):
            raise MemoryContractError("search hit does not match its authorized source scope")

    @property
    def hits(self) -> tuple[SearchHit, ...]:
        return tuple(sorted((hit for _, response in self.sources for hit in response.hits), key=lambda hit: (-hit.total_score, str(hit.id))))[:self.limit]

    @property
    def cursor(self) -> Cursor:
        return self.sources[0][1].cursor

    @property
    def scope_cursors(self) -> tuple[dict[str, object], ...]:
        return tuple({"scope": scope.to_wire(), "cursor": response.cursor.to_wire(), "trace": response.trace.to_wire()} for scope, response in self.sources)

    @property
    def trace(self) -> Trace:
        return self.sources[0][1].trace

    @property
    def suppressed(self) -> Mapping[str, int]:
        return MappingProxyType({name: sum(response.suppressed.get(name, 0) for _, response in self.sources) for name in {name for _, response in self.sources for name in response.suppressed}})

    @property
    def degraded(self) -> bool:
        return any(response.degraded for _, response in self.sources)

    @property
    def degradation_reason(self) -> str | None:
        reasons = sorted({response.degradation_reason for _, response in self.sources if response.degradation_reason})
        return "; ".join(reasons) or None


@dataclass(frozen=True, slots=True)
class ServiceResult:
    trace: Trace
    result: Mapping[str, JsonValue]

    @classmethod
    def from_wire(cls, value: object, expected_trace: Trace) -> ServiceResult:
        raw = _object(value, "memory service response")
        trace = Trace.from_wire(_object(raw.get("trace"), "trace"))
        if trace != expected_trace:
            raise MemoryContractError("memory response trace does not match the request")
        result = _json(raw.get("result"), "memory result")
        if not isinstance(result, dict):
            raise MemoryContractError("memory result must be an object")
        return cls(trace, MappingProxyType(result))


@dataclass(frozen=True, slots=True)
class MemoryHealth:
    state: str
    schema_version: int
    durable: bool
    lexical_available: bool
    schema_digest: Digest

    @classmethod
    def from_wire(cls, value: object) -> MemoryHealth:
        raw = _object(value, "memory health")
        state = raw.get("state")
        if state not in ("ready", "draining"):
            raise MemoryContractError("memory health state is invalid")
        durable = raw.get("durable")
        lexical = raw.get("lexical_available")
        if not isinstance(durable, bool) or not isinstance(lexical, bool):
            raise MemoryContractError("memory health availability flags are invalid")
        return cls(
            state=state,
            schema_version=_uint(raw.get("schema_version"), "schema_version", minimum=1),
            durable=durable,
            lexical_available=lexical,
            schema_digest=Digest(raw.get("schema_digest")),
        )


@dataclass(frozen=True, slots=True)
class MemoryDiagnostics:
    schema_version: int
    record_count: int
    stale_record_count: int
    embedding_count: int
    queued_job_count: int
    active_lease_count: int
    memory_fts_count: int | None = None
    source_fts_count: int | None = None
    budget_job_count: int | None = None
    budget_reservation_count: int | None = None
    budget_unknown_usage_count: int | None = None
    budget_state: str = "disabled"
    recovery_state: str = "degraded"

    def __post_init__(self) -> None:
        if self.budget_state not in {"available", "disabled"}:
            raise MemoryContractError("memory budget diagnostic state is invalid")
        if self.recovery_state not in {"ready", "degraded"}:
            raise MemoryContractError("memory recovery diagnostic state is invalid")

    @classmethod
    def from_wire(cls, value: object) -> MemoryDiagnostics:
        raw = _object(value, "memory diagnostics")
        required_counts = {
            "schema_version",
            "record_count",
            "stale_record_count",
            "embedding_count",
            "queued_job_count",
            "active_lease_count",
        }
        optional_counts = {
            "memory_fts_count",
            "source_fts_count",
            "budget_job_count",
            "budget_reservation_count",
            "budget_unknown_usage_count",
        }
        allowed = required_counts | optional_counts | {"budget_state", "recovery_state"}
        unexpected = set(raw) - allowed
        if unexpected:
            raise MemoryContractError("memory diagnostics contain unexpected fields")
        missing = required_counts - set(raw)
        if missing:
            raise MemoryContractError("memory diagnostics are incomplete")
        counts = {
            name: _uint(
                raw.get(name),
                name,
                minimum=1 if name == "schema_version" else 0,
            )
            for name in required_counts
        }
        counts.update(
            {
                name: _uint(raw.get(name), name) if name in raw else None
                for name in optional_counts
            }
        )
        return cls(
            **counts,
            budget_state=str(raw.get("budget_state", "disabled")),
            recovery_state=str(raw.get("recovery_state", "degraded")),
        )


@dataclass(frozen=True, slots=True)
class PrivateScopeReceipt:
    """Native-issued private execution scope; carries no persistent memory grant."""

    scope_id: Id
    actor_scope: Scope
    scope: Scope
    capability_id: Id
    origin_session_key: Id
    original_actor: Id
    memory_mode: str
    expires_at_ms: int
    retired: bool

    @classmethod
    def from_wire(cls, value: object) -> PrivateScopeReceipt:
        fields = {"scope_id", "actor_scope", "scope", "capability_id", "origin_session_key", "original_actor", "memory_mode", "expires_at_ms", "retired"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise MemoryContractError("private scope receipt fields are invalid")
        actor, scope = Scope.from_wire(value["actor_scope"]), Scope.from_wire(value["scope"])
        expiry = value["expires_at_ms"]
        if actor.owner_id != scope.owner_id or actor.project_id != scope.project_id or scope.workspace_id != value["scope_id"] or actor == scope:
            raise MemoryContractError("private scope does not belong to its issuer")
        if value["memory_mode"] not in {"temporary", "incognito"} or type(expiry) is not int or not 0 < expiry <= 9_007_199_254_740_991 or type(value["retired"]) is not bool:
            raise MemoryContractError("private scope lifetime is invalid")
        return cls(Id(value["scope_id"]), actor, scope, Id(value["capability_id"]), Id(value["origin_session_key"]), Id(value["original_actor"]), value["memory_mode"], expiry, value["retired"])


@dataclass(frozen=True, slots=True)
class AppScopeReceipt:
    """A native app namespace and its current activation grant."""
    scope_id: Id
    actor_scope: Scope
    scope: Scope
    capability_id: Id
    app_name: str
    manifest_digest: Digest
    epoch: int
    expires_at_ms: int
    revoked: bool

    @classmethod
    def from_wire(cls, value: object) -> AppScopeReceipt:
        fields = {'scope_id','actor_scope','scope','capability_id','app_name','manifest_digest','epoch','expires_at_ms','revoked'}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise MemoryContractError('app namespace receipt fields are invalid')
        actor, scope = Scope.from_wire(value['actor_scope']), Scope.from_wire(value['scope'])
        if actor.owner_id != scope.owner_id or actor.project_id != scope.project_id or actor == scope or scope.workspace_id != value['scope_id']:
            raise MemoryContractError('app namespace owner is invalid')
        if type(value['epoch']) is not int or not 0 < value['epoch'] <= 9_007_199_254_740_991 or type(value['expires_at_ms']) is not int or not 0 < value['expires_at_ms'] <= 9_007_199_254_740_991 or type(value['revoked']) is not bool:
            raise MemoryContractError('app namespace activation is invalid')
        from gideon.extensions.apps.manifest import KEBAB_RE
        if not isinstance(value['app_name'], str) or not KEBAB_RE.fullmatch(value['app_name']):
            raise MemoryContractError('app namespace name is invalid')
        return cls(Id(value['scope_id']),actor,scope,Id(value['capability_id']),value['app_name'],Digest(value['manifest_digest']),value['epoch'],value['expires_at_ms'],value['revoked'])


@dataclass(frozen=True, slots=True)
class OwnerWordCapture:
    history_session_id: Id
    source_event_id: Id
    source_digest: Digest
    original_actor: Mapping[str, str]
    effective_actor: Mapping[str, str]
    ingress_event_id: str
    ingress_own_digest: Digest
    own_text: str
    capture_kind: str = "owner_words"

    @classmethod
    def from_verified(cls, capture) -> OwnerWordCapture:
        from gideon.security.capture_origin import validate_capture
        from gideon.security.approval_answer import principal_record
        verified = validate_capture(capture)
        if verified is None:
            raise PermissionError("owner-word capture is no longer verified")
        return cls(verified.history_session_id, verified.native_source_event_id,
                   verified.native_source_digest, principal_record(verified.original_actor),
                   principal_record(verified.effective_work_actor), verified.ingress_event_id,
                   Digest(verified.ingress_own_digest), verified.own_text)

    def to_wire(self) -> dict[str, JsonValue]:
        return {"history_session_id": str(self.history_session_id),
                "source_event_id": str(self.source_event_id), "source_digest": str(self.source_digest),
                "original_actor": dict(self.original_actor), "effective_actor": dict(self.effective_actor),
                "ingress_event_id": self.ingress_event_id, "ingress_own_digest": str(self.ingress_own_digest),
                "own_text": self.own_text, "capture_kind": self.capture_kind}


@dataclass(frozen=True, slots=True)
class ChatRetraction:
    deleted_ids: tuple[Id, ...]
    retained_unproven: int
    retained_independent: int
    cursor: Cursor

    @classmethod
    def from_wire(cls, value: object) -> ChatRetraction:
        body = _object(value, "chat retraction")
        return cls(tuple(Id(item) for item in body["deleted_ids"]),
                   _uint(body["retained_unproven"], "retained_unproven"),
                   _uint(body["retained_independent"], "retained_independent"),
                   Cursor.from_wire(body["cursor"]))
