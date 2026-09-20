"""Resolve record mentions, seed known identities, and replay linking over stored records."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from gideon.cognition.memory_graph import ENTITY_TYPES, AliasIndex, MemoryGraph, Mention

logger = logging.getLogger(__name__)
_ABOUT_PREFIXES = ("user.persona.", "pref.facet.identity.")
_PROJECT_KEY = re.compile(r"^project\.([a-z0-9][a-z0-9_-]*)\.", re.IGNORECASE)
_REFERENCE = re.compile(
    r"\b(?:https?://|[a-z]+\.[a-z0-9_]+\.[a-z0-9_.]+)", re.IGNORECASE
)
_NAME_CUE = re.compile(
    r"(?:my name is|i am|i'm|call me|name's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    re.IGNORECASE,
)


def classify_link(key: str, text: str, mention: Mention, index_names: dict) -> str:
    kind = index_names.get(mention.entity_id, "")
    subject = (key or "").lower().startswith(_ABOUT_PREFIXES)
    if subject:
        return "about"
    project = _PROJECT_KEY.match(key or "")
    if project and kind == "project":
        return "same_project"
    return "references" if _REFERENCE.search(text or "") else "mentions"


class _RecordLinkPass:
    def __init__(self, graph, index, kind, reference, text, key, batch, replace):
        self.graph, self.index = graph, index
        self.kind, self.reference = kind, reference
        self.text, self.key, self.batch, self.replace = text, key, batch, replace
        self.report: dict[str, Any] = dict(
            mentions=0, links=0, proposals=0, entities=[]
        )

    def mentions(self, haystack: str) -> None:
        kinds = {entity.id: entity.entity_type for entity in self.graph.entities()}
        admitted = set()
        for mention in self.index.find(haystack):
            self.report["mentions"] += 1
            relation = classify_link(self.key, self.text, mention, kinds)
            identity = mention.entity_id, relation
            if identity in admitted:
                continue
            admitted.add(identity)
            inserted = self.graph.add_link(
                from_kind=self.kind,
                from_ref=self.reference,
                to_entity=mention.entity_id,
                link_type=relation,
                provenance="extracted",
                confidence=1.0,
                context=mention.context(haystack),
            )
            if inserted:
                self.report["links"] += 1
                self.report["entities"].append(mention.entity_id)

    def propose(self) -> None:
        for name in self.index.unknown_capitalized(self.text or ""):
            self.graph.tally_proposal(name, self.reference)
            self.report["proposals"] += 1

    def run(self) -> dict:
        try:
            content = f"{self.key} {self.text}" if self.key else (self.text or "")
            if self.replace:
                self.graph.drop_links_for(self.kind, self.reference)
            self.mentions(content)
            self.propose()
            if self.batch:
                _link_batch_siblings(
                    self.graph,
                    from_kind=self.kind,
                    from_ref=self.reference,
                    batch=self.batch,
                )
        except Exception:
            logger.debug(
                "memory linker failed for %s/%s",
                self.kind,
                self.reference,
                exc_info=True,
            )
        return self.report


def link_record(
    graph: MemoryGraph,
    index: AliasIndex,
    *,
    from_kind: str,
    from_ref: str,
    text: str,
    key: str = "",
    batch_ref: str | None = None,
    replace: bool = True,
) -> dict:
    return _RecordLinkPass(
        graph, index, from_kind, from_ref, text, key, batch_ref, replace
    ).run()


def _link_batch_siblings(
    graph: MemoryGraph, *, from_kind: str, from_ref: str, batch: str
) -> None:
    previous = graph.db.execute(
        "SELECT DISTINCT from_kind, from_ref FROM mem_links WHERE to_ref = ? AND link_type = 'temporal_proximity'",
        (batch,),
    ).fetchall()
    targets = [batch]
    targets.extend(row["from_ref"] for row in previous if row["from_ref"] != from_ref)
    for target in targets:
        graph.add_link(
            from_kind=from_kind,
            from_ref=from_ref,
            to_ref=target,
            link_type="temporal_proximity",
            provenance="extracted",
            confidence=1.0,
        )


def _text_of(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return next(
            (
                value[name]
                for name in ("text", "rule", "value", "description")
                if isinstance(value.get(name), str)
            ),
            "",
        )
    return ""


def _cased_in_text(slug: str, text: str) -> str | None:
    if slug and text:
        span = re.search(rf"\b{re.escape(slug)}\b", text, flags=re.IGNORECASE)
        if span is not None:
            return text[span.start() : span.end()]
    return None


def _person_names(text: str) -> list[str]:
    names = (match.group(1).strip() for match in _NAME_CUE.finditer(text or ""))
    return [name for name in names if name and name.lower() not in {"not", "the", "a"}]


class _IdentitySeeds:
    @staticmethod
    def from_fact(key: str, value: object):
        if key.startswith("project."):
            match = _PROJECT_KEY.match(key)
            name = match.group(1) if match else None
            if not name and key == "project.name":
                name = value if isinstance(value, str) else None
            if name:
                yield _cased_in_text(str(name), _text_of(value)) or str(name), "project"
        else:
            text = value.get("text") if isinstance(value, dict) else value
            for name in _person_names(str(text or "")):
                yield name, "person"

    @staticmethod
    def knowledge_rows(path: Path):
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as database:
            database.row_factory = sqlite3.Row
            return database.execute(
                "SELECT id, name, entity_type, aliases FROM entities"
            ).fetchall()

    @staticmethod
    def from_knowledge(row):
        kind = (row["entity_type"] or "").lower()
        if kind not in ENTITY_TYPES:
            kind = "topic"
        try:
            aliases = list(json.loads(row["aliases"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            aliases = []
        return row["name"], kind, aliases, f"knowledge:{row['id']}"


def _was_deleted(graph: MemoryGraph, name: str) -> bool:
    return (
        graph.db.execute(
            "SELECT 1 FROM mem_entities WHERE LOWER(name) = LOWER(?) AND is_deleted = 1 LIMIT 1",
            ((name or "").strip(),),
        ).fetchone()
        is not None
    )


def seed_from_memory_facts(graph: MemoryGraph) -> int:
    rows = graph.db.execute(
        "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 "
        "AND (key LIKE 'pref.facet.identity.%' OR key LIKE 'project.%')"
    ).fetchall()
    total = 0
    for row in rows:
        key = row["key"]
        try:
            decoded = json.loads(row["value_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        for name, kind in _IdentitySeeds.from_fact(key, decoded):
            if _was_deleted(graph, name):
                continue
            graph.upsert_entity(name, kind, source="facet")
            total += 1
    return total


def seed_from_knowledge(graph: MemoryGraph, knowledge_db_path=None) -> int:
    selected = knowledge_db_path
    if selected is None:
        try:
            from gideon.cognition.knowledge import knowledge_db_path as configured_path

            selected = configured_path()
        except Exception:
            logger.debug("no knowledge store to seed from", exc_info=True)
            return 0
    path = Path(selected)
    if not path.exists():
        return 0
    try:
        records = _IdentitySeeds.knowledge_rows(path)
    except sqlite3.Error:
        logger.debug("knowledge entity read failed", exc_info=True)
        return 0
    touched = 0
    for row in records:
        name, kind, aliases, source = _IdentitySeeds.from_knowledge(row)
        if _was_deleted(graph, name):
            continue
        try:
            graph.upsert_entity(name, kind, aliases=aliases, source=source)
        except ValueError:
            continue
        touched += 1
    return touched


def seed_all(graph: MemoryGraph, *, knowledge_db_path=None) -> dict:
    counts = {}
    for label, acquire in (
        ("from_facts", lambda: seed_from_memory_facts(graph)),
        ("from_knowledge", lambda: seed_from_knowledge(graph, knowledge_db_path)),
    ):
        counts[label] = acquire()
    return counts


class _LinkReplay:
    def __init__(self, graph: MemoryGraph, batch_size: int, limit: int | None):
        self.graph, self.batch_size, self.limit = graph, batch_size, limit
        self.index = graph.build_index()
        self.before = graph.summary()
        self.processed = self.created = 0

    @staticmethod
    def semantic(row) -> dict:
        try:
            value = json.loads(row["value_json"])
        except (json.JSONDecodeError, TypeError):
            value = row["value_json"]
        content = value.get("text") if isinstance(value, dict) else value
        return dict(from_ref=row["key"], key=row["key"], text=str(content or ""))

    @staticmethod
    def episodic(row) -> dict:
        return dict(from_ref=row["id"], text=row["text"] or "")

    def replay(self, kind: str, query: str, decode) -> None:
        suffix = f" LIMIT {int(self.limit)}" if self.limit else ""
        rows = self.graph.db.execute(query + suffix).fetchall()
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            for row in batch:
                result = link_record(
                    self.graph, self.index, from_kind=kind, **decode(row)
                )
                self.processed += 1
                self.created += result["links"]

    def run(self) -> dict:
        sources = (
            (
                "semantic",
                "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 ORDER BY key",
                self.semantic,
            ),
            (
                "episodic",
                "SELECT id, text FROM episodic_memories WHERE is_deleted = 0 ORDER BY id",
                self.episodic,
            ),
        )
        for kind, query, decode in sources:
            self.replay(kind, query, decode)
        return dict(
            records_processed=self.processed,
            links_created=self.created,
            before=self.before,
            after=self.graph.summary(),
        )


def backfill(
    graph: MemoryGraph, *, batch_size: int = 200, limit: int | None = None
) -> dict:
    return _LinkReplay(graph, batch_size, limit).run()
