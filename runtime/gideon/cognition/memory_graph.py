"""Entity matching, relational graph storage, recall evidence and volunteer statistics."""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations

from gideon.core.sqlite_compat import sqlite3

logger = logging.getLogger(__name__)
ENTITY_TYPES = ("person", "project", "tool", "org", "topic", "place")
LINK_TYPES = (
    "mentions",
    "about",
    "same_project",
    "references",
    "temporal_proximity",
    "same_topic",
)
MIN_ALIAS_TOKEN_LEN = 3
PROPOSAL_THRESHOLD = 3
_CONTEXT_CHARS = 100
GRAPH_BOOST_BETA = 0.1
GRAPH_BOOST_FLOOR = 0.25
_WORD = re.compile(r"[0-9a-z]+(?:'[a-z]+)?", re.IGNORECASE)


def new_entity_id() -> str:
    return "e-" + uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    entity_type: str
    aliases: tuple[str, ...] = ()
    source: str = "user"


@dataclass(frozen=True)
class Mention:
    entity_id: str
    matched: str
    start: int
    end: int

    def context(self, text: str) -> str:
        window = slice(
            max(0, self.start - _CONTEXT_CHARS),
            min(len(text), self.end + _CONTEXT_CHARS),
        )
        return text[window].strip()


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    return [(token.group().lower(), *token.span()) for token in _WORD.finditer(text)]


class AliasIndex:
    """Length-indexed token phrases sharing their first token and ordered entity ids."""

    def __init__(self) -> None:
        self._phrases: dict[str, dict[int, dict[tuple[str, ...], list[str]]]] = {}
        self._registrations = 0

    def add(self, entity_id: str, surface: str) -> bool:
        phrase = tuple(word for word, _start, _end in _tokenize(surface))
        width = len(phrase)
        if not width or (width == 1 and len(phrase[0]) < MIN_ALIAS_TOKEN_LEN):
            return False
        lengths = self._phrases.setdefault(phrase[0], {})
        matches = lengths.setdefault(width, {}).setdefault(phrase, [])
        if entity_id not in matches:
            matches.append(entity_id)
        self._registrations += 1
        return True

    def add_entity(self, entity: Entity) -> int:
        accepted = [
            self.add(entity.id, form) for form in (entity.name, *entity.aliases)
        ]
        return sum(accepted)

    def find(self, text: str) -> list[Mention]:
        if not text or not self._registrations:
            return []
        tokens = _tokenize(text)
        words = tuple(token[0] for token in tokens)
        mentions: list = []
        cursor = 0
        while cursor < len(tokens):
            candidates = self._phrases.get(words[cursor], {})
            for width in sorted(candidates, reverse=True):
                if cursor + width > len(tokens):
                    continue
                identities = candidates[width].get(words[cursor : cursor + width])
                if not identities:
                    continue
                start, end = tokens[cursor][1], tokens[cursor + width - 1][2]
                mentions.extend(
                    Mention(identity, text[start:end], start, end)
                    for identity in identities
                )
                cursor += width
                break
            else:
                cursor += 1
        return mentions

    def unknown_capitalized(self, text: str) -> list[str]:
        resolved = {mention.matched.lower() for mention in self.find(text)}
        candidates = re.finditer(
            r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b", text or ""
        )
        return [
            candidate.group()
            for candidate in candidates
            if candidate.group().lower() not in resolved
        ]


SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS mem_entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_entities_name ON mem_entities(name);
CREATE INDEX IF NOT EXISTS idx_mem_entities_type ON mem_entities(entity_type);

CREATE TABLE IF NOT EXISTS mem_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_kind TEXT NOT NULL,
    from_ref TEXT NOT NULL,
    to_entity TEXT,
    to_ref TEXT,
    link_type TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'extracted',
    confidence REAL NOT NULL DEFAULT 1.0,
    context TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_links_to_entity ON mem_links(to_entity);
