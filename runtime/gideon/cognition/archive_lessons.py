"""Scoped lesson resolution and evidence-backed presentation."""

import hashlib
import json
import struct
from typing import Any


def contract():
    from gideon.cognition import vector_memory

    return vector_memory


class LessonResolution:
    def __init__(self, archive, rule, negative, source, scope, scope_ref):
        from gideon.cognition.memory_record import MemoryScope

        self.store, self.api = archive, contract()
        self.rule, self.source, self.negative = rule, source, negative
        self.scope = MemoryScope.GLOBAL if scope is None else scope
        self.scope_ref = scope_ref if self.scope is MemoryScope.WORKSPACE else None
        self.lower = rule.lower()
        self.words = archive._lesson_keywords(self.lower)
        self.embedding = archive._try_embed(rule) if archive.embed_fn else None
        seed = (
            f"{self.scope_ref}\x00{rule}"
            if self.scope is MemoryScope.WORKSPACE
            else rule
        )
        prefix = "lesson.ws." if self.scope is MemoryScope.WORKSPACE else "lesson."
        self.key = prefix + hashlib.md5(seed.encode()).hexdigest()[:12]
        self.pending, self.attempts, self.neighbor = [], 0, None

    def replace(self, key):
        self.store.supersede_semantic(key, self.key, self.source)
        self.store._carry_lesson_evidence(key, self.key)

    def flush(self):
        if self.pending:
            for blob, key in self.pending:
                self.store.db.execute(
                    "UPDATE semantic_memory SET embedding = ? WHERE key = ?",
                    (blob, key),
                )
            self.store.db.commit()

    def prior_embedding(self, row, text):
        blob = row.get("embedding")
        if blob and isinstance(blob, bytes) and len(blob) >= 4:
            try:
                return list(struct.unpack(f"{len(blob) // 4}f", blob))
            except struct.error:
                return None
        if not self.store.embed_fn or self.attempts >= self.api._MAX_BACKFILLS_PER_CALL:
            return None
        vector = self.store._try_embed(text)
        if vector:
            self.pending.append((struct.pack(f"{len(vector)}f", *vector), row["key"]))
        self.attempts += 1
        return vector

    def compare(self, row):
        text = str(json.loads(row["value_json"]))
        lower, key = text.lower(), row["key"]
        if self.lower in lower:
            self.api.logger.info(
                "Lesson dedup: %r already covered by %r", self.rule[:60], key
            )
            return "covered"
        if lower in self.lower:
            self.replace(key)
            return "replaced"
        words = self.store._lesson_keywords(lower) if self.words else set()
        if words:
            overlap = len(self.words & words) / min(len(self.words), len(words))
            if overlap >= 0.5:
                self.api.logger.info(
                    "Lesson conflict: %r replaces %r (%.0f%% overlap)",
                    self.rule[:60],
                    text[:60],
                    overlap * 100,
                )
                self.replace(key)
                return "replaced"
        if not self.embedding:
            return "distinct"
        previous = self.prior_embedding(row, text)
        if not previous:
            return "distinct"
        similarity = self.store._cosine_sim(self.embedding, previous)
        if similarity > 0.85:
            self.api.logger.info(
                "Lesson semantic dedup: %.2f sim with %r", similarity, key
            )
            if len(self.rule) <= len(text):
                return "covered"
            self.pending = [
                (blob, owner) for blob, owner in self.pending if owner != key
            ]
            self.replace(key)
            return "replaced"
        if 0.5 <= similarity <= 0.85 and self.store.contradiction_judge is not None:
            if self.neighbor is None or similarity > self.neighbor[2]:
                self.neighbor = key, text, similarity
        return "distinct"

    def write(self):
        from gideon.cognition.memory_record import MemoryScope

        for row in self.store._lessons_in_bucket(self.scope, self.scope_ref):
            if self.compare(row) == "covered":
                self.store._observe_lesson(row["key"], self.source)
                self.flush()
                return False
        self.flush()
        value = (
            self.rule if not self.negative else f"{self.rule} — NOT: {self.negative}"
        )
        confidence = 1.0 if self.source == "user_explicit" else 0.9
        rejection = self.store.set_semantic(self.key, value, confidence, self.source)
        if rejection is not None:
            return False
        if self.scope is not MemoryScope.GLOBAL:
            self.store.db.execute(
                "UPDATE semantic_memory SET scope = ?, scope_ref = ? WHERE key = ?",
                (self.scope.value, self.scope_ref, self.key),
            )
            self.store.db.commit()
        self.store._observe_lesson(self.key, self.source)
        if self.embedding:
            encoded = struct.pack(f"{len(self.embedding)}f", *self.embedding)
            self.store.db.execute(
                "UPDATE semantic_memory SET embedding = ? WHERE key = ?",
                (encoded, self.key),
            )
            self.store.db.commit()
        self.judge(value)
        return True

    def judge(self, value):
        if self.neighbor is None:
            return
        key, previous, similarity = self.neighbor
        try:
            if self.store.contradiction_judge(value, previous):
                self.store.supersede_semantic(key, self.key, self.source)
                self.store._contradict_lesson(key)
                self.api.logger.info(
                    "Lesson contradiction: %r superseded %r (sim %.2f)",
                    self.key,
                    key,
                    similarity,
                )
        except Exception:
            self.api.logger.debug(
                "contradiction judge failed — keeping both", exc_info=True
            )


