"""Compare a synthesis timestamp with cited changes and relevant new material."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from gideon.cognition.knowledge.semantics import SYNTHESIZED_KINDS, _parse
from gideon.cognition.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Staleness:
    item_id: str
    stale: bool
    new_source_items: int
    changed_sources: int
    checked_at: str
    scope: str

    def to_dict(self) -> dict[str, object]:
        return {entry.name: getattr(self, entry.name) for entry in fields(self)}


def is_synthesized(item_type: str) -> bool:
    return str(item_type or "") in SYNTHESIZED_KINDS


def _touched_after(row: sqlite3.Row, since: datetime) -> bool:
    timestamps = (_parse(str(row[key] or "")) for key in ("created_at", "updated_at"))
    return any(stamp is not None and stamp > since for stamp in timestamps)


def _citations_table_present(db: sqlite3.Connection) -> bool:
    try:
        found = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'item_citations'"
        ).fetchone()
    except sqlite3.Error:
        logger.debug(
            "could not inspect sqlite_master for item_citations", exc_info=True
        )
        return False
    return found is not None


@dataclass(frozen=True)
class _SourceChanges:
    store: KnowledgeStore
    item_id: str
    since: datetime

    def cited(self) -> tuple[int, bool]:
        if not _citations_table_present(self.store.db):
            return 0, False
        try:
            rows = self.store.db.execute(
                "SELECT DISTINCT c.source_item_id AS source_item_id, i.updated_at AS updated_at "
                "FROM item_citations c JOIN items i ON i.id = c.source_item_id WHERE c.item_id = ?",
                (self.item_id,),
            ).fetchall()
        except sqlite3.Error:
            logger.debug(
                "item_citations unreadable for %s", self.item_id, exc_info=True
            )
            return 0, False
        stamps = (_parse(str(row["updated_at"] or "")) for row in rows)
        return sum(stamp is not None and stamp > self.since for stamp in stamps), True

    def tagged(self, tags: list[int]) -> int:
        if not tags:
            return 0
        slots = ",".join("?" for _ in tags)
        rows = self.store.db.execute(
            "SELECT DISTINCT i.id AS id, i.item_type AS item_type, i.created_at AS created_at, i.updated_at AS updated_at "
            "FROM item_tags it JOIN items i ON i.id = it.item_id "
            f"WHERE it.tag_id IN ({slots}) AND i.id != ? AND COALESCE(i.status, 'active') = 'active'",
            (*tags, self.item_id),
        ).fetchall()
        observed = (
            row for row in rows if not is_synthesized(str(row["item_type"] or ""))
        )
        return sum(_touched_after(row, self.since) for row in observed)


def _changed_cited_sources(
    store: KnowledgeStore, item_id: str, since: datetime
) -> tuple[int, bool]:
    return _SourceChanges(store, item_id, since).cited()


def _new_tagged_items(
    store: KnowledgeStore, item_id: str, tag_ids: list[int], since: datetime
) -> int:
    return _SourceChanges(store, item_id, since).tagged(tag_ids)


def staleness_for(store: KnowledgeStore, item_id: str) -> Staleness:
    item = store.get_item(item_id)
    if not item:
        raise KeyError(item_id)
    checked = datetime.now(timezone.utc).isoformat()
    if not is_synthesized(str(item.get("item_type") or "")):
        return Staleness(
            item_id,
            False,
            0,
            0,
            checked,
            "not a synthesized item — staleness does not apply",
        )
    since = _parse(str(item.get("updated_at") or ""))
    if since is None:
        return Staleness(
            item_id, False, 0, 0, checked, "no readable timestamp on this item"
        )
    tags = store.db.execute(
        "SELECT t.id AS tag_id, t.name AS name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
        "WHERE it.item_id = ? ORDER BY t.name",
        (item_id,),
    ).fetchall()
    names = [str(row["name"]) for row in tags]
    new = _new_tagged_items(store, item_id, [int(row["tag_id"]) for row in tags], since)
    changed, known = _changed_cited_sources(store, item_id, since)
    scope = (
        "items tagged " + ", ".join(names)
        if names
        else "cited sources only — this synthesis carries no tags"
    )
    if not known:
        scope += "; no cited sources known"
    return Staleness(item_id, bool(new or changed), new, changed, checked, scope)