CREATE INDEX IF NOT EXISTS idx_mem_links_from ON mem_links(from_kind, from_ref);
CREATE INDEX IF NOT EXISTS idx_mem_links_type ON mem_links(link_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_links_edge
    ON mem_links(from_kind, from_ref, IFNULL(to_entity, ''), IFNULL(to_ref, ''), link_type);

CREATE TABLE IF NOT EXISTS mem_link_stats (
    entity_id TEXT PRIMARY KEY,
    inbound_count INTEGER NOT NULL DEFAULT 0,
    last_linked_at TEXT,
    community INTEGER
);

CREATE TABLE IF NOT EXISTS mem_entity_proposals (
    name TEXT PRIMARY KEY,
    mention_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    refs TEXT NOT NULL DEFAULT '[]'
);
"""

SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS mem_volunteer_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    arm TEXT NOT NULL,
    confidence REAL NOT NULL,
    from_kind TEXT NOT NULL,
    record_ref TEXT NOT NULL,
    recall_at_volunteer INTEGER NOT NULL DEFAULT 0,
    session_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_volunteer_created ON mem_volunteer_events(created_at);
CREATE INDEX IF NOT EXISTS idx_mem_volunteer_ref ON mem_volunteer_events(record_ref);
CREATE INDEX IF NOT EXISTS idx_mem_volunteer_arm ON mem_volunteer_events(arm);
"""

_USED_EXPR = (
    "CASE WHEN COALESCE(s.recall_count, 0) > v.recall_at_volunteer THEN 1 ELSE 0 END"
)

_VOLUNTEER_FROM_WHERE = (
    "FROM mem_volunteer_events v "
    "LEFT JOIN semantic_memory s ON s.key = v.record_ref "
    "WHERE v.from_kind = 'semantic'"
)


class _GraphTable:
    def __init__(self, graph: "MemoryGraph"):
        self.graph = graph

    @property
    def db(self):
        return self.graph.db

    def rows(self, sql: str, parameters=()) -> list[dict]:
        return list(map(dict, self.db.execute(sql, parameters).fetchall()))


class _EntityRows(_GraphTable):
    def upsert(self, name, entity_type, aliases, source, entity_id):
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"unknown entity_type {entity_type!r}")
        title = (name or "").strip()
        if not title:
            raise ValueError("entity name must not be empty")
        timestamp = _now()
        existing = self.db.execute(
            "SELECT id, aliases FROM mem_entities WHERE LOWER(name) = LOWER(?) AND is_deleted = 0",
            (title,),
        ).fetchone()
        if existing is None:
            identity = entity_id or new_entity_id()
            values = (
                identity,
                title,
                entity_type,
                json.dumps(sorted(set(aliases or []))),
                source,
                timestamp,
                timestamp,
            )
            self.db.execute(
                "INSERT INTO mem_entities (id, name, entity_type, aliases, source, created_at, updated_at, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                values,
            )
            self.db.commit()
            return identity
        if aliases:
            self.db.execute(
                "UPDATE mem_entities SET aliases = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(_merge_aliases(existing["aliases"], aliases)),
                    timestamp,
                    existing["id"],
                ),
            )
            self.db.commit()
        return str(existing["id"])

    def read(self, include_deleted: bool):
        query = "SELECT * FROM mem_entities" + (
            "" if include_deleted else " WHERE is_deleted = 0"
        )
        entities = []
        for row in self.db.execute(query + " ORDER BY name").fetchall():
            try:
                aliases = tuple(json.loads(row["aliases"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                aliases = ()
            entities.append(
                Entity(
                    row["id"], row["name"], row["entity_type"], aliases, row["source"]
                )
            )
        return entities

    def retire(self, entity_id: str) -> bool:
        changed = self.db.execute(
            "UPDATE mem_entities SET is_deleted = 1, updated_at = ? WHERE id = ? AND is_deleted = 0",
            (_now(), entity_id),
        ).rowcount
        if changed:
            for table, column in (
                ("mem_links", "to_entity"),
                ("mem_link_stats", "entity_id"),
            ):
                self.db.execute(f"DELETE FROM {table} WHERE {column} = ?", (entity_id,))
            self.db.commit()
        return bool(changed)


@dataclass(frozen=True)
class _EdgeIdentity:
    from_kind: str
    from_ref: str
    to_entity: str | None
    to_ref: str | None
    link_type: str

    def payload(self) -> str:
        return json.dumps(
            dict(
                from_kind=self.from_kind,
                from_ref=self.from_ref,
                to_entity=self.to_entity,
                to_ref=self.to_ref,
                link_type=self.link_type,
            )
        )


class _LinkRows(_GraphTable):
    def log(self, identity: _EdgeIdentity, action: str, source: str) -> None:
        writer = self.graph._log_event
        if writer is not None:
            payload = identity.payload()
            before, after = (None, payload) if action == "link_add" else (payload, None)
            writer(action, "link", identity.from_ref, before, after, source)

    def insert(
        self, identity: _EdgeIdentity, provenance, confidence, context, source
    ) -> bool:
        if identity.link_type not in LINK_TYPES:
            raise ValueError(f"unknown link_type {identity.link_type!r}")
        if (identity.to_entity is None) == (identity.to_ref is None):
            raise ValueError("exactly one of to_entity / to_ref must be set")
        timestamp = _now()
        try:
            self.db.execute(
                "INSERT INTO mem_links (from_kind, from_ref, to_entity, to_ref, link_type, "
                "provenance, confidence, context, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identity.from_kind,
                    identity.from_ref,
                    identity.to_entity,
                    identity.to_ref,
                    identity.link_type,
                    provenance,
                    float(confidence),
                    context,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError:
            if identity.to_entity:
                self.graph._touch_stats(identity.to_entity, timestamp)
            return False
        if identity.to_entity:
            self.graph._bump_stats(identity.to_entity, timestamp)
        self.db.commit()
        self.log(identity, "link_add", source)
        return True

    def decrement(self, entities) -> None:
        for identity, count in Counter(entity for entity in entities if entity).items():
            self.db.execute(
                "UPDATE mem_link_stats SET inbound_count = MAX(0, inbound_count - ?) WHERE entity_id = ?",
                (count, identity),
            )

    def remove(self, link_id: int, source: str) -> bool:
        row = self.db.execute(
            "SELECT * FROM mem_links WHERE id = ?", (link_id,)
        ).fetchone()
        if row is None:
            return False
        self.db.execute("DELETE FROM mem_links WHERE id = ?", (link_id,))
        self.decrement((row["to_entity"],))
        self.db.commit()
        identity = _EdgeIdentity(
            *(
                row[name]
                for name in (
                    "from_kind",
                    "from_ref",
                    "to_entity",
                    "to_ref",
                    "link_type",
                )
            )
        )
        self.log(identity, "link_remove", source)
        return True

    def clear(self, from_kind: str, from_ref: str) -> int:
        parameters = from_kind, from_ref
        targets = self.db.execute(
            "SELECT to_entity FROM mem_links WHERE from_kind = ? AND from_ref = ?",
            parameters,
        ).fetchall()
        removed = self.db.execute(
            "DELETE FROM mem_links WHERE from_kind = ? AND from_ref = ?", parameters
        ).rowcount
        self.decrement(row["to_entity"] for row in targets)
        self.db.commit()
        return int(removed or 0)

    def reinforce(self, entity_id: str, timestamp: str, increment: bool) -> None:
        assignment = "inbound_count = inbound_count + 1, " if increment else ""
        self.db.execute(
            "INSERT INTO mem_link_stats (entity_id, inbound_count, last_linked_at) VALUES (?, 1, ?) "
            f"ON CONFLICT(entity_id) DO UPDATE SET {assignment}last_linked_at = ?",
            (entity_id, timestamp, timestamp),
        )
        if not increment:
            self.db.commit()


class _ProposalRows(_GraphTable):
    def tally(self, name: str, reference: str) -> int:
        timestamp = _now()
        row = self.db.execute(
            "SELECT mention_count, refs FROM mem_entity_proposals WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO mem_entity_proposals (name, mention_count, first_seen_at, last_seen_at, refs) VALUES (?, 1, ?, ?, ?)",
                (name, timestamp, timestamp, json.dumps([reference])),
            )
            self.db.commit()
            return 1
        try:
            references = list(json.loads(row["refs"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            references = []
        if reference in references:
            return int(row["mention_count"])
        combined = [*references, reference]
        count = len(combined)
        self.db.execute(
            "UPDATE mem_entity_proposals SET mention_count = ?, last_seen_at = ?, refs = ? WHERE name = ?",
            (count, timestamp, json.dumps(combined[:50]), name),
        )
        self.db.commit()
        return count

    def pending(self, threshold: int) -> list[dict]:
        rows = self.rows(
            "SELECT * FROM mem_entity_proposals WHERE mention_count >= ? ORDER BY mention_count DESC, name",
            (threshold,),
        )
        known = {entity.name.lower() for entity in self.graph.entities()}
        known.update(
            alias.lower()
            for entity in self.graph.entities()
            for alias in entity.aliases
        )
        stale, pending = [], []
        for row in rows:
            if str(row["name"]).lower() in known:
                stale.append((row["name"],))
            else:
                pending.append(row)
        if stale:
            self.db.executemany(
                "DELETE FROM mem_entity_proposals WHERE name = ?", stale
            )
            self.db.commit()
        return pending

    def discard(self, name: str) -> bool:
        count = self.db.execute(
            "DELETE FROM mem_entity_proposals WHERE name = ?", (name,)
        ).rowcount
        self.db.commit()
        return bool(count)


class _GraphRecall:
    def __init__(self, graph: "MemoryGraph"):
        self.graph = graph

    def resolve(self, text: str, index) -> list[str]:
        matcher = self.graph.build_index() if index is None else index
        return list(
            dict.fromkeys(mention.entity_id for mention in matcher.find(text or ""))
        )

    def evidence(self, text, index) -> dict:
        names = {entity.id: entity.name for entity in self.graph.entities()}
        evidence: dict = {}
        for entity in self.graph.resolve_query(text, index):
            label = names.get(entity, entity)
            for link in self.graph.backlinks(entity, limit=60):
                labels = evidence.setdefault(link["from_ref"], [])
                if label not in labels:
                    labels.append(label)
        return evidence

    def boosts(self, text, index, limit) -> dict:
        weights: dict = {}
        for entity in self.graph.resolve_query(text, index):
            degree = int(self.graph.stats(entity).get("inbound_count", 0) or 0)
            weight = max(GRAPH_BOOST_FLOOR, GRAPH_BOOST_BETA * math.log1p(degree))
            for link in self.graph.backlinks(entity, limit=limit):
                reference = link["from_ref"]
                weights[reference] = weights.get(reference, 0.0) + weight
        return weights


@dataclass
class _EdgeSupport:
    left: str
    right: str
    records: int = 0
    relations: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    confidence: float = 0.0

    def absorb(self, left, right):
        self.records += 1
        self.relations.update(value for value in (left[1], right[1]) if value)
        self.sources.update(value for value in (left[2], right[2]) if value)
        self.confidence = max(self.confidence, min(left[3], right[3]))

    def public(self):
        return {
            "from": self.left,
            "to": self.right,
            "records": self.records,
            "link_types": sorted(self.relations),
            "provenances": sorted(self.sources),
            "confidence": self.confidence,
        }


class _EntityProjection(_GraphTable):
    def nodes(self, entities):
        rollups = {
            row["entity_id"]: dict(row)
            for row in self.db.execute("SELECT * FROM mem_link_stats").fetchall()
        }
        nodes = []
        for entity in entities:
            stats = rollups.get(entity.id, {})
            community = stats.get("community")
            nodes.append(
                dict(
                    id=entity.id,
                    name=entity.name,
                    entity_type=entity.entity_type,
                    aliases=list(entity.aliases),
                    community=int(community) if community is not None else None,
                    inbound_count=int(stats.get("inbound_count") or 0),
                )
            )
        return nodes

    def edges(self, known):
        records = defaultdict(list)
        rows = self.db.execute(
            "SELECT from_kind, from_ref, to_entity, link_type, provenance, confidence "
            "FROM mem_links WHERE to_entity IS NOT NULL AND to_entity != '' ORDER BY from_kind, from_ref, to_entity"
        ).fetchall()
        for row in rows:
            if row["to_entity"] in known:
                records[str(row["from_kind"]), str(row["from_ref"])].append(
                    (
                        row["to_entity"],
                        str(row["link_type"] or ""),
                        str(row["provenance"] or ""),
                        float(1.0 if row["confidence"] is None else row["confidence"]),
                    )
                )
        edges: dict = {}
        for record in sorted(records):
            for left, right in combinations(records[record], 2):
                if left[0] == right[0]:
                    continue
                key = tuple(sorted((left[0], right[0])))
                edges.setdefault(key, _EdgeSupport(*key)).absorb(left, right)
        return [edges[key].public() for key in sorted(edges)]

    def render(self):
        entities = self.graph.entities()
        nodes = self.nodes(entities)
        edges = self.edges({entity.id for entity in entities})
        return dict(nodes=nodes, edges=edges)


class _VolunteerEvents(_GraphTable):
    @staticmethod
    def query(columns, window_days, suffix=""):
        sql = f"SELECT {columns} {_VOLUNTEER_FROM_WHERE}"
        parameters: tuple[str, ...] = ()
        if window_days:
            sql += " AND v.created_at >= ?"
            parameters = (_iso_days_ago(int(window_days)),)
        return sql + suffix, parameters

    @staticmethod
    def ratio(total, used):
        return dict(n=total, used=used, precision=used / total if total else 0.0)

    def record(
        self,
        entity_id,
        entity_name,
        arm,
        confidence,
        from_kind,
        record_ref,
        recall_at_volunteer,
        session_key,
    ):
        try:
            values = (
                entity_id,
                entity_name,
                arm,
                float(confidence),
                from_kind,
                record_ref,
                int(recall_at_volunteer),
                session_key,
                _now(),
            )
            cursor = self.db.execute(
                "INSERT INTO mem_volunteer_events (entity_id, entity_name, arm, confidence, from_kind, record_ref, "
                "recall_at_volunteer, session_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            self.db.commit()
            return int(cursor.lastrowid or 0)
        except sqlite3.Error:
            logger.debug("volunteer event not logged", exc_info=True)
            return 0

    def qrels(self, window_days):
        query, parameters = self.query(
            f"v.entity_name AS q, v.record_ref AS ref, {_USED_EXPR} AS used",
            window_days,
        )
        try:
            rows = self.db.execute(query, parameters).fetchall()
        except sqlite3.Error:
            logger.debug("volunteer qrels unavailable", exc_info=True)
            return {}
        positives = defaultdict(set)
        for row in rows:
            if int(row["used"] or 0):
                query, reference = (
                    str(row["q"] or "").strip(),
                    str(row["ref"] or "").strip(),
                )
                if query and reference:
                    positives[query].add(reference)
        return {query: sorted(positives[query]) for query in sorted(positives)}

    def precision(self, window_days):
        query, parameters = self.query(
            f"v.arm AS arm, COUNT(*) AS n, SUM({_USED_EXPR}) AS used",
            window_days,
            " GROUP BY v.arm ORDER BY v.arm",
        )
        try:
            rows = self.db.execute(query, parameters).fetchall()
        except sqlite3.Error:
            logger.debug("volunteer precision unavailable", exc_info=True)
            return dict(arms={}, overall=self.ratio(0, 0))
        arms = {}
        total = used = 0
        for row in rows:
            count, consumed = int(row["n"] or 0), int(row["used"] or 0)
            arms[str(row["arm"])] = self.ratio(count, consumed)
            total += count
            used += consumed
        return dict(arms=arms, overall=self.ratio(total, used))

    def prune(self, keep_days):
        try:
            count = self.db.execute(
                "DELETE FROM mem_volunteer_events WHERE created_at < ?",
                (_iso_days_ago(keep_days),),
            ).rowcount
            self.db.commit()
            return int(count or 0)
        except sqlite3.Error:
            logger.debug("volunteer prune skipped", exc_info=True)
            return 0


class MemoryGraph:
    def __init__(self, db: sqlite3.Connection, *, log_event=None) -> None:
        self.db, self._log_event = db, log_event
        self._entity_rows = _EntityRows(self)
        self._link_rows = _LinkRows(self)
        self._proposal_rows = _ProposalRows(self)
        self._recall = _GraphRecall(self)
        self._projection = _EntityProjection(self)
        self._volunteers = _VolunteerEvents(self)

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        *,
        aliases: "list[str] | None" = None,
        source: str = "user",
        entity_id: str | None = None,
    ) -> str:
        return self._entity_rows.upsert(name, entity_type, aliases, source, entity_id)

    def entities(self, *, include_deleted: bool = False) -> list[Entity]:
        return self._entity_rows.read(include_deleted)

    def build_index(self) -> AliasIndex:
        compiled = AliasIndex()
        for entity in self.entities():
            compiled.add_entity(entity)
        return compiled

    def delete_entity(self, entity_id: str) -> bool:
        return self._entity_rows.retire(entity_id)

    def add_link(
        self,
        *,
        from_kind: str,
        from_ref: str,
        link_type: str,
        to_entity: str | None = None,
        to_ref: str | None = None,
        provenance: str = "extracted",
        confidence: float = 1.0,
        context: str | None = None,
        source: str = "linker",
    ) -> bool:
        identity = _EdgeIdentity(from_kind, from_ref, to_entity, to_ref, link_type)
        return self._link_rows.insert(identity, provenance, confidence, context, source)

    def remove_link(self, link_id: int, *, source: str = "user_explicit") -> bool:
        return self._link_rows.remove(link_id, source)

    def links_from(self, from_kind: str, from_ref: str) -> list[dict]:
        return self._link_rows.rows(
            "SELECT * FROM mem_links WHERE from_kind = ? AND from_ref = ? ORDER BY id",
            (from_kind, from_ref),
        )

    def backlinks(self, entity_id: str, *, limit: int = 100) -> list[dict]:
        return self._link_rows.rows(
            "SELECT * FROM mem_links WHERE to_entity = ? ORDER BY id DESC LIMIT ?",
            (entity_id, limit),
        )

    def drop_links_for(self, from_kind: str, from_ref: str) -> int:
        return self._link_rows.clear(from_kind, from_ref)

    def resolve_query(self, text: str, index: "AliasIndex | None" = None) -> list[str]:
        return self._recall.resolve(text, index)

    def recall_evidence(self, text: str, *, index: "AliasIndex | None" = None) -> dict:
        return self._recall.evidence(text, index)

    def recall_refs(
        self, text: str, *, index: "AliasIndex | None" = None, limit: int = 60
    ) -> dict:
        return self._recall.boosts(text, index, limit)

    def stats(self, entity_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM mem_link_stats WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return dict(row) if row else dict(entity_id=entity_id, inbound_count=0)

    def _bump_stats(self, entity_id: str, now: str) -> None:
        self._link_rows.reinforce(entity_id, now, True)

    def _touch_stats(self, entity_id: str, now: str) -> None:
        self._link_rows.reinforce(entity_id, now, False)

    def tally_proposal(self, name: str, from_ref: str) -> int:
        return self._proposal_rows.tally(name, from_ref)

    def proposals(self, *, threshold: int = PROPOSAL_THRESHOLD) -> list[dict]:
        return self._proposal_rows.pending(threshold)

    def accept_proposal(
        self, name: str, entity_type: str, *, source: str = "user"
    ) -> str:
        identity = self.upsert_entity(name, entity_type, source=source)
        self._proposal_rows.discard(name)
        return identity

    def reject_proposal(self, name: str) -> bool:
        return self._proposal_rows.discard(name)

    def orphan_counts(self) -> dict:
        populations = (
            (
                "semantic_orphans",
                "semantic_memory",
                "s",
                "l.from_kind = 'semantic' AND l.from_ref = s.key",
            ),
            (
                "episodic_orphans",
                "episodic_memories",
                "e",
                "l.from_kind = 'episodic' AND l.from_ref = e.id",
            ),
            ("phantom_entities", "mem_entities", "e", "l.to_entity = e.id"),
        )
        counts = {}
        for label, table, alias, relationship in populations:
            query = (
                f"SELECT COUNT(*) AS n FROM {table} {alias} WHERE {alias}.is_deleted = 0 "
                f"AND NOT EXISTS (SELECT 1 FROM mem_links l WHERE {relationship})"
            )
            counts[label] = int(self.db.execute(query).fetchone()["n"])
        return counts

    def summary(self) -> dict:
        queries = (
            ("entities", "SELECT COUNT(*) FROM mem_entities WHERE is_deleted = 0"),
            ("links", "SELECT COUNT(*) FROM mem_links"),
            ("linked_records", "SELECT COUNT(DISTINCT from_ref) FROM mem_links"),
            ("proposals", "SELECT COUNT(*) FROM mem_entity_proposals"),
        )
        result = {
            label: int(self.db.execute(sql).fetchone()[0]) for label, sql in queries
        }
        result.update(self.orphan_counts())
        return result

    def entity_graph(self) -> dict:
        return self._projection.render()

    def log_volunteer(
        self,
        *,
        entity_id: str,
        entity_name: str,
        arm: str,
        confidence: float,
        from_kind: str,
        record_ref: str,
        recall_at_volunteer: int,
        session_key: str = "",
    ) -> int:
        return self._volunteers.record(
            entity_id,
            entity_name,
            arm,
            confidence,
            from_kind,
            record_ref,
            recall_at_volunteer,
            session_key,
        )

    def volunteer_qrels(
        self, *, window_days: int | None = None
    ) -> dict[str, list[str]]:
        return self._volunteers.qrels(window_days)

    def volunteer_precision(self, *, window_days: int | None = None) -> dict:
        return self._volunteers.precision(window_days)

    def prune_volunteer_events(self, *, keep_days: int = 90) -> int:
        return self._volunteers.prune(keep_days)


def _merge_aliases(existing_json: str | None, incoming: "list[str]") -> list[str]:
    try:
        previous = set(json.loads(existing_json or "[]"))
    except (json.JSONDecodeError, TypeError):
        previous = set()
    additions = {alias for alias in incoming if alias and alias.strip()}
    return sorted(previous.union(additions))


def _iso_days_ago(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    return cutoff.isoformat()


def _now() -> str:
    timestamp = datetime.now(tz=timezone.utc)
    return timestamp.isoformat()
