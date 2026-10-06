import json
from pathlib import Path

import pytest

from gideon.extensions.skills import loader, marketplace, overlays, shipped


def payload(text="Original"):
    return [
        {
            "path": "SKILL.md",
            "contents": f"---\nname: sample\ndescription: A sample skill\n---\n{text}\n",
        }
    ]


def record(folder, source="native"):
    marketplace.write_install_record(
        folder,
        skill_id="sample",
        source=source,
        trust_tier="builtin",
        verdict="clean",
        sha256=marketplace.file_digests(folder),
    )


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin" / "sample"
    marketplace.install_skill_files(payload(), "sample", origin.parent)
    monkeypatch.setattr(shipped, "_roots", lambda: [origin.parent])
    return tmp_path / "home" / "skills", origin


def test_sync_updates_baseline_but_preserves_owner_files(library):
    base, origin = library
    assert shipped.sync(base).installed == ["sample"]
    assert marketplace.verify_skill_integrity(base / "sample").state == "intact"
    (origin / "SKILL.md").write_text(payload("Version two")[0]["contents"])
    assert shipped.sync(base).replaced == ["sample"]
    (base / "sample" / "SKILL.md").write_text(payload("Owner text")[0]["contents"])
    (base / "sample" / "asset.bin").write_bytes(b"\x00Owner asset")
    (origin / "SKILL.md").write_text(payload("Version three")[0]["contents"])
    assert shipped.sync(base).kept == ["sample"]
    assert "Owner text" in (base / "sample" / "SKILL.md").read_text()
    assert (base / "sample" / "asset.bin").read_bytes() == b"\x00Owner asset"
    assert marketplace.verify_skill_integrity(base / "sample").state == "edited"


@pytest.mark.parametrize("body", ["{", "[]", '{"sha256":[]}'])
def test_damaged_record_is_tampered_and_sync_keeps_bytes(library, body):
    base, origin = library
    shipped.sync(base)
    lock = base / "sample" / marketplace.LOCK_FILENAME
    lock.write_text(body)
    old = (base / "sample" / "SKILL.md").read_bytes()
    (origin / "SKILL.md").write_text(payload("New version")[0]["contents"])
    assert marketplace.verify_skill_integrity(base / "sample").state == "tampered"
    shipped.sync(base)
    assert lock.read_text() == body
    assert (base / "sample" / "SKILL.md").read_bytes() == old


def test_unknown_legacy_copy_and_other_installer_are_protected(library):
    base, origin = library
    marketplace.install_skill_files(payload("Legacy owner"), "sample", base)
    shipped.sync(base)
    assert "Legacy owner" in (base / "sample" / "SKILL.md").read_text()
    record(base / "sample", source="another-source")
    shipped.sync(base)
    assert "Legacy owner" in (base / "sample" / "SKILL.md").read_text()


def test_full_folder_replacement_removes_old_files_and_failed_stage_preserves(
    library, monkeypatch
):
    base, origin = library
    marketplace.install_skill_files(
        payload() + [{"path": "obsolete.txt", "contents": "old"}], "sample", base
    )
    marketplace.install_skill_files(payload("New"), "sample", base)
    assert not (base / "sample" / "obsolete.txt").exists()

    def fail(*args):
        raise OSError("staging failed")

    monkeypatch.setattr(marketplace, "_stage_files", fail)
    with pytest.raises(OSError):
        marketplace.install_skill_files(payload("Bad"), "sample", base)
    assert "New" in (base / "sample" / "SKILL.md").read_text()


def test_retired_modified_and_refined_copies_remain(library, monkeypatch):
    base, origin = library
    shipped.sync(base)
    overlays.apply_overlay("sample", procedure_md="Owner refinement")
    monkeypatch.setattr(shipped, "_roots", lambda: [])
    assert shipped.sync(base).kept == ["sample"]
    overlays.revert_overlay("sample")
    assert shipped.sync(base).removed == ["sample"]


def test_namespace_group_is_not_deleted(library):
    base, _ = library
    marketplace.install_skill_files(payload(), "sample", base / "group")
    view = loader.ProcedureLibrary(base, install_builtins=False)
    assert not view.delete_skill("group")
    assert (base / "group" / "sample" / "SKILL.md").is_file()
