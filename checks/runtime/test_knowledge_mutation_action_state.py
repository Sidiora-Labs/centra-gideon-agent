"""Knowledge persistence with actual identity, citations, claims and SQLite failures."""

import json
import time

import pytest

from gideon.cognition import knowledge
from gideon.cognition.knowledge import project_scope
from gideon.cognition.knowledge.citations import SourceRef
from gideon.cognition.knowledge.contradiction import Edge
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.integrations.action_providers import knowledge_persist_provider as persist
from gideon.integrations.action_providers.base import ActionContext


@pytest.fixture(autouse=True)
def mutation_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "config.json").write_text('{"providers": []}')
    monkeypatch.setattr(knowledge, "_store", None)
    yield tmp_path
    if knowledge._store is not None:
        knowledge._store.close()


@pytest.fixture
def store():
    database = KnowledgeStore(db_path=str(knowledge_db_path()))
    yield database
    database.close()


def context(project="alpha", run="r-one", node="write"):
    return ActionContext(
        "workflow_node", payload={"project_id": project, "run_id": run, "node_id": node}
    )


async def save(config, ctx=None):
    result = await persist.KnowledgePersistActionProvider().execute(
        config, ctx or context()
    )
    assert result.success, result.error
    return json.loads(result.stdout)


def row(store, identifier):
    result = dict(
        store.db.execute("SELECT * FROM items WHERE id=?", (identifier,)).fetchone()
    )
    result["metadata"] = json.loads(result["file_metadata"])
    return result


@pytest.mark.asyncio
async def test_repeat_and_create_mode_are_real_noops_without_scope_or_content_changes(
    store,
):
    config = {
        "title": "Canonical fact",
        "content": "first wording",
        "tags": [" Team ", "team"],
    }
    original = await save(config)
    before = row(store, original["item_id"])
    noop = await save(config, context("beta", "r-two"))
    refused_create = await save(
        {**config, "content": "another wording", "mode": "create"}
    )
    assert noop["item_id"] == refused_create["item_id"] == original["item_id"]
    assert "conflicts" not in noop and noop["mentions_appended"] == 0
    assert "mode=create" in refused_create["reason"]
    assert row(store, original["item_id"]) == before
    names = [
        item[0] for item in store.db.execute("SELECT name FROM tags WHERE name='team'")
    ]
    assert names == ["team"]


@pytest.mark.asyncio
async def test_reinforcement_keeps_body_age_and_owner_but_adds_independent_support(
    store,
):
    claims = [{"id": "measurement", "statement": "latency is low", "confidence": 0.6}]
    config = {"title": "Measurement", "content": "measured locally", "claims": claims}
    first = await save(config)
    stamp = "2001-01-01T00:00:00+00:00"
    store.db.execute(
        "UPDATE items SET updated_at=?, last_verified=? WHERE id=?",
        (stamp, stamp, first["item_id"]),
    )
    store.db.commit()
    result = await save(
        {
            **config,
            "mode": "append_evidence",
            "sharing_policy": "shared",
            "lineage": {"parent_ids": ["ignored"]},
            "tags": ["ignored-tag"],
        },
        context("beta", "r-two"),
    )
    updated = row(store, first["item_id"])
    assert result["mentions_appended"] == 1 and updated["updated_at"] == stamp
    assert updated["last_verified"] != stamp and updated["content"] == config["content"]
    metadata = updated["metadata"]
    assert (
        metadata["project_id"] == "alpha"
        and metadata["run_id"] == "r-one"
        and metadata["sharing_policy"] == "shared"
    )
    assert metadata["claims"][0]["support_count"] == 2 and metadata["claims"][0][
        "confidence"
    ] == pytest.approx(0.84)
    assert "parent_ids" not in metadata
    tags = {
        item[0]
        for item in store.db.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id=t.id WHERE it.item_id=?",
            (first["item_id"],),
        )
    }
    assert project_scope.scope_tags("alpha")[0] in tags
    assert project_scope.scope_tags("beta")[0] not in tags and "ignored-tag" not in tags
    replay = await save({**config, "mode": "append_evidence"}, context("beta", "r-two"))
    assert replay["mentions_appended"] == 0


