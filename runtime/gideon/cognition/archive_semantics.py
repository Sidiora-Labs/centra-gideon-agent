"""Admission, row projections and semantic mutation cycles."""

import json
import re
from datetime import datetime

_REPLICATION_PREFIXES = ("pref.", "project.", "claim.", "user.")
_DENIED_PREFIXES = (
    "lesson.",
    "slot.",
    "user.procedural.",
    "user.commitment.",
    "user.persona.",
    "user.approval.",
)
_PRIVATE_FIELDS = {
    "credential",
    "credential_ref",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "password",
    "secret",
    "legal_name",
    "full_name",
    "birth_date",
    "date_of_birth",
    "ssn",
    "passport",
    "government_id",
    "email",
    "phone",
    "address",
    "biometric",
    "genome",
}
_PRIVATE_VALUE = re.compile(
    r"(?:\b\d{3}-\d{2}-\d{4}\b|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b)",
    re.IGNORECASE,
)


def contract():
    from gideon.cognition import vector_memory

    return vector_memory


def _timestamp(value, label):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"Invalid semantic import {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid semantic import {label}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Invalid semantic import {label}")
    return parsed


def _private(value):
    from gideon.cognition.onboarding_import.floors import safe_text, strip_secrets

    clean, dropped = strip_secrets(value)
    if dropped or clean != value:
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _PRIVATE_FIELDS or _private(item):
                return True
    elif isinstance(value, list):
        return any(_private(item) for item in value)
    elif isinstance(value, str):
        cleaned, redactions = safe_text(value)
        return bool(redactions or cleaned != value or _PRIVATE_VALUE.search(value))
    return False


