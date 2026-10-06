"""Owner HTTP approval leases expire and remain confined to one loop run."""

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.loop import manager, run_grants, store
from gideon.automation.loop.loop import Loop, LoopStatus
from gideon.automation.triggers.nudge import AutoNudgeService
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.chat_handlers import api_chat_session_approve
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


@pytest.mark.asyncio
async def test_owner_http_run_allowance_expires_and_lifecycle_revokes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    cfg = AppConfig.load()
    cfg.loops.trust_ttl_secs = 1
    cfg.save()
    state = ConsoleState(ConversationDirectory(AppConfig.load()), 0)
    service = AutoNudgeService(base_dir=tmp_path)
    await service.start()
    loop = store.create(
        Loop(
            id="a934abcd",
            name="Release review",
            kind="goal",
            task="Investigate release",
            attended=True,
        )
    )
    other = store.create(
        Loop(
            id="b934abcd",
            name="Other review",
            kind="goal",
            task="Review documentation",
            attended=True,
        )
    )
    await manager.start(state, service, loop.id)
    await manager.start(state, service, other.id)
    main = state._sessions[manager.session_key(loop.id)]
    outsider = state._sessions[manager.session_key(other.id)]
    planner = state.get_or_create_session(name=f"loop-plan-{loop.id}", app="loops")
    manager._arm_worker_approval_posture(state, planner, store.get(loop.id))
    origins = [
        (worker._unattended, worker.acp_mode, worker.created_by_app)
        for worker in (main, planner)
    ]
    app = web.Application(middlewares=[token_auth_middleware(port=19431)])
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/approve", api_chat_session_approve
    )
    cookies = {"gideon_token_19431": generate_token("loop-owner", kind="desktop")}

    async def approve(client, request_id):
        future = asyncio.get_running_loop().create_future()
        main._approval_futures[request_id] = future
        meta = {"request_id": request_id, "tool_kind": "write"}
        main.append("permission", "write_file", json.dumps(meta))
        state.broadcast_ws(
            "approval",
            {
                "session": main.key,
                "id": request_id,
                "tool": "write_file",
                "tool_kind": "write",
            },
        )
        actual_card = state._pending_approvals[f"{main.key}:{request_id}"]
        assert actual_card["loop_run_offer"]["duration_seconds"] == 1
        response = await client.post(
            f"/api/chat/sessions/{main.key}/approve",
            json={"request_id": request_id, "action": "trust"},
            cookies=cookies,
        )
        assert response.status == 200, await response.text()
        assert await future == "approved"

    try:
        async with TestClient(TestServer(app)) as client:
            await approve(client, "lease-1")
            assert main._trust and planner._trust and not outsider._trust
            future_worker = state.get_or_create_session(
                name=f"loop-{loop.id}-task-c", app="loop"
            )
            manager._arm_worker_approval_posture(
                state, future_worker, store.get(loop.id)
            )
            assert future_worker._trust
            assert origins == [
                (worker._unattended, worker.acp_mode, worker.created_by_app)
                for worker in (main, planner)
            ]
            await asyncio.sleep(1.15)
            assert not any(worker._trust for worker in (main, planner, future_worker))
            assert store.get(loop.id).status == LoopStatus.RUNNING
            await approve(client, "lease-2")
            await manager.pause(state, service, loop.id)
            assert not main._trust and not planner._trust
            await manager.start(state, service, loop.id)
            assert not main._trust and not run_grants.refresh(state, planner)
            await approve(client, "lease-3")
            await manager.stop(state, service, loop.id)
            assert not any(worker._trust for worker in (main, planner, future_worker))
    finally:
        run_grants.revoke(state, loop.id)
        service.stop()