@pytest.mark.asyncio
async def test_updating_keeps_birthday_replaces_fts_terms_and_retains_old_tags(store):
    first = await save(
        {"title": "Stable identity", "content": "aardvark", "tags": ["oldtag"]}
    )
    before = row(store, first["item_id"])
    second = await save(
        {"title": "Stable identity", "content": "buffalo", "tags": ["newtag"]}
    )
    assert second["item_id"] == first["item_id"] and not second["created"]
    assert row(store, first["item_id"])["created_at"] == before["created_at"]
    assert store.search_items_fts("aardvark") == []
    assert {item["id"] for item in store.search_items_fts("buffalo")} == {
        first["item_id"]
    }
    assert store.search_items_fts("oldtag") and store.search_items_fts("newtag")


@pytest.mark.parametrize("shape", ["mapping", "typed"])
@pytest.mark.asyncio
async def test_first_chunk_reference_survives_real_citation_rows_and_summary_resolution(
    store, shape
):
    source_id = store.create_typed_item(
        item_type="note", title="Source", content="measured source"
    )
    ref = dict(marker=1, item_id=source_id, chunk_index=0, excerpt="measured source")
    source = ref if shape == "mapping" else SourceRef(**ref)
    result = await save(
        {
            "kind": "insight",
            "title": "Derived",
            "content": "A derived claim [1]. Unknown [9].",
            "summary": "Source [1], unsupported [8].",
            "citation_sources": [source],
        }
    )
    stored = row(store, result["item_id"])
    assert "[9]" not in stored["content"] and "[8]" not in stored["summary"]
    assert len(result["citation_warnings"]) == 2
    assert stored["metadata"]["citations"] == [f"cite:1:0:{source_id}"]
    assert store.item_citations(result["item_id"]) == [
        {
            "marker": 1,
            "source_item_id": source_id,
            "chunk_index": 0,
            "excerpt": "measured source",
        }
    ]


@pytest.mark.asyncio
async def test_summary_only_evidence_is_deduplicated_and_rewrite_replaces_native_markers(
    store,
):
    sources = [
        store.create_typed_item(
            item_type="note", title=f"Source {n}", content=f"Source {n}"
        )
        for n in (1, 2)
    ]
    refs = [
        dict(marker=n, item_id=identifier) for n, identifier in enumerate(sources, 1)
    ]
    first = await save(
        {
            "kind": "insight",
            "title": "Summary",
            "content": "Evidence [1].",
            "summary": "Same [1], plus [2].",
            "citation_sources": refs,
        }
    )
    assert [item["marker"] for item in store.item_citations(first["item_id"])] == [1, 2]
    next_result = await save(
        {
            "kind": "insight",
            "title": "Summary",
            "content": "Only source two [2].",
            "citation_sources": refs,
        }
    )
    assert next_result["item_id"] == first["item_id"]
    assert [item["marker"] for item in store.item_citations(first["item_id"])] == [2]


@pytest.mark.asyncio
async def test_only_named_lineage_fields_can_reach_metadata(store):
    output = await save(
        {
            "title": "Lineage",
            "content": "trusted result",
            "lineage": {
                "parent_ids": ["p1"],
                "reflection_count": 2,
                "consolidated": True,
                "compression_ratio": 0.5,
                "source_count": 3,
                "claims": ["forged"],
                "project_id": "stolen",
                "sharing_policy": "shared",
            },
            "metadata": {"claims": ["also forged"]},
        }
    )
    metadata = row(store, output["item_id"])["metadata"]
    assert metadata["parent_ids"] == ["p1"] and metadata["reflection_count"] == 2
    assert metadata["source_count"] == 3 and metadata["compression_ratio"] == 0.5
    assert metadata["project_id"] == "alpha" and metadata["sharing_policy"] == "private"
    assert "claims" not in metadata


