"""KL-19 — the structural editing verbs, and what each one must not lose.

The tests are organised around the four ways a restructure can look finished while being
wrong, because those are the failures the atom names rather than the ones a row-count check
would catch:

1. **The derived layer keeps the old text's vectors.** A split's halves inherit chunk rows and
   an item vector computed over the parent's body; every search still returns and every result
   is about text that is no longer there. `test_split_does_not_leave_the_halves_holding_the_
   parents_vectors` measures the VECTOR VALUES, not the presence of rows, because a stale
   vector is present.
2. **An inbound reference is silently orphaned.** Citations name an item as a source with no
   foreign key to stop the delete, and `[[Title]]` wikilinks name it by title in another item's
   prose. Both survive a naive restructure looking perfectly well-formed and resolving to
   nothing.
3. **The undo is not really an undo.** Restoring the item row without its relations, its
   memberships and its citations' original chunk numbers reports success and loses the graph.
4. **A doubled submit doubles the effect.** A retry, a double click or an at-least-once queue
   turns one split into two.

The final test is the atom's own validation bar: a real multi-item library is restructured
through a sequence of verbs, and then SEARCH, the GRAPH and CITATIONS are each asserted to
still resolve. A test that only asserted the rows changed would not meet it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.cognition import knowledge as knowledge_pkg
from gideon.cognition.knowledge import maintenance, maintenance_passes, restructure
from gideon.cognition.knowledge.store import KnowledgeStore


class _CharEmbedder:
    """A deterministic, CONTENT-SENSITIVE embedder — the same shape the chunking suite uses.

    Content-sensitivity is the whole point here: a vector that depends on the text is what lets
    a test tell "recomputed from the new body" from "carried over from the old one". A constant
    vector would make the stale-vector test pass no matter what the code did.
    """

    def is_available(self):
        return True

    def embed(self, text):
        if not (text or "").strip():
            return None
        t = text.strip()
        return [float(len(t)), float(ord(t[0])), float(ord(t[-1]))]

    def embed_for_item(self, title, summary, content=None):
        from gideon.cognition.knowledge.embedder import compose_item_text

        return self.embed(compose_item_text(title, summary, content))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real store AND an isolated home.

    `mark_dirty` writes its watermark into `config_dir()`, so without the env override every
    verb in this suite would touch the real ~/.gideon. The knowledge db is under
    `tmp_path` for the same reason: this suite deletes and rewrites items.
    """
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    maintenance.clear_passes()
    s = KnowledgeStore(str(tmp_path / "knowledge.db"))
    try:
        yield s
    finally:
        s.close()
        maintenance.clear_passes()


BODY = (
    "opening paragraph about caching\n"
    "\n"
    "## Eviction policy\n"
    "least recently used wins here\n"
    "\n"
    "## Warmup strategy\n"
    "prefill on deploy for latency\n"
)


def _note(store, title, content="some body text", **kw):
    return store.create_typed_item(item_type="note", title=title, content=content, **kw)


def _offset(content, heading):
    return content.index(heading)


def _apply(store, verb, item_id, params, *, relink=True):
    """Preview then confirm, the way any caller must."""
    plan = restructure.plan(store, verb, item_id, params)
    return restructure.apply(
        store, verb, item_id, params, token=plan.token, relink=relink
    )


def test_split_boundaries_come_from_the_chunkers_own_heading_rule():
    """A split must cut where the chunker sections, or the halves re-chunk along other seams."""
    from gideon.cognition.knowledge import chunking

    bounds = restructure.sections(BODY)

    assert [b["title"] for b in bounds] == ["Eviction policy", "Warmup strategy"]
    for bound in bounds:
        assert BODY[bound["offset"] :].startswith("## " + bound["title"])
    assert [b.title for b in chunking.section_boundaries(BODY)] == [
        b["title"] for b in bounds
    ]


def test_a_document_with_no_headings_offers_no_split_boundaries():
    assert restructure.sections("just one long paragraph with no headings at all") == []


def test_a_merge_states_the_citations_it_would_break_before_applying(store):
    keep = _note(store, "Survivor")
    loser = _note(store, "Folded copy")
    citer = _note(store, "Citer", content="a claim [1]")
    store.set_item_citations(
        citer, [{"marker": 1, "source_item_id": loser, "chunk_index": 2}]
    )

    plan = restructure.plan(store, "merge", keep, {"merge_id": loser})

    kinds = {b.kind for b in plan.breaks}
    assert "citation" in kinds
    citation_break = next(b for b in plan.breaks if b.kind == "citation")
    assert citation_break.relinkable is True
    assert citer in citation_break.refs
    assert store.get_item(loser) is not None
    assert store.item_citations(citer)[0]["source_item_id"] == loser


def test_a_retitle_states_the_wikilinks_it_would_break_and_offers_to_relink(store):
    target = _note(store, "Cache Design")
    _note(
        store, "Referrer", content="see [[Cache Design]] and [[Cache Design|the doc]]"
    )

    plan = restructure.plan(store, "retitle", target, {"title": "Caching Design"})

    link_break = next(b for b in plan.breaks if b.kind == "wikilink")
    assert link_break.relinkable is True
    assert "2" in link_break.message
    assert plan.relinkable is True