class SemanticImport:
    def __init__(self, archive):
        self.archive = archive
        self.api = contract()

    @staticmethod
    def rejected(code, message):
        return contract().SemanticImportResult(False, code, message)

    def apply(self, record):
        from gideon.cognition.memory_record import MemoryKind, MemoryScope, MemoryTier

        if record.kind is not MemoryKind.SEMANTIC:
            return self.rejected(
                "kind_rejected", "Only semantic memory records may be imported"
            )
        if not record.id.startswith(_REPLICATION_PREFIXES) or record.id.startswith(
            _DENIED_PREFIXES
        ):
            return self.rejected(
                "key_rejected", "Semantic key is outside the replication policy"
            )
        key_parts = set(record.id.split("."))
        if key_parts & _PRIVATE_FIELDS:
            return self.rejected(
                "private_identity", "Private identity memory is not replicable"
            )
        derived = (
            record.embedding is not None
            or record.recall_count
            or record.visit_count
            or record.last_accessed_at is not None
            or record.superseded_by is not None
            or record.invalidated_at is not None
            or record.extra
            or record.safe_to_act is not None
            or record.due_window is not None
            or record.channel is not None
            or record.dismissed_at is not None
            or record.source_ref is not None
            or record.conversation_id
            or record.tags
        )
        if derived:
            return self.rejected(
                "derived_state", "Derived or execution memory state is not replicable"
            )
        if record.tier is not MemoryTier.SEMANTIC or record.scope_ref is not None:
            return self.rejected(
                "authority_rejected", "Memory reach authority is not replicable"
            )
        value = record.value if record.value is not None else record.text
        if (
            _private(value)
            or _private(record.source)
            or _private(record.category or "")
        ):
            return self.rejected(
                "private_data", "Credential or private identity data is not replicable"
            )
        try:
            created = _timestamp(record.created_at, "created_at")
            updated = _timestamp(record.updated_at, "updated_at")
        except ValueError as error:
            return self.rejected("invalid_timestamp", str(error))
        if updated < created:
            return self.rejected(
                "invalid_timestamp", "Semantic update predates creation"
            )
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError):
            return self.rejected(
                "invalid_value", "Semantic value is not JSON serializable"
            )
        rejection = self.archive.validate_semantic(
            record.id,
            value,
            record.confidence,
            record.source,
            value_json=encoded,
        )
        if rejection is not None:
            return self.rejected(rejection[0].value, rejection[1])
        scope = record.scope.value if record.scope else MemoryScope.GLOBAL.value
        try:
            self.archive.db.execute("BEGIN IMMEDIATE")
            previous = self.archive.db.execute(
                "SELECT * FROM semantic_memory WHERE key = ?",
                (record.id,),
            ).fetchone()
            mutation = SemanticMutation(
                self.archive,
                record.id,
                encoded,
                record.confidence,
                record.source,
            )
            mutation.previous = previous
            conflict = mutation.conflict()
            if conflict is not None:
                self.archive.db.rollback()
                return self.rejected(
                    self.api.SemanticRejectCode.CONFLICT.value, conflict
                )
            prior_value = previous["value_json"] if previous is not None else None
            changed = previous is None or any(
                (
                    previous["value_json"] != encoded,
                    previous["confidence"] != record.confidence,
                    previous["source"] != record.source,
                    previous["created_at"] != record.created_at,
                    previous["updated_at"] != record.updated_at,
                    bool(previous["is_deleted"]) != bool(record.is_deleted),
                    previous["scope"] != scope,
                    previous["category"] != record.category,
                )
            )
            if not changed:
                self.archive.db.rollback()
                return self.api.SemanticImportResult(True, "unchanged")
            event = (
                "import_delete"
                if record.is_deleted
                else ("import_update" if previous else "import_create")
            )
            try:
                self.archive.db.execute(
                    "INSERT INTO semantic_memory "
                    "(key,value_json,confidence,source,created_at,updated_at,is_deleted,embedding,recall_count,superseded_by,invalidated_at,tier,scope,scope_ref,category,visit_count,contributor,holder,weight) "
                    "VALUES (?,?,?,?,?,?,?,NULL,0,NULL,NULL,'semantic',?,NULL,?,0,'','',1.0) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,confidence=excluded.confidence,source=excluded.source,created_at=excluded.created_at,updated_at=excluded.updated_at,is_deleted=excluded.is_deleted,embedding=NULL,recall_count=0,superseded_by=NULL,invalidated_at=NULL,tier='semantic',scope=excluded.scope,scope_ref=NULL,category=excluded.category,visit_count=0,contributor='',holder='',weight=1.0",
                    (
                        record.id,
                        encoded,
                        record.confidence,
                        record.source,
                        record.created_at,
                        record.updated_at,
                        int(record.is_deleted),
                        scope,
                        record.category,
                    ),
                )
                self.archive.db.execute(
                    "INSERT INTO memory_events(event_type,memory_type,memory_key,old_value,new_value,source,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        event,
                        "semantic",
                        record.id,
                        prior_value,
                        None if record.is_deleted else encoded,
                        record.source,
                        self.api._now_iso(),
                    ),
                )
                self.archive.db.commit()
            except BaseException:
                self.archive.db.rollback()
                raise
        except Exception as error:
            return self.rejected("store_error", str(error))
        self.archive.invalidate_alias_index()
        return self.api.SemanticImportResult(True, event)


class SemanticAdmission:
    def __init__(self, archive):
        self.archive = archive
        self.api = contract()

    def check(self, key, value, confidence, source, encoded):
        api, archive = self.api, self.archive
        codes = api.SemanticRejectCode
        problem = archive._validate_key(key)
        if problem:
            return codes.KEY_FORMAT, problem
        if not archive._matches_allowlist(key):
            return (
                codes.ALLOWLIST,
                f"Key must match an allowed prefix ({', '.join(archive._prefixes)})",
            )
        if key.startswith("system.") and source != "user_explicit":
            return (
                codes.RESERVED_PREFIX,
                "Reserved key prefix requires user_explicit source",
            )
        if source != "user_explicit" and confidence < archive._confidence_threshold:
            return (
                codes.CONFIDENCE,
                f"Confidence {confidence:.2f} below threshold {archive._confidence_threshold}",
            )
        payload = json.dumps(value) if encoded is None else encoded
        size = len(payload.encode("utf-8"))
        if size > api._MAX_VALUE_BYTES:
            return (
                codes.VALUE_SIZE,
                f"Value too large ({size} bytes, max {api._MAX_VALUE_BYTES})",
            )
        if api._contains_injection(payload):
            return codes.INJECTION, "Value contains blocked content patterns"
        return self.slot_limit(key, value)

    def slot_limit(self, key, value):
        slots = self.api.memory_slots
        if not key.startswith(slots.SLOT_PREFIX):
            return None
        name = slots.name_from_key(key)
        ceiling, used = slots.cap_for(name), slots.live_chars(value)
        if used <= ceiling:
            return None
        proposal = slots.TrimProposal(
            slot=name,
            cap_chars=ceiling,
            current_chars=used,
            incoming_chars=0,
            drop_candidates=[
                line.text for line in slots.live_lines(slots.parse_lines(value))
            ],
        )
        return self.api.SemanticRejectCode.SLOT_CAP, proposal.message


