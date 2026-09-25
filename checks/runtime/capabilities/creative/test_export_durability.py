import tarfile
from pathlib import Path

from gideon.workspace.capabilities.creative.exports import ManuscriptExports
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.snapshot import snapshot_main


def test_real_manuscript_export_records_pins_and_files_survive_snapshot_extraction(
    tmp_path, monkeypatch
):
    home = tmp_path / "source-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    works = WorkStore(home)
    work = works.create(
        {
            "request_id": "durable-work",
            "title": "Durable manuscript",
            "kind": "work",
            "prompt": "Preserve this manuscript",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    drafted = works.draft(
        work["id"],
        {
            "request_id": "durable-draft",
            "revision": work["revision"],
            "text": "# Source\n\nCanonical export bytes.",
            "note": "snapshot proof",
        },
    )
    store = ManuscriptExports(home, works=works)
    receipt = store.create(
        {
            "request_id": "durable-export",
            "source_kind": "work",
            "source_id": drafted["work"]["id"],
            "source_revision": drafted["work"]["revision"],
            "title": "Durable edition",
            "creator": "Gideon",
            "language": "en",
            "identifier": "urn:gideon:durable-export",
        }
    )
    original = {
        kind: store.file(receipt["id"], kind)[0].read_bytes()
        for kind in ("epub", "print")
    }
    assert receipt["selections"][0] == {
        "title": "Durable manuscript",
        "work_id": drafted["work"]["id"],
        "work_revision": drafted["work"]["revision"],
        "draft_id": drafted["draft"]["id"],
        "artifact_id": drafted["draft"]["artifact_id"],
        "artifact_version": drafted["draft"]["artifact_version"],
        "sha256": receipt["selections"][0]["sha256"],
    }

    output = tmp_path / "snapshots"
    assert snapshot_main([str(output), "--keep", "1"]) == 0
    archive = next(output.glob("gideon-snapshot-*.tar.gz"))
    extracted = tmp_path / "extracted"
    with tarfile.open(archive) as bundle:
        bundle.extractall(extracted, filter="data")
    snapshot_home = next(path for path in extracted.iterdir() if path.is_dir())
    fresh_home = tmp_path / "fresh-source-home"
    snapshot_home.rename(fresh_home)

    restored = ManuscriptExports(fresh_home, works=WorkStore(fresh_home))
    assert restored.get(receipt["id"]) == receipt
    assert restored.list() == [receipt]
    for kind in ("epub", "print"):
        assert restored.file(receipt["id"], kind)[0].read_bytes() == original[kind]