def test_change_kind_warns_when_the_new_kind_expects_citations_the_item_lacks(store):
    item = _note(store, "Unsourced")

    plan = restructure.plan(store, "change_kind", item, {"kind": "report"})

    contract = next(b for b in plan.breaks if b.kind == "kind_contract")
    assert contract.relinkable is False


def test_a_verb_with_nothing_to_break_reports_no_breaks(store):
    item = _note(store, "Lonely", content=BODY)

    plan = restructure.plan(
        store, "split", item, {"offsets": [_offset(BODY, "## Eviction")]}
    )

    assert plan.breaks == ()
    assert plan.relinkable is False


def test_a_confirm_with_a_token_from_no_preview_is_refused(store):
    item = _note(store, "Doc", content=BODY)
    params = {"offsets": [_offset(BODY, "## Eviction")]}

    with pytest.raises(restructure.PreviewStale) as caught:
        restructure.apply(store, "split", item, params, token="0" * 32)

    assert caught.value.code == "preview_stale"
    assert caught.value.detail["plan"]["token"] != "0" * 32
    assert store.get_item(item)["content"] == BODY


def test_a_body_edited_between_preview_and_confirm_invalidates_the_token(store):
    item = _note(store, "Doc", content=BODY)
    params = {"offsets": [_offset(BODY, "## Eviction")]}
    plan = restructure.plan(store, "split", item, params)
    store.update_item(item, content=BODY + "\nan edit that arrived after the preview\n")

    with pytest.raises(restructure.PreviewStale):
        restructure.apply(store, "split", item, params, token=plan.token)


def test_a_token_cannot_be_reused_for_different_parameters(store):
    """The durability lesson: binding only the STATE lets a confirm act on another selection."""
    item = _note(store, "Doc", content=BODY)
    previewed = restructure.plan(
        store, "split", item, {"offsets": [_offset(BODY, "## Eviction")]}
    )

    with pytest.raises(restructure.PreviewStale):
        restructure.apply(
            store,
            "split",
            item,
            {"offsets": [_offset(BODY, "## Warmup")]},
            token=previewed.token,
        )


def test_a_break_appearing_between_the_phases_invalidates_the_token(store):
    """The user decided against the break list they were shown; a new one needs a new decision."""
    keep = _note(store, "Survivor")
    loser = _note(store, "Folded copy")
    plan = restructure.plan(store, "merge", keep, {"merge_id": loser})
    assert plan.breaks == ()
    citer = _note(store, "Late citer", content="claim [1]")
    store.set_item_citations(citer, [{"marker": 1, "source_item_id": loser}])

    with pytest.raises(restructure.PreviewStale):
        restructure.apply(store, "merge", keep, {"merge_id": loser}, token=plan.token)


def test_a_doubled_split_submit_creates_one_set_of_children(store):
    item = _note(store, "Doc", content=BODY)
    params = {"offsets": [_offset(BODY, "## Eviction"), _offset(BODY, "## Warmup")]}
    plan = restructure.plan(store, "split", item, params)

    first = restructure.apply(store, "split", item, params, token=plan.token)
    second = restructure.apply(store, "split", item, params, token=plan.token)

    assert first["idempotent"] is False
    assert len(first["created"]) == 2
    assert second["idempotent"] is True
    assert second["created"] == first["created"]
    total = store.db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert total == 3


@pytest.mark.parametrize(
    ("verb", "params"),
    [
        ("split", {"offsets": [37]}),
        ("extract", {"start": 0, "end": 20, "title": "Lifted"}),
        ("retitle", {"title": "A New Name"}),
        ("move", {"tags": ["reshelved"]}),
        ("change_kind", {"kind": "insight"}),
    ],
)
def test_every_verb_is_idempotent_under_a_doubled_submit(store, verb, params):
    item = _note(store, "Doc", content=BODY)
    if verb == "split":
        params = {"offsets": [_offset(BODY, "## Eviction")]}
    plan = restructure.plan(store, verb, item, params)

    first = restructure.apply(store, verb, item, params, token=plan.token)
    before = store.db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    second = restructure.apply(store, verb, item, params, token=plan.token)
    after = store.db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]

    assert second["idempotent"] is True
    assert second["kept"] == first["kept"]
    assert before == after


def test_a_doubled_merge_submit_deletes_one_item(store):
    keep = _note(store, "Survivor")
    loser = _note(store, "Folded copy")
    params = {"merge_id": loser}
    plan = restructure.plan(store, "merge", keep, params)

    restructure.apply(store, "merge", keep, params, token=plan.token)
    second = restructure.apply(store, "merge", keep, params, token=plan.token)

    assert second["idempotent"] is True
    assert store.get_item(keep) is not None


def test_a_split_child_inherits_provenance_and_is_linked_to_its_parent(store):
    shelf = store.create_collection(name="Reading")
    parent = _note(
        store, "Caching", content=BODY, tags=["infra"], url="https://example.test/a"
    )
    store.add_to_collection(shelf, parent)
    store.set_item_identity(parent, kind="reference")

    result = _apply(store, "split", parent, {"offsets": [_offset(BODY, "## Eviction")]})
    child = store.get_item(result["created"][0])

    assert child["title"] == "Eviction policy"
    assert child["tags"] == ["infra"]
    assert child["url"] == "https://example.test/a"
    assert child["kind"] == "reference"
    assert [c["id"] for c in store.collections_for_item(child["id"])] == [shelf]
    edge = store.db.execute(
        "SELECT relation_type FROM item_relations WHERE source_item_id = ? AND target_item_id = ?",
        (child["id"], parent),
    ).fetchone()
    assert edge["relation_type"] == restructure.LINEAGE_RELATION


