"""Persist workflow knowledge through explicit admission, metadata and mutation stages."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from gideon.cognition.knowledge import project_scope
from gideon.cognition.knowledge.semantics import (
    Claim,
    Mention,
    check_persist,
    decide_write,
)
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
DEFAULT_ITEM_TYPE = "note"
_LINEAGE_FIELDS = (
    "parent_ids",
    "reflection_count",
    "consolidated",
    "compression_ratio",
    "source_count",
)


@dataclass
class _CitationWiring:
    content: str
    summary: str
    stored: list[str] = field(default_factory=list)
    records: list[Any] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ConflictReview:
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _resolve_citations(
    cfg: dict[str, Any], *, body: str, summary: str
) -> _CitationWiring:
    raw = cfg.get("citation_sources")
    if not raw:
        return _CitationWiring(
            body, summary, [str(value) for value in cfg.get("citations") or ()]
        )
    from gideon.cognition.knowledge import citations

    references = _coerce_source_refs(raw)
    resolved = citations.resolve(body, references)
    records = list(resolved.citations)
    result = _CitationWiring(
        resolved.text,
        summary,
        records=records,
        warnings=list(resolved.warnings),
    )
    if summary and citations.parse_markers(summary):
        caption = citations.resolve(summary, references)
        result.summary = caption.text
        result.warnings.extend(caption.warnings)
        present = {int(getattr(record, "marker", 0)) for record in records}
        records.extend(
            record
            for record in caption.citations
            if int(getattr(record, "marker", 0)) not in present
        )
        records.sort(key=lambda record: int(getattr(record, "marker", 0)))
    result.stored = list(citations.persist_form(records))
    return result


def _coerce_source_refs(raw: Any) -> list[Any]:
    from gideon.cognition.knowledge.citations import SourceRef

    result = []
    for value in raw if isinstance(raw, list) else [raw]:
        if not isinstance(value, dict):
            result.append(value)
            continue
        try:
            marker = int(value.get("marker") or 0)
            raw_chunk = value.get("chunk_index", -1)
            chunk = -1 if raw_chunk is None else int(raw_chunk)
        except (ValueError, TypeError):
            continue
        identifier = str(value.get("item_id") or "").strip()
        if marker > 0 and identifier:
            result.append(
                SourceRef(
                    marker=marker,
                    item_id=identifier,
                    chunk_index=chunk,
                    excerpt=str(value.get("excerpt") or ""),
                )
            )
    return result


def _write_item_citations(store, item_id: str, records: list[Any]) -> None:
    if not records:
        return
    write = getattr(store, "set_item_citations", None)
    if not callable(write):
        logger.debug(
            "knowledge store keeps no per-marker citations — skipped for %s", item_id
        )
        return
    try:
        write(item_id, records)
    except Exception:
        logger.warning(
            "per-marker citations not stored for %s — the compact list still is",
            item_id,
            exc_info=True,
        )


@dataclass(frozen=True)
class _PreparedWrite:
    config: dict[str, Any]
    context: ActionContext
    title: str
    citations: _CitationWiring
    claims: Any
    validation: Any

    @classmethod
    def prepare(cls, config: dict[str, Any], context: ActionContext) -> _PreparedWrite:
        content = config.get("content")
        if content is None:
            raise ValueError(
                "knowledge-persist is missing 'content' — bind it to a node's output"
            )
        title = str(config.get("title") or "").strip()
        citations = _resolve_citations(
            config,
            body=content if isinstance(content, str) else _stringify(content),
            summary=str(config.get("summary") or ""),
        )
        claims = _bound_claims(config, citations.content)
        validation = check_persist(
            kind=str(config.get("kind") or "fact"),
            title=title,
            content=citations.content,
            summary=citations.summary,
            claims=[claim for claim in claims if isinstance(claim, dict)],
            citations=citations.stored,
            marker_citations=citations.records,
            unsourced=bool(config.get("unsourced")),
            ttl=str(config.get("ttl") or ""),
            expires_at=str(config.get("expires_at") or ""),
        )
        return cls(config, context, title, citations, claims, validation)

    def execute(self, store: Any, started: float) -> ActionResult:
        identity, fingerprint, previous = _lookup(store, self.validation.logical_key)
        decision = decide_write(
            logical_key=self.validation.logical_key,
            content_hash=self.validation.content_hash,
            existing_id=identity,
            existing_hash=fingerprint,
            mode=str(self.config.get("mode") or "upsert"),
        )
        receipt = dict(
            item_id=decision.item_id,
            logical_key=self.validation.logical_key,
            created=False,
            relations_persisted=0,
            mentions_appended=0,
            citation_warnings=self.citations.warnings,
            reason=decision.reason,
            conflicts=[],
            conflict_candidates=[],
        )

        def finish() -> ActionResult:
            return ActionResult(
                True,
                stdout=json.dumps(receipt),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if decision.action == "noop":
            receipt["relations_persisted"] = _write_model_edges(
                store, self.config, source_item=decision.item_id
            )
            return finish()
        source = str(self.config.get("source_ref") or _run_source_ref(self.context))
        identifier = decision.item_id or uuid.uuid4().hex[:12]
        metadata = {
            **previous,
            **_scope_metadata(self.config, self.context, existing=previous),
        }
        metadata.pop("conflicts", None)
        metadata.pop("conflict_candidates", None)
        scope_tags = project_scope.scope_tags(
            str(metadata.get(project_scope.PROJECT_ID_KEY, ""))
        )
        review = _ConflictReview()
        if self.claims:
            arriving = [claim for claim in self.claims if isinstance(claim, dict)]
            review = _detect_conflicts(
                store,
                arriving,
                item_id=decision.item_id,
                source_ref=identifier,
                edge_source=decision.item_id or "",
            )
            metadata["claims"], receipt["mentions_appended"] = _merge_claims(
                existing=metadata.get("claims") or [],
                incoming=arriving,
                source_ref=source,
            )
            if review.conflicts:
                metadata["conflicts"] = review.conflicts
            if review.candidates:
                metadata["conflict_candidates"] = review.candidates
        receipt["conflicts"] = review.conflicts
        receipt["conflict_candidates"] = review.candidates
        if decision.action == "reinforce":
            _write_metadata(store, decision.item_id, metadata, source_ref=source)
            _write_tags(store, decision.item_id, scope_tags)
            receipt["relations_persisted"] = _write_model_edges(
                store, self.config, source_item=decision.item_id
            )
            return finish()
        lineage = self.config.get("lineage")
        if isinstance(lineage, dict):
            metadata.update(
                {name: lineage[name] for name in _LINEAGE_FIELDS if name in lineage}
            )
        metadata.update(
            {
                name: self.config[name]
                for name in ("read_when", "citations", "source", "extraction")
                if self.config.get(name)
            }
        )
        if self.citations.records is not None:
            metadata["citations"] = list(self.citations.stored)
        try:
            _upsert_item(
                store,
                item_id=identifier,
                title=self.title,
                content=self.citations.content,
                summary=self.citations.summary,
                kind=self.validation.normalized_kind,
                logical_key=self.validation.logical_key,
                content_hash=self.validation.content_hash,
                expires_at=self.validation.expires_at,
                metadata=metadata,
                tags=[
                    *(str(tag) for tag in self.config.get("tags") or ()),
                    *scope_tags,
                ],
                creating=decision.action == "create",
            )
        except Exception as exc:
            return ActionResult(False, error=f"knowledge write failed: {exc}")
        if review.conflicts:
            _write_conflict_edges(store, review.conflicts, source_item=identifier)
        receipt["relations_persisted"] = _write_model_edges(
            store, self.config, source_item=identifier
        )
        _write_item_citations(store, identifier, self.citations.records or [])
        _enqueue_enrichment(identifier)
        receipt.update(item_id=identifier, created=decision.action == "create")
        return finish()


@dataclass(frozen=True)
class _ItemMutation:
    store: Any
    identifier: str
    fields: dict[str, Any]
    creating: bool

    def write(self, tags: list[str]) -> None:
        prior = None if self.creating else _fts_snapshot(self.store, self.identifier)
        if self.creating:
            values = {
                "id": self.identifier,
                "item_type": DEFAULT_ITEM_TYPE,
                **self.fields,
                "created_at": self.fields["updated_at"],
            }
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            self.store.db.execute(
                f"INSERT INTO items ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
        else:
            assignment = ", ".join(f"{name}=?" for name in self.fields)
            self.store.db.execute(
                f"UPDATE items SET {assignment} WHERE id=?",
                (*self.fields.values(), self.identifier),
            )
        self.store.db.commit()
        _write_tags(self.store, self.identifier, tags)
        _sync_fts(
            self.store,
            self.identifier,
            title=self.fields["title"],
            content=self.fields["content"],
            creating=self.creating,
            prior=prior,
        )


def _upsert_item(
    store,
    *,
    item_id: str,
    title: str,
    content: str,
    summary: str,
    kind: str,
    logical_key: str,
    content_hash: str,
    expires_at: str,
    metadata: dict[str, Any],
    tags: list[str],
    creating: bool,
) -> None:
    stamp = _now()
    fields = dict(
        title=title,
        content=content,
        summary=summary,
        updated_at=stamp,
        kind=kind,
        logical_key=logical_key,
        content_hash=content_hash,
        expires_at=expires_at,
        last_verified=stamp,
        file_metadata=json.dumps(metadata, ensure_ascii=False),
    )
    _ItemMutation(store, item_id, fields, creating).write(tags)


def _open_store():
    from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _bound_claims(config: dict[str, Any], content: str) -> list[dict[str, Any]]:
    """Claims explicitly supplied by a caller, or the assertion carried by a fact write.

    ``contradiction-review`` persists its statement as the body of a ``fact``.  Treating the
    body as mere prose meant that write never entered the claim index, so both the deterministic
    pass and the following model leg received an empty review.  An explicit ``claims`` key keeps
    full control, including the deliberate opt-out ``claims: []``.
    """
    if "claims" in config:
        raw = config.get("claims")
        return (
            [claim for claim in raw if isinstance(claim, dict)]
            if isinstance(raw, list)
            else []
        )
    if str(config.get("kind") or "fact").strip().lower() != "fact":
        return []
    statement = " ".join((content or "").split())
    return [{"statement": statement}] if statement else []


def _lookup(store, logical_key: str) -> tuple[str, str, dict[str, Any]]:
    if not logical_key:
        return "", "", {}
    try:
        record = store.db.execute(
            "SELECT id, content_hash, file_metadata FROM items WHERE logical_key = ? LIMIT 1",
            (logical_key,),
        ).fetchone()
    except Exception:
        logger.debug("knowledge lookup failed", exc_info=True)
        return "", "", {}
    if record is None:
        return "", "", {}
    try:
        metadata = json.loads(record["file_metadata"] or "{}")
    except (ValueError, TypeError):
        metadata = {}
    return (
        str(record["id"]),
        str(record["content_hash"] or ""),
        metadata if isinstance(metadata, dict) else {},
    )


def _write_tags(store, item_id: str, tags: list[str]) -> int:
    if not tags:
        return 0
    count, stamp = 0, _now()
    try:
        names = (tag.strip().lower() for tag in tags)
        for name in names:
            if not name:
                continue
            store.db.execute(
                "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)",
                (name, stamp),
            )
            tag = store.db.execute(
                "SELECT id FROM tags WHERE name = ?", (name,)
            ).fetchone()
            if tag is not None:
                store.db.execute(
                    "INSERT OR IGNORE INTO item_tags (item_id, tag_id, source, added_at) VALUES (?, ?, 'workflow', ?)",
                    (item_id, tag["id"], stamp),
                )
                count += 1
        store.db.commit()
    except Exception:
        logger.warning("knowledge tag write failed for %s", item_id, exc_info=True)
    if not count:
        logger.warning(
            "knowledge item %s: none of %d tag(s) attached", item_id, len(tags)
        )
    return count


@dataclass(frozen=True)
class _IndexRevision:
    store: Any
    identifier: str

    def snapshot(self) -> tuple[int, str, str, str] | None:
        try:
            row = self.store.db.execute(
                "SELECT rowid, title, content, tags FROM items_fts_src WHERE rowid = (SELECT rowid FROM items WHERE id = ?)",
                (self.identifier,),
            ).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        return (
            int(row["rowid"]),
            str(row["title"] or ""),
            str(row["content"] or ""),
            str(row["tags"] or ""),
        )

    def replace(
        self,
        title: str,
        content: str,
        creating: bool,
        prior: tuple[int, str, str, str] | None,
    ) -> None:
        try:
            row = self.store.db.execute(
                "SELECT rowid FROM items WHERE id = ?", (self.identifier,)
            ).fetchone()
            if row is None:
                return
            tags = self.store.db.execute(
                "SELECT COALESCE(group_concat(t.name, ' '), '') FROM item_tags it JOIN tags t ON t.id = it.tag_id WHERE it.item_id = ?",
                (self.identifier,),
            ).fetchone()
            if not creating and prior is not None:
                self.store.db.execute(
                    "INSERT INTO items_fts (items_fts, rowid, title, content, tags) VALUES ('delete', ?, ?, ?, ?)",
                    prior,
                )
            self.store.db.execute(
                "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (row[0], title, content, str(tags[0] or "") if tags else ""),
            )
            self.store.db.commit()
        except Exception:
            logger.warning(
                "knowledge FTS sync failed for %s — it will not be searchable",
                self.identifier,
                exc_info=True,
            )


def _fts_snapshot(store, item_id: str) -> tuple[int, str, str, str] | None:
    return _IndexRevision(store, item_id).snapshot()


def _sync_fts(
    store,
    item_id: str,
    *,
    title: str,
    content: str,
    creating: bool,
    prior: tuple[int, str, str, str] | None = None,
) -> None:
    _IndexRevision(store, item_id).replace(title, content, creating, prior)


def _write_metadata(
    store, item_id: str, metadata: dict[str, Any], *, source_ref: str
) -> None:
    try:
        values = json.dumps(metadata, ensure_ascii=False), _now(), item_id
        store.db.execute(
            "UPDATE items SET file_metadata=?, last_verified=? WHERE id=?", values
        )
        store.db.commit()
    except Exception:
        logger.debug("knowledge metadata write failed", exc_info=True)


@dataclass(frozen=True)
class _ClaimNeighborhood:
    store: Any
    excluded: str

    def identifiers(self, statements: list[str]) -> list[str]:
        order: dict = {}
        for statement in statements[:3]:
            for row in _search_rows(self.store, statement):
                identifier = str(row.get("id") or "")
                if identifier and identifier != self.excluded:
                    order.setdefault(identifier, None)
        for identifier in _claim_bearing_ids(self.store, exclude=self.excluded):
            order.setdefault(identifier, None)
        return list(order)

    def claims(self, incoming: list) -> list:
        from gideon.cognition.knowledge import contradiction

        statements = [claim.statement for claim in incoming if claim.statement]
        if not statements:
            return []
        load = getattr(self.store, "claim_neighbours", None)
        if callable(load):
            return [
                contradiction.Claim.from_dict(raw)
                for raw in load(
                    statements,
                    exclude=self.excluded,
                    limit=contradiction.MAX_CONFLICT_CANDIDATES,
                )
                if isinstance(raw, dict) and raw.get("statement")
            ]
        result = []
        for identifier in self.identifiers(statements):
            for raw in _stored_claims(self.store, identifier):
                claim = contradiction.Claim.from_dict({**raw, "source_ref": identifier})
                if claim.statement:
                    result.append(claim)
            if len(result) >= contradiction.MAX_CONFLICT_CANDIDATES:
                break
        return result[: contradiction.MAX_CONFLICT_CANDIDATES]


def _neighbour_claims(store, incoming: list, *, exclude: str) -> list:
    return _ClaimNeighborhood(store, exclude).claims(incoming)


def _detect_conflicts(
    store, incoming: list[dict], *, item_id: str, source_ref: str, edge_source: str = ""
) -> _ConflictReview:
    try:
        from gideon.cognition.knowledge import contradiction

        arriving = [
            contradiction.Claim.from_dict(
                {**claim, "source_ref": claim.get("source_ref") or source_ref}
            )
            for claim in incoming
        ]
        candidates = _neighbour_claims(store, arriving, exclude=item_id)
        if not candidates:
            return _ConflictReview()
        conflicts = contradiction.find_conflicts(arriving, candidates)
        unsettled = contradiction.unsettled_candidates(arriving, candidates)
        if edge_source and conflicts:
            _write_edges(
                store,
                contradiction.edges_from_conflicts(conflicts),
                source_item=edge_source,
            )
        return _ConflictReview(
            conflicts=[conflict.to_dict() for conflict in conflicts],
            candidates=[candidate.to_dict() for candidate in unsettled],
        )
    except Exception:
        logger.warning("conflict detection failed — the write proceeds", exc_info=True)
        return _ConflictReview()


def run_ingest_conflict_pass(store: Any, item_id: str) -> _ConflictReview:
    """Flag claims extracted from a normal ingest using the provider write contract."""
    item = store.get_item(item_id)
    if not item:
        return _ConflictReview()
    insights = item.get("insights") or {}
    points = insights.get("key_points") if isinstance(insights, dict) else None
    arriving = [
        {"statement": point, "origin": "external", "source_ref": item_id}
        for point in (points or [])
        if isinstance(point, str) and point.strip()
    ]
    if not arriving:
        return _ConflictReview()
    metadata = dict(item.get("file_metadata") or {})
    metadata["claims"], _ = _merge_claims(
        existing=metadata.get("claims") or [], incoming=arriving, source_ref=item_id
    )
    review = _detect_conflicts(
        store, arriving, item_id=item_id, source_ref=item_id, edge_source=item_id
    )
    metadata.pop("conflicts", None)
    metadata.pop("conflict_candidates", None)
    if review.conflicts:
        metadata["conflicts"] = review.conflicts
    if review.candidates:
        metadata["conflict_candidates"] = review.candidates
    store.update_item(item_id, touch=False, file_metadata=metadata)
    store.db.commit()
    return review


def _claim_bearing_ids(store, *, exclude: str, limit: int = 40) -> list[str]:
    try:
        records = store.db.execute(
            "SELECT id FROM items WHERE is_archived = 0 AND id != ? AND file_metadata LIKE '%\"claims\"%' ORDER BY updated_at DESC LIMIT ?",
            (exclude or "", max(1, limit)),
        )
        return [str(row["id"]) for row in records]
    except Exception:
        logger.debug("claim-bearing scan failed", exc_info=True)
        return []


def _search_rows(store, query: str) -> list[dict]:
    try:
        expression = _fts_safe(query)
        records = store.db.execute(
            "SELECT i.id FROM items_fts f JOIN items i ON i.rowid = f.rowid WHERE items_fts MATCH ? AND i.is_archived = 0 LIMIT 10",
            (expression,),
        )
        return [dict(row) for row in records]
    except Exception:
        logger.debug("neighbour search failed", exc_info=True)
        return []


def _fts_safe(query: str) -> str:
    terms = [match.group() for match in re.finditer(r"[A-Za-z0-9]{3,}", query or "")]
    return " OR ".join(terms[:12]) or '""'


def _stored_claims(store, item_id: str) -> list[dict]:
    try:
        row = store.db.execute(
            "SELECT file_metadata FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    except Exception:
        return []
    if row is None:
        return []
    try:
        metadata = json.loads(row["file_metadata"] or "{}")
    except (ValueError, TypeError):
        return []
    claims = metadata.get("claims") if isinstance(metadata, dict) else None
    return (
        [claim for claim in claims if isinstance(claim, dict)]
        if isinstance(claims, list)
        else []
    )


def _relation_for(conflict: dict) -> str:
    preference = str(conflict.get("prefer") or "")
    return {"left": "supersedes"}.get(preference, "contradicts")


def _write_conflict_edges(store, conflicts: list[dict], *, source_item: str) -> int:
    from gideon.cognition.knowledge.contradiction import Edge

    edges = []
    for record in conflicts:
        edge = Edge(
            source=source_item,
            target=str(record.get("right_item") or ""),
            relation=_relation_for(record),
            confidence=float(record.get("confidence") or 1.0),
            provenance="extracted",
            justification=str(record.get("detail") or ""),
        )
        if edge.valid:
            edges.append(edge)
    return _write_edges(store, edges, source_item=source_item)


def _write_model_edges(store, proposal: Any, *, source_item: str) -> int:
    from gideon.cognition.knowledge.contradiction import parse_edge_proposals

    return _write_edges(
        store,
        parse_edge_proposals(proposal, source_item=source_item),
        source_item=source_item,
    )


@dataclass(frozen=True)
class _EdgeBatch:
    store: Any
    source: str

    def write(self, edges: list) -> int:
        written = 0
        for edge in edges:
            try:
                written += bool(
                    self.store.add_item_relation(
                        self.source,
                        edge.target,
                        edge.relation,
                        confidence=edge.confidence,
                        provenance=edge.provenance,
                    )
                )
            except Exception:
                logger.warning(
                    "could not write %s edge %s -> %s",
                    edge.relation,
                    self.source,
                    edge.target,
                    exc_info=True,
                )
        return written


def _write_edges(store, edges: list, *, source_item: str) -> int:
    return _EdgeBatch(store, source_item).write(edges)


def _merge_claims(
    *, existing: list, incoming: list[dict], source_ref: str
) -> tuple[list[dict], int]:
    claims = {}
    for raw in existing or ():
        parsed = Claim.from_dict(raw)
        if parsed:
            claims[parsed.id] = parsed
    added = 0
    for raw in incoming:
        arriving = Claim.from_dict(raw)
        if arriving is None:
            continue
        first = arriving.id not in claims
        accumulated = claims.setdefault(arriving.id, arriving)
        changed = accumulated.add_mention(
            Mention(source_ref=source_ref, confidence=arriving.confidence, quote="")
        )
        if first or changed:
            added += 1
    return [claim.to_dict() for claim in claims.values()], added


def _enqueue_enrichment(item_id: str) -> None:
    try:
        from gideon.cognition.knowledge.ingest import enqueue_item

        enqueue_item(item_id)
    except Exception:
        logger.debug(
            "knowledge enrichment enqueue unavailable for %s", item_id, exc_info=True
        )


def _scope_metadata(
    cfg: dict[str, Any], ctx: ActionContext, *, existing: dict[str, Any]
) -> dict[str, str]:
    payload = getattr(ctx, "payload", None) or {}
    scope = project_scope.write_scope(
        project_id=str(payload.get("project_id") or ""),
        run_id=str(payload.get("run_id") or ""),
        requested_policy=cfg.get("sharing_policy"),
    )
    if not scope:
        return {}
    result = {
        key: str(value)
        for key in (project_scope.PROJECT_ID_KEY, project_scope.RUN_ID_KEY)
        if (value := existing.get(key) or scope.get(key))
    }
    key = project_scope.SHARING_POLICY_KEY
    declared = str(cfg.get("sharing_policy") or "").strip()
    result[key] = (
        scope[key] if declared or not existing.get(key) else str(existing[key])
    )
    return result


def _run_source_ref(ctx: ActionContext) -> str:
    payload = getattr(ctx, "payload", None) or {}
    run, node = str(payload.get("run_id") or ""), str(payload.get("node_id") or "")
    if not node:
        return "workflow:unattributed"
    return f"workflow:{run}:{node}" if run else f"workflow:node:{node}"


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class KnowledgePersistActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "knowledge-persist"

    @property
    def display_name(self) -> str:
        return "Persist Knowledge"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        started = time.monotonic()
        config = action_config or {}
        if config.get("content") is None:
            return ActionResult(
                False,
                error="knowledge-persist is missing 'content' — bind it to a node's output",
            )
        prepared = _PreparedWrite.prepare(config, ctx)
        if not prepared.validation.ok:
            return ActionResult(False, error=prepared.validation.error)
        try:
            store = _open_store()
        except Exception as exc:
            return ActionResult(False, error=f"knowledge store unavailable: {exc}")
        try:
            return prepared.execute(store, started)
        finally:
            try:
                store.close()
            except Exception:
                logger.debug("knowledge persist store close failed", exc_info=True)
