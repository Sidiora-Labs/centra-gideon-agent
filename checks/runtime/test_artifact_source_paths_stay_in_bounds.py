from __future__ import annotations

from gideon.core.file_roots import dashboard_roots
from gideon.workspace.artifacts import source_files


def test_artifact_source_admission_matches_files_and_refuses_escapes(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))

    allowed = workspace / "brief.md"
    allowed.write_text("brief", encoding="utf-8")
    secret = home / "config.json"
    secret.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "escape.md"
    link.symlink_to(outside)

    roots = [root for _label, root in dashboard_roots()]
    assert str(workspace.resolve()) in roots
    assert source_files.admitted(str(allowed)) == str(allowed.resolve())
    assert source_files.admitted(str(secret)) is None
    assert source_files.admitted(str(outside)) is None
    assert source_files.admitted(str(link)) is None


def test_read_only_outside_home_grant_never_becomes_an_artifact_source(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    for path in (home, workspace, external):
        path.mkdir()
    source = external / "permitted-read-only.md"
    source.write_text("do not write", encoding="utf-8")
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))

    # Files may extend its roots with an owner-granted read path at the call site;
    # artifact source roots remain exactly the writable dashboard roots.
    assert source_files.admitted(str(source)) is None
    assert source.read_text(encoding="utf-8") == "do not write"
