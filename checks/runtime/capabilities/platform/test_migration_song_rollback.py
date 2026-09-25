import hashlib
import json

import pytest

from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.music.store import RepertoireStore
from gideon.workspace.capabilities.platform.migration import MigrationError, commit, preview, receipts
from test_migration import song_archive, song_record


def stores(tmp_path):
    home = tmp_path / "home"
    artifacts = NativeArtifactProvider(home / "artifacts")
    repertoire = RepertoireStore(home / "capabilities" / "music", artifacts)
    return home, artifacts, repertoire


def source_archive():
    raw = b"%PDF-1.7\nrollback safety\n"
    attachment = {"filename": "score.pdf", "label": "Score", "mime": "application/pdf", "size": len(raw),
                  "sha256": hashlib.sha256(raw).hexdigest()}
    return song_archive(song_record(identity="song-rollback", attachments=[attachment]), {"score.pdf": raw}), raw


def reviewed(source):
    checked = preview(source)
    return {**source, "archive_digest": checked["archive_digest"], "review_token": checked["review_token"]}, checked


def interrupted(tmp_path, *, fingerprint=True):
    home, artifacts, repertoire = stores(tmp_path)
    source, raw = source_archive()
    _, checked = reviewed(source)
    slug = "legacy-song-" + hashlib.sha256(b"song-rollback:score.pdf").hexdigest()[:32]
    artifacts.create_binary(name="Score", data=raw, mime="application/pdf", kind="pdf", source="import", slug=slug,
        tags=["archive-migration", "song-attachment"], description="Restored song attachment: score.pdf",
        event_metadata={"archive_digest": checked["archive_digest"], "source_filename": "score.pdf"})
    import_id = "platform-song-" + checked["archive_digest"]
    repertoire.import_song(import_id=import_id, source_fingerprint=checked["review_token"], item_id="song-rollback",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-09-25T12:00:00+00:00",
        data={"title": "Midnight Train", "attachment_refs": [{"slug": slug, "version": 1}]},
        schedule={"stage": "learning", "ease": 2.7, "interval": 6, "repetitions": 4,
                  "due_at": "2026-10-01T12:00:00+00:00", "last_practiced_at": "2026-09-25T12:00:00+00:00",
                  "last_grade": 4})
    plan = {"slug": slug, "version": 1, "kind": "pdf", "mime": "application/pdf",
            "sha256": hashlib.sha256(raw).hexdigest(), "owned": True}
    if fingerprint:
        plan["ownership_fingerprint"] = artifacts.state_fingerprint(slug)
    journal = {"schema": 1, "status": "artifacts_ready", "archive_digest": checked["archive_digest"],
               "review_token": checked["review_token"], "import_id": import_id,
               "song_id": "song-rollback", "artifacts": [plan]}
    root = home / "capabilities" / "platform" / "migration-journals"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "interrupted.json"
    path.write_text(json.dumps(journal))
    return home, artifacts, repertoire, path, slug, checked["review_token"], import_id


def test_unchanged_exact_owned_artifact_is_conditionally_rolled_back(tmp_path):
    home, artifacts, repertoire, path, slug, _, _ = interrupted(tmp_path)
    assert receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts) == []
    assert artifacts.get(slug) is None and not path.exists()
    assert repertoire.list() == []


def test_version_and_event_drift_preserves_song_artifact_history_and_journal(tmp_path):
    home, artifacts, repertoire, path, slug, _, _ = interrupted(tmp_path)
    original = artifacts.get(slug)
    artifacts.update_binary(slug, data=b"%PDF-1.7\nuser version two\n", mime="application/pdf",
                            actor="user", expect_version=1)
    with pytest.raises(MigrationError, match="cannot be rolled back safely") as caught:
        receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts)
    assert caught.value.status == 409
    assert artifacts.get(slug).version == 2 and artifacts.get(slug, version=1) is not None
    assert artifacts.get(slug).updated_at != original.updated_at
    assert repertoire.get("song-rollback")["title"] == "Midnight Train" and path.exists()


def test_metadata_drift_preserves_song_artifact_and_journal(tmp_path):
    home, artifacts, repertoire, path, slug, _, _ = interrupted(tmp_path)
    artifacts.update(slug, name="User renamed score", description="Retain this history")
    with pytest.raises(MigrationError, match="cannot be rolled back safely"):
        receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts)
    artifact = artifacts.get(slug)
    assert artifact.name == "User renamed score" and artifact.description == "Retain this history"
    assert repertoire.get("song-rollback")["title"] == "Midnight Train" and path.exists()


def test_legacy_ambiguous_ownership_fails_closed_but_complete_replay_stays_exact(tmp_path):
    home, artifacts, repertoire, ambiguous_path, slug, token, import_id = interrupted(tmp_path, fingerprint=False)
    with pytest.raises(MigrationError, match="ambiguous artifact ownership"):
        receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts)
    assert artifacts.get(slug) is not None and ambiguous_path.exists()
    assert repertoire.get("song-rollback")["title"] == "Midnight Train"
    ambiguous_path.unlink()
    repertoire.rollback_import(import_id, token)
    artifacts.delete(slug)
    source, _ = source_archive()
    payload, checked = reviewed(source)
    receipt, created = commit(PeopleStore(home / "people"), payload, repertoire=repertoire, artifacts=artifacts)
    path = home / "capabilities" / "platform" / "migration-journals" / f'{checked["archive_digest"]}.json'
    body = json.loads(path.read_text())
    assert created is True and body["status"] == "complete"
    assert all(plan.get("ownership_fingerprint") for plan in body["artifacts"] if plan["owned"])
    before = path.read_bytes()
    replay, replay_created = commit(PeopleStore(home / "people"), payload, repertoire=repertoire, artifacts=artifacts)
    assert replay_created is False and replay == receipt and path.read_bytes() == before


def test_prepared_missing_fingerprint_refuses_existing_symlink_before_song_mutation(tmp_path):
    home, artifacts, repertoire, path, slug, _, _ = interrupted(tmp_path, fingerprint=False)
    body = json.loads(path.read_text())
    body["status"] = "prepared"
    path.write_text(json.dumps(body))
    assert artifacts.delete(slug) is True
    external = tmp_path / "external-artifact"
    external.mkdir()
    (external / "meta.json").write_text('{"user":"owned"}')
    (artifacts.root / slug).symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="safe directory"):
        artifacts.state_fingerprint(slug)
    with pytest.raises(MigrationError, match="cannot be rolled back safely") as caught:
        receipts(PeopleStore(home / "people"), repertoire=repertoire, artifacts=artifacts)
    assert caught.value.status == 409
    with repertoire._db() as database:
        assert database.execute("SELECT count(*) FROM items WHERE id='song-rollback'").fetchone()[0] == 1
    assert (artifacts.root / slug).is_symlink() and (external / "meta.json").read_text() == '{"user":"owned"}'
    assert path.exists()
