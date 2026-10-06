"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-d — reconcile a peer's rows into the live store.

The bridge that composes read-local → merge → apply. The crown check is criterion 4 at this
layer: a task made on A and one made on B both exist on both after one reconcile each way, and
a delete on A stays deleted on B. Non-row kinds are declined (routed elsewhere), and a poison
entry yields a payload-bad verdict rather than aborting the pull.
"""

from __future__ import annotations

import json

from gideon.operations.durability import conflict_resolve, conflicts
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import reconcile
from gideon.operations.durability.cursor import CONSUMED, PAYLOAD_BAD
from gideon.operations.durability.shards import (
    _json_rows_from_entity_dir,
    export_shards,
    import_shards,
)


def _entity_entry(**kw) -> inv.StateEntry:
    base = dict(
        id="tasks",
        kind=inv.KIND_JSON_ENTITY_DIR,
        path="tasks",
        domain="knowledge",
        merge=inv.MERGE_UNION_BY_ID,
        tombstones=True,
    )
    base.update(kw)
    return inv.StateEntry(**base)


def _write_entity(home, entry, rid, data):
    d = home / entry.path
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.json").write_text(json.dumps(data), encoding="utf-8")


class TestReconcileRowEntry:
    def test_remote_only_row_is_brought_in(self, tmp_path):
        home = tmp_path / "home"
        entry = _entity_entry()
        _write_entity(home, entry, "local1", {"title": "mine"})
        remote = [{"id": "remote1", "data": {"title": "theirs"}}]
        r = reconcile.reconcile_entry(home, entry, remote)
        assert r.handled and r.verdict == CONSUMED and r.added == 1
        ids = {row["id"] for row in _json_rows_from_entity_dir(home / "tasks")}
        assert ids == {"local1", "remote1"}

    def test_empty_local_store_takes_all_remote(self, tmp_path):
        home = tmp_path / "home"
        entry = _entity_entry()
        remote = [{"id": "r1", "data": {}}, {"id": "r2", "data": {}}]
        r = reconcile.reconcile_entry(home, entry, remote)
        assert r.added == 2
        assert (home / "tasks" / "r1.json").exists()

    def test_tombstone_delete_propagates(self, tmp_path):
        home = tmp_path / "home"
        entry = _entity_entry()
        _write_entity(home, entry, "x", {"title": "here"})
        r = reconcile.reconcile_entry(
            home, entry, [{"id": "x", "deleted_at": "2026-08-06"}]
        )
        assert r.removed == 1
        assert not (home / "tasks" / "x.json").exists()


class TestConvergence:
    """Criterion 4 at the reconcile layer."""

    def test_two_machines_converge(self, tmp_path):
        entry = _entity_entry()
        a_home = tmp_path / "A"
        b_home = tmp_path / "B"
        _write_entity(a_home, entry, "task-a", {"t": "a"})
        _write_entity(b_home, entry, "task-b", {"t": "b"})
        a_rows = _json_rows_from_entity_dir(a_home / "tasks")
        b_rows = _json_rows_from_entity_dir(b_home / "tasks")
        reconcile.reconcile_entry(a_home, entry, b_rows)
        reconcile.reconcile_entry(b_home, entry, a_rows)
        a_ids = {r["id"] for r in _json_rows_from_entity_dir(a_home / "tasks")}
        b_ids = {r["id"] for r in _json_rows_from_entity_dir(b_home / "tasks")}
        assert a_ids == b_ids == {"task-a", "task-b"}

    def test_delete_on_a_stays_deleted_on_b(self, tmp_path):
        entry = _entity_entry()
        b_home = tmp_path / "B"
        _write_entity(b_home, entry, "task-x", {"t": "live"})
        reconcile.reconcile_entry(
            b_home, entry, [{"id": "task-x", "deleted_at": "2026-08-06"}]
        )
        assert not (b_home / "tasks" / "task-x.json").exists()


class TestDeclineAndErrors:
    def test_sqlite_kind_is_declined_not_raised(self, tmp_path):
        entry = _entity_entry(
            id="memory_db",
            kind=inv.KIND_SQLITE,
            path="memory.db",
            merge=inv.MERGE_SQLITE_ATTACH_IGNORE,
        )
        r = reconcile.reconcile_entry(tmp_path, entry, [])
        assert r.handled is False

    def test_tree_kind_is_declined(self, tmp_path):
        entry = _entity_entry(
            id="memory_faiss",
            kind=inv.KIND_TREE,
            path="memory.faiss",
            merge=inv.MERGE_REPLACE_ONLY,
        )
        assert reconcile.reconcile_entry(tmp_path, entry, []).handled is False

    def test_handles_kind_predicate(self):
        assert reconcile.handles_kind(inv.KIND_JSON_ENTITY_DIR)
        assert reconcile.handles_kind(inv.KIND_JSONL_APPEND)
        assert not reconcile.handles_kind(inv.KIND_SQLITE)
        assert not reconcile.handles_kind(inv.KIND_TREE)

    def test_poison_entry_yields_payload_bad_not_a_crash(self, tmp_path, monkeypatch):
        entry = _entity_entry()

        def boom(*a, **k):
            raise RuntimeError("corrupt shard")

        monkeypatch.setattr(reconcile, "merge_rows", boom)
        r = reconcile.reconcile_entry(tmp_path, entry, [{"id": "x", "data": {}}])
        assert r.handled and r.verdict == PAYLOAD_BAD and "corrupt shard" in r.detail


def test_peer_arrival_waits_for_local_consent_and_preserves_local_authority(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    entry = inv.by_id("triggers")
    assert entry is not None and entry.records_field == "triggers"
    local = {
        "version": 1,
        "saved_at": 12,
        "triggers": [
            {
                "id": "alpha",
                "name": "Local name",
                "kind": "clock",
                "enabled": True,
                "run_count": 4,
                "last_run_id": "local-run",
                "spec": {"kind": "interval", "interval_secs": 300},
            }
        ],
    }
    (home / entry.path).write_text(json.dumps(local), encoding="utf-8")
    baseline = {"id": "alpha", "data": {"id": "alpha", "name": "Before edit"}}
    remote = [
        {
            "id": "alpha",
            "data": {
                "id": "alpha",
                "name": "Peer edit",
                "kind": "clock",
                "enabled": True,
                "run_count": 900,
                "last_run_id": "peer-run",
                "spec": {"kind": "interval", "interval_secs": 600},
            },
        },
        {
            "id": "beta",
            "data": {
                "id": "beta",
                "name": "Peer-created trigger",
                "kind": "clock",
                "enabled": True,
                "run_count": 300,
                "spec": {"kind": "interval", "interval_secs": 900},
            },
        },
    ]
    queue = conflicts.ConflictQueue(home)
    result = reconcile.reconcile_entry(
        home,
        entry,
        remote,
        ancestors={"alpha": conflicts.row_sha(inv.shared_value(entry, baseline))},
        queue=queue,
        now="2026-09-30T00:00:00Z",
    )
    assert result.verdict == CONSUMED and result.conflicts == 1 and result.added == 1
    arrived = json.loads((home / entry.path).read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in arrived["triggers"]}
    assert by_id["alpha"]["name"] == "Local name"  # conflict remains held for review
    assert by_id["alpha"]["enabled"] is True
    assert by_id["alpha"]["run_count"] == 4
    assert by_id["alpha"]["last_run_id"] == "local-run"
    assert by_id["beta"]["enabled"] is False
    assert "run_count" not in by_id["beta"] and "last_run_id" not in by_id["beta"]

    pending = queue.items(entry_id=entry.id, status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1
    resolved = conflict_resolve.resolve_conflict(
        home, pending[0].id, conflict_resolve.CHOICE_TAKE_REMOTE, now="resolved"
    )
    assert resolved.ok
    after_resolution = json.loads((home / entry.path).read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in after_resolution["triggers"]}
    assert by_id["alpha"]["name"] == "Peer edit"
    assert by_id["alpha"]["enabled"] is True
    assert by_id["alpha"]["run_count"] == 4
    assert by_id["alpha"]["last_run_id"] == "local-run"

    shards_dir = tmp_path / "shards"
    export_shards(home, shards_dir, entries=[entry.id])
    exported = import_shards(shards_dir).rows[entry.id]
    assert all(
        not ({"enabled", "run_count", "last_run_id"} & row["data"].keys())
        for row in exported
    )

    agents = inv.by_id("agents")
    assert agents is not None
    agent_dir = home / agents.path
    agent_dir.mkdir(parents=True)
    (agent_dir / "local-agent.json").write_text(
        json.dumps(
            {
                "name": "local-agent",
                "description": "Before edit",
                "enabled": True,
                "allowed_tools": ["shell"],
                "capabilities": ["local-execution"],
            }
        ),
        encoding="utf-8",
    )
    agent_update = reconcile.reconcile_entry(
        home,
        agents,
        [
            {
                "id": "local-agent",
                "data": {
                    "name": "local-agent",
                    "description": "Shared edit",
                    "enabled": True,
                    "allowed_tools": ["browser"],
                    "capabilities": ["remote-execution"],
                },
            },
            {
                "id": "peer-agent",
                "data": {
                    "name": "peer-agent",
                    "description": "Peer definition",
                    "enabled": True,
                    "allowed_tools": ["shell"],
                    "capabilities": ["remote-execution"],
                },
            },
        ],
    )
    assert agent_update.verdict == CONSUMED and agent_update.updated == 1
    assert agent_update.added == 1
    local_agent = json.loads(
        (agent_dir / "local-agent.json").read_text(encoding="utf-8")
    )
    peer_agent = json.loads((agent_dir / "peer-agent.json").read_text(encoding="utf-8"))
    assert local_agent["description"] == "Shared edit"
    assert local_agent["allowed_tools"] == ["shell"]
    assert local_agent["capabilities"] == ["local-execution"]
    assert local_agent["enabled"] is True
    assert peer_agent["enabled"] is False
    assert peer_agent["allowed_tools"] == peer_agent["capabilities"] == []

    workflows = inv.by_id("workflows")
    assert workflows is not None
    workflow_dir = home / workflows.path
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "local-workflow.json").write_text(
        json.dumps(
            {"name": "local-workflow", "description": "Before edit", "enabled": True}
        ),
        encoding="utf-8",
    )
    workflow_update = reconcile.reconcile_entry(
        home,
        workflows,
        [
            {
                "id": "local-workflow",
                "data": {
                    "name": "local-workflow",
                    "description": "Shared edit",
                    "enabled": True,
                },
            },
            {
                "id": "peer-workflow",
                "data": {"name": "peer-workflow", "enabled": True, "steps": ["run"]},
            },
        ],
    )
    assert workflow_update.verdict == CONSUMED
    local_workflow = json.loads(
        (workflow_dir / "local-workflow.json").read_text(encoding="utf-8")
    )
    peer_workflow = json.loads(
        (workflow_dir / "peer-workflow.json").read_text(encoding="utf-8")
    )
    assert local_workflow["description"] == "Shared edit"
    assert local_workflow["enabled"] is True
    assert peer_workflow["enabled"] is False

    assert {"workflow_runs_db", "loops_db", "cron_history", "subagents"}.isdisjoint(
        {row.id for row in inv.export_entries()}
    )

    import sqlite3

    from checks.runtime.test_durability_convergence_e2e import FolderTransport
    from gideon.integrations.sync_transports.base import SyncObject
    from gideon.operations.durability import pull_engine
    from gideon.operations.durability.cursor import Cursor
    from gideon.operations.durability.registry import Registry, shard_prefix
    from gideon.operations.durability.shards import (
        ExportResult,
        _write_manifest,
        _write_shard,
    )

    runs = inv.by_id("workflow_runs_db")
    assert runs is not None and runs.machine_local
    db_path = home / runs.path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT)")
        connection.execute("INSERT INTO runs VALUES ('local-run', 'done')")
        connection.commit()

    # Construct a valid pre-policy shard as an older Gideon version would have published.
    legacy_home = tmp_path / "legacy-peer"
    legacy_home.mkdir()
    legacy_dir = tmp_path / "legacy-shards"
    legacy_dir.mkdir()
    written = _write_shard(
        legacy_dir,
        "workflow_runs_db/runs.jsonl",
        [{"id": "peer-run", "status": "failed", "run_count": 81}],
    )
    _write_manifest(legacy_home, legacy_dir, ExportResult(entries=1, shards=written))

    shared = tmp_path / "shared-sync"
    transport = FolderTransport(shared)
    prefix = shard_prefix("legacy-machine", 1)
    transport.push(
        [
            SyncObject(
                key=prefix + path.relative_to(legacy_dir).as_posix(),
                data=path.read_bytes(),
            )
            for path in legacy_dir.rglob("*")
            if path.is_file()
        ]
    )
    registry = Registry()
    registry.bump("legacy-machine", manifest_sha="legacy", now="legacy")
    cursor = Cursor(tmp_path / "sync" / "local")
    pull = pull_engine.pull_from_peers(
        transport, home, registry, cursor, self_id="local"
    )
    assert pull.advanced == 1 and cursor.seq_of("legacy-machine") == 1
    assert pull.outcomes[0].deferred_db == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT id, status FROM runs").fetchall() == [
            ("local-run", "done")
        ]
