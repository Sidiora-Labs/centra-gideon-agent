"""A real authenticated app worker starts and retires native private execution."""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_authenticated_app_worker_private_workflow_lifetime(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.automation.workflows import private_runs, private_work, service, store
    from gideon.automation.workflows.controller import EngineServices
    from gideon.automation.workflows.handlers import register_workflow_routes
    from gideon.automation.workflows.models import RunStatus
    from gideon.automation.workflows.watchdog import WorkflowWatchdog
    from gideon.cognition.context import PromptAssembler
    from gideon.core.config.loader import AgentProfile, AppConfig
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.subagent import DelegationSupervisor
    from gideon.extensions.apps.app_work import from_bound, held
    from gideon.extensions.providers.provider_bridge import create_provider_factory
    from gideon.extensions.providers.use_cases import save_active_models
    from gideon.hypermid.foundation import Id, Scope
    from gideon.hypermid.memory import HypermidMemoryProvider, _trace
    from gideon.hypermid.private_scopes import NativePrivateScopes
    from gideon.integrations.action_providers.services import (
        ActionServices,
        get_action_services,
        set_action_services,
    )
    from gideon.integrations.llm.registry import register_scripted_provider_type
    from gideon.integrations.llm.scripted import ScriptedProvider
    from gideon.integrations.tool_providers.registry import (
        create_workflows_provider,
        list_providers,
        register_provider,
        unregister_provider,
    )
    from gideon.interfaces.dashboard.handlers.apps import api_app_agent_run
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.interfaces.dashboard.token_auth import (
        generate_token,
        revoke_all_sessions,
        token_auth_middleware,
        use_ephemeral_secret,
        use_persistent_secret,
    )
    from gideon.security.approval_answer import APP
    from gideon.security.session_credentials import credential_for, current_work, verify

    home = tmp_path / "home"
    installed = home / "apps" / "private-app"
    installed.mkdir(parents=True)
    (installed / "installed.json").write_text(
        json.dumps({"name": "private-app", "enabled": True, "version": "1.0.0"})
    )
    (installed / "app.json").write_text(
        json.dumps(
            {
                "name": "private-app",
                "version": "1.0.0",
                "permissions": {"agent": "read"},
            }
        )
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"version": 1, "turns": [{"text": "complete"}]}))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    record = tmp_path / "native" / "connection.json"
    scope = Scope("operator-test", "project-test", "host-workspace-test")
    process = subprocess.Popen(
        [
            str(Path("target/debug/hypermid-daemon").resolve()),
            "--socket",
            str(tmp_path / "run" / "private.sock"),
            "--connection-record",
            str(record),
            "--local-credential-id",
            "native-app-credential",
            "--local-owner-id",
            str(scope.owner_id),
            "--local-project-id",
            str(scope.project_id),
            "--local-workspace-id",
            str(scope.workspace_id),
            "--local-capability-id",
            "native-private-admin",
            "--local-capability-operation",
            "administer",
            "--local-capability-resource",
            "memory-service",
            "--local-capability-expires-ms",
            str(int(time.time() * 1000) + 120000),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    ready, finish = asyncio.Event(), asyncio.Event()
    proofs = []

    actual_runtimes = []
    supported_factory = create_provider_factory("chat")
    original_complete = ScriptedProvider.complete

    async def observed_complete(self, *arguments, **options):
        # Observe a real SDK request after production factory acquisition and host binding.
        proof = current_work()
        assert (
            proof is not None
            and verify(credential_for(proof.session_key), proof.session_key) is proof
        )
        runtime = next(
            runtime
            for runtime in actual_runtimes
            if runtime._session_key == proof.session_key
        )
        assert isinstance(runtime, NativeAgentRuntime)
        assert runtime._app_work.app == "private-app"
        assert "workflow_start" in runtime._tool_index
        assert runtime._app_work.refusal("workflow_start") == ""
        assert proof.execution_model == "Scripted:scripted-1"
        assert proof.execution_runtime == "native" and proof.allowed_models == (
            proof.execution_model,
        )
        proofs.append(proof)
        ready.set()
        await finish.wait()
        async for event in original_complete(self, *arguments, **options):
            yield event

    monkeypatch.setattr(ScriptedProvider, "complete", observed_complete)

    def factory(key, **options):
        runtime = supported_factory(key, **options)
        actual_runtimes.append(runtime)
        return runtime

    assert register_scripted_provider_type()
    save_active_models(
        {"chat": ["Scripted:scripted-1"], "orchestration": ["Scripted:scripted-1"]}
    )
    cfg = AppConfig.load()
    cfg.agent.provider = "native"
    cfg.agents["private-worker"] = AgentProfile(
        provider="native", model="Scripted:scripted-1", tools=["workflow_start"]
    )
    cfg.save()
    workflow_registration = None
    if not any(provider.name == "gideon-workflows" for provider in list_providers()):
        workflow_registration = register_provider(create_workflows_provider())
        assert workflow_registration["accepted"] is not False, workflow_registration

    sessions = ConversationDirectory(AppConfig.load(), provider_factory=factory)
    manager = DelegationSupervisor(sessions, PromptAssembler())
    state = ConsoleState(sessions=sessions, subagents=manager, start_time=time.time())
    provider = None
    watchdog = None
    previous_services = get_action_services()
    use_ephemeral_secret(b"private-app-test-token-secret-32")
    revoke_all_sessions()
    try:
        deadline = time.monotonic() + 10
        while not record.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        provider = HypermidMemoryProvider(
            record, scope=scope, capability_id=Id("native-private-admin")
        )
        watchdog = WorkflowWatchdog(
            state=state, services=EngineServices(memory=provider)
        )
        state.workflows = watchdog
        set_action_services(
            ActionServices(
                state, asyncio.create_task, subagents=manager, workflows=watchdog
            )
        )
        app = web.Application(
            middlewares=[
                token_auth_middleware(
                    port=10000,
                    internal_secret="test-private-internal",
                    mixed_internal_routes=frozenset(
                        {
                            ("POST", "/api/workflows/runs"),
                            ("GET", "/api/workflows/runs/{run_id}"),
                        }
                    ),
                )
            ]
        )
        app["state"] = state
        app.router.add_post("/api/apps/{name}/agent-run", api_app_agent_run)
        register_workflow_routes(app)
        owner_token = generate_token("operator", ttl_seconds=600)
        app_token = generate_token("operator", app="private-app", ttl_seconds=600)
        auth = {"Authorization": f"Bearer {owner_token}"}
        async with TestClient(TestServer(app)) as client:
            start = await client.post(
                f"/api/apps/private-app/agent-run?app_token={app_token}",
                headers=auth,
                json={"task": "Review the private batch", "agent": "private-worker"},
            )
            assert start.status == 202, await start.text()
            info = manager.get((await start.json())["id"])
            await asyncio.wait_for(ready.wait(), 10)
            proof = proofs[0]
            assert proof.initiator.kind == APP and proof.initiator.name == "private-app"
            assert (
                proof.created_by_app == "private-app"
                and proof.memory_mode == "temporary"
            )
            assert (
                proof.origin_session_key == f"subagent:{info.id}" == proof.session_key
            )
            assert from_bound(proof).current_tier() == "read"
            headers = {
                "X-Internal-Secret": "test-private-internal",
                "X-Session-Key": proof.session_key,
                "X-Session-Proof": credential_for(proof.session_key),
            }
            spec = {
                "name": "app-private-once",
                "root": {
                    "kind": "transform",
                    "id": "private-output",
                    "config": {"expr": "1"},
                },
            }
            # Source identity/proof cannot be supplied through a different caller key.
            spoof = await client.post(
                "/api/workflows/runs",
                headers={**headers, "X-Session-Key": "subagent:forged"},
                json={"name": "app-private-once", "run_once": spec},
            )
            assert spoof.status == 403
            accepted = await client.post(
                "/api/workflows/runs",
                headers=headers,
                json={"name": "app-private-once", "run_once": spec},
            )
            assert accepted.status == 202, await accepted.text()
            run_id = (await accepted.json())["run_id"]
            controller = watchdog.controller(run_id)
            assert await controller.run_to_completion(timeout=10) == RunStatus.COMPLETE
            run = store.get(run_id)
            assert run.extra["memory_mode"] == "temporary"
            assert run.extra["app_work"] == {"app": "private-app", "tier": "read"}
            assert run.extra["chat_owner"] == proof.origin_session_key
            receipt = private_work.receipts(watchdog)[proof.origin_session_key]
            assert receipt.memory_mode == "temporary" and receipt.scope != scope
            assert str(receipt.capability_id) not in json.dumps(run.extra)
            assert not (await service.get_def("app-private-once"))["ok"]
            assert (
                await client.get(f"/api/workflows/runs/{run_id}", headers=headers)
            ).status == 200
            foreign_token = generate_token(
                "operator", app="different-app", ttl_seconds=600
            )
            assert (
                await client.get(
                    f"/api/workflows/runs/{run_id}?app_token={foreign_token}",
                    headers=auth,
                )
            ).status == 404
            assert store.run_dir(run_id).exists()
            await private_runs.reconcile(watchdog)
            assert (
                store.get(run_id) is not None
            )  # Actual source worker is still active.
            finish.set()
            for _ in range(200):
                if info.done:
                    break
                await asyncio.sleep(0.02)
            assert info.done and not info.error, info.error
            directory = store.run_dir(run_id)
            await private_runs.reconcile(watchdog)
            assert store.get(run_id) is None and not directory.exists()
            assert proof.origin_session_key not in private_work.receipts(watchdog)
            with pytest.raises(Exception):
                await asyncio.to_thread(
                    provider._loop.call,
                    lambda memory: NativePrivateScopes(memory).resolve(
                        receipt, trace=_trace()
                    ),
                )
            assert verify(headers["X-Session-Proof"], proof.session_key) is None
    finally:
        finish.set()
        for info in manager.all_agents:
            await manager.cancel(info.id)
        if watchdog is not None:
            await watchdog.stop()
        if provider is not None:
            provider.close()
        process.terminate()
        await asyncio.to_thread(process.wait, timeout=10)
        if workflow_registration is not None:
            unregister_provider(
                "gideon-workflows",
                owner_type="core",
                owner=workflow_registration["owner"],
                instance_id=workflow_registration["instance_id"],
            )
        set_action_services(previous_services)
        revoke_all_sessions()
        use_persistent_secret()