class RecordProjection:
    def __init__(self, archive):
        self.archive = archive

    def semantic(self, key=None, pattern=None):
        predicates, parameters = ["is_deleted = 0"], []
        if key is not None:
            predicates.append("key = ?")
            parameters.append(key)
        if pattern is not None:
            predicates.append("key LIKE ?")
            parameters.append(pattern)
        cursor = self.archive.db.execute(
            "SELECT * FROM semantic_memory WHERE "
            + " AND ".join(predicates)
            + " ORDER BY key",
            parameters,
        )
        return [dict(row) for row in cursor.fetchall()]

    def one(self, key):
        row = self.archive.db.execute(
            "SELECT * FROM semantic_memory WHERE key = ? AND is_deleted = 0",
            (key,),
        ).fetchone()
        return None if row is None else dict(row)

    @staticmethod
    def tables():
        from gideon.cognition.memory_record import MemoryRecord

        return (
            ("semantic_memory", "key", "key", MemoryRecord.from_semantic_row),
            (
                "episodic_memories",
                "id",
                "created_at DESC",
                MemoryRecord.from_episodic_row,
            ),
        )

    def get(self, identifier):
        for table, identity, _, convert in self.tables():
            row = self.archive.db.execute(
                f"SELECT * FROM {table} WHERE {identity} = ? AND is_deleted = 0",
                (identifier,),
            ).fetchone()
            if row is not None:
                return convert(row)
        return None

    def inventory(self, kinds, include_deleted):
        from gideon.cognition.memory_record import MemoryKind

        wanted = {str(kind) for kind in kinds} if kinds else None
        semantic_kinds = {
            MemoryKind.SEMANTIC.value,
            MemoryKind.LESSON.value,
            MemoryKind.PREFERENCE.value,
            MemoryKind.NOTE.value,
            MemoryKind.PROCEDURAL.value,
            MemoryKind.SELF_PERSONA.value,
            MemoryKind.COMMITMENT.value,
            MemoryKind.APPROVAL.value,
            MemoryKind.SLOT.value,
        }
        permitted = (semantic_kinds, {MemoryKind.EPISODIC.value})
        where = "" if include_deleted else " WHERE is_deleted = 0"
        result = []
        for (table, _, order, convert), supported in zip(self.tables(), permitted):
            if wanted is not None and not wanted.intersection(supported):
                continue
            rows = self.archive.db.execute(
                f"SELECT * FROM {table}{where} ORDER BY {order}"
            ).fetchall()
            for row in rows:
                record = convert(row)
                if (
                    table == "episodic_memories"
                    or wanted is None
                    or record.kind.value in wanted
                ):
                    result.append(record)
        return result


