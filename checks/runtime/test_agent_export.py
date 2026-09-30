"""Owner-scoped Claude Code export through the real dashboard routes."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AgentProfile, AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard import handlers as dashboard_handlers
from gideon.interfaces.dashboard.handlers import agents as agents_handler
from gideon.interfaces.dashboard.handlers import messaging
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    revoke_all_sessions,
    token_auth_middleware,
    use_ephemeral_secret,
    use_persistent_secret,
)


@pytest.mark.asyncio
async def test_authenticated_owner_previews_and_confirms_agent_export(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    gideon_home = tmp_path / "gideon-home"
    home.mkdir()
    gideon_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(gideon_home))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("GIDEON_HOSTED", "0")
    use_ephemeral_secret(b"agent-export-test-signing-key-32b")
    revoke_all_sessions()
    agents_handler._agent_export_previews.clear()

    cfg = AppConfig.load()
    cfg.agents["export-default"] = AgentProfile(source="local", description="Default agent")
    cfg.agents["research-notes"] = AgentProfile(
        source="local",
        description="Summarizes technical research",
        model="claude-sonnet-4",
        system_prompt="Use primary sources and report uncertainty.",
        voice="Direct and concise.",
        skills=["research", "citations"],
        tools=["AKIAIOSFODNN7EXAMPLE"],
    )
    cfg.agents["release-helper"] = AgentProfile(
        source="gideon",
        description="Prepares release notes",
        system_prompt="Summarize verified release changes.",
    )
    cfg.agents["marketplace-agent"] = AgentProfile(source="marketplace")
    cfg.agents["gideon-lite"] = AgentProfile(source="local")
    cfg.default_agent = "export-default"
    cfg.save()
    before = AppConfig.load().to_dict()

    app = web.Application(middlewares=[token_auth_middleware(port=10000)])
    app["state"] = ConsoleState(sessions=ConversationDirectory(cfg), start_time=0)
    app.router.add_get("/api/agents", agents_handler.api_gideon_agents)
    app.router.add_post("/api/notifications/trust", dashboard_handlers.api_notification_trust)
    agents_handler.register_agent_export_routes(app)
    assert dashboard_handlers.api_notification_trust is messaging.api_notification_trust
    assert any(
        route.resource.canonical == "/api/notifications/trust"
        for route in app.router.routes()
    )
    token = generate_token("operator", ttl_seconds=600)
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(TestServer(app))
    try:
        async with client:
            missing_trust_action = await client.post("/api/notifications/trust", headers=headers, json={})
            assert missing_trust_action.status == 400

            list_response = await client.get("/api/agents", headers=headers)
            assert list_response.status == 200
            assert (await list_response.json())["agent_export_enabled"] is True

            owner_only = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["research-notes", "release-helper"], "destination": str(home / "claude" / "agents")},
            )
            assert owner_only.status == 200
            preview = await owner_only.json()
            assert preview["destination"] == str(home / "claude" / "agents")
            assert {row["path"] for row in preview["files"]} == {"research-notes.md", "release-helper.md"}
            assert {row["status"] for row in preview["files"]} == {"new"}
            assert preview["preview_token"]
            assert not (home / "claude").exists(), "preview must not create the destination"
            assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(preview)

            refused_selection = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["export-default"], "destination": str(home / "blocked")},
            )
            assert refused_selection.status == 400
            refused_builtin = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["gideon-lite"], "destination": str(home / "blocked")},
            )
            assert refused_builtin.status == 400
            refused_marketplace = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["marketplace-agent"], "destination": str(home / "blocked")},
            )
            assert refused_marketplace.status == 400

            no_preview = await client.post("/api/agents/export", headers=headers, json={})
            assert no_preview.status == 400

            stale_preview = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["research-notes"], "destination": str(home / "stale" / "agents")},
            )
            stale_body = await stale_preview.json()
            (home / "stale" / "agents").mkdir(parents=True)
            stale_write = await client.post(
                "/api/agents/export",
                headers=headers,
                json={"preview_token": stale_body["preview_token"]},
            )
            assert stale_write.status == 409
            assert not (home / "stale" / "agents" / "research-notes.md").exists()

            foreign_dir = home / "foreign" / "agents"
            foreign_dir.mkdir(parents=True)
            foreign_preview = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["research-notes"], "destination": str(foreign_dir)},
            )
            foreign_plan = await foreign_preview.json()
            foreign_target = foreign_dir / "research-notes.md"
            foreign_target.write_text("operator-owned Claude Code file\n", encoding="utf-8")
            foreign_write = await client.post(
                "/api/agents/export",
                headers=headers,
                json={"preview_token": foreign_plan["preview_token"]},
            )
            assert foreign_write.status == 409
            assert foreign_target.read_text(encoding="utf-8") == "operator-owned Claude Code file\n"

            foreign_plan_response = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["research-notes"], "destination": str(foreign_dir)},
            )
            foreign_plan = await foreign_plan_response.json()
            assert foreign_plan["files"][0]["status"] == "foreign"
            assert foreign_plan["preview_token"] is None
            assert foreign_target.read_text(encoding="utf-8") == "operator-owned Claude Code file\n"

            clean_dest = home / "clean" / "agents"
            ready = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["research-notes", "release-helper"], "destination": str(clean_dest)},
            )
            ready_body = await ready.json()
            result = await client.post(
                "/api/agents/export",
                headers=headers,
                json={"preview_token": ready_body["preview_token"]},
            )
            assert result.status == 200
            receipt = await result.json()
            assert receipt["ok"] is True
            assert set(receipt["files"]) == {"research-notes.md", "release-helper.md"}
            rendered = (clean_dest / "research-notes.md").read_text(encoding="utf-8")
            assert "research-notes" in rendered
            assert "AKIAIOSFODNN7EXAMPLE" not in rendered
            assert AppConfig.load().to_dict() == before, "export must not mutate agent settings"

            monkeypatch.setenv("GIDEON_HOSTED", "1")
            hosted_list = await client.get("/api/agents", headers=headers)
            assert (await hosted_list.json())["agent_export_enabled"] is False
            hosted_write = await client.post(
                "/api/agents/export/preview",
                headers=headers,
                json={"agents": ["research-notes"], "destination": str(home / "hosted")},
            )
            assert hosted_write.status == 404
            assert not (home / "hosted").exists()

            hosted_routes = web.Application()
            agents_handler.register_agent_export_routes(hosted_routes)
            assert all("/api/agents/export" not in str(route.resource) for route in hosted_routes.router.routes())
    finally:
        await client.close()
        agents_handler._agent_export_previews.clear()
        revoke_all_sessions()
        use_persistent_secret()
