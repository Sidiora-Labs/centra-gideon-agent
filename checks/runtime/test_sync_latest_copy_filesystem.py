"""Newest-copy protocol through the real filesystem transport contract fixture."""

import json
from pathlib import Path

from test_durability_convergence_e2e import FolderTransport

from gideon.operations.durability import inventory as inv
from gideon.operations.durability.cursor import Cursor
from gideon.operations.durability.registry import Registry
from gideon.operations.durability.shards import export_shards, import_shards
from gideon.operations.durability.sync_cycle import read_registry, run_sync_cycle


def task(home, value):
    folder = home / "tasks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "item.json").write_text(json.dumps({"id": "item", "title": value}))


def cycle(transport, home, identity, now="2026-10-06T12:00:00+00:00"):
    return run_sync_cycle(transport, home, self_id=identity, encrypt="off", now=now)


def test_unchanged_semantic_state_sends_no_second_copy(tmp_path):
    home = tmp_path / "a"
    task(home, "first")
    transport = FolderTransport(tmp_path / "store")
    first = cycle(transport, home, "a")
    assert first.ok, first.detail
    second = cycle(transport, home, "a")
    assert second.ok and second.unchanged_since == 1, second.detail
    assert read_registry(transport).seq_of("a") == 1


def test_newest_only_and_missing_complete_copy_holds_cursor(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    task(a, "first")
    transport = FolderTransport(tmp_path / "store")
    assert cycle(transport, a, "a").ok
    task(a, "second")
    assert cycle(transport, a, "a").ok
    prefix = tmp_path / "store" / "machines" / "a" / "seq-0002"
    manifest = json.loads((prefix / "manifest.json").read_text())
    record = next(
        record for record in manifest["shards"] if record["path"].startswith("tasks/")
    )
    shard = prefix / record["path"]
    saved = shard.read_bytes()
    shard.unlink()
    result = cycle(transport, b, "b")
    assert not result.ok
    assert Cursor(b / "sync").seq_of("a") == 0
    shard.write_bytes(saved)
    result = cycle(transport, b, "b")
    assert result.ok, result.detail
    assert [outcome.seq for outcome in result.pulled.outcomes] == [2]
    assert Cursor(b / "sync").seq_of("a") == 2
    assert json.loads((b / "tasks" / "item.json").read_text())["title"] == "second"
    assert "ancestors" not in json.loads(
        (tmp_path / "store" / "registry.json").read_text()
    )
    assert (
        tmp_path / "store" / "machines" / "b" / "seq-0001" / "agreements.json"
    ).exists()


class RemovingFolderTransport(FolderTransport):
    removes_old_copies = True

    def remove(self, keys):
        removed = 0
        for key in keys:
            path = self._root / key
            if path.is_file():
                path.unlink()
                removed += 1
        return removed


def test_retirement_waits_for_reader_grace_and_prunes_journal(tmp_path):
    a = tmp_path / "a"
    transport = RemovingFolderTransport(tmp_path / "store")
    task(a, "first")
    assert cycle(transport, a, "a", "2026-10-06T12:00:00+00:00").ok
    task(a, "second")
    assert cycle(transport, a, "a", "2026-10-06T12:01:00+00:00").ok
    assert transport.list_remote("machines/a/seq-0001/")
    result = cycle(transport, a, "a", "2026-10-06T12:17:00+00:00")
    assert result.ok and result.copies_removed == [1], result.detail
    assert not transport.list_remote("machines/a/seq-0001/")
    assert not list((a / "sync" / "outbox").glob("*seq-0001.json"))


def test_legacy_registry_cas_uses_original_wire_digest():
    import hashlib

    data = b'{"machines": {"a": {"seq": 1}}, "ancestors": {"tasks": {"id":"sha"}}}'
    registry = Registry.loads(data)
    assert registry.sha() == hashlib.sha256(data).hexdigest()
    assert "ancestors" not in json.loads(registry.to_bytes())


def test_immutable_consent_data_roundtrips_without_local_grants(tmp_path, monkeypatch):
    from gideon.automation.workflows.automation_versions import digest, keep_spec
    from gideon.automation.workflows.models import WorkflowDef

    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("GIDEON_HOME", str(a))
    spec = WorkflowDef.from_dict(
        {
            "name": "sample",
            "version": 1,
            "root": {"id": "done", "kind": "transform", "config": {"expr": "1"}},
        }
    ).to_dict()
    keep_spec(spec)
    grants = a / "grants"
    grants.mkdir(exist_ok=True)
    (grants / "owner.json").write_text('{"approved":true}')
    transport = FolderTransport(tmp_path / "store")
    assert cycle(transport, a, "a").ok
    monkeypatch.setenv("GIDEON_HOME", str(b))
    result = cycle(transport, b, "b")
    assert result.ok, result.detail
    arrived = json.loads(
        (b / "workflows" / "consent_specs" / f"{digest(spec)}.json").read_text()
    )
    assert arrived == spec and digest(arrived) == digest(spec)
    assert not (b / "grants").exists()
    entry = inv.by_id("workflows")
    row = {"id": "consent_specs/" + digest(spec), "data": spec}
    assert inv.immutable_consent_spec(entry, row)
    changed = {**spec, "name": "changed"}
    assert not inv.immutable_consent_spec(entry, {"id": row["id"], "data": changed})


def test_agreed_peer_edit_and_handed_back_version_preserve_newer_owner_edit(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    task(a, "base")
    transport = FolderTransport(tmp_path / "store")
    for home, identity in ((a, "a"), (b, "b"), (a, "a")):
        result = cycle(transport, home, identity)
        assert result.ok, result.detail
    task(a, "first edit")
    assert cycle(transport, a, "a").ok
    assert cycle(transport, b, "b").ok
    assert json.loads((b / "tasks" / "item.json").read_text())["title"] == "first edit"
    task(a, "second edit")
    result = cycle(transport, a, "a")
    assert result.ok and result.conflicts == 0, result.detail
    assert json.loads((a / "tasks" / "item.json").read_text())["title"] == "second edit"
    assert cycle(transport, b, "b").ok
    assert json.loads((b / "tasks" / "item.json").read_text())["title"] == "second edit"


def test_peer_agreements_are_encrypted_by_real_codec(tmp_path):
    from gideon.operations.durability.ancestors import Ancestors
    from gideon.operations.durability.crypto import (
        SyncCodec,
        derive_master,
        is_ciphertext,
    )
    from gideon.operations.durability.outbox import Outbox
    from gideon.operations.durability.pull_engine import pull_from_peers
    from gideon.operations.durability.push_engine import publish_export

    a, b = tmp_path / "a", tmp_path / "b"
    task(a, "private")
    out = tmp_path / "export"
    export_shards(a, out, agreements={"b": {"tasks": {"item": "private-sha"}}})
    transport = FolderTransport(tmp_path / "store")
    codec = SyncCodec(derive_master("local test secret", b"0123456789abcdef"))
    registry = Registry.empty()
    result = publish_export(
        transport,
        out,
        registry,
        Outbox(a / "sync"),
        self_id="a",
        manifest_sha="local-test",
        now="2026-10-06T12:00:00+00:00",
        codec=codec,
    )
    assert result.registry_committed
    ciphertext = (
        tmp_path / "store" / "machines" / "a" / "seq-0001" / "agreements.json"
    ).read_bytes()
    assert is_ciphertext(ciphertext) and b"private-sha" not in ciphertext
    pulled = pull_from_peers(
        transport,
        b,
        read_registry(transport),
        Cursor(b / "sync"),
        self_id="b",
        codec=codec,
        ancestors=Ancestors(b / "sync"),
    )
    assert pulled.outcomes[0].advanced
    assert json.loads((b / "tasks" / "item.json").read_text())["title"] == "private"