def test_a_split_child_does_not_inherit_the_watched_source_persist_key(store):
    """Copying `source_id`/`guid` would collide on the partial UNIQUE index or fake a sighting."""
    source_id = store.create_source(
        name="Feed", provider="watched-feed", kind="feed", spec={}
    )
    parent = store.create_typed_item(
        item_type="note", title="Polled", content=BODY, source_id=source_id, guid="g-1"
    )

    result = _apply(store, "split", parent, {"offsets": [_offset(BODY, "## Eviction")]})
    child = store.get_item(result["created"][0])

    assert not child.get("source_id")
    assert not child.get("guid")


def test_a_highlight_follows_the_text_it_marks_into_the_new_item(store):
    parent = _note(store, "Caching", content=BODY)
    store.add_annotation(parent, "least recently used wins here")
    store.add_annotation(parent, "opening paragraph about caching")

    result = _apply(store, "split", parent, {"offsets": [_offset(BODY, "## Eviction")]})
    child = result["created"][0]

    assert result["annotations_moved"] == 1
    assert [a["quote"] for a in store.list_annotations(child)] == [
        "least recently used wins here"
    ]
    assert [a["quote"] for a in store.list_annotations(parent)] == [
        "opening paragraph about caching"
    ]


def test_a_highlight_spanning_the_cut_is_reported_rather_than_moved_arbitrarily(store):
    parent = _note(store, "Caching", content=BODY)
    store.add_annotation(parent, "caching\n\n## Eviction policy\nleast")

    plan = restructure.plan(
        store, "split", parent, {"offsets": [_offset(BODY, "## Eviction")]}
    )

    stranded = next(b for b in plan.breaks if b.kind == "annotation")
    assert stranded.relinkable is False
    assert "stop marking" in stranded.message


def test_deleting_a_related_item_no_longer_trips_the_foreign_key(store):
    """The plain delete path, which is where the cascade's own fix is load-bearing.

    Worth its own test rather than trusting the merge one: `merge_items` REDIRECTS the relation
    rows before it reaches the cascade, so by then there is nothing left for the cascade to
    delete and the merge test passes with the cascade fix removed. `delete_item` has no such
    redirect — it is the caller that actually depends on it, and without this test the fix
    could have shipped inert.
    """
    doomed = _note(store, "Doomed")
    bystander = _note(store, "Bystander")
    assert store.add_item_relation(bystander, doomed, "depends_on")
    assert store.add_item_relation(doomed, bystander, "supersedes")

    store.delete_item(doomed)

    assert store.get_item(doomed) is None
    assert store.get_item(bystander) is not None
    assert (
        store.db.execute("SELECT COUNT(*) AS n FROM item_relations").fetchone()["n"]
        == 0
    )


def test_deleting_an_item_clears_its_membership_and_its_own_citations(store):
    """The other two rows nothing cleaned: `collection_items` has no FK on `item_id` at all."""
    shelf = store.create_collection(name="Shelf")
    doomed = _note(store, "Doomed")
    store.add_to_collection(shelf, doomed)
    store.set_item_citations(
        doomed, [{"marker": 1, "source_item_id": _note(store, "Src")}]
    )

    store.delete_item(doomed)

    assert (
        store.db.execute("SELECT COUNT(*) AS n FROM collection_items").fetchone()["n"]
        == 0
    )
    assert store.item_citations(doomed) == []


def test_merging_a_related_item_no_longer_trips_the_foreign_key(store):
    """A pre-existing defect this atom had to fix to ship merge at all.

    `item_relations` declares a bare `REFERENCES items(id)` and nothing ever deleted those
    rows, so with `foreign_keys=ON` any item a synthesis had written a typed relation for was
    undeletable AND unmergeable — `merge_items` raised `IntegrityError` from the cascade.
    """
    keep = _note(store, "Survivor")
    loser = _note(store, "Folded copy")
    third = _note(store, "Bystander")
    assert store.add_item_relation(third, loser, "depends_on")

    moved = store.merge_items(keep, loser)

    assert moved["relations"] == 1
    rows = [
        (r["source_item_id"], r["target_item_id"])
        for r in store.db.execute(
            "SELECT source_item_id, target_item_id FROM item_relations"
        )
    ]
    assert rows == [(third, keep)]


def test_a_merge_relinks_citations_at_the_survivor_and_widens_their_chunk(store):
    keep = _note(store, "Survivor")
    loser = _note(store, "Folded copy")
    citer = _note(store, "Citer", content="claim [1]")
    store.set_item_citations(
        citer, [{"marker": 1, "source_item_id": loser, "chunk_index": 4}]
    )

    _apply(store, "merge", keep, {"merge_id": loser})

    row = store.item_citations(citer)[0]
    assert row["source_item_id"] == keep
    assert row["chunk_index"] == -1


def test_declining_the_relink_leaves_the_break_the_preview_described(store):
    keep = _note(store, "Survivor")
    loser = _note(store, "Folded copy")
    citer = _note(store, "Citer", content="claim [1]")
    store.set_item_citations(citer, [{"marker": 1, "source_item_id": loser}])

    _apply(store, "merge", keep, {"merge_id": loser}, relink=False)

    assert store.item_citations(citer)[0]["source_item_id"] == loser


