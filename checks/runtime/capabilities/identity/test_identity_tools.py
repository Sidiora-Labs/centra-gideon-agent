import importlib
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from gideon.engine import session_restrictions
from gideon.extensions.apps.manifest import AppManifest
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.integrations.tool_providers.base import RiskLevel, ToolProvider
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider, create_provider
from gideon.workspace.capabilities.identity.twin import TwinStore


@pytest.fixture
def local_session():
    session = "dashboard:identity-tools"
    token = set_current_session_key(session)
    yield session
    session_restrictions.clear(session)
    reset_current_session_key(token)


async def invoke(provider, name, **arguments):
    result = await provider.invoke(name, arguments)
    assert result.success, result.error
    assert result.metadata["operation"] == name
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_manifest_factory_discovery_and_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    root = Path(__file__).resolve().parents[4]
    manifest_path = root / "runtime/gideon/extensions/apps/native/gideon-identity/app.json"
    raw = json.loads(manifest_path.read_text())
    manifest = AppManifest.from_dict(raw)
    assert manifest.validate() == []
    module, factory = raw["provider"]["implementation"].split(":")
    provider = getattr(importlib.import_module(module), factory)({})
    assert isinstance(provider, ToolProvider)
    assert provider.name == raw["name"] == "gideon-identity"
    assert provider.home == tmp_path
    assert provider.connected
    assert provider.display_name == "Human Identity"
    assert not (tmp_path / "capabilities/identity").exists()
    tools = {tool.name: tool for tool in await provider.list_tools()}
    assert "identity_story_create" in tools
    assert "identity_twin_context" in tools
    assert "identity_twin_enrich" in tools
    for tool in tools.values():
        Draft202012Validator.check_schema(tool.parameters)
        assert tool.parameters["additionalProperties"] is False
        assert "home" not in tool.parameters["properties"]
        assert "session_key" not in tool.parameters["properties"]
        assert tool.provider == "gideon-identity"
    assert tools["identity_story_create"].requires_approval
    assert tools["identity_story_delete"].risk_level == RiskLevel.DESTRUCTIVE
    assert not tools["identity_twin_context"].requires_approval
    assert tools["identity_twin_enrich"].risk_level == RiskLevel.CAUTION
    with pytest.raises(ValueError, match="runtime home"):
        create_provider({"home": str(tmp_path / "elsewhere")})


@pytest.mark.asyncio
async def test_story_tools_persist_full_lifecycle(tmp_path, local_session):
    provider = IdentityToolProvider(tmp_path)
    assert await invoke(provider, "identity_story_list") == []
    body = {"prompt": "An important lesson?", "theme": "Learning", "text": "Keep asking questions.", "request_id": "root"}
    root = await invoke(provider, "identity_story_create", **body)
    assert root["revision"] == 1
    assert await invoke(provider, "identity_story_create", **body) == root
    child = await invoke(provider, "identity_story_create", **{**body, "prompt": "Who taught you?", "parent_id": root["id"], "request_id": "child"})
    edited = await invoke(provider, "identity_story_update", story_id=child["id"], expected_revision=1,
                          prompt=child["prompt"], theme=child["theme"], text="My teacher.", parent_id=root["id"])
    assert edited["revision"] == 2
    reopened = IdentityToolProvider(tmp_path)
    assert await invoke(reopened, "identity_story_get", story_id=child["id"]) == edited
    assert await invoke(reopened, "identity_story_chain", story_id=child["id"]) == [root, edited]
    assert await invoke(reopened, "identity_story_history", story_id=child["id"]) == [child, edited]
    exported = await invoke(reopened, "identity_story_export")
    assert exported["stories"] == [root, edited]
    assert len(exported["history"]) == 3
    conflict = await provider.invoke("identity_story_delete", {"story_id": child["id"], "expected_revision": 1})
    assert not conflict.success
    assert conflict.metadata["status"] == "conflict"
    assert conflict.recovery_hints
    parent_failure = await provider.invoke("identity_story_delete", {"story_id": root["id"], "expected_revision": 1})
    assert not parent_failure.success
    assert "follow-ups" in parent_failure.error
    assert await invoke(provider, "identity_story_delete", story_id=child["id"], expected_revision=2) == {"deleted": child["id"]}
    assert await invoke(provider, "identity_story_chain", story_id=root["id"]) == [root]
    assert await invoke(provider, "identity_story_history", story_id=child["id"]) == [child, edited]


