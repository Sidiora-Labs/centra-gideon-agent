from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.cognition.memory_service import MemoryService

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from .contracts import GideonLegacySnapshot as ContractLegacySnapshot
from .contracts import LegacyConversationLogEvidence as ContractConversationLogEvidence
from .contracts import LegacyKnowledgeCategory as ContractKnowledgeCategory
from .contracts import LegacySourceItem as ContractSourceItem
from .contracts import (
    MutationRequest,
)
from .foundation import Cursor, Digest, Id, Scope
from .history import SourceAdapter
from .memory_client import MemoryClient
from .portability import (
    ContextExportBundle,
    ContextImportStore,
    ContextMigrationCheckpoint,
    ContextMigrationCoordinator,
    ContextPortabilityEntryKind,
    ContextPortabilityError,
    validate_context_export,
)

if TYPE_CHECKING:
    from .writer import WriterCoordinator, WriterSnapshot


MIGRATION_SCHEMA_VERSION = 1
IMPORT_VERSION = 1


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class KnowledgeCategory(str, Enum):
    PAGE = "page"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    LESSON = "lesson"
    SLOT = "slot"
    GRAPH_LINK = "graph_link"
    AUDIT_EVENT = "audit_event"
    SUMMARY = "summary"


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MigrationError(
            "INVALID_JSON", "migration data is not canonical JSON"
        ) from exc


def canonical_digest(value: object) -> Digest:
    return Digest.sha256(canonical_bytes(value))


def _legacy_id(value: str) -> Id:
    try:
        return Id(value)
    except ValueError:
        prefix = value.partition(":")[0]
        try:
            Id(prefix)
        except ValueError:
            prefix = "legacy"
        return Id(f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}")