@pytest.mark.asyncio
async def test_host_bound_worker_issuer_rejects_unarmed_alias_and_stale_offer(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    cfg = AppConfig.load()
    cfg.loops.trust_ttl_secs = 1
    cfg.save()
    state = ConsoleState(ConversationDirectory(AppConfig.load()), 0)
    service = AutoNudgeService(base_dir=tmp_path)
    await service.start()
    loop = store.create(
        Loop(
            id="c934abcd",
            name="Bound release",
            kind="goal",
            task="Investigate release",
            attended=True,
        )
    )
    await manager.start(state, service, loop.id)
    main = state._sessions[manager.session_key(loop.id)]
    alias = state.get_or_create_session(name=f"loop-{loop.id}-invented", app="loop")
    from gideon.security.approval_answer import YOU

    def pending(worker, request_id):
        future = asyncio.get_running_loop().create_future()
        worker._approval_futures[request_id] = future
        state.broadcast_ws(
            "approval", {"session": worker.key, "id": request_id, "tool": "write_file"}
        )
        return future

    try:
        pending(alias, "alias")
        assert not state.grant_chat_trust(alias, "alias", by=YOU)
        assert not alias._trust
        pending(main, "stale")
        await manager.pause(state, service, loop.id)
        await manager.start(state, service, loop.id)
        assert not state.grant_chat_trust(main, "stale", by=YOU)
        pending(main, "current")
        assert state.grant_chat_trust(main, "current", by=YOU)
        assert run_grants.refresh(state, main)
        assert not run_grants.refresh(state, alias)
        await asyncio.sleep(1.15)
        assert not main._trust and not run_grants.refresh(state, main)
        assert not alias._trust
    finally:
        run_grants.revoke(state, loop.id)
        service.stop()


@pytest.mark.asyncio
async def test_real_native_permissions_use_live_lease_and_ask_again_after_expiry(
    tmp_path, monkeypatch
):
    from gideon.core.config.loader import AgentProfile
    from gideon.core.constants import dashboard_session_key
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.interfaces.dashboard.chat_handlers import api_chat

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    script = tmp_path / "model.json"

    def call(identity, name):
        return {
            "tool_calls": [
                {
                    "id": identity,
                    "name": "write_file",
                    "input": {"path": str(tmp_path / name), "content": identity},
                }
            ]
        }

    script.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [
                    call("first", "first.txt"),
                    call("second", "second.txt"),
                    {"text": "Done"},
                    call("third", "third.txt"),
                    {"text": "Done"},
                ],
            }
        )
    )
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    cfg = AppConfig.load()
    cfg.loops.trust_ttl_secs = 3
    cfg.agents["lease-worker"] = AgentProfile(
        provider="gideon", model="Scripted", tools=["write_file"], skills=[]
    )
    cfg.save()
    from gideon.integrations.llm.registry import register_scripted_provider_type

    register_scripted_provider_type()
    state = ConsoleState(
        ConversationDirectory(
            AppConfig.load(), provider_factory=cfg.create_provider_factory()
        ),
        0,
    )
    from gideon.engine.hooks import ScriptHookStore, set_global_hook_store

    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)
    service = AutoNudgeService(base_dir=tmp_path / "home")
    await service.start()
    loop = store.create(
        Loop(
            id="d934abcd",
            name="Native permission review",
            kind="goal",
            task="Write three notes",
            attended=True,
            agent="lease-worker",
            model="Scripted",
            workspace_dir=str(tmp_path),
        )
    )
    await manager.start(state, service, loop.id)
    session = state._sessions[manager.session_key(loop.id)]
    session._titled = True
    app = web.Application(middlewares=[token_auth_middleware(port=19432)])
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/approve", api_chat_session_approve
    )
    app.router.add_post("/api/chat", api_chat)
    cookies = {
        "gideon_token_19432": generate_token("native-loop-owner", kind="desktop")
    }
    tasks = []

    async def actual_pending():
        for _ in range(300):
            pending = [
                key
                for key, future in session._approval_futures.items()
                if not future.done()
            ]
            if pending:
                return pending[0]
            if tasks[-1].done():
                await tasks[-1]
                pytest.fail(
                    f"Native turn ended before requesting permission: {session.messages[-5:]}"
                )
            await asyncio.sleep(0.05)
        pytest.fail("Native permission request did not arrive")

    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/chat?ws=1",
                json={"session": session.key, "message": "Write the first two notes"},
                cookies=cookies,
            )
            assert response.status == 200, await response.text()
            tasks.append(session.task)
            request_id = await actual_pending()
            response = await client.post(
                f"/api/chat/sessions/{session.key}/approve",
                json={"request_id": request_id, "action": "trust"},
                cookies=cookies,
            )
            assert response.status == 200, await response.text()
            await asyncio.wait_for(tasks[-1], 15)
            provider = state.sessions.get_provider(dashboard_session_key(session.key))
            assert isinstance(provider, NativeAgentRuntime)
            assert (
                state.sessions.get_approval_policy(dashboard_session_key(session.key))
                == ""
            )
            assert (tmp_path / "first.txt").read_text() == "first"
            assert (tmp_path / "second.txt").read_text() == "second"
            await asyncio.sleep(3.15)
            response = await client.post(
                "/api/chat?ws=1",
                json={"session": session.key, "message": "Write the third note"},
                cookies=cookies,
            )
            assert response.status == 200, await response.text()
            tasks.append(session.task)
            request_id = await actual_pending()
            assert not (tmp_path / "third.txt").exists()
            response = await client.post(
                f"/api/chat/sessions/{session.key}/approve",
                json={"request_id": request_id, "action": "rejected"},
                cookies=cookies,
            )
            assert response.status == 200, await response.text()
            await asyncio.wait_for(tasks[-1], 15)
            assert not (tmp_path / "third.txt").exists()
    finally:
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not None), return_exceptions=True
        )
        run_grants.revoke(state, loop.id)
        service.stop()
