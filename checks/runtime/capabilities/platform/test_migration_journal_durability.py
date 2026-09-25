import hashlib
import json

from checks.runtime.capabilities.platform.test_migration import (
    song_archive,
    song_record,
)
from gideon.operations.durability import inventory
from gideon.workspace import snapshot as snapshot_module
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.music.store import RepertoireStore
from gideon.workspace.capabilities.platform.migration import commit, preview, receipts


def test_migrated_song_artifact_and_complete_journal_restore_into_fresh_home(
    tmp_path, monkeypatch
):
    source_home = tmp_path / "source-home"
    monkeypatch.setenv("GIDEON_HOME", str(source_home))
    artifacts = NativeArtifactProvider(source_home / "artifacts")
    repertoire = RepertoireStore(source_home / "capabilities" / "music", artifacts)
    score = b"%PDF-1.7\nimmutable restored score\n"
    metadata = {
        "filename": "score.pdf",
        "label": "Archive score",
        "mime": "application/pdf",
        "size": len(score),
        "sha256": hashlib.sha256(score).hexdigest(),
    }
    source = song_archive(
        song_record("durable-song", attachments=[metadata]), {"score.pdf": score}
    )
    checked = preview(source)
    reviewed = {
        **source,
        "archive_digest": checked["archive_digest"],
        "review_token": checked["review_token"],
    }
    receipt, created = commit(
        PeopleStore(source_home / "capabilities" / "communications"),
        reviewed,
        repertoire=repertoire,
        artifacts=artifacts,
    )
    assert created is True
    ref = receipt["records"][0]["attachment_refs"][0]
    assert repertoire.get("durable-song")["attachment_refs"] == [ref]
    assert artifacts.raw_bytes(ref["slug"], version=1) == (score, "application/pdf")
    journal = (
        source_home
        / "capabilities"
        / "platform"
        / "migration-journals"
        / f'{checked["archive_digest"]}.json'
    )
    journal_bytes = journal.read_bytes()
    journal_body = json.loads(journal_bytes)
    assert journal_body["status"] == "complete"
    assert journal_body["receipt"] == receipt
    entry = inventory.claim_for("capabilities/platform/migration-journals")
    assert entry is not None
    assert entry.kind == inventory.KIND_TREE
    assert entry in inventory.backup_entries()

    output = tmp_path / "snapshots"
    assert snapshot_module.snapshot_main([str(output), "--keep", "1"]) == 0
    archive = next(output.glob("gideon-snapshot-*.tar.gz"))
    restored_home = tmp_path / "restored-home"
    monkeypatch.setenv("GIDEON_HOME", str(restored_home))
    monkeypatch.setattr(snapshot_module, "_is_gateway_running", lambda: False)
    assert snapshot_module.restore_apply(archive, "replace", None) == {
        "ok": True,
        "mode": "replace",
        "snapshot": archive.name,
    }

    restored_artifacts = NativeArtifactProvider(restored_home / "artifacts")
    restored_repertoire = RepertoireStore(
        restored_home / "capabilities" / "music", restored_artifacts
    )
    restored_item = restored_repertoire.get("durable-song")
    assert restored_item["attachment_refs"] == [ref]
    assert restored_item["stage"] == "learning"
    assert restored_item["last_grade"] == 4
    assert restored_artifacts.raw_bytes(ref["slug"], version=1) == (
        score,
        "application/pdf",
    )
    restored_journal = (
        restored_home
        / "capabilities"
        / "platform"
        / "migration-journals"
        / journal.name
    )
    assert restored_journal.read_bytes() == journal_bytes
    restored_people = PeopleStore(restored_home / "capabilities" / "communications")
    assert receipts(
        restored_people, repertoire=restored_repertoire, artifacts=restored_artifacts
    ) == [receipt]
    replay, replay_created = commit(
        restored_people,
        reviewed,
        repertoire=restored_repertoire,
        artifacts=restored_artifacts,
    )
    assert replay_created is False
    assert replay == receipt
    assert restored_artifacts.raw_bytes(ref["slug"], version=1) == (
        score,
        "application/pdf",
    )
