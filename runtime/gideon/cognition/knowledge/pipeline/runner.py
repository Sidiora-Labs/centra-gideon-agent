"""Ingestion runner — orchestrates one item through its node-graph (#30).

Entry point ``ingest_item``: load the item → pick its code-owned graph → execute the
DAG (each node output → the extracted-content pool) → run terminal stages over the
whole bundle (consolidated text → insights → embed) → set ``processing_status``.
Per-node + per-stage progress is broadcast over per-resource SSE so the detail view
can show live ingestion transparency.

Pure-python in Task A (text/document graphs); model-backed media/video nodes layer on
in Task B (#47) and degrade gracefully (skipped when their use-case has no model).
"""

from __future__ import annotations

import logging

from gideon.cognition.knowledge.pipeline import (
    TERMINAL_STAGES,
    ensure_nodes_registered,
    graph_for,
)
from gideon.cognition.knowledge.pipeline.executor import PipelineExecutor
from gideon.cognition.knowledge.pipeline.types import NodeContext
from gideon.integrations.knowledge_providers.base import ENRICHMENT_FULL, ENRICHMENT_RAW

logger = logging.getLogger(__name__)


def _enrichment_for(store, item: dict) -> str:
    """The enrichment mode governing this item's ingestion (WATCHED-SOURCES §6.3).

    ``full`` for everything the user created locally (no ``source_id``) — the native path
    is unchanged. For an item a WatchedSource wrote, its source's setting decides.

    The unresolvable case is deliberately asymmetric: a source item whose ``sources`` row
    is gone or unreadable degrades to ``raw``, not ``full``. The no-AI setting is a promise
    made to the user about specific content, and content whose promise we can no longer
    READ must not be handed to a model on the assumption it was fine; the cost of guessing
    raw is a missing summary, the cost of guessing full is a broken guarantee.
    """
    source_id = (item or {}).get("source_id")
    if not source_id:
        return ENRICHMENT_FULL
    try:
        source = store.get_source(source_id)
    except (
        Exception
    ):  # noqa: BLE001 — an unreadable source row must not fail the ingest
        logger.debug("enrichment lookup failed for source %s", source_id, exc_info=True)
        return ENRICHMENT_RAW
    if not source:
        return ENRICHMENT_RAW
    return (
        ENRICHMENT_FULL
        if source.get("enrichment") == ENRICHMENT_FULL
        else ENRICHMENT_RAW
    )


def progress_feed(item_id: str) -> str:
    return f"knowledge:ingest:{item_id}"