@pytest.mark.asyncio
async def test_real_constraint_failure_is_returned_without_losing_borrowed_connection(
    store,
):
    store.db.execute(
        "CREATE TRIGGER refuse_owned_item BEFORE INSERT ON items WHEN NEW.title = 'Refused' BEGIN SELECT RAISE(ABORT, 'local admission constraint'); END"
    )
    store.db.commit()
    result = await persist.KnowledgePersistActionProvider().execute(
        {"title": "Refused", "content": "cannot land"}, context()
    )
    assert (
        not result.success
        and result.error == "knowledge write failed: local admission constraint"
    )
    assert store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    assert store.db.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_missing_index_is_a_real_best_effort_failure_after_durable_item_write(
    store,
):
    prepared = persist._PreparedWrite.prepare(
        {"title": "No index", "content": "durable text"}, context()
    )
    store.db.execute("DROP TABLE items_fts")
    result = await prepared.execute(store, time.monotonic())
    assert result.success, result.error
    output = json.loads(result.stdout)
    assert row(store, output["item_id"])["content"] == "durable text"
    assert persist._fts_snapshot(store, output["item_id"]) is not None


@pytest.mark.asyncio
async def test_conflicting_claims_keep_both_records_and_write_supersession_after_insert(
    store,
):
    previous = await save(
        {
            "title": "Observed latency",
            "content": "original measurement",
            "claims": [
                {
                    "id": "old",
                    "statement": "Cold start latency is 4.2 seconds",
                    "origin": "external",
                }
            ],
        }
    )
    latest = await save(
        {
            "title": "Corrected latency",
            "content": "operator measurement",
            "claims": [
                {
                    "id": "new",
                    "statement": "Cold start latency is 9.1 seconds",
                    "origin": "user",
                }
            ],
        }
    )
    assert latest["conflicts"] and latest["conflicts"][0]["prefer"] == "left"
    assert row(store, previous["item_id"])["metadata"]["claims"][0][
        "statement"
    ].endswith("4.2 seconds")
    assert row(store, latest["item_id"])["metadata"]["claims"][0]["statement"].endswith(
        "9.1 seconds"
    )
    relation = dict(
        store.db.execute(
            "SELECT * FROM item_relations WHERE source_item_id=?", (latest["item_id"],)
        ).fetchone()
    )
    assert (
        relation["target_item_id"] == previous["item_id"]
        and relation["relation_type"] == "supersedes"
    )
    assert relation["provenance"] == "extracted"


@pytest.mark.asyncio
async def test_edge_batch_retains_valid_relation_when_another_real_fk_fails(store):
    first = await save({"title": "Source", "content": "one"})
    second = await save({"title": "Target", "content": "two"})
    source, target = first["item_id"], second["item_id"]
    edges = [
        Edge(source=source, target=source, relation="contradicts"),
        Edge(source=source, target="missing-row", relation="contradicts"),
        Edge(source=source, target=target, relation="contradicts"),
    ]
    assert persist._write_edges(store, edges, source_item=source) == 1
    assert store.db.execute("SELECT count(*) FROM item_relations").fetchone()[0] == 1


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({}, "workflow:unattributed"),
        ({"run_id": "r"}, "workflow:unattributed"),
        ({"node_id": "n"}, "workflow:node:n"),
        ({"run_id": "r", "node_id": "n"}, "workflow:r:n"),
    ],
)
def test_source_identity_uses_real_action_context(payload, expected):
    assert (
        persist._run_source_ref(ActionContext("workflow_node", payload=payload))
        == expected
    )


@pytest.mark.asyncio
async def test_json_content_preserves_unicode_and_markerless_legacy_citations(store):
    result = await save(
        {
            "kind": "insight",
            "title": "Unicode",
            "content": {"city": "München", "n": 4},
            "citations": ["manual notebook"],
        }
    )
    stored = row(store, result["item_id"])
    assert json.loads(stored["content"]) == {"city": "München", "n": 4}
    assert "München" in stored["content"] and stored["metadata"]["citations"] == [
        "manual notebook"
    ]
    assert store.item_citations(result["item_id"]) == []