def test_a_retitle_rewrites_inbound_wikilinks_and_re_derives_the_logical_key(store):
    target = _note(store, "Cache Design")
    store.set_item_identity(target, kind="reference")
    referrer = _note(
        store, "Referrer", content="see [[Cache Design]] and [[Cache Design|docs]]"
    )
    before = store.get_item(target)["logical_key"]

    result = _apply(store, "retitle", target, {"title": "Caching Design"})

    assert result["wikilinks_relinked"]["links"] == 2
    body = store.get_item(referrer)["content"]
    assert "[[Caching Design]]" in body
    assert "[[Caching Design|docs]]" in body
    assert store.get_item(target)["logical_key"] == "reference:caching-design"
    assert before != store.get_item(target)["logical_key"]


def test_a_retitle_declining_the_relink_leaves_the_wikilinks_alone(store):
    target = _note(store, "Cache Design")
    referrer = _note(store, "Referrer", content="see [[Cache Design]]")

    _apply(store, "retitle", target, {"title": "Caching Design"}, relink=False)

    assert "[[Cache Design]]" in store.get_item(referrer)["content"]


def test_move_reshelves_and_retags_without_touching_the_body(store):
    keep_shelf = store.create_collection(name="Keep")
    drop_shelf = store.create_collection(name="Drop")
    item = _note(store, "Doc", content="untouched body", tags=["old"])
    store.add_to_collection(drop_shelf, item)

    _apply(store, "move", item, {"collections": [keep_shelf], "tags": ["new"]})

    assert [c["id"] for c in store.collections_for_item(item)] == [keep_shelf]
    assert store.get_item(item)["tags"] == ["new"]
    assert store.get_item(item)["content"] == "untouched body"


def test_move_refuses_a_smart_shelf_and_says_why(store):
    smart = store.create_collection(name="Recent", kind="smart", query="caching")
    item = _note(store, "Doc")

    with pytest.raises(restructure.RestructureError) as caught:
        restructure.plan(store, "move", item, {"collections": [smart]})

    assert caught.value.code == "smart_collection"
    assert "query" in caught.value.message


def test_change_kind_refuses_a_kind_outside_the_vocabulary(store):
    item = _note(store, "Doc")

    with pytest.raises(restructure.RestructureError) as caught:
        restructure.plan(store, "change_kind", item, {"kind": "not-a-kind"})

    assert caught.value.code == "unknown_kind"


def test_splitting_at_the_documents_first_heading_is_refused(store):
    body = "## Opening\nonly section\n"
    item = _note(store, "Doc", content=body)

    with pytest.raises(restructure.RestructureError) as caught:
        restructure.plan(store, "split", item, {"offsets": [0]})

    assert caught.value.code == "boundary_at_start"


def test_splitting_at_an_offset_that_is_not_a_boundary_is_refused(store):
    item = _note(store, "Doc", content=BODY)

    with pytest.raises(restructure.RestructureError) as caught:
        restructure.plan(store, "split", item, {"offsets": [5]})

    assert caught.value.code == "not_a_section_boundary"
    assert [s["title"] for s in caught.value.detail["sections"]] == [
        "Eviction policy",
        "Warmup strategy",
    ]


def test_extracting_the_whole_body_is_refused_rather_than_emptying_the_item(store):
    body = "one short passage"
    item = _note(store, "Doc", content=body)

    with pytest.raises(restructure.RestructureError) as caught:
        restructure.plan(
            store, "extract", item, {"start": 0, "end": len(body), "title": "All"}
        )

    assert caught.value.code == "extract_empties_source"


def test_extract_can_copy_a_passage_without_removing_it(store):
    body = "keep this sentence. and this one too."
    item = _note(store, "Doc", content=body)

    result = _apply(
        store,
        "extract",
        item,
        {"start": 0, "end": 19, "title": "Lifted", "keep_in_source": True},
    )

    assert store.get_item(item)["content"] == body
    assert store.get_item(result["created"][0])["content"] == "keep this sentence."


