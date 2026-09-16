"""Maintenance and report boundaries exercised with native rows and persisted definitions."""

import json
import time
from datetime import datetime, timezone

import pytest

from gideon.automation.schedule import ScheduleDefinition
from gideon.automation.triggers import claims
from gideon.cognition import knowledge
from gideon.cognition.knowledge import consolidation
from gideon.cognition.knowledge import research_reports as rr
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.integrations.action_providers import (
    knowledge_maintain_provider as maintenance,
)
from gideon.integrations.action_providers import knowledge_report_provider as reports
from gideon.integrations.action_providers.base import ActionContext


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
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


def context():
    return ActionContext(
        "", payload={"run_id": "local-report", "node_id": "maintenance"}
    )


def definition(**changes):
    values = dict(
        id="local-report",
        name="Local report",
        prompt="Describe the observations.",
        schedule=ScheduleDefinition(kind="every", every_secs=86400),
        source=rr.Scope(tags=("observations",)),
        created_ts=time.time(),
    )
    values.update(changes)
    return rr.ReportDefinition(**values)


def item(
    store,
    title,
    *,
    tags=("observations",),
    content="Measured latency was 12 milliseconds.",
):
    identifier = store.create_typed_item(
        item_type="note", title=title, content=content, tags=list(tags)
    )
    assert identifier
    return identifier


@pytest.mark.asyncio
async def test_native_preview_does_not_stamp_and_empty_run_advances_watermark(store):
    identifier = item(store, "Measured observation")
    report = rr.save_report(definition())
    provider = reports.KnowledgeReportActionProvider()
    preview = await provider.execute(
        {"report_id": report.id, "manual": True, "dry_run": True}, context()
    )
    assert preview.success, preview.error
    assert json.loads(preview.stdout)["source_items"] == [identifier]
    assert rr.get_report(report.id).watermark_ts == 0
    assert rr.get_report(report.id).last_run_ts is None
    report.source = rr.Scope(tags=("not-in-store",))
    rr.save_report(report)
    before = time.time()
    empty = await provider.execute({"report_id": report.id, "manual": True}, context())
    payload = json.loads(empty.stdout)
    stored = rr.get_report(report.id)
    assert empty.success and payload["model_calls"] == 0
    assert payload["watermark_ts"] == stored.watermark_ts >= before
    assert stored.last_status == "ok"
    assert not claims.is_running(rr.report_claim_id(report.id))
    assert store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_native_lease_prevents_duplicate_execution_without_changing_report(store):
    report = rr.save_report(definition())
    assert reports._hold_claim(report.id)
    try:
        assert not reports._hold_claim(report.id)
        result = await reports.KnowledgeReportActionProvider().execute(
            {"report_id": report.id, "manual": True}, context()
        )
        assert json.loads(result.stdout) == {
            "report_id": report.id,
            "skipped": "already_running",
        }
        assert rr.get_report(report.id).last_run_ts is None
    finally:
        reports._release_claim(report.id)
    assert not claims.is_running(rr.report_claim_id(report.id))


def test_scope_uses_real_cyclic_taxonomy_and_keeps_newest_forty(store):
    identifiers = [
        item(store, f"Observation {n}", tags=("observations" if n % 2 else "child",))
        for n in range(45)
    ]
    tags = {row["name"]: row["id"] for row in store.list_tags()}
    store.db.execute(
        "UPDATE tags SET parent_id=? WHERE id=?", (tags["observations"], tags["child"])
    )
    store.db.execute(
        "UPDATE tags SET parent_id=? WHERE id=?", (tags["child"], tags["observations"])
    )
    for n, identifier in enumerate(identifiers):
        stamp = datetime.fromtimestamp(1_700_000_000 + n, timezone.utc).isoformat()
        store.db.execute(
            "UPDATE items SET updated_at=? WHERE id=?", (stamp, identifier)
        )
    store.db.commit()
    assert reports._tag_closure(store, [" OBSERVATIONS "]) == set(tags.values())
    selected = reports._resolve_scope(
        store,
        rr.Scope(tags=("observations",)),
        cutoff_ts=0,
        exclude_kind=rr.FINDING_KIND,
    )
    assert [row["id"] for row in selected] == identifiers[-40:]
    store.db.execute("UPDATE items SET is_archived=1 WHERE id=?", (identifiers[-1],))
    store.set_item_identity(identifiers[-2], kind=rr.FINDING_KIND)
    selected = reports._resolve_scope(
        store,
        rr.Scope(tags=("observations",)),
        cutoff_ts=0,
        exclude_kind=rr.FINDING_KIND,
    )
    assert [row["id"] for row in selected] == identifiers[3:-2]


