"""Admission, row projections and semantic mutation cycles."""

import json


def contract():
    from gideon.cognition import vector_memory

    return vector_memory


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