def _vectors(store, item_id):
    row = store.db.execute(
        "SELECT embedding FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    chunks = store.get_chunks(item_id, with_embedding=True)
    return row["embedding"], [c.get("embedding") for c in chunks]


def _run_refresh(store, monkeypatch, embedder):
    """Drive the refresh through KL-14's registered pass, never by calling the backfill."""
    monkeypatch.setattr(knowledge_pkg, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(knowledge_pkg, "get_knowledge_embedder", lambda: embedder)
    maintenance_passes.register_all()
    return maintenance.execute(batch_size=50)


def test_split_does_not_leave_the_halves_holding_the_parents_vectors(
    store, monkeypatch
):
    """🔴 "A split whose halves keep the parent's vectors is silently wrong."

    Asserted on the VECTOR VALUES, not on row presence: a stale vector is present, non-null and
    the wrong answer, so a test that only checked "has an embedding" would pass on the defect.

    Measured while writing this, and worth stating because it decides WHICH vector the
    assertion belongs on: the whole-item vector is composed from **title + summary only**.
    `compose_item_text` accepts `content` and documents it as unused — KL-9 moved body-level
    semantics into the chunk index on purpose. So a split legitimately leaves the parent's ITEM
    vector equal (its title and summary did not change), and the vectors that MUST move are the
    chunk ones. Asserting inequality on the item vector would have been asserting a behaviour
    the architecture deliberately does not have; asserting it on the chunks is the real contract.
    """
    embedder = _CharEmbedder()
    parent = _note(store, "Caching", content=BODY)
    _run_refresh(store, monkeypatch, embedder)
    parent_vector, parent_chunks = _vectors(store, parent)
    assert parent_vector is not None and parent_chunks
    before_texts = {c["text"] for c in store.get_chunks(parent)}
    assert any("prefill on deploy" in t for t in before_texts)

    result = _apply(store, "split", parent, {"offsets": [_offset(BODY, "## Warmup")]})
    child = result["created"][0]

    assert _vectors(store, parent) == (None, [])
    assert _vectors(store, child) == (None, [])

    outcome = _run_refresh(store, monkeypatch, embedder)
    assert outcome.per_pass[maintenance_passes.PASS_DERIVED_REFRESH] > 0
    new_parent_vector, new_parent_chunks = _vectors(store, parent)
    new_child_vector, new_child_chunks = _vectors(store, child)
    assert new_parent_vector is not None and new_parent_chunks
    assert new_child_vector is not None and new_child_chunks

    after_parent_texts = {c["text"] for c in store.get_chunks(parent)}
    assert not any("prefill on deploy" in t for t in after_parent_texts)
    assert any("prefill on deploy" in c["text"] for c in store.get_chunks(child))
    assert new_child_chunks != new_parent_chunks
    parent_set = {tuple(v or ()) for v in new_parent_chunks}
    child_set = {tuple(v or ()) for v in new_child_chunks}
    assert parent_set.isdisjoint(child_set)
    assert new_child_vector != new_parent_vector


def test_a_verb_clears_only_the_similarity_edges_the_item_itself_claimed(store):
    """A restructure must reclaim ITS OWN stale findings without destroying a neighbour's.

    The pair of assertions is the point. Dropping the item's own edge is the refresh working;
    leaving the neighbour's edge alone is the `by_source`/`by_target` writer-claim rule working,
    and a naive `DELETE WHERE source_item_id = ? OR target_item_id = ?` would pass the first
    assertion and silently fail the second.
    """
    a = _note(store, "Caching", content=BODY)
    mine = _note(store, "My finding", content="a document A's own pass matched")
    theirs = _note(
        store, "Their finding", content="a document that matched A from its side"
    )
    store.upsert_similarity_edges(
        [
            {
                "source_item_id": a,
                "target_item_id": mine,
                "score": 0.9,
                "claimed_by": a,
            },
            {
                "source_item_id": a,
                "target_item_id": theirs,
                "score": 0.8,
                "claimed_by": theirs,
            },
        ]
    )
    assert store.count_similarity_edges() == 2

    _apply(store, "split", a, {"offsets": [_offset(BODY, "## Eviction")]})

    remaining = [
        (r["source_item_id"], r["target_item_id"])
        for r in store.db.execute(
            "SELECT source_item_id, target_item_id FROM item_similarity_edges"
        )
    ]
    assert len(remaining) == 1
    assert theirs in remaining[0] and a in remaining[0]
    assert (
        store.db.execute(
            "SELECT 1 FROM similarity_sweeps WHERE item_id = ?", (a,)
        ).fetchone()
        is None
    )


def test_the_derived_refresh_pass_is_registered_on_the_maintenance_host(store):
    maintenance.clear_passes()
    registered = maintenance_passes.register_all()

    assert maintenance_passes.PASS_DERIVED_REFRESH in registered
    assert maintenance_passes.PASS_DERIVED_REFRESH in maintenance.registered_passes()


def test_the_refresh_pass_reports_progress_not_backlog_size(store, monkeypatch):
    """Registered `batched=True`, so returning a backlog COUNT would busy-loop the host.

    An item whose content is whitespace to Python but not to SQLite can never be chunked and
    never leaves the backlog. Returning work DONE means it contributes 0 and the host stops.
    """
    embedder = _CharEmbedder()
    _note(store, "Real", content=BODY)
    store.db.execute("UPDATE items SET content = ' ' WHERE title = 'Real'")
    store.db.commit()

    first = _run_refresh(store, monkeypatch, embedder)
    second = _run_refresh(store, monkeypatch, embedder)

    assert second.per_pass[maintenance_passes.PASS_DERIVED_REFRESH] == 0
    assert first.errors == {}


def test_a_restructure_marks_the_maintenance_watermark_dirty(store):
    maintenance.clear_up_to(maintenance.load_state().get("dirty_ts") or 0.0)
    item = _note(store, "Doc", content=BODY)
    maintenance.clear_up_to(float(maintenance.load_state().get("dirty_ts") or 0.0))
    assert not maintenance.is_dirty()

    _apply(store, "split", item, {"offsets": [_offset(BODY, "## Eviction")]})

    assert maintenance.is_dirty()


def test_undoing_a_merge_restores_the_item_its_relations_and_its_citations(store):
    keep = _note(store, "Survivor", content="survivor body")
    loser = _note(store, "Folded copy", content="folded body", tags=["gone"])
    shelf = store.create_collection(name="Shelf")
    store.add_to_collection(shelf, loser)
    store.add_annotation(loser, "folded body")
    bystander = _note(store, "Bystander")
    store.add_item_relation(bystander, loser, "depends_on")
    citer = _note(store, "Citer", content="claim [1]")
    store.set_item_citations(
        citer, [{"marker": 1, "source_item_id": loser, "chunk_index": 7}]
    )

    result = _apply(store, "merge", keep, {"merge_id": loser})
    assert store.get_item(loser) is None

    restructure.undo(store, result["undo_token"])

    restored = store.get_item(loser)
    assert restored is not None
    assert restored["content"] == "folded body"
    assert restored["tags"] == ["gone"]
    assert [c["id"] for c in store.collections_for_item(loser)] == [shelf]
    assert [a["quote"] for a in store.list_annotations(loser)] == ["folded body"]
    rows = [
        (r["source_item_id"], r["target_item_id"])
        for r in store.db.execute(
            "SELECT source_item_id, target_item_id FROM item_relations"
        )
    ]
    assert rows == [(bystander, loser)]
    row = store.item_citations(citer)[0]
    assert (row["source_item_id"], row["chunk_index"]) == (loser, 7)


def test_undoing_a_split_removes_the_children_and_restores_the_body(store):
    parent = _note(store, "Caching", content=BODY)
    result = _apply(store, "split", parent, {"offsets": [_offset(BODY, "## Eviction")]})
    children = result["created"]

    outcome = restructure.undo(store, result["undo_token"])

    assert outcome["restored"]["created_removed"] == len(children)
    assert store.get_item(parent)["content"] == BODY
    assert [store.get_item(c) for c in children] == [None for _ in children]
    assert (
        store.db.execute("SELECT COUNT(*) AS n FROM item_relations").fetchone()["n"]
        == 0
    )


def test_undoing_a_retitle_restores_the_referrers_bodies_too(store):
    target = _note(store, "Cache Design")
    referrer = _note(store, "Referrer", content="see [[Cache Design]] for detail")
    result = _apply(store, "retitle", target, {"title": "Caching Design"})
    assert "[[Caching Design]]" in store.get_item(referrer)["content"]

    restructure.undo(store, result["undo_token"])

    assert store.get_item(target)["title"] == "Cache Design"
    assert "[[Cache Design]]" in store.get_item(referrer)["content"]


def test_an_undo_re_invalidates_the_derived_layer(store, monkeypatch):
    embedder = _CharEmbedder()
    parent = _note(store, "Caching", content=BODY)
    result = _apply(store, "split", parent, {"offsets": [_offset(BODY, "## Eviction")]})
    _run_refresh(store, monkeypatch, embedder)
    split_vector, _ = _vectors(store, parent)
    assert split_vector is not None

    restructure.undo(store, result["undo_token"])

    assert _vectors(store, parent) == (None, [])


def test_an_undo_can_only_be_spent_once(store):
    item = _note(store, "Doc", content=BODY)
    result = _apply(store, "split", item, {"offsets": [_offset(BODY, "## Eviction")]})
    restructure.undo(store, result["undo_token"])

    with pytest.raises(restructure.RestructureError) as caught:
        restructure.undo(store, result["undo_token"])

    assert caught.value.code == "unknown_undo_token"


def test_the_undo_journal_is_bounded(store):
    for index in range(store.UNDO_KEEP + 4):
        item = _note(store, f"Doc {index}")
        _apply(store, "retitle", item, {"title": f"Renamed {index}"})

    assert len(store.list_undo(limit=200)) == store.UNDO_KEEP


def test_the_undo_journal_survives_reopening_the_store(store, tmp_path):
    """The snapshot lives in the database, not in process memory.

    An in-memory undo is lost by the one event most likely to follow a restructure the user
    regrets — a gateway restart — and "reversible within the session" would then mean
    "reversible until something restarts".
    """
    item = _note(store, "Caching", content=BODY)
    result = _apply(store, "split", item, {"offsets": [_offset(BODY, "## Eviction")]})
    path = store.db_path
    store.close()

    reopened = KnowledgeStore(str(path))
    try:
        restructure.undo(reopened, result["undo_token"])
        assert reopened.get_item(item)["content"] == BODY
    finally:
        reopened.close()


def test_restructuring_a_real_library_leaves_search_graph_and_citations_resolving(
    store, monkeypatch
):
    """The atom's own bar: restructure a real multi-item library, then prove it still works.

    Not "the rows changed" — the three READ PATHS a user actually depends on. Each is exercised
    before and after, so the assertion is that restructuring preserved them rather than that
    they happen to be non-empty.
    """
    embedder = _CharEmbedder()

    shelf = store.create_collection(name="Infrastructure")
    caching = _note(store, "Caching notes", content=BODY, tags=["infra", "perf"])
    queues = _note(
        store,
        "Queue notes",
        content="## Backpressure\nshed load early\n\n## Retries\nexponential and jittered\n",
        tags=["infra"],
    )
    glossary = _note(store, "Glossary", content="LRU means least recently used")
    synthesis = _note(
        store, "Latency review", content="warm caches help [1] and queues shed [2]"
    )
    referrer = _note(
        store, "Stray thought", content="see [[Caching notes]] for the eviction rule"
    )
    for item in (caching, queues, glossary):
        store.add_to_collection(shelf, item)
    store.add_annotation(caching, "least recently used wins here")

    entity = store.add_entity("LRU", "concept")
    store.add_mention(caching, entity)
    store.add_mention(glossary, entity)
    other = store.add_entity("Backpressure", "concept")
    store.add_mention(queues, other)
    store.add_entity_relation(entity, other, "related_to", source_item_id=caching)

    store.add_item_relation(synthesis, caching, "derived_from")
    store.add_item_relation(synthesis, queues, "derived_from")
    store.set_item_citations(
        synthesis,
        [
            {
                "marker": 1,
                "source_item_id": caching,
                "chunk_index": 1,
                "excerpt": "warm",
            },
            {
                "marker": 2,
                "source_item_id": queues,
                "chunk_index": 0,
                "excerpt": "shed",
            },
        ],
    )
    _run_refresh(store, monkeypatch, embedder)

    def search(term):
        return {r["id"] for r in store.search_items_fts(term, limit=50)}

    def graph_resolves():
        """Every graph edge still names live endpoints, and the ego graph still builds."""
        subgraph = store.get_entity_subgraph(entity, depth=2)
        live = {r["id"] for r in store.db.execute("SELECT id FROM items")}
        relations = [
            (r["source_item_id"], r["target_item_id"])
            for r in store.db.execute(
                "SELECT source_item_id, target_item_id FROM item_relations"
            )
        ]
        assert all(s in live and t in live for s, t in relations), relations
        mentions = [
            r["item_id"] for r in store.db.execute("SELECT item_id FROM mentions")
        ]
        assert all(m in live for m in mentions), mentions
        return subgraph

    def citations_resolve():
        """Every citation names an item that still exists — the staleness join's own test."""
        rows = store.db.execute(
            "SELECT c.source_item_id AS src, i.id AS found FROM item_citations c "
            "LEFT JOIN items i ON i.id = c.source_item_id WHERE c.item_id = ?",
            (synthesis,),
        ).fetchall()
        assert rows, "the synthesis lost its attributions entirely"
        return {r["src"]: r["found"] for r in rows}

    assert caching in search("eviction")
    assert queues in search("backpressure")
    assert graph_resolves()["nodes"]
    assert all(found is not None for found in citations_resolve().values())

    split = _apply(store, "split", caching, {"offsets": [_offset(BODY, "## Warmup")]})
    warmup = split["created"][0]
    _apply(store, "retitle", caching, {"title": "Caching notes (eviction)"})
    extracted = _apply(
        store,
        "extract",
        queues,
        {
            "start": 0,
            "end": queues_len(store, queues),
            "title": "Backpressure",
            "keep_in_source": True,
        },
    )
    _apply(store, "change_kind", glossary, {"kind": "glossary"})
    _apply(store, "move", warmup, {"collections": [shelf], "tags": ["perf"]})
    _apply(store, "merge", glossary, {"merge_id": extracted["created"][0]})
    _run_refresh(store, monkeypatch, embedder)

    assert "[[Caching notes (eviction)]]" in store.get_item(referrer)["content"]

    assert caching in search("eviction"), "the parent lost its own remaining text"
    assert warmup in search("prefill"), "the split-off half is unsearchable"
    assert queues in search("backpressure")
    assert search("caching"), "the FTS index went empty"
    assert caching in search("eviction")

    subgraph = graph_resolves()
    assert subgraph["nodes"], "the entity graph collapsed"
    lineage = store.db.execute(
        "SELECT COUNT(*) AS n FROM item_relations WHERE relation_type = ?",
        (restructure.LINEAGE_RELATION,),
    ).fetchone()["n"]
    assert lineage >= 2

    resolved = citations_resolve()
    assert all(found is not None for found in resolved.values()), resolved
    assert len(store.item_citations(synthesis)) == 2, "a marker was lost"

    for item_id in (caching, warmup, queues):
        vector, chunks = _vectors(store, item_id)
        assert vector is not None, f"{item_id} was left vector-less"
        assert chunks, f"{item_id} was left without chunks"
    for row in store.db.execute("SELECT item_id, text FROM chunks"):
        body = store.get_item(row["item_id"])["content"]
        assert row["text"].strip()[:40] in body, (row["item_id"], row["text"][:40])


def queues_len(store, item_id):
    """The offset of the second section of the queues note, for a keep-in-source extract."""
    content = store.get_item(item_id)["content"]
    return restructure.sections(content)[1]["offset"]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _call(store, handler, method, path, *, match_info=None, body=None):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(method, path, app=app, match_info=match_info or {})

    async def _json():
        if body is None:
            raise ValueError("no body")
        return body

    req.json = _json
    resp = _run(handler(req))
    return resp, json.loads(resp.body)


def _handlers():
    from gideon.interfaces.dashboard.handlers import knowledge as H

    return H


def test_every_restructure_route_is_actually_registered():
    """A handler nobody routed to is unreachable — assert the wiring, not just the function.

    The other tests in this file call the handlers directly, which proves they work and proves
    nothing about whether a request can reach them. `routes.md` is generated by a STATIC scan of
    the `add_post` calls, so it would list a route whose registration was unreachable too.
    """
    from gideon.interfaces.dashboard.handlers import knowledge as H

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=None)
    app["knowledge_llm_pool"] = object()
    H.setup_knowledge_routes(app)

    registered = {
        (r.method, str(getattr(r.resource, "canonical", "")))
        for r in app.router.routes()
    }
    assert ("GET", "/api/knowledge/items/{id}/sections") in registered
    assert ("POST", "/api/knowledge/items/{id}/restructure/{verb}") in registered
    assert ("GET", "/api/knowledge/restructure/undo") in registered
    assert ("POST", "/api/knowledge/restructure/undo") in registered


def test_the_restructure_verb_route_resolves_a_real_request_path():
    """The dynamic `{verb}` segment must not be shadowed by a sibling `/items/{id}/…` route."""
    from gideon.interfaces.dashboard.handlers import knowledge as H

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=None)
    app["knowledge_llm_pool"] = object()
    H.setup_knowledge_routes(app)
    app.freeze()

    resolved = _run(
        app.router.resolve(
            make_mocked_request(
                "POST", "/api/knowledge/items/abc/restructure/split", app=app
            )
        )
    )
    assert resolved.route.handler is H.restructure_item
    assert resolved.get_info() or True
    assert dict(resolved) == {"id": "abc", "verb": "split"}


