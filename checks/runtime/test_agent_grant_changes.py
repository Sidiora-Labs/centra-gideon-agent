"""Owner-reviewed profile changes and live native grant refresh."""

import json

import pytest


@pytest.mark.asyncio
async def test_owner_receipt_and_live_native_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.core.config.loader import AgentProfile, AppConfig
    from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.scripted import ScriptedProvider
    from gideon.interfaces.dashboard.handlers.agents import api_gideon_agent_update
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        revoke_all_sessions,
        token_auth_middleware,
        use_ephemeral_secret,
        use_persistent_secret,
    )

    cfg = AppConfig.load()
    cfg.agents["grant-reader"] = AgentProfile(
        provider="gideon", tools=["read_file"], skills=["one"]
    )
    cfg.save()
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [{"text": "first"}, {"text": "second"}, {"text": "third"}],
            }
        )
    )
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="grant-reader", tools=["read_file"], skills=["one"]
        ),
        model_provider=ScriptedProvider(),
        tool_providers=[
            NativeBuiltinToolProvider(
                cwd=tmp_path,
                categories={"filesystem", "shell"},
                provider_name="gideon-filesystem",
            )
        ],
        cwd=tmp_path,
    )
    await runtime.start()
    use_ephemeral_secret(b"grant-change-test-secret-32bytes")
    revoke_all_sessions()
    app = web.Application(middlewares=[token_auth_middleware(port=10000)])
    app.router.add_put("/api/agents/{name}", api_gideon_agent_update)
    owner = {"Authorization": f"Bearer {generate_token('operator', ttl_seconds=600)}"}
    other = {"Authorization": f"Bearer {generate_token('other', ttl_seconds=600)}"}
    path = "/api/agents/grant-reader"
    payload = {"tools": ["read_file", "bash"], "skills": ["one", "two"]}
    try:
        async with TestClient(TestServer(app)) as client:
            denied = await client.put(
                path, headers=owner, json={**payload, "confirmed": True}
            )
            assert denied.status == 409
            assert AppConfig.load().agents["grant-reader"].tools == ["read_file"]
            offered = await client.put(
                path, headers=owner, json={**payload, "grant_preview": True}
            )
            offer = await offered.json()
            assert offered.status == 200 and offer["confirmation_required"]
            assert {r["field"] for r in offer["changes"]} == {"tools", "skills"}
            assert offer["changes"][0]["after"] == payload["tools"]
            changed = await client.put(
                path,
                headers=owner,
                json={**payload, "tools": [], "grant_receipt": offer["grant_receipt"]},
            )
            assert changed.status == 409
            offer = await (
                await client.put(
                    path, headers=owner, json={**payload, "grant_preview": True}
                )
            ).json()
            assert (
                await client.put(
                    path,
                    headers=other,
                    json={**payload, "grant_receipt": offer["grant_receipt"]},
                )
            ).status == 409
            offer = await (
                await client.put(
                    path, headers=owner, json={**payload, "grant_preview": True}
                )
            ).json()
            saved = await client.put(
                path,
                headers=owner,
                json={**payload, "grant_receipt": offer["grant_receipt"]},
            )
            assert saved.status == 200
            assert AppConfig.load().agents["grant-reader"].provider == "gideon"
            assert "bash" not in runtime._tool_index
            async for _ in runtime.stream("ready"):
                pass
            assert "bash" in runtime._tool_index
            assert runtime._skill_grants.names == ("one", "two")
            assert (
                await client.put(
                    path,
                    headers=owner,
                    json={"tools": ["read_file"], "skills": ["one"]},
                )
            ).status == 200
            meta = {}
            assert "tool list" in await runtime._invoke(
                "bash", {"command": "true"}, meta_sink=meta
            )
            assert "bash" not in runtime._tool_index
            async for _ in runtime.stream("ready"):
                pass
            meta = {}
            assert "tool list" in await runtime._invoke(
                "bash", {"command": "true"}, meta_sink=meta
            )
            assert meta["refused_by"] == "agent_tools"
            # Receipts bind the stored base as well as the offered exact payload.
            offer = await (
                await client.put(
                    path, headers=owner, json={**payload, "grant_preview": True}
                )
            ).json()
            assert (
                await client.put(path, headers=owner, json={"description": "changed"})
            ).status == 200
            assert (
                await client.put(
                    path,
                    headers=owner,
                    json={**payload, "grant_receipt": offer["grant_receipt"]},
                )
            ).status == 409
            (tmp_path / "home" / "config.json").write_text("{broken")
            async for _ in runtime.stream("ready"):
                pass
            assert "read_file" not in runtime._tool_index
    finally:
        await runtime.shutdown()
        revoke_all_sessions()
        use_persistent_secret()


def test_skill_and_tool_widening_match_real_grants():
    from gideon.engine.agents.grant_changes import widening

    assert not widening(
        {"tools": [], "skills": []}, {"tools": ["read_file"], "skills": ["one"]}
    )
    assert not widening({"tools": ["read_*"]}, {"tools": ["read_file"]})
    assert widening({"skills": ["one*"]}, {"skills": ["one"]})
    assert widening(
        {"tools": ["read_file"], "skills": ["one"]}, {"tools": [], "skills": []}
    )


def test_loop_exception_requires_host_binding_actual_store_and_no_app(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    from gideon.automation.loop import store
    from gideon.automation.loop.loop import Loop, LoopStatus
    from gideon.engine.agents.loop_skills import bind, names
    from gideon.engine.agents.skill_list import AgentSkills
    from gideon.engine.agents.skill_list import hold as hold_skills
    from gideon.engine.agents.skill_list import let_go as release_skills
    from gideon.extensions.apps.app_work import AppWork, hold, let_go
    from gideon.integrations import mcp_core

    loop = Loop(
        id="a1b2c3d4",
        name="Exact worker",
        task="Inspect",
        kind="goal",
        agent="grant-reader",
        skill_ids=["selected", "*", "other?"],
    )
    store.create(loop)
    assert names("loop-a1b2c3d4", "grant-reader") == ()
    bind("loop-a1b2c3d4", loop.id, "grant-reader")
    assert names("loop-a1b2c3d4", "grant-reader") == ("selected",)
    assert names("loop-a1b2c3d4-forged", "grant-reader") == ()
    assert names("loop-a1b2c3d4", "other") == ()
    token = mcp_core.set_current_session_key("loop-a1b2c3d4")
    scope = hold_skills(AgentSkills.of("grant-reader", ["base"]))
    try:
        grants, _ = mcp_core._skill_library()
        assert grants.allows("selected") and not grants.allows("unselected")
        app = hold(AppWork("uninstalled-app", "tools"))
        try:
            assert not mcp_core._skill_library()[0].allows("selected")
        finally:
            let_go(app)
    finally:
        release_skills(scope)
        mcp_core.reset_current_session_key(token)
    store.update_status(loop.id, LoopStatus.STOPPED)
    assert names("loop-a1b2c3d4", "grant-reader") == ()
