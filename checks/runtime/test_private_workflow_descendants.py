"""Signed private descendants and terminal forks against actual native scope authority."""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_background_completion_contract import configured_completion

from gideon.automation.workflows import defs, private_work, service, store
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.handlers import register_workflow_routes
from gideon.automation.workflows.models import RunStatus
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.extensions.providers.provider_bridge import resolve_provider_for_use_case
from gideon.extensions.providers.use_cases import save_active_models
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.memory import HypermidMemoryProvider
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import OWNER, Principal
from gideon.security.durable_work import recorded_run_origin
from gideon.security.execution_lineage import host_runtime_admission
from gideon.security.session_credentials import (
    begin_turn,
    bind_execution,
    end_turn,
    verify,
)


@pytest.mark.asyncio
async def test_real_private_child_and_terminal_fork_keep_scope_model_and_live_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    record = tmp_path / "connection.json"
    scope = Scope("descendant-owner", "descendant-project", "descendant-host")
    process = subprocess.Popen(
        [
            str(Path("target/debug/hypermid-daemon").resolve()),
            "--socket",
            str(tmp_path / "native.sock"),
            "--connection-record",
            str(record),
            "--local-credential-id",
            "descendant-credential",
            "--local-owner-id",
            str(scope.owner_id),
            "--local-project-id",
            str(scope.project_id),
            "--local-workspace-id",
            str(scope.workspace_id),
            "--local-capability-id",
            "descendant-capability",
            "--local-capability-operation",
            "administer",
            "--local-capability-operation",
            "read",
            "--local-capability-resource",
            "memory-service",
            "--local-capability-resource",
            "memory-records",
            "--local-capability-expires-ms",
            str(int(time.time() * 1000) + 120000),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    provider = runtime = credential = watchdog = None
    deadline = time.monotonic() + 10
    try:
        while not record.exists():
            assert process.poll() is None, (
                process.stderr.read() if process.poll() is not None else ""
            )
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        provider = HypermidMemoryProvider(
            record, scope=scope, capability_id=Id("descendant-capability")
        )
        native = NativeWorkflowDefProvider()
        defs.register_provider(native)
        child_spec = {
            "name": "private-child",
            "root": {
                "kind": "infer",
                "id": "answer",
                "config": {"prompt": "private descendant"},
            },
        }
        await native.save_def(**child_spec)
        state = ConsoleState(sessions=None, start_time=0)
        watchdog = WorkflowWatchdog(
            state=state, services=EngineServices(memory=provider)
        )
        state.workflows = watchdog
        async with configured_completion(lambda request, n: "PRIVATE-CHILD-DONE") as (
            requests,
            _,
        ):
            save_active_models(
                {
                    "chat": ["ContractSDK:good"],
                    "background": ["ContractSDK:bad"],
                    "reasoning": ["ContractSDK:bad"],
                }
            )
            credential = begin_turn(
                "dashboard:descendant",
                Principal(OWNER, "sir"),
                turn_id="root",
                memory_mode="incognito",
            )
            with host_runtime_admission(credential):
                runtime = resolve_provider_for_use_case(
                    "chat", session_key=credential.work.session_key
                )
                await runtime.start()
            bind_execution(credential, runtime)

            @web.middleware
            async def authenticated(request, handler):
                proof = verify(
                    request.headers.get("X-Session-Proof", ""),
                    request.headers.get("X-Session-Key", ""),
                )
                if proof is not None:
                    request["_session_work_proof"] = proof
                return await handler(request)

            app = web.Application(middlewares=[authenticated])
            app["state"] = state
            register_workflow_routes(app)
            headers = {
                "X-Session-Key": credential.work.session_key,
                "X-Session-Proof": credential.bearer,
            }
            async with TestClient(TestServer(app)) as client:
                root_spec = {
                    "name": "private-parent",
                    "root": {
                        "kind": "subworkflow",
                        "id": "child",
                        "config": {"ref": "private-child"},
                    },
                }
                response = await client.post(
                    "/api/workflows/runs",
                    headers=headers,
                    json={
                        "name": "private-parent",
                        "run_once": root_spec,
                        "skip_preflight": True,
                    },
                )
                body = await response.json()
                assert response.status == 202, body
                parent_id = body["run_id"]
                assert (
                    await watchdog.controller(parent_id).run_to_completion(timeout=15)
                    == RunStatus.COMPLETE
                )
                parent = store.get(parent_id)
                children = [
                    run
                    for run in store.list_runs(limit=100)[0]
                    if run.parent_run_id == parent_id
                ]
                assert len(children) == 1
                child = children[0]
                assert child.status == RunStatus.COMPLETE
                for key in (
                    "memory_mode",
                    "chat_owner",
                    "private_scope_origin",
                    "private_scope_id",
                ):
                    assert child.extra[key] == parent.extra[key]
                assert (
                    recorded_run_origin(child)["execution_model"] == "ContractSDK:good"
                )
                assert recorded_run_origin(child)["run_id"] == child.id
                assert [request["model"] for request in requests] == ["good"]
                # The old synchronous API cannot bypass current private scope admission.
                assert not service.fork_run(parent_id, supervisor=watchdog)["ok"]
                response = await client.post(
                    f"/api/workflows/runs/{parent_id}/fork", headers=headers, json={}
                )
                forked = await response.json()
                assert response.status == 201 and forked.get("child_run_id"), forked
                fork_id = forked.get("run_id") or forked.get("child_run_id")
                fork = store.get(fork_id)
                assert recorded_run_origin(fork)["run_id"] == fork.id
                assert (
                    recorded_run_origin(fork)["execution_model"] == "ContractSDK:good"
                )
                assert (
                    fork.extra["private_scope_id"] == parent.extra["private_scope_id"]
                )
                await private_work.retire(watchdog, credential.work.origin_session_key)
                before = len(store.list_runs(limit=100)[0])
                response = await client.post(
                    f"/api/workflows/runs/{parent_id}/fork", headers=headers, json={}
                )
                assert response.status >= 400
                assert len(store.list_runs(limit=100)[0]) == before
    finally:
        if watchdog is not None:
            await watchdog.stop()
        if runtime is not None:
            await runtime.shutdown()
        end_turn(credential)
        if provider is not None:
            await asyncio.to_thread(provider.close)
        defs.unregister_provider("native")
        process.terminate()
        await asyncio.to_thread(process.wait, 10)
