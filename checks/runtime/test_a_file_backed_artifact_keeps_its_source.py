from __future__ import annotations

import pytest

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.integrations.mcp_artifacts import _read_artifact_content
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.artifacts.source_files import revision_of


def test_absent_content_seeds_artifact_without_writing_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    source = workspace / "brief.md"
    source.write_text("the source stays", encoding="utf-8")
    before = source.read_bytes()

    provider = NativeArtifactProvider(home / "artifacts")
    artifact = provider.create(
        name="Brief",
        source_path=str(source),
        source_revision=revision_of("the source stays"),
    )

    assert artifact.content == "the source stays"
    assert source.read_bytes() == before
    assert provider.get(artifact.slug).content == "the source stays"


def test_empty_text_is_explicit_revision_checked_replacement(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    source = workspace / "brief.md"
    source.write_text("initial", encoding="utf-8")
    provider = NativeArtifactProvider(home / "artifacts")
    artifact = provider.create(
        name="Brief",
        source_path=str(source),
        source_revision=revision_of("initial"),
    )

    provider.update(artifact.slug, content="", source_revision=revision_of("initial"))

    assert source.read_text(encoding="utf-8") == ""
    assert provider.get(artifact.slug).content == ""


def test_stale_update_preserves_artifact_and_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    source = workspace / "brief.md"
    source.write_text("original", encoding="utf-8")
    provider = NativeArtifactProvider(home / "artifacts")
    artifact = provider.create(
        name="Brief",
        source_path=str(source),
        source_revision=revision_of("original"),
    )
    source.write_text("changed elsewhere", encoding="utf-8")

    with pytest.raises(ValueError, match="changed"):
        provider.update(
            artifact.slug,
            content="stale edit",
            source_revision=revision_of("original"),
        )

    assert source.read_text(encoding="utf-8") == "changed elsewhere"
    assert provider.get(artifact.slug).content == "changed elsewhere"
    assert provider.get(artifact.slug, version=1).content == "original"


def test_stale_source_revision_refuses_before_artifact_or_file_mutation(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    source = workspace / "brief.md"
    source.write_text("current", encoding="utf-8")
    provider = NativeArtifactProvider(home / "artifacts")

    with pytest.raises(ValueError, match="changed"):
        provider.create(
            name="Brief",
            content="stale edit",
            source_path=str(source),
            source_revision=revision_of("older copy"),
        )

    assert source.read_text(encoding="utf-8") == "current"
    assert provider.list() == []


def test_contentless_source_create_requires_the_named_revision(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    source = workspace / "brief.md"
    source.write_text("current", encoding="utf-8")
    provider = NativeArtifactProvider(home / "artifacts")

    with pytest.raises(ValueError, match="source_revision is required"):
        provider.create(name="Brief", source_path=str(source))

    assert source.read_text(encoding="utf-8") == "current"
    assert provider.list() == []


def test_unapproved_source_is_refused_before_artifact_creation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    provider = NativeArtifactProvider(home / "artifacts")

    with pytest.raises(ValueError, match="artifact source"):
        provider.create(
            name="Outside",
            source_path=str(outside),
            source_revision=revision_of("private"),
        )

    assert provider.list() == []
    assert outside.read_text(encoding="utf-8") == "private"


@pytest.mark.asyncio
async def test_native_write_file_requires_explicit_text_but_accepts_empty_text(
    tmp_path,
):
    provider = NativeBuiltinToolProvider(cwd=tmp_path, sandbox_mode="off")
    missing = await provider._t_write_file({"path": "missing.txt"})
    null = await provider._t_write_file({"path": "missing.txt", "content": None})
    assert not missing.success and not null.success
    assert not (tmp_path / "missing.txt").exists()

    empty = await provider._t_write_file({"path": "cleared.txt", "content": ""})
    assert empty.success
    assert (tmp_path / "cleared.txt").read_text(encoding="utf-8") == ""


def test_mcp_content_adapter_preserves_absent_null_and_empty():
    assert _read_artifact_content({}) == (None, None)
    assert _read_artifact_content({"content": None}) == (None, None)
    assert _read_artifact_content({"content": ""}) == ("", None)
    assert _read_artifact_content({"content": 3}) == (
        None,
        "content must be text or null",
    )