def test_a_post_without_confirm_previews_and_touches_nothing(store):
    item = _note(store, "Doc", content=BODY)

    resp, payload = _call(
        store,
        _handlers().restructure_item,
        "POST",
        f"/api/knowledge/items/{item}/restructure/split",
        match_info={"id": item, "verb": "split"},
        body={"offsets": [_offset(BODY, "## Eviction")]},
    )

    assert resp.status == 200
    assert payload["confirmed"] is False
    assert payload["token"]
    assert payload["plan"]["summary"]
    assert store.get_item(item)["content"] == BODY


def test_a_confirm_echoing_the_token_applies(store):
    item = _note(store, "Doc", content=BODY)
    params = {"offsets": [_offset(BODY, "## Eviction")]}
    _, preview = _call(
        store,
        _handlers().restructure_item,
        "POST",
        f"/api/knowledge/items/{item}/restructure/split",
        match_info={"id": item, "verb": "split"},
        body=params,
    )

    resp, payload = _call(
        store,
        _handlers().restructure_item,
        "POST",
        f"/api/knowledge/items/{item}/restructure/split",
        match_info={"id": item, "verb": "split"},
        body={**params, "confirm": True, "token": preview["token"]},
    )

    assert resp.status == 200
    assert payload["ok"] is True
    assert payload["undo_token"] == preview["token"]
    assert len(payload["created"]) == 1


