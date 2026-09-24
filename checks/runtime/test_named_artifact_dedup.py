from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZipFile

import pytest

from gideon.integrations.mcp_artifacts import _call_tool, _list_tools
from gideon.integrations.mcp_core import _CURRENT_SESSION_KEY
from gideon.workspace.artifacts import registry
from gideon.workspace.artifacts.native import NativeArtifactProvider


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    previous = registry.get_provider("native")
    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    registry.register_provider(provider)
    token = _CURRENT_SESSION_KEY.set("dashboard:named-artifacts")
    try:
        yield provider
    finally:
        _CURRENT_SESSION_KEY.reset(token)
        registry.unregister_provider("native")
        if previous is not None:
            registry.register_provider(previous)


FORMATS = [
    ("document_create", "docx"),
    ("sheet_create", "xlsx"),
    ("deck_create", "pptx"),
    ("sheet_create", "csv"),
]


def create(tool, fmt, name="Quarterly Report", text="InitialValue", **kwargs):
    body = (
        {"rows": [["Value"], [text]]}
        if tool == "sheet_create"
        else {"markdown": f"# Report\n\n## Results\n\n- {text}"}
    )
    return _call_tool(tool, {"name": name, "format": fmt, **body, **kwargs})


def contents(provider, slug, fmt, version=None):
    if fmt == "csv":
        return provider.get(slug, version=version).content
    data, _mime = provider.raw_bytes(slug, version=version)
    with ZipFile(io.BytesIO(data)) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xml")
        )


@pytest.mark.parametrize("tool,fmt", FORMATS)
def test_named_creation_updates_real_file_and_preserves_version(provider, tool, fmt):
    first = create(tool, fmt)
    assert first.startswith(f"Created {fmt}:")
    original = provider.list()[0]
    reply = create(tool, fmt, name="Quarterly report!", text="RevisedValue")
    assert reply.startswith(f"Updated {fmt}: {original.slug} (v2,")
    assert len(provider.list()) == 1
    assert "RevisedValue" in contents(provider, original.slug, fmt)
    assert "InitialValue" in contents(provider, original.slug, fmt, version=1)
    assert provider.get(original.slug).events[-1].type == "iterated"


def test_name_lookup_is_scoped_to_output_kind(provider):
    for tool, fmt in FORMATS:
        assert create(tool, fmt).startswith(f"Created {fmt}:")
    assert len(provider.list()) == len(FORMATS)
    for tool, fmt in FORMATS:
        original = provider.list(kind=fmt)[0]
        assert create(tool, fmt, text="RevisedValue").startswith(
            f"Updated {fmt}: {original.slug} (v2,"
        )
        assert "RevisedValue" in contents(provider, original.slug, fmt)
    assert len(provider.list()) == len(FORMATS)


@pytest.mark.parametrize("tool,fmt", FORMATS)
def test_explicit_slug_updates_selected_artifact_and_can_create_separate_one(
    provider, tool, fmt
):
    create(tool, fmt)
    create(tool, fmt, name="Other Artifact", slug="selected-artifact")
    reply = create(tool, fmt, slug="selected-artifact", text="SelectedValue")
    assert reply.startswith(f"Updated {fmt}: selected-artifact (v2,")
    assert provider.get("quarterly-report").version == 1
    assert "SelectedValue" in contents(provider, "selected-artifact", fmt)
    reply = create(tool, fmt, slug="separate-artifact")
    assert reply.startswith(f"Created {fmt}: separate-artifact (v1,")
    assert len(provider.list()) == 3


@pytest.mark.parametrize("tool,fmt", FORMATS)
def test_explicit_slug_kind_mismatch_does_not_fall_back_to_name(provider, tool, fmt):
    create(tool, fmt)
    provider.create(
        name="Wrong kind", slug="wrong-kind", content="preserve", kind="text"
    )
    reply = create(tool, fmt, slug="wrong-kind", text="RefusedValue")
    assert reply.startswith("Error:") and f"not {fmt}" in reply
    assert provider.get("quarterly-report").version == 1
    assert provider.get("wrong-kind").content == "preserve"
    assert len(provider.list()) == 2