def _contains_forbidden_source_field(value: object) -> bool:
    forbidden = (
        "credential",
        "secret",
        "api_key",
        "access_token",
        "refresh_token",
        "private_key",
        "writer_lease",
        "fencing_token",
    )
    if isinstance(value, Mapping):
        return any(
            any(term in str(key).lower() for term in forbidden)
            or _contains_forbidden_source_field(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_source_field(child) for child in value)
    return False


def _json_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return os.fspath(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise MigrationError(
        "INVALID_SOURCE", f"unsupported source value {type(value).__name__}"
    )


def _decoded(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _content(value: object) -> str:
    return value if isinstance(value, str) else canonical_bytes(value).decode("utf-8")


def _semantic_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    value = _decoded(row.get("value", row.get("value_json")))
    return {
        "legacy_id": str(row.get("key") or row.get("id")),
        "value": _json_value(value),
        "content": _content(_json_value(value)),
        "confidence": float(row.get("confidence", 0.5) or 0.5),
        "source": str(row.get("source") or "gideon_legacy"),
        "scope": str(row.get("scope") or "global"),
        "scope_ref": row.get("scope_ref"),
        "category": row.get("category"),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
    }


def _episodic_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_tags = _decoded(row.get("tags", []))
    tags = raw_tags if isinstance(raw_tags, list) else []
    return {
        "legacy_id": str(row.get("id")),
        "conversation_id": str(row.get("conversation_id") or ""),
        "content": str(row.get("text") or ""),
        "tags": [str(value) for value in tags],
        "importance": float(row.get("importance", 0.5) or 0.5),
        "contributor": str(row.get("contributor") or ""),
        "created_at": str(row.get("created_at") or ""),
        "last_accessed_at": row.get("last_accessed_at"),
    }


def _audit_payload(row: Mapping[str, Any], fallback_id: int) -> dict[str, Any]:
    return {
        "legacy_id": str(row.get("id") or row.get("event_id") or fallback_id),
        "event_type": str(row.get("event_type") or "unknown"),
        "memory_type": str(row.get("memory_type") or "unknown"),
        "memory_key": str(row.get("memory_key") or ""),
        "old_value": row.get("old_value"),
        "new_value": row.get("new_value"),
        "source": str(row.get("source") or "gideon_legacy"),
        "created_at": str(row.get("created_at") or ""),
        "undone_at": row.get("undone_at"),
    }


@dataclass(frozen=True, slots=True)
class SourceItem:
    item_key: Id
    category: KnowledgeCategory
    source_identity: str
    payload: Mapping[str, Any]
    source_digest: Digest

    @classmethod
    def build(
        cls,
        item_key: str,
        category: KnowledgeCategory,
        source_identity: str,
        payload: Mapping[str, Any],
    ) -> SourceItem:
        normalized = _json_value(payload)
        if not isinstance(normalized, Mapping):
            raise MigrationError("INVALID_SOURCE", "source payload must be an object")
        if _contains_forbidden_source_field(normalized):
            raise MigrationError(
                "FORBIDDEN_SOURCE_STATE",
                "source payload contains credential or writer authority state",
            )
        if not source_identity or len(source_identity) > 4096:
            raise MigrationError("INVALID_SOURCE", "source identity is invalid")
        return cls(
            item_key=_legacy_id(item_key),
            category=KnowledgeCategory(category),
            source_identity=source_identity,
            payload=normalized,
            source_digest=canonical_digest(normalized),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "item_key": str(self.item_key),
            "category": self.category.value,
            "source_identity": self.source_identity,
            "source_digest": str(self.source_digest),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class ConversationLogEvidence:
    relative_path: str
    byte_length: int
    source_digest: Digest

    def to_mapping(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "byte_length": self.byte_length,
            "source_digest": str(self.source_digest),
            "disposition": "retained_in_conversation_log",
        }

    def to_wire(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "byte_length": self.byte_length,
            "source_digest": str(self.source_digest),
        }


@dataclass(frozen=True, slots=True)
class GideonSourceSnapshot:
    scope: Scope
    items: tuple[SourceItem, ...]
    conversation_logs: tuple[ConversationLogEvidence, ...]
    source_digest: Digest

    @classmethod
    def build(
        cls,
        scope: Scope,
        items: Iterable[SourceItem],
        conversation_logs: Iterable[ConversationLogEvidence] = (),
    ) -> GideonSourceSnapshot:
        ordered_items = tuple(
            sorted(items, key=lambda item: (item.category.value, str(item.item_key)))
        )
        keys = [(item.source_digest, item.item_key) for item in ordered_items]
        if len(set(keys)) != len(keys):
            raise MigrationError(
                "DUPLICATE_SOURCE", "source item identity is duplicated"
            )
        logs = tuple(sorted(conversation_logs, key=lambda item: item.relative_path))
        material = {
            "scope": scope.to_wire(),
            "items": [item.to_mapping() for item in ordered_items],
            "conversation_logs": [item.to_mapping() for item in logs],
        }
        return cls(scope, ordered_items, logs, canonical_digest(material))

    def counts(self) -> dict[str, int]:
        result = {category.value: 0 for category in KnowledgeCategory}
        for item in self.items:
            result[item.category.value] += 1
        return result

    def to_wire(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_wire(),
            "items": [item.to_mapping() for item in self.items],
            "conversation_logs": [item.to_wire() for item in self.conversation_logs],
            "source_digest": str(self.source_digest),
        }

    def to_contract(self) -> ContractLegacySnapshot:
        return ContractLegacySnapshot(
            self.scope,
            tuple(
                ContractSourceItem(
                    item.item_key,
                    ContractKnowledgeCategory(item.category.value),
                    item.source_identity,
                    item.source_digest,
                    item.payload,
                )
                for item in self.items
            ),
            tuple(
                ContractConversationLogEvidence(
                    item.relative_path, item.byte_length, item.source_digest
                )
                for item in self.conversation_logs
            ),
            self.source_digest,
        )


def fingerprint_conversation_logs(
    root: str | os.PathLike[str],
) -> tuple[ConversationLogEvidence, ...]:
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise MigrationError(
            "INVALID_CONVERSATION_LOG", "ConversationLog root is not a directory"
        )
    evidence: list[ConversationLogEvidence] = []
    for candidate in sorted(base.rglob("*.jsonl")):
        if candidate.is_symlink() or not stat.S_ISREG(candidate.stat().st_mode):
            raise MigrationError(
                "INVALID_CONVERSATION_LOG", "ConversationLog contains an unsafe entry"
            )
        data = candidate.read_bytes()
        evidence.append(
            ConversationLogEvidence(
                relative_path=candidate.relative_to(base).as_posix(),
                byte_length=len(data),
                source_digest=Digest.sha256(data),
            )
        )
    return tuple(evidence)


class GideonMemorySource:
    """Read every legacy MemoryService surface without copying transcript JSONL."""

    def __init__(
        self, scope: Scope, memory_service: MemoryService, journal: object | None = None
    ) -> None:
        self.scope = scope
        self.memory = memory_service
        self.journal = journal

    def snapshot(
        self, conversation_log_root: str | os.PathLike[str] | None = None
    ) -> GideonSourceSnapshot:
        items: list[SourceItem] = []
        if self.journal is not None:
            for key, reader in (
                ("preferences", "read_preferences"),
                ("projects", "read_projects"),
            ):
                content = getattr(self.journal, reader)()
                items.append(
                    SourceItem.build(
                        f"page:{key}",
                        KnowledgeCategory.PAGE,
                        f"MemoryJournal:{key}",
                        {"name": key, "content": content},
                    )
                )
        record_references: list[str] = []
        for row in self.memory.get_all_semantic():
            key = str(row.get("key") or row.get("id"))
            if key.startswith("lesson.") or key.startswith("slot."):
                continue
            items.append(
                SourceItem.build(
                    f"semantic:{key}",
                    KnowledgeCategory.SEMANTIC,
                    f"SemanticArchive:{key}",
                    _semantic_payload(row),
                )
            )
            record_references.append(f"sem:{key}")
        offset = 0
        while True:
            page = self.memory.episodic_list(limit=500, offset=offset)
            for row in page:
                key = str(row.get("id"))
                items.append(
                    SourceItem.build(
                        f"episodic:{key}",
                        KnowledgeCategory.EPISODIC,
                        f"SemanticArchive:episodic:{key}",
                        _episodic_payload(row),
                    )
                )
                record_references.append(f"epi:{key}")
            if len(page) < 500:
                break
            offset += len(page)
        existing = {str(item.item_key) for item in items}
        for index, row in enumerate(self.memory.get_lessons()):
            key = str(row.get("key") or row.get("id") or index)
            item_key = f"lesson:{key}"
            if item_key not in existing:
                items.append(
                    SourceItem.build(
                        item_key,
                        KnowledgeCategory.LESSON,
                        f"SemanticArchive:lesson:{key}",
                        _semantic_payload(row),
                    )
                )
        for index, row in enumerate(self.memory.slots()):
            key = str(row.get("id") or row.get("name") or index)
            items.append(
                SourceItem.build(
                    f"slot:{key}",
                    KnowledgeCategory.SLOT,
                    f"MemoryService:slot:{key}",
                    row,
                )
            )
        for index, row in enumerate(self.memory.graph_entities()):
            key = str(row.get("id") or index)
            items.append(
                SourceItem.build(
                    f"graph:entity:{key}",
                    KnowledgeCategory.GRAPH_LINK,
                    f"MemoryGraph:entity:{key}",
                    {"graph_kind": "entity", "value": row},
                )
            )
        seen_links: set[str] = set()
        for reference in record_references:
            for index, row in enumerate(self.memory.graph_record_links(reference)):
                key = str(row.get("id") or f"{reference}:{index}")
                if key in seen_links:
                    continue
                seen_links.add(key)
                items.append(
                    SourceItem.build(
                        f"graph:edge:{key}",
                        KnowledgeCategory.GRAPH_LINK,
                        f"MemoryGraph:edge:{key}",
                        {"graph_kind": "edge", "value": row},
                    )
                )
        offset = 0
        while True:
            page = self.memory.get_events(limit=500, offset=offset)
            for index, row in enumerate(page):
                key = str(row.get("id") or row.get("event_id") or offset + index)
                items.append(
                    SourceItem.build(
                        f"audit:{key}",
                        KnowledgeCategory.AUDIT_EVENT,
                        f"SemanticArchive:event:{key}",
                        _audit_payload(row, offset + index),
                    )
                )
            if len(page) < 500:
                break
            offset += len(page)
        logs = (
            fingerprint_conversation_logs(conversation_log_root)
            if conversation_log_root is not None
            else ()
        )
        return GideonSourceSnapshot.build(self.scope, items, logs)


@dataclass(frozen=True, slots=True)
class ImportReceipt:
    source_digest: Digest
    cursor: Cursor
    imported: int
    existing: int
    destination_digest: Digest


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    source_digest: Digest
    destination_digest: Digest
    validation_digest: Digest
    cursor: Cursor
    counts: Mapping[str, int]
    context_digests: tuple[Digest, ...]


@dataclass(frozen=True, slots=True)
class AuthorityReceipt:
    authority: str
    writer_epoch: int
    checkpoint_digest: Digest
    target_retained: bool = True


@dataclass(frozen=True, slots=True)
class DestinationValidation:
    batch_id: Id
    state: str
    migration_source_digest: Digest
    destination_digest: Digest
    cursor: Cursor
    record_count: int
    item_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "batch_id", Id(self.batch_id))
        object.__setattr__(
            self, "migration_source_digest", Digest(self.migration_source_digest)
        )
        object.__setattr__(self, "destination_digest", Digest(self.destination_digest))
        if self.state != "applied":
            raise MigrationError(
                "DESTINATION_NOT_APPLIED", "Rust memory import is not applied"
            )
        for value in (self.record_count, self.item_count):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MigrationError(
                    "INVALID_DESTINATION", "destination counts are invalid"
                )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "batch_id": str(self.batch_id),
            "state": self.state,
            "migration_source_digest": str(self.migration_source_digest),
            "destination_digest": str(self.destination_digest),
            "cursor": self.cursor.to_wire(),
            "record_count": self.record_count,
            "item_count": self.item_count,
        }


class RustLegacyImport:
    """Import legacy records through MemoryApi and retain only validation evidence."""

    def __init__(
        self,
        memory: MemoryClient,
        *,
        snapshot: GideonSourceSnapshot,
        request: MutationRequest,
        batch_id: Id,
        verification_request: MutationRequest,
        verification_export_id: Id,
    ) -> None:
        if memory.scope != snapshot.scope:
            raise MigrationError(
                "SCOPE_MISMATCH", "memory client scope differs from migration"
            )
        self.memory = memory
        self.snapshot = snapshot
        self.request = request
        self.batch_id = Id(batch_id)
        self.verification_request = verification_request
        self.verification_export_id = Id(verification_export_id)
        self._validation: DestinationValidation | None = None

    async def prepare(self) -> DestinationValidation:
        if self._validation is not None:
            return self._validation
        receipt = await self.memory.import_legacy(
            self.request,
            batch_id=self.batch_id,
            snapshot=self.snapshot.to_contract(),
        )
        if (
            receipt.source_digest != self.snapshot.source_digest
            or receipt.batch.batch_id != self.batch_id
            or receipt.batch.state != "applied"
            or receipt.batch.rejected
        ):
            raise MigrationError(
                "DESTINATION_VALIDATION_FAILED",
                "Rust legacy import receipt does not prove an applied source snapshot",
            )
        exported = await self.memory.export_scope(
            self.verification_request,
            export_id=self.verification_export_id,
            include_grants=False,
        )
        manifest = exported.manifest
        if (
            manifest.scope != self.snapshot.scope
            or manifest.stream_digest != receipt.destination_digest
            or manifest.record_count != receipt.record_count
            or manifest.item_count != receipt.item_count
        ):
            raise MigrationError(
                "DESTINATION_VALIDATION_FAILED",
                "actual Rust memory export differs from the legacy import receipt",
            )
        self._validation = DestinationValidation(
            batch_id=self.batch_id,
            state=receipt.batch.state,
            migration_source_digest=self.snapshot.source_digest,
            destination_digest=manifest.stream_digest,
            cursor=manifest.cursor,
            record_count=manifest.record_count,
            item_count=manifest.item_count,
        )
        return self._validation

    def require_validation(self) -> DestinationValidation:
        if self._validation is None:
            raise MigrationError(
                "WRITER_BARRIER_REQUIRED",
                "destination import must run inside the writer preparation barrier",
            )
        return self._validation


class MigrationStore:
    def __init__(self, path: str | os.PathLike[str], scope: Scope) -> None:
        self.path = Path(path)
        self.scope = scope
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists() and (
            self.path.is_symlink() or not stat.S_ISREG(self.path.stat().st_mode)
        ):
            raise MigrationError(
                "INVALID_STORE", "migration store must be a regular file"
            )
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._initialize()
        except BaseException:
            self._db.close()
            raise
        os.chmod(self.path, 0o600)

    def _initialize(self) -> None:
        self._db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS migration_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                schema_version INTEGER NOT NULL,
                scope_json TEXT NOT NULL,
                authority TEXT NOT NULL,
                writer_epoch INTEGER NOT NULL,
                source_digest TEXT,
                validation_digest TEXT,
                destination_digest TEXT
            );
            CREATE TABLE IF NOT EXISTS imported_items (
                sequence INTEGER PRIMARY KEY,
                item_key TEXT NOT NULL,
                category TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                destination_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                import_version INTEGER NOT NULL,
                previous_digest TEXT NOT NULL,
                entry_digest TEXT NOT NULL,
                UNIQUE(source_digest, item_key)
            );
            CREATE TABLE IF NOT EXISTS migration_effects (
                effect_id TEXT PRIMARY KEY,
                effect_state TEXT NOT NULL CHECK(effect_state IN ('not_started','committed','unknown'))
            );
            CREATE TABLE IF NOT EXISTS conversation_log_evidence (
                relative_path TEXT PRIMARY KEY,
                byte_length INTEGER NOT NULL,
                source_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cutover_markers (
                writer_epoch INTEGER PRIMARY KEY,
                authority TEXT NOT NULL,
                checkpoint_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS destination_validations (
                batch_id TEXT PRIMARY KEY,
                receipt_json TEXT NOT NULL,
                receipt_digest TEXT NOT NULL
            );
            COMMIT;
            """)
        scope_json = canonical_bytes(self.scope.to_wire()).decode("utf-8")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT schema_version, scope_json FROM migration_meta WHERE singleton=1"
            ).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO migration_meta VALUES (1, ?, ?, 'gideon', 1, NULL, NULL, NULL)",
                    (MIGRATION_SCHEMA_VERSION, scope_json),
                )
            elif int(row["schema_version"]) > MIGRATION_SCHEMA_VERSION:
                raise MigrationError(
                    "STORE_AHEAD", "migration store is newer than this runtime"
                )
            elif int(row["schema_version"]) < MIGRATION_SCHEMA_VERSION:
                raise MigrationError(
                    "MIGRATION_REQUIRED",
                    "older migration store is read-only pending explicit migration",
                )
            elif row["scope_json"] != scope_json:
                raise MigrationError(
                    "SCOPE_MISMATCH", "migration store belongs to another scope"
                )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> MigrationStore:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _destination(self, item: SourceItem) -> Digest:
        payload = {
            "scope": self.scope.to_wire(),
            "category": item.category.value,
            "item_key": str(item.item_key),
            "source_identity": item.source_identity,
            "source_digest": str(item.source_digest),
            "payload": dict(item.payload),
            "import_version": IMPORT_VERSION,
        }
        return canonical_digest(payload)

    def import_snapshot(self, snapshot: GideonSourceSnapshot) -> ImportReceipt:
        if snapshot.scope != self.scope:
            raise MigrationError(
                "SCOPE_MISMATCH", "source snapshot belongs to another scope"
            )
        prepared = [(item, self._destination(item)) for item in snapshot.items]
        self._db.execute("BEGIN IMMEDIATE")
        imported = existing_count = 0
        try:
            meta = self._db.execute(
                "SELECT authority, source_digest FROM migration_meta WHERE singleton=1"
            ).fetchone()
            if meta["authority"] != "gideon":
                raise MigrationError(
                    "ALREADY_CUT_OVER", "cannot import after authority cutover"
                )
            if meta["source_digest"] not in (None, str(snapshot.source_digest)):
                raise MigrationError(
                    "SOURCE_CHANGED", "migration source changed after import began"
                )
            tail = self._db.execute(
                "SELECT sequence, entry_digest FROM imported_items ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 0 if tail is None else int(tail["sequence"])
            previous = "0" * 64 if tail is None else str(tail["entry_digest"])
            for item, destination_digest in prepared:
                row = self._db.execute(
                    "SELECT destination_digest FROM imported_items WHERE source_digest=? AND item_key=?",
                    (str(item.source_digest), str(item.item_key)),
                ).fetchone()
                if row is not None:
                    if row["destination_digest"] != str(destination_digest):
                        raise MigrationError(
                            "IMPORT_CONFLICT",
                            "idempotency key resolves to different content",
                        )
                    existing_count += 1
                    continue
                sequence += 1
                entry_digest = canonical_digest(
                    {
                        "cursor": {"epoch": 1, "sequence": sequence},
                        "previous_digest": previous,
                        "destination_digest": str(destination_digest),
                    }
                )
                self._db.execute(
                    "INSERT INTO imported_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        sequence,
                        str(item.item_key),
                        item.category.value,
                        item.source_identity,
                        str(item.source_digest),
                        str(destination_digest),
                        "{}",
                        IMPORT_VERSION,
                        previous,
                        str(entry_digest),
                    ),
                )
                previous = str(entry_digest)
                imported += 1
            for evidence in snapshot.conversation_logs:
                row = self._db.execute(
                    "SELECT byte_length, source_digest FROM conversation_log_evidence WHERE relative_path=?",
                    (evidence.relative_path,),
                ).fetchone()
                expected = (evidence.byte_length, str(evidence.source_digest))
                if row is None:
                    self._db.execute(
                        "INSERT INTO conversation_log_evidence VALUES (?,?,?)",
                        (evidence.relative_path, *expected),
                    )
                elif tuple(row) != expected:
                    raise MigrationError(
                        "SOURCE_CHANGED", "ConversationLog evidence changed"
                    )
            if imported:
                self._db.execute(
                    "UPDATE migration_meta SET source_digest=?, validation_digest=NULL, destination_digest=? WHERE singleton=1",
                    (str(snapshot.source_digest), previous),
                )
            else:
                self._db.execute(
                    "UPDATE migration_meta SET source_digest=?, destination_digest=? WHERE singleton=1",
                    (str(snapshot.source_digest), previous),
                )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return ImportReceipt(
            snapshot.source_digest,
            Cursor(1, sequence),
            imported,
            existing_count,
            Digest(previous),
        )

    def validate(
        self,
        snapshot: GideonSourceSnapshot,
        *,
        context_bundles: Sequence[ContextExportBundle] = (),
        source_adapter: SourceAdapter | None = None,
    ) -> ValidationReceipt:
        if snapshot.scope != self.scope:
            raise MigrationError(
                "SCOPE_MISMATCH", "validation scope differs from target"
            )
        rows = self._db.execute(
            "SELECT * FROM imported_items ORDER BY sequence"
        ).fetchall()
        if len(rows) != len(snapshot.items):
            raise MigrationError(
                "COUNT_MISMATCH",
                "destination does not contain the full source inventory",
            )
        expected = {
            (str(item.source_digest), str(item.item_key)): item
            for item in snapshot.items
        }
        counts = {category.value: 0 for category in KnowledgeCategory}
        previous = "0" * 64
        for sequence, row in enumerate(rows, start=1):
            item = expected.get((row["source_digest"], row["item_key"]))
            if item is None or row["category"] != item.category.value:
                raise MigrationError(
                    "SOURCE_MISMATCH", "destination item has no exact source"
                )
            destination_digest = self._destination(item)
            if row["destination_digest"] != str(destination_digest):
                raise MigrationError(
                    "DIGEST_MISMATCH", "destination record digest is invalid"
                )
            if row["previous_digest"] != previous:
                raise MigrationError(
                    "CHAIN_MISMATCH", "destination digest chain is broken"
                )
            entry_digest = canonical_digest(
                {
                    "cursor": {"epoch": 1, "sequence": sequence},
                    "previous_digest": previous,
                    "destination_digest": row["destination_digest"],
                }
            )
            if row["entry_digest"] != str(entry_digest):
                raise MigrationError(
                    "CHAIN_MISMATCH", "destination entry digest is invalid"
                )
            previous = str(entry_digest)
            counts[row["category"]] += 1
        if counts != snapshot.counts():
            raise MigrationError("COUNT_MISMATCH", "knowledge category counts differ")
        stored_logs = tuple(
            (row["relative_path"], int(row["byte_length"]), row["source_digest"])
            for row in self._db.execute(
                "SELECT relative_path, byte_length, source_digest FROM conversation_log_evidence ORDER BY relative_path"
            )
        )
        expected_logs = tuple(
            (entry.relative_path, entry.byte_length, str(entry.source_digest))
            for entry in snapshot.conversation_logs
        )
        if stored_logs != expected_logs:
            raise MigrationError("SOURCE_MISMATCH", "ConversationLog evidence differs")
        context_digests: list[Digest] = []
        for bundle in context_bundles:
            validate_context_export(bundle, self.scope)
            if source_adapter is None:
                raise MigrationError(
                    "SOURCE_UNAVAILABLE",
                    "context validation requires ConversationLog source access",
                )
            for record in bundle.records:
                if record.kind is ContextPortabilityEntryKind.SOURCE_REFERENCE:
                    source = source_adapter.resolve(
                        self.scope,
                        bundle.binding.session_id,
                        Id(record.payload["source_event_id"]),
                    )
                    if Digest.sha256(source) != Digest(record.payload["source_digest"]):
                        raise MigrationError(
                            "SUMMARY_SOURCE_MISMATCH", "context source bytes changed"
                        )
            context_digests.append(canonical_digest(bundle.to_mapping()))
        validation = canonical_digest(
            {
                "scope": self.scope.to_wire(),
                "source_digest": str(snapshot.source_digest),
                "destination_digest": previous,
                "counts": counts,
                "conversation_logs": [
                    entry.to_mapping() for entry in snapshot.conversation_logs
                ],
                "context_digests": [str(value) for value in context_digests],
            }
        )
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "UPDATE migration_meta SET validation_digest=?, destination_digest=? WHERE singleton=1",
                (str(validation), previous),
            )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return ValidationReceipt(
            snapshot.source_digest,
            Digest(previous),
            validation,
            Cursor(1, len(rows)),
            counts,
            tuple(context_digests),
        )

    def status(self) -> dict[str, Any]:
        meta = self._db.execute(
            "SELECT * FROM migration_meta WHERE singleton=1"
        ).fetchone()
        count = int(
            self._db.execute("SELECT COUNT(*) FROM imported_items").fetchone()[0]
        )
        return {
            "scope": self.scope.to_wire(),
            "storage_version": int(meta["schema_version"]),
            "authority": meta["authority"],
            "writer_epoch": int(meta["writer_epoch"]),
            "source_digest": meta["source_digest"],
            "destination_digest": meta["destination_digest"],
            "validation_digest": meta["validation_digest"],
            "imported_items": count,
            "digest_health": "healthy" if meta["validation_digest"] else "unvalidated",
        }

    def record_effect(self, effect_id: str, effect_state: str) -> None:
        if effect_state not in {"not_started", "committed", "unknown"}:
            raise MigrationError("INVALID_EFFECT_STATE", "effect state is invalid")
        self._db.execute(
            "INSERT INTO migration_effects VALUES (?,?) ON CONFLICT(effect_id) DO UPDATE SET effect_state=excluded.effect_state",
            (str(Id(effect_id)), effect_state),
        )

    def record_destination_validation(
        self, validation: DestinationValidation
    ) -> Digest:
        status = self.status()
        if validation.migration_source_digest != Digest(status["source_digest"]):
            raise MigrationError(
                "SOURCE_DIGEST_MISMATCH",
                "Rust destination receipt does not bind this migration source",
            )
        receipt_json = canonical_bytes(validation.to_mapping()).decode("utf-8")
        receipt_digest = Digest.sha256(receipt_json.encode("utf-8"))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing = self._db.execute(
                "SELECT receipt_json, receipt_digest FROM destination_validations WHERE batch_id=?",
                (str(validation.batch_id),),
            ).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO destination_validations VALUES (?,?,?)",
                    (str(validation.batch_id), receipt_json, str(receipt_digest)),
                )
            elif tuple(existing) != (receipt_json, str(receipt_digest)):
                raise MigrationError(
                    "DESTINATION_CONFLICT",
                    "Rust destination batch identity was reused",
                )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return receipt_digest

    def _require_known_effects(self) -> None:
        if self._db.execute(
            "SELECT 1 FROM migration_effects WHERE effect_state='unknown' LIMIT 1"
        ).fetchone():
            raise MigrationError(
                "OUTCOME_UNKNOWN",
                "unknown effects must be reconciled before authority changes",
            )

    def authority_digest(self) -> Digest:
        status = self.status()
        return canonical_digest(
            {
                "scope": status["scope"],
                "authority": status["authority"],
                "writer_epoch": status["writer_epoch"],
                "destination_digest": status["destination_digest"],
                "validation_digest": status["validation_digest"],
            }
        )

    def cutover(
        self,
        *,
        expected_authority_digest: str,
        destination: DestinationValidation,
        writer: WriterSnapshot,
    ) -> AuthorityReceipt:
        self._require_known_effects()
        if str(self.authority_digest()) != expected_authority_digest:
            raise MigrationError(
                "AUTHORITY_CHANGED", "authority changed after plan review"
            )
        if not writer.owns_writes or writer.lease is None:
            raise MigrationError(
                "WRITER_BARRIER_REQUIRED",
                "Hypermid authority requires an acquired writer barrier",
            )
        if writer.lease.scope != self.scope:
            raise MigrationError(
                "SCOPE_MISMATCH", "writer lease belongs to another scope"
            )
        receipt_digest = self.record_destination_validation(destination)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            meta = self._db.execute(
                "SELECT * FROM migration_meta WHERE singleton=1"
            ).fetchone()
            if not meta["validation_digest"]:
                raise MigrationError(
                    "VALIDATION_REQUIRED",
                    "complete validation is required before cutover",
                )
            if meta["authority"] != "gideon":
                raise MigrationError(
                    "AUTHORITY_CONFLICT", "Gideon is no longer the writer"
                )
            epoch = writer.lease.fence_epoch
            if epoch <= int(meta["writer_epoch"]):
                raise MigrationError(
                    "STALE_FENCE", "writer barrier did not advance the epoch"
                )
            checkpoint = canonical_digest(
                {
                    "authority": "hypermid",
                    "writer_epoch": epoch,
                    "validation_digest": meta["validation_digest"],
                    "destination_receipt_digest": str(receipt_digest),
                    "destination_digest": str(destination.destination_digest),
                }
            )
            self._db.execute(
                "UPDATE migration_meta SET authority='hypermid', writer_epoch=? WHERE singleton=1",
                (epoch,),
            )
            self._db.execute(
                "INSERT INTO cutover_markers VALUES (?, 'hypermid', ?)",
                (epoch, str(checkpoint)),
            )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return AuthorityReceipt("hypermid", epoch, checkpoint)

    def rollback(
        self,
        *,
        expected_authority_digest: str,
        writer: WriterSnapshot,
    ) -> AuthorityReceipt:
        self._require_known_effects()
        if str(self.authority_digest()) != expected_authority_digest:
            raise MigrationError(
                "AUTHORITY_CHANGED", "authority changed after plan review"
            )
        restore = writer.restore_receipt
        if (
            writer.writer != "gideon"
            or writer.lease_state != "none"
            or restore is None
            or restore.scope != self.scope
            or writer.authority_epoch != restore.gideon_epoch
        ):
            raise MigrationError(
                "WRITER_HANDBACK_REQUIRED",
                "rollback requires a durable Gideon writer handback receipt",
            )
        self._db.execute("BEGIN IMMEDIATE")
        try:
            meta = self._db.execute(
                "SELECT * FROM migration_meta WHERE singleton=1"
            ).fetchone()
            if meta["authority"] != "hypermid":
                raise MigrationError(
                    "AUTHORITY_CONFLICT", "Hypermid is not the active writer"
                )
            if restore.prior_fence_epoch != int(meta["writer_epoch"]):
                raise MigrationError(
                    "STALE_FENCE", "writer handback does not release the active epoch"
                )
            epoch = restore.gideon_epoch
            if epoch <= restore.prior_fence_epoch:
                raise MigrationError(
                    "STALE_FENCE", "Gideon handback epoch is not newer"
                )
            checkpoint = canonical_digest(
                {
                    "authority": "gideon",
                    "writer_epoch": epoch,
                    "retained_digest": meta["destination_digest"],
                    "restore_request_id": str(restore.request_id),
                    "journal_digest": str(restore.journal_digest),
                }
            )
            self._db.execute(
                "UPDATE migration_meta SET authority='gideon', writer_epoch=? WHERE singleton=1",
                (epoch,),
            )
            self._db.execute(
                "INSERT INTO cutover_markers VALUES (?, 'gideon', ?)",
                (epoch, str(checkpoint)),
            )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return AuthorityReceipt("gideon", epoch, checkpoint)

    def rows_for_export(self) -> tuple[Mapping[str, Any], ...]:
        raise MigrationError(
            "PROGRESS_JOURNAL_ONLY",
            "the migration journal contains no authoritative record payloads",
        )

    def conversation_logs_for_export(self) -> tuple[ConversationLogEvidence, ...]:
        return tuple(
            ConversationLogEvidence(
                row["relative_path"],
                int(row["byte_length"]),
                Digest(row["source_digest"]),
            )
            for row in self._db.execute(
                "SELECT relative_path, byte_length, source_digest FROM conversation_log_evidence ORDER BY relative_path"
            )
        )


@dataclass(frozen=True, slots=True)
class VersionHandshake:
    protocol_version: int
    storage_version: int
    storage_mode: str


def negotiate_versions(
    *,
    client_current: int,
    server_min: int,
    server_max: int,
    storage_version: int,
    supported_storage: int = MIGRATION_SCHEMA_VERSION,
) -> VersionHandshake:
    supported_protocols = {client_current, client_current - 1}
    overlap = sorted(
        supported_protocols.intersection(range(server_min, server_max + 1))
    )
    if not overlap:
        raise MigrationError(
            "UNSUPPORTED_PROTOCOL", "client and daemon protocol ranges do not overlap"
        )
    if storage_version > supported_storage:
        raise MigrationError("STORE_AHEAD", "newer storage cannot be downgraded")
    mode = (
        "ready"
        if storage_version == supported_storage
        else "read_only_migration_required"
    )
    return VersionHandshake(max(overlap), storage_version, mode)


class MigrationCoordinator:
    def __init__(self, store: MigrationStore) -> None:
        self.store = store
        self.context_imports = ContextImportStore()
        self.context_migrations = ContextMigrationCoordinator()
        self._staged_context: list[Id] = []
        self._context_runs: list[Id] = []

    def context_checkpoints(self) -> tuple[ContextMigrationCheckpoint, ...]:
        return tuple(
            self.context_migrations.checkpoint(migration_id)
            for migration_id in self._context_runs
        )

    def stage(
        self,
        snapshot: GideonSourceSnapshot,
        *,
        context_bundles: Sequence[ContextExportBundle] = (),
        source_adapter: SourceAdapter | None = None,
    ) -> ValidationReceipt:
        self.store.import_snapshot(snapshot)
        for index, bundle in enumerate(context_bundles):
            staging_id = _legacy_id(
                f"context-stage:{bundle.manifest.manifest_id}:{index}"
            )
            self.context_imports.stage(staging_id, self.store.scope, bundle)
            if source_adapter is None:
                raise MigrationError(
                    "SOURCE_UNAVAILABLE", "context source adapter is required"
                )
            validate_context_export(bundle, self.store.scope)
            self._validate_context_sources(bundle, source_adapter)
            source_ids = tuple(
                Id(record.payload["source_event_id"])
                for record in bundle.records
                if record.kind is ContextPortabilityEntryKind.SOURCE_REFERENCE
            )
            if not source_ids:
                raise MigrationError(
                    "SOURCE_UNAVAILABLE",
                    "context migration requires authoritative source references",
                )
            cursor = bundle.binding.cursor
            if cursor.sequence < len(source_ids):
                raise MigrationError(
                    "INVALID_CURSOR",
                    "context cursor does not cover its source references",
                )
            migration_id = _legacy_id(
                f"context-migration:{bundle.manifest.manifest_id}"
            )
            initial_cursor = Cursor(cursor.epoch, cursor.sequence - len(source_ids))
            checkpoint = self.context_migrations.begin(
                migration_id,
                self.store.scope,
                bundle.binding.session_id,
                initial_cursor,
            )
            if checkpoint.committed_cursor != cursor:
                checkpoint = self.context_migrations.commit_batch(
                    migration_id,
                    _legacy_id(f"context-batch:{bundle.manifest.manifest_id}"),
                    checkpoint.committed_cursor,
                    cursor,
                    source_ids,
                )
            if checkpoint.committed_cursor != cursor:
                raise MigrationError(
                    "INVALID_CURSOR",
                    "context migration checkpoint did not reach the export cursor",
                )
            if staging_id not in self._staged_context:
                self._staged_context.append(staging_id)
            if migration_id not in self._context_runs:
                self._context_runs.append(migration_id)
        return self.store.validate(
            snapshot, context_bundles=context_bundles, source_adapter=source_adapter
        )

    @staticmethod
    def _validate_context_sources(
        bundle: ContextExportBundle, source_adapter: SourceAdapter
    ) -> None:
        for record in bundle.records:
            if record.kind is not ContextPortabilityEntryKind.SOURCE_REFERENCE:
                continue
            source = source_adapter.resolve(
                bundle.binding.scope,
                bundle.binding.session_id,
                Id(record.payload["source_event_id"]),
            )
            if Digest.sha256(source) != Digest(record.payload["source_digest"]):
                raise MigrationError(
                    "SUMMARY_SOURCE_MISMATCH",
                    "ConversationLog source digest does not match",
                )

    async def cutover(
        self,
        *,
        expected_authority_digest: str,
        destination: RustLegacyImport,
        writer: WriterCoordinator,
    ) -> AuthorityReceipt:
        snapshot = await writer.activate_primary()
        try:
            destination_validation = destination.require_validation()
            for migration_id in self._context_runs:
                self.context_migrations.cutover(
                    migration_id,
                    quiescent=True,
                    validation_passed=True,
                )
            for staging_id in self._staged_context:
                self.context_imports.commit(staging_id)
            receipt = self.store.cutover(
                expected_authority_digest=expected_authority_digest,
                destination=destination_validation,
                writer=snapshot,
            )
        except BaseException:
            for migration_id in self._context_runs:
                self.context_migrations.rollback(migration_id, quiescent=True)
            await writer.deactivate()
            raise
        self._staged_context.clear()
        return receipt

    async def rollback(
        self,
        *,
        expected_authority_digest: str,
        writer: WriterCoordinator,
    ) -> AuthorityReceipt:
        self.store._require_known_effects()
        snapshot = await writer.deactivate()
        receipt = self.store.rollback(
            expected_authority_digest=expected_authority_digest,
            writer=snapshot,
        )
        for migration_id in self._context_runs:
            self.context_migrations.rollback(migration_id, quiescent=True)
        return receipt


__all__ = [
    "AuthorityReceipt",
    "ConversationLogEvidence",
    "DestinationValidation",
    "GideonMemorySource",
    "GideonSourceSnapshot",
    "ImportReceipt",
    "KnowledgeCategory",
    "MigrationCoordinator",
    "MigrationError",
    "MigrationStore",
    "RustLegacyImport",
    "SourceItem",
    "ValidationReceipt",
    "VersionHandshake",
    "canonical_bytes",
    "canonical_digest",
    "fingerprint_conversation_logs",
    "negotiate_versions",
]
