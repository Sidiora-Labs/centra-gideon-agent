import json
from pathlib import Path

import pytest

from gideon.engine import session_restrictions
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import ProviderRegistry, ToolTypeHandler
from gideon.integrations.mcp_core import reset_current_session_key, set_current_session_key
from gideon.integrations.tool_providers.registry import get_provider


ROOT = Path(__file__).parents[4]
APPS = ROOT / "runtime/gideon/extensions/apps/native"


@pytest.mark.asyncio
async def test_completed_identity_and_platform_apps_register_and_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    registry = ProviderRegistry()
    registry.register_type_handler("tool", ToolTypeHandler())
    names = ("gideon-identity", "gideon-platform", "gideon-peers", "gideon-remote-media", "gideon-media-sharing", "remote-agent-sessions")
    try:
        for name in names:
            registry.register(AppManifest.from_json_file(APPS / name / "app.json"), enabled=True)
            assert get_provider(name) is not None

        identity = get_provider("gideon-identity")
        guarded = {tool.name: tool for tool in await identity.list_tools() if tool.name.startswith("identity_guarded_")}
        assert len(guarded) == 7
        assert guarded["identity_guarded_catalog"].requires_approval is False
        assert guarded["identity_guarded_save"].requires_approval is True
        token = set_current_session_key("dashboard:native-registry")
        try:
            identity_result = await identity.invoke("identity_story_list", {})
        finally:
            session_restrictions.clear("dashboard:native-registry")
            reset_current_session_key(token)
        assert identity_result.success and json.loads(identity_result.output) == []

        peers = get_provider("gideon-peers")
        assert (await peers.list_tools())[0].requires_approval is False
        peers_result = await peers.invoke("platform_peer_projection", {})
        assert peers_result.success and json.loads(peers_result.output)["peers"] == []

        media = get_provider("gideon-remote-media")
        media_tools = {tool.name: tool for tool in await media.list_tools()}
        assert media_tools["remote_media_list"].requires_approval is False
        assert media_tools["remote_media_dispatch"].requires_approval is True
        assert media_tools["remote_media_cancel"].requires_approval is True
        assert (await media.invoke("remote_media_list", {})).success is True

        sharing = get_provider("gideon-media-sharing")
        sharing_tools = {tool.name: tool for tool in await sharing.list_tools()}
        assert sharing_tools["platform_media_shares_list"].requires_approval is False
        assert sharing_tools["platform_media_share"].requires_approval is True
        assert sharing_tools["platform_media_share_revoke"].requires_approval is True
        assert (await sharing.invoke("platform_media_shares_list", {})).success is True

        remote = get_provider("remote-agent-sessions")
        remote_tools = {tool.name: tool for tool in await remote.list_tools()}
        assert remote_tools["remote_agent_sessions"].requires_approval is False
        assert remote_tools["remote_agent_message"].requires_approval is True
        assert (await remote.invoke("remote_agent_sessions", {"connection_id": "missing"})).success is False
    finally:
        for name in reversed(names):
            registry.deregister(name)