def test_a_stale_confirm_is_a_409_carrying_the_fresh_preview(store):
    item = _note(store, "Doc", content=BODY)
    params = {"offsets": [_offset(BODY, "## Eviction")]}

    resp, payload = _call(
        store,
        _handlers().restructure_item,
        "POST",
        f"/api/knowledge/items/{item}/restructure/split",
        match_info={"id": item, "verb": "split"},
        body={**params, "confirm": True, "token": "0" * 32},
    )

    assert resp.status == 409
    assert payload["error"]["code"] == "preview_stale"
    assert payload["plan"]["token"] != "0" * 32
    assert store.get_item(item)["content"] == BODY


def test_an_unknown_verb_is_a_404_in_the_platform_error_envelope(store):
    item = _note(store, "Doc")

    resp, payload = _call(
        store,
        _handlers().restructure_item,
        "POST",
        f"/api/knowledge/items/{item}/restructure/frobnicate",
        match_info={"id": item, "verb": "frobnicate"},
        body={},
    )

    assert resp.status == 404
    assert payload["error"]["code"] == "unknown_verb"
    assert payload["error"]["message"]


def test_a_refusal_reports_the_verbs_stable_code(store):
    item = _note(store, "Doc", content=BODY)

    resp, payload = _call(
        store,
        _handlers().restructure_item,
        "POST",
        f"/api/knowledge/items/{item}/restructure/split",
        match_info={"id": item, "verb": "split"},
        body={"offsets": [5]},
    )

    assert resp.status == 400
    assert payload["error"]["code"] == "not_a_section_boundary"
    assert payload["sections"]


