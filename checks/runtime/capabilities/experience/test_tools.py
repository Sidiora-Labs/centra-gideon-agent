import json
from pathlib import Path

import pytest
from aiohttp import web

from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import ProviderRegistry, ToolTypeHandler
from gideon.integrations.tool_providers.registry import get_provider
from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.sdk.tool import RiskLevel, ToolProvider
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.narration_http import JOBS
from gideon.workspace.capabilities.experience.tools import ExperienceTools, create_provider


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return create_provider()


async def call(provider, name, arguments):
    result = await provider.invoke("experience_" + name, arguments)
    assert result.success, result.error
    return json.loads(result.output)


def authored():
    return {"title": "Forked road", "start_node": "fork", "nodes": [
        {"id": "fork", "text": "Choose a road.", "kind": "scene", "choices": [
            {"id": "left", "label": "Left", "target": "orchard"},
            {"id": "right", "label": "Right", "target": "lake"},
        ]},
        {"id": "orchard", "text": "An orchard.", "kind": "ending", "choices": []},
        {"id": "lake", "text": "A lake.", "kind": "ending", "choices": []},
    ]}


@pytest.mark.asyncio
async def test_native_manifest_loads_registers_invokes_and_disables_real_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    path = ROOT / "runtime/gideon/extensions/apps/native/gideon-experience/app.json"
    manifest = AppManifest.from_json_file(path)
    assert manifest.native is True
    assert manifest.name == "gideon-experience"
    registry = ProviderRegistry()
    registry.register_type_handler("tool", ToolTypeHandler())
    registry.register(manifest, enabled=True)
    registered = registry.get("gideon-experience")
    assert registered.enabled
    assert not registered.error
    provider = registered.provider_instance
    assert isinstance(provider, ToolProvider)
    assert get_provider("gideon-experience") is provider
    result = await call(provider, "story_create", {"story": authored()})
    assert result["story"]["title"] == "Forked road"
    assert ExperienceStore(tmp_path).story(result["story"]["id"]) == result["story"]
    assert registry.disable("gideon-experience")
    assert get_provider("gideon-experience") is None


@pytest.mark.asyncio
async def test_static_tool_schemas_preserve_approval_and_do_not_create_storage(provider, tmp_path):
    definitions = await provider.list_tools()
    assert len(definitions) == 12
    assert len({tool.name for tool in definitions}) == 12
    assert all(tool.provider == "gideon-experience" for tool in definitions)
    assert not (tmp_path / "capabilities/experience.sqlite3").exists()
    for tool in definitions:
        assert tool.parameters["additionalProperties"] is False
        assert set(tool.parameters["required"]) == set(tool.parameters["properties"])
        assert "home" not in tool.parameters["properties"]
        if tool.name.endswith(("_list", "_get")):
            assert tool.requires_approval is False
            assert tool.risk_level == RiskLevel.SAFE
        elif tool.name == "experience_story_delete":
            assert tool.requires_approval is True
            assert tool.risk_level == RiskLevel.DESTRUCTIVE
        else:
            assert tool.requires_approval is True
            assert tool.risk_level == RiskLevel.CAUTION


@pytest.mark.asyncio
async def test_tools_author_update_play_both_branches_and_preserve_source_after_delete(provider):
    story = (await call(provider, "story_create", {"story": authored()}))["story"]
    key = story["id"]
    assert (await call(provider, "story_get", {"id": key}))["story"] == story
    assert (await call(provider, "story_list", {}))["stories"] == [story]
    first = await call(provider, "session_start", {"story_id": key, "story_revision": 1, "request_id": "first"})
    second = await call(provider, "session_start", {"story_id": key, "story_revision": 1, "request_id": "second"})
    assert (await call(provider, "session_list", {}))["sessions"][0]["id"] == second["session"]["id"]
    left = await call(provider, "session_choose", {"id": first["session"]["id"], "revision": 1, "request_id": "left", "choice_id": "left"})
    right = await call(provider, "session_choose", {"id": second["session"]["id"], "revision": 1, "request_id": "right", "choice_id": "right"})
    assert left["node"]["text"] == "An orchard."
    assert right["node"]["text"] == "A lake."
    assert left["session"]["history"][0]["choice_id"] == "left"
    edited = await call(provider, "story_update", {"id": key, "story": {**authored(), "title": "Edited road", "revision": 1}})
    assert edited["story"]["revision"] == 2
    assert await call(provider, "story_delete", {"id": key, "revision": 2}) == {"deleted": True}
    assert await call(provider, "story_list", {}) == {"stories": []}
    assert await call(provider, "session_get", {"id": first["session"]["id"]}) == left
    assert await call(ExperienceTools(provider.store), "session_get", {"id": second["session"]["id"]}) == right


@pytest.mark.asyncio
async def test_tool_and_dashboard_share_narration_lifecycle_without_restart_recovery(provider):
    app = web.Application()
    app[STORE] = provider.store
    register(app)
    story = (await call(provider, "story_create", {"story": authored()}))["story"]
    session = (await call(provider, "session_start", {"story_id": story["id"], "story_revision": 1, "request_id": "start"}))["session"]
    queued = app[JOBS].start(session["id"], {"revision": 1, "request_id": "dashboard"})
    assert queued["status"] == "queued"
    assert provider.jobs is app[JOBS]
    visible = await call(provider, "narration_get", {"id": queued["id"]})
    assert visible["narration"]["status"] == "queued"
    cancelled = await call(provider, "narration_cancel", {"id": queued["id"]})
    assert cancelled["narration"]["status"] == "cancelled"
    assert app[JOBS].get(queued["id"])["status"] == "cancelled"
    created = await call(provider, "narration_start", {"id": session["id"], "revision": 1, "request_id": "tool"})
    assert created["narration"]["story_revision"] == 1
    assert created["narration"]["node_id"] == "fork"
    assert (await call(provider, "narration_cancel", {"id": created["narration"]["id"]}))["narration"]["status"] == "cancelled"
    assert provider.jobs.artifacts.list() == []
    await provider.jobs.close()


@pytest.mark.asyncio
async def test_unknown_and_malformed_operations_do_not_mutate(provider):
    for name, body in [("story_list", {}), ("experience_unknown", {}), ("experience_story_list", {"home": "/tmp/elsewhere"}), ("experience_story_create", []), ("experience_session_start", {})]:
        result = await provider.invoke(name, body)
        assert not result.success
        assert result.error
    assert await call(provider, "story_list", {}) == {"stories": []}
    assert await call(provider, "session_list", {}) == {"sessions": []}
    missing = await provider.invoke("experience_story_get", {"id": "missing"})
    assert not missing.success
    assert missing.metadata == {"kind": "NotFound"}
    malformed = await provider.invoke("experience_story_create", {"story": {"title": "Incomplete"}})
    assert not malformed.success
    assert malformed.metadata == {"kind": "ValueError"}


@pytest.mark.asyncio
async def test_stale_edits_and_request_conflicts_are_returned_to_agent(provider):
    story = (await call(provider, "story_create", {"story": authored()}))["story"]
    start = {"story_id": story["id"], "story_revision": 1, "request_id": "open"}
    opened = await call(provider, "session_start", start)
    assert await call(provider, "session_start", start) == opened
    await call(provider, "story_update", {"id": story["id"], "story": {**authored(), "revision": 1}})
    result = await provider.invoke("experience_story_update", {"id": story["id"], "story": {**authored(), "revision": 1}})
    assert not result.success
    assert result.metadata == {"kind": "Conflict"}
    result = await provider.invoke("experience_session_start", {**start, "story_revision": 2})
    assert not result.success
    assert "different input" in result.error