@pytest.mark.asyncio
async def test_summary_batch_preserves_protected_inputs_and_records_actual_lineage(
    store,
):
    protected = item(store, "User decision")
    ordinary = item(store, "Agent observation")
    cluster = consolidation.Cluster(
        items=[
            consolidation.Item(
                id=protected,
                title="User decision",
                content="The user chose 12ms.",
                origin="user",
            ),
            consolidation.Item(
                id=ordinary,
                title="Agent observation",
                content="Measured 12ms.",
                origin="agent",
            ),
        ]
    )
    plan = consolidation.ConsolidationPlan(clusters=[cluster])
    count, issues = await maintenance._apply(
        store,
        plan,
        [
            None,
            {"cluster": 4, "content": "no"},
            {"cluster": 0, "content": " "},
            {
                "cluster": 0,
                "title": "Consolidated measurement",
                "content": "The decision and observation agree on 12ms.",
            },
        ],
        context(),
    )
    assert count == 1
    assert issues == [
        "summary entry is not an object",
        "no cluster at index 4",
        "cluster 0: empty summary, nothing written",
        f"{protected}: protected, left unarchived",
    ]
    rows = {row["id"]: dict(row) for row in store.db.execute("SELECT * FROM items")}
    assert not rows[protected]["is_archived"] and rows[ordinary]["is_archived"]
    summary = next(row for row in rows.values() if row["kind"] == "insight")
    assert json.loads(summary["file_metadata"])["parent_ids"] == [protected, ordinary]
    archived = json.loads(rows[ordinary]["file_metadata"])
    assert (
        archived["summary_of"] == [protected, ordinary]
        and archived["reflection_count"] == 1
    )


@pytest.mark.asyncio
async def test_real_report_provenance_reaches_citations_and_metadata(store):
    identifier = item(store, "Observation source")
    report = definition()
    refs = reports._numbered_refs(rr, report, [store.get_item(identifier)], [])
    request = reports._persist_config(
        rr, report, text="Observed latency was 12ms [1].", refs=refs
    )
    result = await reports._persist(request, context())
    assert result.success, result.error
    saved = store.get_item(json.loads(result.stdout)["item_id"])
    assert saved["kind"] == rr.FINDING_KIND
    meta = saved["file_metadata"]
    assert request["source_ref"] == "research-report:local-report"
    assert identifier in str(meta["citations"])
    cited = store.db.execute(
        "SELECT * FROM item_citations WHERE item_id=?", (saved["id"],)
    ).fetchall()
    assert len(cited) == 1


def test_draft_state_tracks_continuation_and_enforces_budget_without_inference():
    draft = reports._FindingDraft(definition(), (), 2)
    assert draft.accept("First observations [1].\nCONTINUE\n")
    assert draft.calls == 1 and draft.notes == ["First observations [1]."]
    assert not draft.accept("Revised observations [1].\nCONTINUE")
    assert draft.calls == 2 and draft.text == "Revised observations [1]."
    prompt = reports._build_prompt(definition(), (), turn=2, cap=2, notes=draft.notes)
    assert "pass 2 of at most 2" in prompt and "First observations" in prompt
    assert "CONTINUE" not in draft.text


def test_missing_index_is_unavailable_and_rows_remain_inspectable(store):
    identifier = item(store, "Still durable")
    store.db.execute("DROP TABLE items_fts")
    assert maintenance._indexed_ids(store) is None
    assert [record.id for record in maintenance._load_items(store)] == [identifier]
    assert maintenance._reindex(store, [identifier]) == []
    assert store.get_item(identifier)["title"] == "Still durable"
