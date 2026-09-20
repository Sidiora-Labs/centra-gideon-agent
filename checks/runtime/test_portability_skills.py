import io
import zipfile

from gideon.workspace.portability import apply_import_zip, create_export_zip


def test_export_import_carries_all_skill_trees_without_overwriting(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    files = {
        "skills/authored/SKILL.md": "authored",
        "skills/auto/generated/SKILL.md": "generated",
        "skills/team/auto/helper.txt": "nested-auto",
    }
    for rel, content in files.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setenv("GIDEON_HOME", str(source))
    archive_bytes, manifest = create_export_zip()
    names = zipfile.ZipFile(io.BytesIO(archive_bytes)).namelist()
    assert manifest["contents"]["skill_count"] == len(files)
    assert all(any(name.endswith(rel) for name in names) for rel in files)

    archive = tmp_path / "export.zip"
    archive.write_bytes(archive_bytes)
    target = tmp_path / "target"
    existing = target / "skills/auto/generated/SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("local", encoding="utf-8")
    monkeypatch.setenv("GIDEON_HOME", str(target))

    summary = apply_import_zip(archive, mode="merge")

    assert existing.read_text(encoding="utf-8") == "local"
    assert (target / "skills/authored/SKILL.md").read_text(
        encoding="utf-8"
    ) == "authored"
    assert (target / "skills/team/auto/helper.txt").read_text(
        encoding="utf-8"
    ) == "nested-auto"
    assert "skills (merged)" in summary["items"]
