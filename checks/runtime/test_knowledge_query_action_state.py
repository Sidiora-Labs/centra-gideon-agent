"""Knowledge query actions through actual SQLite, artifact and proposal storage."""

import json

import pytest

from gideon.cognition import knowledge
from gideon.cognition.knowledge import reports
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.cognition.learning import proposals
from gideon.integrations.action_providers import knowledge_propose_provider as propose
from gideon.integrations.action_providers import knowledge_render_provider as render
from gideon.integrations.action_providers import knowledge_retrieve_provider as retrieve
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.inbox_providers import native_source
from gideon.workspace.artifacts import registry as artifacts

CTX = ActionContext(
    "workflow_node", payload={"run_id": "query-run", "node_id": "knowledge"}
)
SPEC = {"title": "Local report", "blocks": [{"type": "table", "dataset": "rows"}]}


@pytest.fixture(autouse=True)
def query_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "config.json").write_text('{"providers": []}')
    monkeypatch.setattr(artifacts, "_providers", {})
    monkeypatch.setattr(native_source, "_dashboard_state", None)
    monkeypatch.setattr(knowledge, "_store", None)
    yield tmp_path
    if knowledge._store is not None:
        knowledge._store.close()


@pytest.fixture
def store():
    database = KnowledgeStore(db_path=str(knowledge_db_path()))
    yield database
    database.close()


def add_item(store, title, *, kind="fact", content="local source", **metadata):
    identifier = store.create_typed_item(
        item_type="note",
        title=title,
        content=content,
        extra=metadata,
    )
    store.set_item_identity(identifier, kind=kind)
    store.db.commit()
    return identifier


@pytest.mark.asyncio
async def test_public_fts_retrieve_promotes_overview_beyond_filtered_cap_and_reports_omission(
    store,
):
    for index in range(4):
        add_item(store, f"Topic fact {index}", content="topic source")
    overview = add_item(store, "Topic", kind="overview", content="the topic outline")
    result = await retrieve.KnowledgeRetrieveActionProvider().execute(
        {"query": "topic", "mode": "fts", "top_k": 1, "filters": {"kind": "fact"}}, CTX
    )
    assert result.success, result.error
    body = json.loads(result.stdout)
    assert (
        body["strategy"] == "fts" and body["truncated"] == "3 older items not consulted"
    )
    assert len(body["items"]) == 2 and body["items"][0]["item_id"] == overview
    assert body["items"][0]["create_safety"] == "exists"
    assert body["items"][1]["kind"] == "fact" and body["coverage_gap"] is False
    assert store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 5


def test_metadata_enrichment_preserves_locators_and_loads_repeated_ids_once(store):
    identifier = add_item(
        store,
        "Evidence",
        file_metadata=json.dumps(
            {
                "claims": [{"support_count": 3}, {"support_count": 8}],
                "read_when": ["debug"],
            }
        ),
    )
    minimal = dict(
        id=identifier,
        title="Evidence",
        content="first\nsecond\nthird",
        score=0.02,
        match_type="keyword+graph",
        section="Details",
        line_range=(2, 3),
    )
    statements = []
    store.db.set_trace_callback(statements.append)
    enriched = retrieve._enrich(store, [minimal, dict(minimal)])
    store.db.set_trace_callback(None)
    reads = [sql for sql in statements if "FROM items WHERE id =" in sql]
    assert len(reads) == 1 and "kind" not in minimal
    assert [row["kind"] for row in enriched] == ["fact", "fact"]
    shaped = retrieve._shape_hit(
        store, enriched[0], query="debug", detail="compact", rank=1
    )
    assert shaped["section"] == "Details" and shaped["line_range"] == [2, 3]
    assert shaped["evidence"] == "graph" and shaped["create_safety"] == "probable"
    assert shaped["support_count"] == 8 and shaped["read_when"] == ["debug"]


def test_actual_missing_index_reaches_literal_substring_fallback_without_embedding(
    store,
):
    identifier = add_item(store, "path_a%tail", content="literal identifier")
    add_item(store, "pathXaYtail", content="different identifier")
    store.db.execute("DROP TABLE items_fts")
    hits, strategy = retrieve._search(store, "path_a%", top_k=3, mode="semantic")
    assert strategy == "substring_fallback" and [hit["id"] for hit in hits] == [
        identifier
    ]
    assert retrieve._search(
        store, "nonexistent sentinel", top_k=3, mode="semantic"
    ) == ([], "none")