def test_the_sections_endpoint_serves_the_split_boundaries(store):
    item = _note(store, "Doc", content=BODY)

    resp, payload = _call(
        store,
        _handlers().get_item_sections,
        "GET",
        f"/api/knowledge/items/{item}/sections",
        match_info={"id": item},
    )

    assert resp.status == 200
    assert [s["title"] for s in payload["sections"]] == [
        "Eviction policy",
        "Warmup strategy",
    ]
    assert payload["length"] == len(BODY)


def test_the_undo_endpoints_list_and_reverse(store):
    item = _note(store, "Doc", content=BODY)
    result = _apply(store, "split", item, {"offsets": [_offset(BODY, "## Eviction")]})

    resp, listed = _call(
        store,
        _handlers().list_restructure_undo,
        "GET",
        "/api/knowledge/restructure/undo",
    )
    assert resp.status == 200
    assert [u["token"] for u in listed["undoable"]] == [result["undo_token"]]
    assert listed["undoable"][0]["verb"] == "split"

    resp, payload = _call(
        store,
        _handlers().undo_restructure,
        "POST",
        "/api/knowledge/restructure/undo",
        body={"token": result["undo_token"]},
    )
    assert resp.status == 200
    assert payload["ok"] is True
    assert store.get_item(item)["content"] == BODY


def test_undoing_an_unknown_token_is_a_409_not_a_500(store):
    resp, payload = _call(
        store,
        _handlers().undo_restructure,
        "POST",
        "/api/knowledge/restructure/undo",
        body={"token": "nope"},
    )

    assert resp.status == 409
    assert payload["error"]["code"] == "unknown_undo_token"