class LessonEvidence:
    def __init__(self, archive):
        self.store, self.api = archive, contract()

    def record(self, operation, key, *, source=None, successor=None):
        try:
            evidence = self.store._lesson_evidence_store()
            if operation == "observation":
                evidence.record_observation(
                    key, human_authored=source in self.api._HUMAN_AUTHORED_SOURCES
                )
            elif operation == "contradiction":
                evidence.record_contradiction(key)
            elif operation == "reversal":
                evidence.record_reversal(key)
            else:
                evidence.carry_forward(key, successor)
        except Exception:
            if operation == "carry":
                self.api.logger.debug(
                    "lesson evidence not carried %r → %r", key, successor, exc_info=True
                )
            else:
                self.api.logger.debug(
                    f"lesson {operation} not recorded for %r", key, exc_info=True
                )

    def standings(self, rows):
        keys = [str(row.get("key") or "") for row in rows]
        try:
            from gideon.cognition.learning import lesson_confidence as lc

            store = self.store._lesson_evidence_store()
            floor = lc.configured_threshold()
            records = store.evidence_map(keys)
            verdicts = {}
            for key in filter(None, keys):
                observation = records.get(key, lc.LessonEvidence())
                verdicts[key] = lc.classify(
                    observation,
                    threshold=floor,
                    active_days_idle=store.idle_active_days(observation),
                )
            return verdicts
        except Exception:
            self.api.logger.debug(
                "lesson standings unavailable; failing open", exc_info=True
            )
            from gideon.cognition.learning import lesson_confidence as lc

            return {
                key: lc.LessonVerdict(
                    1.0,
                    lc.LessonStanding.INJECTED,
                    "confidence unavailable — injected rather than silently dropped",
                    lc.LessonEvidence(),
                )
                for key in keys
                if key
            }


class LessonCatalogue:
    def __init__(self, archive):
        self.store = archive

    def rows(self, predicate, arguments, limit):
        parts = [
            "SELECT * FROM semantic_memory WHERE is_deleted = 0 AND key LIKE 'lesson.%'",
            predicate,
            "ORDER BY updated_at DESC",
        ]
        if limit is not None and limit > 0:
            parts.append("LIMIT ?")
            arguments += (limit,)
        return [
            dict(row)
            for row in self.store.db.execute(" ".join(parts), arguments).fetchall()
        ]

    def visible(self, workspace, limit):
        reach = self.store._LESSON_SCOPE
        predicate = f"AND {reach} = 'global' "
        arguments: tuple[Any, ...] = ()
        if workspace:
            predicate = f"AND ({reach} = 'global' OR ({reach} = 'workspace' AND scope_ref = ?)) "
            arguments = (workspace,)
        return self.store._lesson_rows(predicate, arguments, limit)

    def bucket(self, scope, reference, limit):
        from gideon.cognition.memory_record import MemoryScope

        if scope is MemoryScope.WORKSPACE:
            predicate = (
                f"AND {self.store._LESSON_SCOPE} = 'workspace' AND scope_ref = ? "
            )
            args = (reference,)
        else:
            predicate = f"AND {self.store._LESSON_SCOPE} = ? "
            args = (scope.value,)
        return self.store._lesson_rows(predicate, args, limit)

    def forget(self, substring):
        changed = False
        for row in self.store.get_lessons():
            value = json.loads(row["value_json"])
            if substring.lower() not in str(value).lower():
                continue
            self.store.delete_semantic(row["key"], "user_explicit")
            self.store._reverse_lesson(str(row["key"]))
            changed = True
        return changed

    def context(self, workspace):
        rows = self.store.lessons_visible_in(workspace, limit=50)
        if not rows:
            return ""
        verdicts = self.store.lesson_standings(rows)
        lines = []
        for row in rows:
            verdict = verdicts.get(str(row.get("key") or ""))
            if verdict is None or verdict.injected:
                lines.append(f"- {json.loads(row['value_json'])}")
        if not lines:
            return ""
        return "\n".join(
            (
                "[Learned corrections — user-taught rules from past mistakes.\nALWAYS follow these. They override default behavior.]",
                *lines,
                "[End of learned corrections]\n",
            )
        )