@pytest.mark.parametrize("location", [None, [], [0, 2], [2, 30], ["bad", 3], [3, 1]])
def test_bad_passage_locations_keep_head_payload_and_original_citation_shape(location):
    hit = dict(
        id="id", title="Source", content="first\nsecond\nthird", line_range=location
    )
    result = retrieve._shape_hit(None, hit, query="different", detail="compact")
    assert result["content"] == hit["content"] and result["content_windowed"] is False
    assert result["line_range"] == (
        list(location) if isinstance(location, (list, tuple)) else None
    )


def test_real_search_rows_keep_hybrid_rank_scores_and_keyword_cliff():
    hits = [{"kind": "fact", "score": 0.02}, {"kind": "overview", "score": 0.8}]
    assert (
        retrieve._apply_filters(hits, {"kind": "FACT"}, strategy="hybrid") == hits[:1]
    )
    assert retrieve._apply_filters(hits, {"kind": "fact"}, strategy="fts") == []
    assert retrieve._create_safety(exact=False, rank=2, evidence="graph") == "unknown"


@pytest.mark.asyncio
async def test_json_bound_render_only_preserves_non_ascii_and_writes_no_artifact(
    query_home,
):
    result = await render.KnowledgeRenderReportActionProvider().execute(
        {
            "spec": json.dumps(SPEC),
            "data": '{"rows":[{"city":"München","n":7}]}',
            "render_only": "YES",
            "slug": "../../ignored",
        },
        CTX,
    )
    assert result.success, result.error
    body = json.loads(result.stdout)
    assert "München" in body["html"] and body["bytes"] == len(body["html"])
    assert body["blocks"] == 1 and "spec_slug" not in body
    reports.assert_self_contained(body["html"])
    assert not (query_home / "artifacts").exists()


@pytest.mark.asyncio
async def test_actual_report_versions_keep_operator_fields_and_export_lineage():
    provider = render.KnowledgeRenderReportActionProvider()
    config = {
        "slug": "local-brief",
        "spec": SPEC,
        "data": {"rows": [{"n": 1}]},
        "name": "First name",
        "tags": ["original"],
        "collection": "first",
    }
    initial = await provider.execute(config, CTX)
    assert initial.success, initial.error
    store = artifacts.get_provider()
    original = json.loads(initial.stdout)
    store.update(
        "local-brief",
        name="Operator name",
        description="Operator note",
        tags=["operator"],
        snapshot=False,
    )
    versions = store.list_versions("local-brief")
    reordered = {"blocks": SPEC["blocks"], "title": SPEC["title"]}
    again = await provider.execute(
        {**config, "spec": reordered, "data": {"rows": [{"n": 17}]}}, CTX
    )
    assert (
        again.success
        and json.loads(again.stdout)["spec_version"] == original["spec_version"]
    )
    assert store.list_versions("local-brief") == versions
    assert "17" in store.get("local-brief-report").content
    export_versions = store.list_versions("local-brief-report")
    changed = await provider.execute(
        {**config, "spec": {**SPEC, "title": "Second title"}, "collection": "second"},
        CTX,
    )
    assert changed.success, changed.error
    spec = store.get("local-brief")
    export = store.get("local-brief-report")
    assert (
        spec.name == "Operator name"
        and spec.description == "Operator note"
        and spec.tags == ["operator"]
    )
    assert spec.collection == export.collection == "second"
    assert json.loads(spec.content)["title"] == "Second title"
    assert len(store.list_versions("local-brief")) > len(versions)
    assert store.list_versions("local-brief-report") == export_versions
    assert export.events[-1].metadata == {
        "derived_from": "local-brief",
        "spec_version": spec.version,
    }


@pytest.mark.asyncio
async def test_actual_export_write_failure_keeps_written_spec_and_reports_partial_state():
    store = artifacts.get_provider()
    store.create(
        name="Locked export",
        slug="locked-report",
        content="old export",
        kind="html",
        readonly=True,
    )
    result = await render.KnowledgeRenderReportActionProvider().execute(
        {"slug": "locked", "spec": SPEC, "data": {"rows": [{"n": 3}]}}, CTX
    )
    assert not result.success and "could not write 'locked'" in result.error
    assert (
        store.get("locked") is not None
        and store.get("locked-report").content == "old export"
    )


