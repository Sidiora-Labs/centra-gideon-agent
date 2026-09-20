import time

import pytest

from gideon.automation.workflows import pool
from gideon.engine.tasks import registry
from gideon.engine.tasks.native import NativeTaskProvider


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    provider = NativeTaskProvider()
    monkeypatch.setattr(registry, "_providers", {"native": provider})
    return provider


@pytest.mark.asyncio
async def test_real_registry_crud_comments_and_unknown_filter_fallback(source):
    row = await registry.create_task(
        title="Search search", description="query", labels=["one"], priority="high"
    )
    other = await registry.create_task(
        title="Other",
        description="search search search",
        labels=["two"],
        priority="low",
    )
    rows, count = await registry.list_all_tasks(provider_filter="absent")
    assert count == 2 and {item.id for item in rows} == {row.id, other.id}
    with pytest.raises(ValueError, match="Unknown task provider"):
        await registry.get_task(row.id, provider_name="absent")
    assert (await registry.get_task(row.id, provider_name="native")).id == row.id
    found, total = await registry.search_tasks("search", limit=1)
    assert total == 2 and [item.id for item in found] == [row.id]
    found, total = await registry.search_tasks(
        "search", tags=["two"], priorities=["low"]
    )
    assert total == 1 and found[0].id == other.id
    comment = await registry.add_comment(row.id, "Actual note", author="owner")
    assert [item.id for item in await registry.get_comments(row.id)] == [comment.id]
    assert await registry.delete_comment(row.id, comment.id)
    assert await registry.get_comments(row.id) == []
    with pytest.raises(ValueError, match="Unknown task provider"):
        await registry.update_task(row.id, provider_name="unknown", title="Refused")
    assert (await registry.get_task(row.id)).title == "Search search"
    updated = await registry.update_task(
        row.id, provider_name="native", title="Changed"
    )
    assert updated.title == "Changed"
    assert await registry.delete_task(row.id)
    assert await registry.get_task(row.id) is None
    assert not await registry.delete_comment("missing", "missing")
    with pytest.raises(ValueError, match="Unknown task provider"):
        await registry.create_task(provider_name="unknown", title="Refused")


@pytest.mark.asyncio
async def test_actual_task_leases_exclude_ready_work_and_release_restores_it(source):
    root = await registry.create_task(title="Prerequisite", priority="medium")
    dependent = await registry.create_task(
        title="Dependent", dependencies=[{"depends_on_task_id": root.id}]
    )
    rival = await registry.create_task(title="Peer", priority="medium")
    ready = await registry.ready_tasks(mine_only=False)
    assert ready[0].id == root.id and dependent.id not in {row.id for row in ready}
    lease, error = pool.claim_task(
        root.id, holder="another-worker", now=time.time(), ttl_seconds=300
    )
    assert lease is not None and error == ""
    assert [row.id for row in await registry.ready_tasks(mine_only=False)] == [rival.id]
    released, error = pool.release_task(root.id, holder="another-worker")
    assert released and not error
    assert (await registry.ready_tasks(mine_only=False))[0].id == root.id


@pytest.mark.asyncio
async def test_real_provider_aggregation_retains_per_source_rows(source):
    row = await source.create_task(title="Shared source")
    directory = registry.TaskDirectory({"first": source, "second": source})
    records = await directory.collect(
        None, {"status": None, "assignee": None, "project": None}
    )
    assert [item.id for item in records] == [row.id, row.id]
    selected = await directory.collect(
        "second", {"status": None, "assignee": None, "project": None}
    )
    assert [item.id for item in selected] == [row.id]
    assert (await directory.find(row.id))[0] is source