@pytest.mark.asyncio
async def test_twin_tools_preserve_private_sources_and_real_context(tmp_path, local_session):
    provider = IdentityToolProvider(tmp_path)
    core = TwinStore(tmp_path / "capabilities/identity/twin.sqlite3")
    secret = core.save_document(title="Private", text="personal secret", private=True, expected_revision=0)["documents"][0]
    visible = await invoke(provider, "identity_twin_get")
    assert visible["documents"] == []
    public = await invoke(provider, "identity_twin_save_document", title="Values", text="Curiosity matters.", expected_revision=1)
    assert len(public["documents"]) == 1
    public_id = public["documents"][0]["id"]
    assert secret["id"] not in json.dumps(public)
    configured = await invoke(provider, "identity_twin_configure", expected_revision=2, enabled=True,
                              traits={"curiosity": 9}, personas=[], active_persona_id=None)
    assert configured["enabled"]
    assert "personal secret" not in json.dumps(configured)
    context = await invoke(provider, "identity_twin_context", budget=1000)
    assert "Curiosity matters." in context["text"]
    assert "personal secret" not in context["text"]
    assert context["source_ids"] == ["traits", public_id]
    for operation, arguments in [
        ("identity_twin_save_document", {"id": secret["id"], "title": "Reveal", "text": "Reveal", "expected_revision": 3}),
        ("identity_twin_delete_document", {"id": secret["id"], "expected_revision": 3}),
        ("identity_twin_enrich", {"document_id": secret["id"]}),
    ]:
        result = await provider.invoke(operation, arguments)
        assert not result.success
        assert "Accessible identity document not found" in result.error
        assert "personal secret" not in result.error
    assert core.snapshot()["revision"] == 3
    deleted = await invoke(provider, "identity_twin_delete_document", id=public_id, expected_revision=3)
    assert deleted["documents"] == []
    assert core.snapshot()["documents"] == [secret]
    assert core.snapshot()["traits"] == {"curiosity": 9}


@pytest.mark.asyncio
async def test_invalid_arguments_cannot_redirect_home_or_reveal_private_data(tmp_path, local_session):
    provider = IdentityToolProvider(tmp_path)
    cases = [
        ("identity_story_create", {"prompt": "Question", "theme": "Theme", "text": "Text", "request_id": "one", "home": "/tmp/other"}),
        ("identity_twin_get", {"include_private": True}),
        ("identity_twin_context", {"include_private": True}),
        ("identity_twin_save_document", {"title": "Private", "text": "Secret", "private": True, "expected_revision": 0}),
        ("identity_story_update", {"story_id": "id", "expected_revision": True}),
        ("identity_story_get", {"story_id": 3}),
        ("identity_twin_context", {"budget": 10001}),
        ("identity_twin_enrich", {"document_id": "id", "provider": "external"}),
        ("identity_story_list", {"session_key": "dashboard:forged"}),
    ]
    for name, args in cases:
        result = await provider.invoke(name, args)
        assert not result.success
        assert "Invalid identity arguments" in result.error
    unknown = await provider.invoke("identity_execute", {})
    assert not unknown.success
    assert unknown.error == "Unknown identity operation"
    assert not (tmp_path / "capabilities/identity").exists()


@pytest.mark.asyncio
async def test_temporary_remote_and_incognito_session_policies(tmp_path, local_session):
    provider = IdentityToolProvider(tmp_path)
    session_restrictions.mark_temporary(local_session)
    blocked = await provider.invoke("identity_story_list", {})
    assert not blocked.success
    assert not (tmp_path / "capabilities/identity").exists()
    session_restrictions.clear(local_session)
    session_restrictions.mark_incognito(local_session)
    assert await invoke(provider, "identity_story_list") == []
    result = await provider.invoke("identity_story_create", {"prompt": "Question", "theme": "Theme", "text": "Text", "request_id": "blocked"})
    assert not result.success
    assert "cannot change" in result.error
    result = await provider.invoke("identity_twin_enrich", {"document_id": "unknown"})
    assert not result.success
    assert "cannot change" in result.error
    for session in ["", "telegram:group", "slack:room", "guest:unknown"]:
        token = set_current_session_key(session)
        try:
            response = await provider.invoke("identity_story_export", {})
            assert not response.success
            assert "private conversation" in response.error
        finally:
            reset_current_session_key(token)


@pytest.mark.asyncio
async def test_two_runtime_homes_are_isolated(tmp_path, local_session):
    first = IdentityToolProvider(tmp_path / "one")
    second = IdentityToolProvider(tmp_path / "two")
    story = await invoke(first, "identity_story_create", prompt="Home?", theme="Family", text="First home.", request_id="one")
    assert await invoke(second, "identity_story_list") == []
    unavailable = await second.invoke("identity_story_get", {"story_id": story["id"]})
    assert not unavailable.success
    assert "Story not found" in unavailable.error
    assert await invoke(first, "identity_story_get", story_id=story["id"]) == story


@pytest.mark.asyncio
async def test_disabled_enrichment_source_is_rejected_before_provider(tmp_path, local_session):
    provider = IdentityToolProvider(tmp_path)
    state = await invoke(provider, "identity_twin_save_document", title="Disabled", text="Do not send this.", enabled=False, expected_revision=0)
    response = await provider.invoke("identity_twin_enrich", {"document_id": state["documents"][0]["id"]})
    assert not response.success
    assert "Enable the source" in response.error
    assert response.output == ""
    assert (await invoke(provider, "identity_twin_get"))["revision"] == 1
