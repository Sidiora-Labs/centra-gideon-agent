import json
import os
import zipfile

import pytest

from gideon.extensions.packs.build import build_pack
from gideon.extensions.packs.import_ import import_pack
from gideon.extensions.packs.update import PackUpdateError, apply_update, plan_update
from gideon.security.supply_chain import TrustTier


def actual_versions(tmp_path, monkeypatch):
    author = tmp_path / "author"
    (author / "prompts").mkdir(parents=True)
    versions = []
    for version in ["1.0.0", "2.0.0"]:
        for name in ["linked", "edited", "safe"]:
            (author / "prompts" / f"{name}.yaml").write_text(
                f"name: {name}\nkind: user\ncontent: version {version}\n"
            )
        monkeypatch.setenv("GIDEON_HOME", str(author))
        path = tmp_path / f"{version}.gideon"
        build_pack(
            ["prompt:linked", "prompt:edited", "prompt:safe"],
            name="sample",
            version=version,
            out_path=path,
        )
        with zipfile.ZipFile(path) as reader:
            members = {name: reader.read(name) for name in reader.namelist()}
        manifest = json.loads(members["pack.json"])
        manifest["pack_owned"] = ["prompts/*"]
        members["pack.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, "w") as writer:
            for name, data in members.items():
                writer.writestr(name, data)
        versions.append(path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    import_pack(versions[0], tier=TrustTier.BUILTIN)
    return home, versions[1]


@pytest.mark.parametrize("kind", ["hard", "symbolic"])
def test_public_update_skips_link_and_edit_updates_safe_sibling(
    tmp_path, monkeypatch, kind
):
    home, new = actual_versions(tmp_path, monkeypatch)
    linked = home / "prompts/linked.yaml"
    outside = tmp_path / "outside"
    outside.write_bytes(linked.read_bytes())
    linked.unlink()
    if kind == "hard":
        os.link(outside, linked)
    else:
        linked.symlink_to(outside)
    edited = home / "prompts/edited.yaml"
    edited.write_text("owner changed this")
    before = outside.read_bytes()
    preview = plan_update("sample", new, tier=TrustTier.BUILTIN)
    row = next(item for item in preview.components if item.ref == "prompt:linked")
    assert row.action == "skip_unverifiable"
    assert "linked" in row.reason
    result = apply_update("sample", new, tier=TrustTier.BUILTIN)
    assert result.applied is True
    assert "prompt:safe" in result.overwritten
    assert "prompt:linked" in result.skipped
    assert "prompt:edited" in result.skipped
    assert any(
        "prompt:linked" in reason and "linked" in reason
        for reason in result.drift_notes
    )
    assert outside.read_bytes() == before
    assert edited.read_text() == "owner changed this"
    assert "version 2.0.0" in (home / "prompts/safe.yaml").read_text()


def test_public_update_journal_link_refuses_before_safe_publication(
    tmp_path, monkeypatch
):
    home, new = actual_versions(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    installing = home / "packs/.installing"
    assert not installing.exists()
    installing.symlink_to(outside, target_is_directory=True)
    safe = home / "prompts/safe.yaml"
    before = safe.read_bytes()
    ledger = (home / "packs/installed.json").read_bytes()
    with pytest.raises(PackUpdateError, match="destinations refused"):
        apply_update("sample", new, tier=TrustTier.BUILTIN)
    assert safe.read_bytes() == before
    assert (home / "packs/installed.json").read_bytes() == ledger
    assert list(outside.iterdir()) == []