class SemanticMutation:
    def __init__(self, archive, key, encoded, confidence, source):
        self.archive = archive
        self.api = contract()
        self.key, self.encoded = key, encoded
        self.confidence, self.source = confidence, source
        self.previous = None

    def conflict(self):
        row = self.previous
        if (
            row is None
            or row["is_deleted"]
            or self.source in self.api._HUMAN_AUTHORED_SOURCES
        ):
            return None
        if row["source"] in self.api._HUMAN_AUTHORED_SOURCES:
            return (
                "Existing entry set by user cannot be overwritten by automated source"
            )
        prior_confidence = row["confidence"]
        if (
            self.confidence > prior_confidence
            or abs(self.confidence - prior_confidence) < 0.1
        ):
            return None
        return f"Existing entry has higher confidence ({prior_confidence:.2f} vs {self.confidence:.2f})"

    def attribution(self, holder, weight):
        api = self.api
        if holder is not None or weight is not None:
            normalized = api.memory_holder.normalize_holder(holder)
            requested = (
                api.memory_holder.weight_cap(normalized) if weight is None else weight
            )
            return normalized, api.memory_holder.normalize_weight(normalized, requested)
        normalized = str(api._row_value(self.previous, "holder", "") or "")
        try:
            strength = float(api._row_value(self.previous, "weight", 1.0))
        except (TypeError, ValueError):
            strength = 1.0
        return normalized, strength

    def apply(self, contributor, holder, weight):
        archive = self.archive
        self.previous = archive.db.execute(
            "SELECT * FROM semantic_memory WHERE key = ?", (self.key,)
        ).fetchone()
        refusal = self.conflict()
        live = self.previous is not None and not self.previous["is_deleted"]
        event = (
            "conflict_skip" if refusal is not None else ("update" if live else "create")
        )
        archive._log_event(
            event,
            "semantic",
            self.key,
            self.previous["value_json"] if live else None,
            self.encoded,
            self.source,
        )
        if refusal is not None:
            return refusal
        now = self.api._now_iso()
        author = self.api.current_username() if contributor is None else contributor
        attribution, strength = self.attribution(holder, weight)
        archive.db.execute(
            "INSERT INTO semantic_memory "
            "(key, value_json, confidence, source, created_at, updated_at, is_deleted, tier, contributor, holder, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 'semantic', ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value_json=excluded.value_json, confidence=excluded.confidence, "
            "source=excluded.source, updated_at=excluded.updated_at, is_deleted=0, "
            "holder=excluded.holder, weight=excluded.weight",
            (
                self.key,
                self.encoded,
                self.confidence,
                self.source,
                now,
                now,
                author,
                attribution,
                strength,
            ),
        )
        archive.db.commit()
        if live:
            self.retire_previous()
        return None

    def retire_previous(self):
        if self.previous is None:
            return
        old_value = self.previous["value_json"]
        try:
            readable = (
                json.loads(old_value) if isinstance(old_value, str) else str(old_value)
            )
        except (json.JSONDecodeError, TypeError):
            readable = str(old_value)
        if isinstance(readable, str) and len(readable) >= 3:
            self.archive._retire_stale_episodic(self.key, readable)


class SemanticRetirement:
    def __init__(self, archive):
        self.archive = archive
        self.api = contract()

    def tombstone(self, key, source, successor=None, supersede=False):
        previous = self.archive.get_semantic(key)
        if not previous:
            return False
        now = self.api._now_iso()
        assignments, values = ["is_deleted = 1", "updated_at = ?"], [now]
        if supersede:
            assignments.extend(("superseded_by = ?", "invalidated_at = ?"))
            values.extend((successor, now))
        self.archive.db.execute(
            "UPDATE semantic_memory SET " + ", ".join(assignments) + " WHERE key = ?",
            (*values, key),
        )
        self.archive.db.commit()
        self.archive._log_event(
            "supersede" if supersede else "delete",
            "semantic",
            key,
            previous["value_json"],
            successor,
            source,
        )
        return True

    def chain(self, key):
        rows, visited = [], set()
        while key:
            if key in visited:
                break
            visited.add(key)
            row = self.archive.db.execute(
                "SELECT key, value_json, is_deleted, superseded_by, invalidated_at "
                "FROM semantic_memory WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                break
            rows.append(dict(row))
            key = row["superseded_by"]
        return rows

    def stale_episodes(self, key, old_value):
        archive = self.archive
        suffix = key.rsplit(".", 1)[-1].replace("_", " ")
        query = f"{suffix}: {old_value}"
        handled = set()

        def retire(row):
            identity = row["id"]
            if identity in handled:
                return
            handled.add(identity)
            archive.db.execute(
                "UPDATE episodic_memories SET is_deleted = 1 WHERE id = ?", (identity,)
            )
            archive._log_event(
                "conflict_retire",
                "episodic",
                identity,
                row["text"][:200],
                None,
                "semantic_update",
            )

        vector = archive._try_embed(query)
        if vector is not None:
            for row in archive.search_episodic(
                query_embedding=vector, query_text="", limit=10
            ):
                if row.get("cosine_sim", 0) > 0.7:
                    retire(row)
        for pattern in (f"%{suffix}: {old_value}%", f"%{suffix} {old_value}%"):
            rows = archive.db.execute(
                "SELECT id, text FROM episodic_memories WHERE is_deleted = 0 AND text LIKE ?",
                (pattern,),
            ).fetchall()
            for row in rows:
                retire(row)
        if handled:
            archive.db.commit()
            self.api.logger.info(
                "Retired %d stale episodic entries for key %r", len(handled), key
            )