@pytest.mark.asyncio
async def test_spec_error_precedes_slug_validation_without_partial_write(query_home):
    result = await render.KnowledgeRenderReportActionProvider().execute(
        {"spec": {"blocks": [{"type": "unknown"}]}, "slug": "../bad"}, CTX
    )
    assert (
        not result.success
        and "unknown" in result.error
        and "not a valid id" not in result.error
    )
    assert not (query_home / "artifacts").exists()


@pytest.mark.asyncio
async def test_draft_batch_cap_counts_all_candidates_but_only_submits_first_ten(
    query_home,
):
    drafts = [
        {
            "title": f"Draft {n}",
            "body": f"A grounded local claim for target number {n}.",
            "target": f"topic-{n}",
        }
        for n in range(12)
    ]
    result = await propose.KnowledgeProposeActionProvider().execute(
        {
            "drafts": json.dumps(drafts),
            "provenance": "human",
            "source_cadence": "local-review",
            "tags": ["local", ""],
        },
        CTX,
    )
    assert result.success, result.error
    body = json.loads(result.stdout)
    assert body["counts"] == {"filed": 10, "skipped": 0, "considered": 12}
    pending = proposals.list_pending()
    assert len(pending) == 10 and {row.target for row in pending} == {
        f"topic-{n}" for n in range(10)
    }
    assert all(
        row.run_id == "query-run"
        and row.source_cadence == "local-review"
        and row.tags == ["local"]
        for row in pending
    )
    assert not list(query_home.glob("workspace/knowledge/*.db"))


@pytest.mark.asyncio
async def test_draft_own_evidence_wins_over_shared_map_and_is_fenced():
    result = await propose.KnowledgeProposeActionProvider().execute(
        {
            "drafts": {
                "entity": "Local entity",
                "content": "A grounded claim.",
                "source_excerpt": ["local one", "local two"],
                "sufficient_evidence": "true",
            },
            "evidence": '{"Local entity":["ignored shared evidence"]}',
            "occurrences": "4",
        },
        CTX,
    )
    assert result.success and json.loads(result.stdout)["counts"]["filed"] == 1
    row = proposals.list_pending()[0]
    assert row.title == "Local entity" and row.reinforcements == 4
    assert (
        "local one\nlocal two" in row.source_excerpt
        and "ignored shared" not in row.source_excerpt
    )
    assert "<untrusted_content" in row.source_excerpt


@pytest.mark.asyncio
async def test_malformed_batch_falls_back_to_single_draft_and_rejection_skips_successfully():
    provider = propose.KnowledgeProposeActionProvider()
    config = {
        "drafts": "[broken",
        "title": "Local claim",
        "body": "A source claim to be reviewed.",
        "provenance": "human",
    }
    first = await provider.execute(config, CTX)
    assert first.success
    identifier = json.loads(first.stdout)["filed"][0]["id"]
    assert proposals.reject(identifier)
    second = await provider.execute(config, CTX)
    receipt = json.loads(second.stdout)
    assert second.success and receipt["counts"] == {
        "filed": 0,
        "skipped": 1,
        "considered": 1,
    }
    assert receipt["skipped"][0]["reason"] and proposals.list_pending() == []


@pytest.mark.asyncio
async def test_invalid_and_thin_drafts_produce_separate_skip_receipts():
    result = await propose.KnowledgeProposeActionProvider().execute(
        {
            "drafts": [
                {"title": "Missing", "body": ""},
                {"title": "Thin", "body": "Some text", "sufficient_evidence": "false"},
                {
                    "title": "Below floor",
                    "body": "A single reported occurrence",
                    "mentions": "1",
                },
                {
                    "title": "Human source",
                    "body": "Operator supplied a measured value",
                    "target": "measured",
                },
            ]
        },
        CTX,
    )
    payload = json.loads(result.stdout)
    assert result.success and payload["counts"] == {
        "filed": 1,
        "skipped": 3,
        "considered": 4,
    }
    assert [row["title"] for row in payload["skipped"]] == [
        "Missing",
        "Thin",
        "Below floor",
    ]
    assert len(proposals.list_pending()) == 1