@pytest.mark.parametrize("tool", ["document_create", "sheet_create", "deck_create"])
def test_tool_and_reference_describe_name_matching_and_slug_precedence(tool):
    description = next(
        row["description"] for row in _list_tools() if row["name"] == tool
    )
    reference = (
        Path(__file__).resolve().parents[2] / "runtime/gideon/reference/tools.md"
    ).read_text()
    section = reference.split(f"### `{tool}`\n", 1)[1].split("\n### ", 1)[0]
    for text in (description, section):
        assert "matching name in the same format" in text
        assert "An explicit slug takes precedence over name matching" in text
        assert "Updated" in text


def test_similarity_project_scope_distinguishes_all_unscoped_and_named(provider):
    for project_id in ("", "project-a", "project-b"):
        provider.create(
            name="Shared Name",
            content=project_id or "unscoped",
            kind="text",
            project_id=project_id,
        )
    matches = {art.project_id: art for art in provider.list()}
    assert provider.find_similar("Shared Name", kind="text", project_id=None).slug in {
        art.slug for art in matches.values()
    }
    for project_id, art in matches.items():
        match = provider.find_similar(
            "Shared name!", kind="text", project_id=project_id
        )
        assert match.slug == art.slug
        assert match.project_id == project_id
        assert (
            provider.find_similar("Shared Name", kind="widget", project_id=project_id)
            is None
        )
    assert provider.find_similar("Shared Name", project_id="absent") is None
    scoped = provider.create(name="Scoped Only", content="x", project_id="project-a")
    assert provider.find_similar("Scoped Only", project_id=None).slug == scoped.slug
    assert provider.find_similar("Scoped Only").slug == scoped.slug
    assert provider.find_similar("Scoped Only", project_id="") is None


@pytest.mark.parametrize("tool,fmt", FORMATS)
def test_named_creation_deduplicates_only_within_current_project(provider, tool, fmt):
    from gideon.engine.agents.native.builtin_tools import (
        bind_tool_context,
        reset_tool_context,
    )

    for project_id in ("project-a", "project-b", ""):
        tokens = bind_tool_context(cwd=None, project_id=project_id)
        try:
            assert create(tool, fmt, text=project_id or "UnscopedValue").startswith(
                "Created "
            )
            assert create(tool, fmt, text="RevisedValue").startswith("Updated ")
        finally:
            reset_tool_context(tokens)
    artifacts = provider.list()
    assert len(artifacts) == 3
    assert {art.project_id for art in artifacts} == {"project-a", "project-b", ""}
    assert all(art.version == 2 for art in artifacts)
    for art in artifacts:
        assert "RevisedValue" in contents(provider, art.slug, fmt)
        assert (art.project_id or "UnscopedValue") in contents(
            provider, art.slug, fmt, version=1
        )


def test_artifact_save_name_hint_is_project_scoped(provider):
    from gideon.engine.agents.native.builtin_tools import (
        bind_tool_context,
        reset_tool_context,
    )

    for project_id in ("project-a", "project-b", ""):
        tokens = bind_tool_context(cwd=None, project_id=project_id)
        try:
            args = {"name": "Shared Name", "kind": "text", "content": "SavedValue"}
            assert _call_tool("artifact_save", args).startswith("Saved artifact")
            assert "already exists" in _call_tool("artifact_save", args)
        finally:
            reset_tool_context(tokens)
    assert len(provider.list()) == 3
    assert {art.project_id for art in provider.list()} == {"project-a", "project-b", ""}


@pytest.mark.asyncio
async def test_rest_name_hint_is_project_scoped(provider):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.workspace.artifacts.handlers import register_artifact_routes

    app = web.Application()
    app["state"] = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    register_artifact_routes(app)
    async with TestClient(TestServer(app)) as client:
        for project_id in ("project-a", "project-b", ""):
            args = {
                "name": "Shared Name",
                "content": "SavedValue",
                "project_id": project_id,
            }
            response = await client.post("/api/artifacts", json=args)
            assert response.status == 201
            created = await response.json()
            response = await client.post("/api/artifacts", json=args)
            assert response.status == 409
            assert (await response.json())["similar"]["slug"] == created["slug"]
    assert len(provider.list()) == 3