async def ingest_item(
    store,
    item_id: str,
    *,
    embedder=None,
    insights_pool=None,
    params_for=None,
    publish=None,
) -> str:
    """Run the full ingestion graph for *item_id*. Returns the final status
    (``done`` | ``partial`` | ``failed``). Never raises — a failure is recorded on
    the item as ``processing_status='failed'`` + ``processing_error``.

    *publish* (optional) is a ``(event: str, data: dict) -> None`` SSE emitter for
    live progress; *params_for* layers user node-execution-param config.
    """
    ensure_nodes_registered()
    item = store.get_item(item_id)
    if not item:
        return "failed"

    item_type = item.get("type") or item.get("item_type") or "note"
    enrichment = _enrichment_for(store, item)
    raw_mode = enrichment == ENRICHMENT_RAW

    def _emit(event: str, **data) -> None:
        if publish:
            try:
                publish(event, {"item_id": item_id, **data})
            except Exception:
                logger.debug("knowledge ingest publish failed", exc_info=True)

    store.update_item(
        item_id, processing_status="processing", processing_error=None, touch=False
    )
    store.db.commit()
    _emit("ingest_started", item_type=item_type)

    try:
        graph = graph_for(item_type, enrichment=enrichment)
    except Exception as exc:
        logger.exception("graph build failed for %s", item_type)
        store.update_item(
            item_id, processing_status="failed", processing_error=str(exc), touch=False
        )
        store.db.commit()
        _emit("ingest_failed", error=str(exc))
        return "failed"

    ctx = NodeContext(
        item_id=item_id,
        item_type=item_type,
        file_path=item.get("file_path") or "",
        content=item.get("content") or "",
        url=item.get("url") or "",
    )
    executor = PipelineExecutor(
        graph,
        params_for=params_for,
        on_node=lambda nt, phase: _emit("node", node=nt, phase=phase),
    )

    try:
        result = await executor.run(ctx)

        if store.get_item(item_id) is None:
            _cleanup_orphaned_artifacts(item_id)
            return "deleted"

        store.clear_extracted_contents(item_id)
        for out in result.pooled_outputs():
            store.add_extracted_content(
                item_id,
                out.node_type,
                backend=out.backend,
                text=out.text,
                metadata=out.metadata,
            )
        for row in result.pool_rows():
            store.add_extracted_content(
                item_id,
                row.node_type,
                backend=row.backend,
                text=row.text,
                metadata=row.metadata,
            )

        _persist_structural_metadata(store, item_id, item, result)

        pooled = result.pooled_outputs()
        consolidated = ""
        if "consolidate" in result.outputs and result.outputs["consolidate"].success:
            consolidated = result.outputs["consolidate"].text
        elif pooled:
            consolidated = pooled[0].text
        consolidated = consolidated or (item.get("content") or "")

        if not consolidated.strip() and (item.get("file_path") or ""):
            fresh = store.get_item(item_id) or item
            consolidated = _structural_descriptor(fresh) or consolidated

        if consolidated and not (item.get("content") or "").strip():
            store.update_item(item_id, content=consolidated, touch=False)
            store.db.commit()
        else:
            wc = len((item.get("content") or "").split())
            if wc != (item.get("word_count") or 0):
                store.update_item(item_id, word_count=wc, touch=False)
                store.db.commit()

        if raw_mode:
            insights_phase = entities_phase = intents_phase = "skipped"
            insights_ok = True
            for stage in ("insights", "entities", "intents"):
                _emit("node", node=stage, phase="skipped")
        else:
            _emit("node", node="insights", phase="running")
            insights_ok = await _run_insights(
                store, item_id, consolidated, insights_pool
            )
            insights_phase = "done" if insights_ok else "failed"
            _emit("node", node="insights", phase=insights_phase)
            if insights_ok:
                from gideon.integrations.action_providers.knowledge_persist_provider import (
                    run_ingest_conflict_pass,
                )

                run_ingest_conflict_pass(store, item_id)

            _emit("node", node="entities", phase="running")
            entities_phase = await _run_entities_stage(
                store, item_id, consolidated, insights_pool
            )
            _emit("node", node="entities", phase=entities_phase)

            _emit("node", node="intents", phase="running")
            intents_phase = await _run_intents_stage(
                store, item_id, item_type, consolidated, insights_pool
            )
            _emit("node", node="intents", phase=intents_phase)

        _emit("node", node="embed", phase="running")
        embed_phase = _embed(store, item_id, embedder)
        _emit("node", node="embed", phase=embed_phase)

        _emit("node", node="dedup", phase="running")
        dedup_phase, dedup_result = _run_dedup_stage(store, item_id, embedder)
        _emit("node", node="dedup", phase=dedup_phase)
        if dedup_result:
            _emit("dedup", **dedup_result)
    except Exception as exc:
        if store.get_item(item_id) is None:
            _cleanup_orphaned_artifacts(item_id)
            return "deleted"
        logger.exception("knowledge ingest failed mid-pipeline for %s", item_id)
        store.update_item(
            item_id,
            processing_status="failed",
            processing_error=str(exc)[:500],
            touch=False,
        )
        store.db.commit()
        _emit("ingest_failed", error=str(exc))
        return "failed"

    status = result.status
    proc_error = None
    if status in ("failed", "partial") and result.failed:
        msgs = []
        for nt in result.failed:
            fout = result.outputs.get(nt)
            err = (getattr(fout, "error", "") or "").strip() if fout else ""
            msgs.append(f"{nt}: {err}" if err else nt)
        proc_error = "; ".join(msgs)[:500]
        if status == "failed":
            scrape = result.outputs.get("bookmark_scrape")
            scrape_meta = getattr(scrape, "metadata", None) or {} if scrape else {}
            only_scrape_failed = result.failed == ["bookmark_scrape"]
            if only_scrape_failed and scrape_meta.get("error_kind") == "unreachable":
                status = "unreachable"
    elif status == "partial" and result.skipped:
        proc_error = "Skipped (optional steps unavailable): " + ", ".join(
            result.skipped[:12]
        )
    if not insights_ok:
        if status == "done":
            status = "partial"
        insights_msg = (
            "insights: model unavailable (insights not refreshed — try regenerating)"
        )
        if not proc_error:
            proc_error = insights_msg
        elif not proc_error.startswith(insights_msg):
            proc_error = f"{insights_msg}; {proc_error}"
    node_phases: dict[str, str] = {}
    for nt in result.ran:
        node_phases[nt] = "done"
    for nt in result.failed:
        node_phases[nt] = "failed"
    for nt in result.skipped:
        node_phases[nt] = "skipped"
    for nt in getattr(graph, "nodes", {}):
        node_phases.setdefault(nt, "skipped")
    terminal_phases = {
        "insights": insights_phase,
        "entities": entities_phase,
        "intents": intents_phase,
        "embed": embed_phase,
        "dedup": dedup_phase,
    }
    node_phases.update({stage: terminal_phases[stage] for stage in TERMINAL_STAGES})
    _merge_file_metadata(store, item_id, {"node_phases": node_phases})

    store.update_item(
        item_id, processing_status=status, processing_error=proc_error, touch=False
    )
    store.db.commit()
    _emit(
        "ingest_complete",
        status=status,
        ran=result.ran,
        skipped=result.skipped,
        failed=result.failed,
    )
    from gideon.extensions.apps.app_events import KNOWLEDGE_INGESTED
    from gideon.extensions.apps.app_events import emit as emit_platform_event

    if status in ("done", "partial"):
        emit_platform_event(KNOWLEDGE_INGESTED, {"item_id": item_id, "status": status})
    return status


def _structural_descriptor(item: dict) -> str:
    """A minimal human-readable line for a file item whose text extraction degraded —
    derived from the filename + structural metadata (dimensions, format, pages, size).
    Gives an otherwise content-less media item something to title, embed, and find on.
    """
    import os

    meta = item.get("file_metadata") or {}
    item_type = (item.get("item_type") or item.get("type") or "file").strip()
    name = os.path.basename(item.get("file_path") or "") or item_type
    bits: list[str] = []
    if meta.get("width") and meta.get("height"):
        bits.append(f"{meta['width']}×{meta['height']}")
    if meta.get("format"):
        bits.append(str(meta["format"]))
    if meta.get("page_count"):
        bits.append(f"{meta['page_count']} pages")
    if meta.get("duration_seconds"):
        bits.append(f"{round(float(meta['duration_seconds']))}s")
    if item.get("file_size"):
        kb = item["file_size"] / 1024
        bits.append(f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB")
    shape = ", ".join(bits)
    label = item_type.capitalize()
    return f"{label}: {name}" + (f" ({shape})" if shape else "")


def _cleanup_orphaned_artifacts(item_id: str) -> None:
    """Delete any derived files this item's pipeline wrote (``<item_id>.audio.wav`` /
    ``<item_id>.frame_NNN.jpg`` / ``<item_id>.dense*``) when the item was deleted while
    processing — the delete handler's sweep ran before these late-written files existed.
    Mirrors the delete handler's guard: only inside the knowledge files dir, item_id is a
    UUID so the glob has no metacharacters."""
    from pathlib import Path

    from gideon.cognition.knowledge import knowledge_files_dir

    try:
        files_root = Path(knowledge_files_dir()).resolve()
        for p in files_root.glob(f"{item_id}.*"):
            resolved = p.resolve()
            if resolved.is_relative_to(files_root) and resolved.is_file():
                resolved.unlink(missing_ok=True)
    except OSError:
        logger.debug("orphaned-artifact cleanup failed for %s", item_id, exc_info=True)


def _merge_file_metadata(store, item_id: str, new_keys: dict) -> None:
    """Merge keys into the item's file_metadata, re-reading current state first so a
    prior merge (structural metadata) in the same run isn't clobbered."""
    fresh = store.get_item(item_id) or {}
    merged = dict(fresh.get("file_metadata") or {})
    merged.update(new_keys)
    store.update_item(item_id, file_metadata=merged, touch=False)
    store.db.commit()


def _persist_structural_metadata(store, item_id: str, item, result) -> None:
    """Persist non-pooled media-node outputs onto the item. The exif node yields
    width/height/format/mode → merged into ``file_metadata`` (it sets ``pooled=False``
    so it never reaches the text pool — without this its output would be discarded).
    The thumbnail is made inline at upload, so the graph produces none here."""
    fields: dict[str, object] = {}

    exif = result.outputs.get("exif")
    if (
        exif is not None
        and getattr(exif, "success", False)
        and getattr(exif, "metadata", None)
    ):
        merged = dict((item or {}).get("file_metadata") or {})
        merged.update(exif.metadata)
        fields["file_metadata"] = merged

    doc = result.outputs.get("document_read") or result.outputs.get("bookmark_scrape")
    if (
        doc is not None
        and getattr(doc, "success", False)
        and getattr(doc, "metadata", None)
    ):
        shape = {
            k: v
            for k, v in doc.metadata.items()
            if k
            in (
                "page_count",
                "format",
                "sheet_count",
                "slide_count",
                "row_count",
                "paragraph_count",
            )
            and v is not None
        }
        if shape:
            _fm = fields.get("file_metadata") or (item or {}).get("file_metadata") or {}
            merged = dict(_fm) if isinstance(_fm, dict) else {}
            merged.update(shape)
            fields["file_metadata"] = merged

    sliced = result.outputs.get("document_slice")
    if (
        sliced is not None
        and getattr(sliced, "success", False)
        and (getattr(sliced, "metadata", None) or {}).get("sliced")
    ):
        keep = ("sections", "section_strategies", "references", "references_unkeyed")
        found = {k: v for k, v in sliced.metadata.items() if k in keep}
        if found:
            _fm = fields.get("file_metadata") or (item or {}).get("file_metadata") or {}
            merged = dict(_fm) if isinstance(_fm, dict) else {}
            merged.update(found)
            fields["file_metadata"] = merged

    scrape = result.outputs.get("bookmark_scrape")
    if (
        scrape is not None
        and getattr(scrape, "success", False)
        and getattr(scrape, "metadata", None)
    ):
        meta = scrape.metadata
        scraped_title = (meta.get("url_title") or "").strip()
        if scraped_title and not ((item or {}).get("url_title") or "").strip():
            fields["url_title"] = scraped_title
        if (
            meta.get("url_description")
            and not ((item or {}).get("url_description") or "").strip()
        ):
            fields["url_description"] = meta["url_description"]
        from gideon.cognition.knowledge.store import normalize_url

        cur_title = ((item or {}).get("title") or "").strip()
        cur_url = ((item or {}).get("url") or "").strip()
        title_is_url_placeholder = not cur_title or normalize_url(
            cur_title
        ) == normalize_url(cur_url)
        if scraped_title and title_is_url_placeholder:
            fields["title"] = scraped_title

    if fields:
        store.update_item(item_id, touch=False, **fields)
        store.db.commit()


def _write_extracted_entity(store, ent: dict) -> tuple[str, str] | None:
    name = (ent.get("name") or "").strip()
    if not name:
        return None
    aliases = ent.get("aliases") or []
    existing = store.find_entity(name)
    if existing:
        eid = existing["id"]
        store.backfill_entity_description(eid, ent.get("description"))
        store.merge_entity_aliases(eid, aliases)
    else:
        eid = store.add_entity(
            name=name,
            entity_type=ent.get("type", "concept"),
            description=ent.get("description"),
            aliases=aliases,
        )
    return name, eid


async def _run_entities_stage(store, item_id: str, content: str, pool) -> str:
    """Link + extract entities for the item, writing to the entity graph.

    Two passes, deliberately in this order:

    1. **The deterministic alias pre-pass** (MEMORY-GRAPH §1.3) — every entity the graph
       ALREADY knows whose name or alias literally appears gets a mention. Zero LLM calls,
       and crucially it runs **even when `pool is None`**: without it, a user with no model
       bound ingests a document that plainly names a known entity and gets nothing, because
       the extractor is the only thing that ever linked.
    2. **LLM extraction** — finds what is NEW (entities the graph has never seen, and the
       relations between them), which a string matcher structurally cannot.

    They are complementary, not redundant: the model discovers, the trie guarantees. Both
    write through `add_mention` (`INSERT OR IGNORE`), so an entity both find is one mention.

    Re-runs cleanly: the extraction path clears this item's prior mentions/relations first so
    a re-ingest doesn't dup — and the pre-pass is re-applied after that clear, so its links
    survive the very stage that wipes them.

    Returns the phase to report. Unlike the intents stage, this one is NOT wholly
    model-dependent: pass 1 is the deliberate model-free guarantee, so with no pool the stage
    still ran and linked — ``done``, not ``skipped``. Only a contentless item skips outright;
    an errored extraction reports ``failed`` (pass 1's links stand regardless).
    """
    if not content.strip():
        return "skipped"

    try:
        from gideon.cognition.knowledge.alias_prepass import link_known_entities

        link_known_entities(store, item_id, content)
    except Exception:
        logger.debug("alias pre-pass failed for %s", item_id, exc_info=True)

    if pool is None:
        return "done"
    try:
        from gideon.cognition.knowledge.extractor import EntityExtractor

        extraction = await EntityExtractor(pool=pool).extract(content)
    except Exception:
        logger.debug("entity extraction failed for %s", item_id, exc_info=True)
        return "failed"
    entities = extraction.get("entities") or []
    relations = extraction.get("relations") or []
    if not entities:
        return "done"
    try:
        prepass_links: list[tuple[str, str, str]] = []
        try:
            for row in store.db.execute(
                "SELECT m.entity_id, e.name, e.entity_type, m.context "
                "FROM mentions m JOIN entities e ON e.id = m.entity_id "
                "WHERE m.item_id = ?",
                (item_id,),
            ).fetchall():
                prepass_links.append(
                    (row["name"], row["entity_type"] or "concept", row["context"] or "")
                )
        except Exception:
            logger.debug(
                "alias pre-pass snapshot failed for %s", item_id, exc_info=True
            )

        store.clear_item_entities(item_id)

        for name, etype, context in prepass_links:
            try:
                existing = store.find_entity(name)
                eid = (
                    existing["id"]
                    if existing
                    else store.add_entity(name=name, entity_type=etype)
                )
                store.add_mention(item_id, eid, context=context or None)
            except Exception:
                logger.debug(
                    "alias re-link failed for %s → %r", item_id, name, exc_info=True
                )
        entity_map: dict[str, str] = {}
        for ent in entities:
            written = _write_extracted_entity(store, ent)
            if not written:
                continue
            name, eid = written
            entity_map[name] = eid
            store.add_mention(item_id, eid, context=ent.get("description"))
        for rel in relations:
            src = entity_map.get((rel.get("source") or "").strip())
            tgt = entity_map.get((rel.get("target") or "").strip())
            if src and tgt:
                store.add_entity_relation(
                    source_id=src,
                    target_id=tgt,
                    relation_type=rel.get("type", "uses") or "uses",
                    description=rel.get("description"),
                    source_item_id=item_id,
                )
        store.db.commit()
        store._load_graph()
        return "done"
    except Exception:
        logger.debug("entity graph write failed for %s", item_id, exc_info=True)
        return "failed"


async def _run_insights(store, item_id: str, content: str, pool) -> bool:
    """Extract + persist insights for the item. Returns False when the model call
    errored (e.g. cold/unavailable pool) so the caller can mark the item ``partial``
    instead of silently leaving it ``done`` with stale/empty insights. Returns True on
    success or when there's legitimately nothing to do (no content / empty result)."""
    if not content.strip():
        return True
    try:
        from gideon.cognition.knowledge.insights import InsightsExtractor

        insights = await InsightsExtractor(pool=pool).extract(
            content, raise_on_error=True
        )
    except Exception:
        logger.debug("insights extraction failed for %s", item_id, exc_info=True)
        return False
    if not insights:
        return True
    item = store.get_item(item_id)
    ai_title = str(insights.pop("title", "") or "").strip()
    prev_insights = dict((item or {}).get("insights") or {})
    prev_summary = str(prev_insights.get("summary") or "")
    merged = dict(prev_insights)
    merged.update(insights)
    fields: dict[str, object] = {"insights": merged}
    cur_summary = ((item or {}).get("summary") or "").strip()
    if insights.get("summary") and (
        not cur_summary or cur_summary == prev_summary.strip()
    ):
        fields["summary"] = insights["summary"]
    item_type = (item or {}).get("item_type") or (item or {}).get("type") or ""
    if ai_title and item_type != "journal":
        fields["ai_title"] = ai_title
        cur_title = ((item or {}).get("title") or "").strip()
        is_file_type = item_type in (
            "image",
            "audio",
            "video",
            "pdf",
            "document",
            "sheet",
            "slides",
        )
        content = (item or {}).get("content") or ""
        titled_by_content = bool(cur_title) and cur_title == content[:60].strip()
        orig_fn = str(
            ((item or {}).get("file_metadata") or {}).get("original_filename") or ""
        ).strip()
        titled_by_filename = cur_title == orig_fn if orig_fn else True
        if (is_file_type and titled_by_filename) or not cur_title or titled_by_content:
            fields["title"] = ai_title
    topics = [
        t for t in (insights.get("topics") or []) if isinstance(t, str) and t.strip()
    ]
    if topics and store.tags_are_all_ai_authored(item_id):
        fields["tags"] = topics
        fields["tag_source"] = "ai"
    store.update_item(item_id, touch=False, **fields)
    store.db.commit()
    return True


def _intents_path(store):
    """The intents.json sibling of the knowledge DB (per-store, cwd-partition model)."""
    from pathlib import Path

    db_path = getattr(store, "db_path", "") or ""
    return Path(db_path).parent / "intents.json" if db_path else Path("intents.json")


async def _run_intents_stage(
    store, item_id: str, item_type: str, content: str, pool
) -> str:
    """Run Tier-3 user intents over the consolidated content. Each relevant match is
    persisted as an outcome BY VALUE in the intent_outcomes table, with only a soft
    back-reference to this item — so the gathered insight survives item deletion.

    Returns the phase to report: ``skipped`` when the stage had nothing to run (no
    content, no user intents defined, or no model to match with — matching is the whole
    stage, so without a pool nothing happened), ``failed`` when the run errored,
    ``done`` when intents were actually matched against the content."""
    if not content.strip():
        return "skipped"
    try:
        from gideon.cognition.knowledge.intents import IntentStore, run_intents

        intents = IntentStore(_intents_path(store)).load()
        if not intents:
            return "skipped"
        if not pool:
            return "skipped"
        matches = await run_intents(intents, item_type, content, pool=pool)
    except Exception:
        logger.debug("intent stage failed for %s", item_id, exc_info=True)
        return "failed"
    store.clear_item_intent_outcomes(item_id)
    if not matches:
        return "done"
    item = store.get_item(item_id)
    item_title = (item or {}).get("title") or (item or {}).get("ai_title") or ""
    by_id = {i.id: i for i in intents}
    for m in matches:
        try:
            store.record_intent_outcome(
                m.intent_id,
                intent_name=(_bi.goal if (_bi := by_id.get(m.intent_id)) else ""),
                item_id=item_id,
                item_title=item_title,
                takeaway=m.takeaway,
                fields=m.fields,
            )
        except Exception:
            logger.debug(
                "recording outcome for intent %s failed", m.intent_id, exc_info=True
            )
    return "done"


def _embed(store, item_id: str, embedder) -> str:
    """Embed the item and return the phase to report: ``done`` only when a vector was
    actually written, ``skipped`` when there was no embedder / no vector to write (the
    common case — no embedding model bound), ``failed`` when the attempt errored.

    The phase is the item's ONLY record that this step ran, so it must reflect whether a
    vector exists. Reporting "done" for a no-op made an item with no embedding look
    fully processed, hiding the missing-vector condition from the ingest view.

    KL-9: after the WHOLE-ITEM vector, the item's consolidated text is structurally
    chunked (``knowledge.chunking``) and each chunk embedded into the ``chunks`` table.
    Chunks are ADDITIVE — the item row keeps its own vector; the chunk index is what
    gives retrieval reach into content deep in a long document."""
    if not embedder:
        return "skipped"
    try:
        from gideon.cognition.knowledge.embedder import floats_to_bytes

        item = store.get_item(item_id)
        if not item:
            return "skipped"
        vec = embedder.embed_for_item(
            item.get("title") or "",
            item.get("summary"),
            item.get("content"),
        )
        if not vec:
            return "skipped"
        store.db.execute(
            "UPDATE items SET embedding = ? WHERE id = ?",
            (floats_to_bytes(vec), item_id),
        )
        store.db.commit()
        embed_item_chunks(store, item_id, item.get("content") or "", embedder)
        return "done"
    except Exception:
        logger.debug("knowledge embed failed for %s", item_id, exc_info=True)
        return "failed"


def embedding_space_fingerprint(embedder) -> tuple[str, str]:
    """Return the provider/model identity of vectors produced by *embedder*.

    Retrieval is often handed ``UnifiedEmbedder.embed`` rather than the wrapper itself, so a
    bound method is unwrapped first. Explicit attributes keep provider implementations and
    focused embedders deterministic; the unified production path falls back to the active
    embedding binding. Unknown custom embedders deliberately share the legacy empty/empty
    space instead of pretending their Python class name identifies a vector space.
    """
    owner = getattr(embedder, "__self__", None) or embedder

    def _value(*names: str) -> str:
        for name in names:
            try:
                value = getattr(owner, name, "")
            except Exception:
                continue
            if value is not None and not callable(value) and str(value).strip():
                return str(value).strip()
        return ""

    provider = _value("embedding_provider", "provider_name", "provider")
    model = _value("embedding_model", "model_name", "model")
    if provider and model:
        return provider, model
    try:
        from gideon.cognition.knowledge.embedder import UnifiedEmbedder

        if isinstance(owner, UnifiedEmbedder):
            from gideon.integrations.embedding_providers.registry import (
                _active_embedding_spec,
            )

            spec = _active_embedding_spec()
            if spec:
                return str(spec[0] or ""), str(spec[1] or "")
    except Exception:
        logger.debug("embedding space fingerprint unavailable", exc_info=True)
    return provider, model


def active_batch_embed_fn(embedder):
    """The provider's BATCH embedding entry point for the active selection, or ``None``.

    ``None`` is not a failure — it means "this provider has no batch path", and
    ``embed_batch.embed_texts`` then falls back to the per-text fn for an identical result,
    only slower. So every caller passes both and reasons about one code path.

    Gated on *embedder* being the ``UnifiedEmbedder`` that ``create_embedder_from_config``
    builds, because that is the only embedder guaranteed to resolve the SAME active
    ``embedding`` selection the registry accessor resolves — i.e. the only one for which the
    batch fn and the embedder are the same model. An embedder the caller supplied (a test
    stub, or one handed in by an app) therefore keeps going through its own ``.embed``:
    routing it through the registry instead would write vectors from one model beside vectors
    from another in the same index, which is exactly the mixed-model corruption the
    embedding re-index path exists to prevent.

    Shared by ``embed_item_chunks`` here and ``KnowledgeStore.reembed_all`` — the two batch
    call sites — so the gate is decided once rather than re-derived per site.
    """
    from gideon.cognition.knowledge.embedder import UnifiedEmbedder

    if not isinstance(embedder, UnifiedEmbedder):
        return None
    try:
        from gideon.integrations.embedding_providers.registry import (
            get_active_embed_many_fn,
        )

        return get_active_embed_many_fn()
    except (
        Exception
    ):  # noqa: BLE001 — no batch path degrades to per-text, never fails ingest
        logger.debug("embed batching: batch entry point unresolvable", exc_info=True)
        return None


def embed_item_chunks(store, item_id: str, content: str, embedder) -> None:
    """Structurally chunk *content* and write each chunk (with its embedding) to the
    ``chunks`` table, additive to the item's whole-item vector.

    Public because it is the ONE chunk-write unit: the ingest path calls it for a new item
    and ``knowledge.chunk_backfill`` calls it for every pre-chunking item. Both therefore
    go through ``store.replace_chunks``, which is what keeps the ANN index (KL-11) in step
    — a bulk writer taking any other route would leave that index stale.

    All of an item's chunks are embedded in ONE pass through ``embed_batch.embed_texts``
    (KL-15) rather than one provider call per chunk. Two things change beyond the round
    trips: a transient failure is now retried with backoff instead of being swallowed by an
    inline ``except Exception: vec = None``, and a group that fails in a batch-shaped way is
    bisected, so a provider's undeclared ceiling costs a split rather than an item's whole
    chunk layer. The old inline swallow left an unembeddable library indistinguishable from a
    working one, with no log line anywhere.

    Never raises into the ingest: a chunking/embedding hiccup must not fail an item whose
    whole-item vector already landed. A chunk whose embedding degrades to None is stored
    vector-less (still FTS/keyword reachable) rather than dropped. Re-index embedders that
    expose only ``embed_for_item`` are adapted to the same single-text contract so repairing
    item vectors cannot leave the chunk layer stale."""
    from gideon.cognition.knowledge.chunking import chunk_text
    from gideon.cognition.knowledge.embed_batch import embed_texts
    from gideon.cognition.knowledge.embedder import floats_to_bytes

    embed_one = getattr(embedder, "embed", None)
    if not callable(embed_one):
        embed_for_item = getattr(embedder, "embed_for_item", None)
        if callable(embed_for_item):

            def embed_one(text):
                return embed_for_item(text, None)

        else:
            return
    try:
        provider, model = embedding_space_fingerprint(embedder)
        chunks = chunk_text(content)
        if chunks:
            vectors = embed_texts(
                [c.text for c in chunks],
                embed_many=active_batch_embed_fn(embedder),
                embed_one=embed_one,
            )
            for c, vec in zip(chunks, vectors):
                c.embedding = floats_to_bytes(vec) if vec else None
                c.embedding_provider = provider if vec else ""
                c.embedding_model = model if vec else ""
        store.replace_chunks(item_id, chunks)
    except Exception:
        logger.debug("knowledge chunk-embed failed for %s", item_id, exc_info=True)


def _run_dedup_stage(store, item_id: str, embedder) -> tuple[str, dict | None]:
    """P12 TIER-2 semantic dedup — runs AFTER `_embed` (the vector must exist; it doesn't at
    create time in the create-fast/enrich-async model). Fetches same-type candidates carrying
    an embedding and asks the pure `dedup.resolve_duplicate` (filename + cosine + date-gate) if
    the just-enriched item duplicates one. On a confirmed dup it ARCHIVES the format-recall
    LOSER (never deletes — archived is excluded from retrieval + reversible), which may be the
    NEW item or the existing one. Returns the truthful terminal phase and an optional verdict
    dict for SSE. Never raises into the pipeline — a dedup fault reports ``failed`` without
    failing an otherwise successful ingest.

    An unavailable embedder or missing vector reports ``skipped`` because no comparison could
    run. A completed candidate scan reports ``done`` whether or not it found a duplicate.
    TIER-1 exact dedup (URL/byte-hash, create-time in store.py) is unaffected.
    """
    if not embedder:
        return "skipped", None
    try:
        if not getattr(embedder, "is_available", lambda: True)():
            return "skipped", None
        from gideon.cognition.knowledge import dedup as dedup_mod

        item = store.get_item(item_id)
        if not item:
            return "skipped", None
        from gideon.cognition.knowledge.embedder import bytes_to_floats

        row = store.db.execute(
            "SELECT embedding FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        raw = (
            row["embedding"]
            if row is not None and not isinstance(row, tuple)
            else (row[0] if row else None)
        )
        vec = bytes_to_floats(raw or b"")
        if not vec:
            return "skipped", None
        candidate = {
            "id": item_id,
            "title": item.get("title") or "",
            "file_path": item.get("file_path") or "",
            "summary": item.get("summary") or "",
            "item_type": item.get("item_type") or "",
            "word_count": item.get("word_count", 0),
            "content_len": len(item.get("content") or ""),
            "processing_status": item.get("processing_status", ""),
            "created_at": item.get("created_at", ""),
            "embedding": vec,
        }
        for existing in store.find_fuzzy_dup_candidates(item_id):
            verdict = dedup_mod.resolve_duplicate(candidate, existing)
            if not verdict.is_dup:
                continue
            loser_id = verdict.loser_id
            winner_id = verdict.winner_id
            if not loser_id or loser_id == winner_id:
                continue
            store.update_item(loser_id, is_archived=True)
            store.db.commit()
            logger.info(
                "knowledge dedup: item %s duplicates %s (cos=%.3f, fsim=%.3f) — archived loser %s",
                item_id,
                existing.get("id"),
                verdict.cosine,
                verdict.filename_sim,
                loser_id,
            )
            return "done", {
                "winner_id": winner_id,
                "loser_id": loser_id,
                "cosine": round(verdict.cosine, 3),
                "filename_sim": round(verdict.filename_sim, 3),
            }
        return "done", None
    except Exception:
        logger.debug(
            "knowledge dedup failed for %s (non-fatal)", item_id, exc_info=True
        )
        return "failed", None


def _dedup(store, item_id: str, embedder) -> dict | None:
    """Return only the semantic-dedup verdict for compatibility with direct callers."""
    return _run_dedup_stage(store, item_id, embedder)[1]
