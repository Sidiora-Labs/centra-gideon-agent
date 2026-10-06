"""Real native run-once stores, authenticated ownership, events and cleanup."""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from gideon.assurance import trace_recorder
from gideon.automation.workflows import chat_runs, private_runs, service, store
from gideon.automation.workflows.controller import EngineServices
from gideon.automation.workflows.handlers import register_workflow_routes
from gideon.automation.workflows.models import OriginKind, RunStatus
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.hypermid.client import HypermidRemoteError
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.hypermid.private_scopes import NativePrivateScopes
from gideon.interfaces.dashboard.sse import SseRegistry
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.security.approval_answer import YOU, agent
from gideon.security.approval_answer import app as app_actor
from gideon.security.session_credentials import begin_turn, end_turn, verify


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)


def test_temporary_run_once_has_owner_visibility_and_ends_with_chat(
    tmp_path, monkeypatch
):
    async def run_test():
        state = ConsoleState(sessions=None, start_time=0)
        chat = _ChatSession("mine", memory_mode="temporary")
        chat._initiator = {"kind": "owner"}
        state._sessions["mine"] = chat
        registry = SseRegistry()
        state.workflow_sse = lambda: registry
        record = tmp_path / "native" / "connection.json"
        scope = Scope("workflow-owner", "workflow-project", "host-workspace")
        process = subprocess.Popen(
            [
                str(Path("target/debug/hypermid-daemon").resolve()),
                "--socket",
                str(tmp_path / "native.sock"),
                "--connection-record",
                str(record),
                "--local-credential-id",
                "workflow-private-credential",
                "--local-owner-id",
                str(scope.owner_id),
                "--local-project-id",
                str(scope.project_id),
                "--local-workspace-id",
                str(scope.workspace_id),
                "--local-capability-id",
                "private-capability",
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
        deadline = time.monotonic() + 10
        while not record.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        provider = HypermidMemoryProvider(
            record, scope=scope, capability_id=Id("private-capability")
        )
        app_folder = tmp_path / "apps" / "private-app"
        app_folder.mkdir(parents=True)
        (app_folder / "installed.json").write_text(
            json.dumps({"name": "private-app", "enabled": True, "version": "1.0.0"})
        )
        (app_folder / "app.json").write_text(
            json.dumps(
                {
                    "name": "private-app",
                    "version": "1.0.0",
                    "permissions": {"agent": "read"},
                }
            )
        )
        chat._initiator = {"kind": "app", "name": "private-app"}
        watchdog = WorkflowWatchdog(
            state=state, services=EngineServices(memory=provider)
        )
        state.workflows = watchdog
        credential = begin_turn(
            "dashboard:mine",
            app_actor("private-app"),
            turn_id="private-test",
            memory_mode="temporary",
            work_actor=app_actor("private-app"),
            created_by_app="private-app",
        )
        other = begin_turn(
            "dashboard:other",
            YOU,
            turn_id="private-other",
            memory_mode="temporary",
            work_actor=agent("dashboard:other"),
        )
        assert credential and other

        @web.middleware
        async def credentials(request, handler):
            proof = verify(
                request.headers.get("X-Session-Proof", ""),
                request.headers.get("X-Session-Key", ""),
            )
            if proof is not None:
                request["_session_work_proof"] = proof
            return await handler(request)

        app = web.Application(middlewares=[credentials])
        app["state"] = state
        register_workflow_routes(app)
        own_headers = {
            "X-Session-Key": credential.work.session_key,
            "X-Session-Proof": credential.bearer,
        }
        other_headers = {
            "X-Session-Key": other.work.session_key,
            "X-Session-Proof": other.bearer,
        }
        try:
            async with TestServer(app) as server:
                async with ClientSession() as client:
                    spec = {
                        "name": "private-batch",
                        "root": {
                            "kind": "transform",
                            "id": "private-output",
                            "config": {"expr": "1"},
                        },
                    }
                    async with client.post(
                        server.make_url("/api/workflows/runs"),
                        json={"name": "private-batch", "run_once": spec},
                        headers=own_headers,
                    ) as response:
                        body = await response.json()
                        assert response.status == 202, body
                        run_id = body["run_id"]
                    controller = watchdog.controller(run_id)
                    assert (
                        await controller.run_to_completion(timeout=10)
                        == RunStatus.COMPLETE
                    )
                    run = store.get(run_id)
                    assert run.extra["memory_mode"] == "temporary"
                    assert run.extra["chat_owner"] == "dashboard:mine"
                    from gideon.extensions.apps.app_work import from_record

                    assert from_record(run.extra).app == "private-app"
                    receipt = watchdog._private_receipts["dashboard:mine"]
                    assert receipt.scope != scope and receipt.memory_mode == "temporary"
                    assert receipt.original_actor == "app:private-app"
                    assert run.origin.kind == OriginKind.SUBAGENT_TOOL
                    assert not (await service.get_def("private-batch"))["ok"]
                    for path in (
                        f"/api/workflows/runs/{run_id}",
                        f"/api/workflows/runs/{run_id}/events",
                        f"/api/workflows/runs/{run_id}/continuations",
                    ):
                        async with client.get(
                            server.make_url(path), headers=other_headers
                        ) as response:
                            assert response.status == 404, await response.text()
                    async with client.get(
                        server.make_url(f"/api/workflows/runs/{run_id}"),
                        headers=own_headers,
                    ) as response:
                        assert response.status == 200
                    async with client.get(
                        server.make_url("/api/workflows/runs"), headers=other_headers
                    ) as response:
                        listing = await response.json()
                        assert listing["runs"] == [] and listing["total"] == 0
                    trace_dir = tmp_path / "trace"
                    monkeypatch.setenv("GIDEON_TRACE_DIR", str(trace_dir))
                    trace_recorder.reset_for_test()
                    assert trace_recorder.is_recording()
                    key = f"workflow:{run_id}"
                    queue = registry.hub(key).subscribe()
                    registry.publish(key, "snapshot", {"private": "private payload"})
                    assert json.loads((await queue.get()).data) == {
                        "private": "private payload"
                    }
                    assert not list(trace_dir.rglob("*.jsonl"))
                    wait_spec = {
                        "name": "private-ask",
                        "root": {
                            "kind": "gate",
                            "id": "private-approval",
                            "config": {
                                "kind": "approval",
                                "prompt": "Approve private work?",
                                "timeout_secs": 0,
                            },
                        },
                    }
                    async with client.post(
                        server.make_url("/api/workflows/runs"),
                        json={"name": "private-ask", "run_once": wait_spec},
                        headers=own_headers,
                    ) as response:
                        waiting = await response.json()
                        assert response.status == 202, waiting
                    wait_id = waiting["run_id"]
                    wait_controller = watchdog.controller(wait_id)
                    assert (
                        await wait_controller.run_to_completion(timeout=10)
                        == RunStatus.NEEDS_INPUT
                    )
                    from gideon.automation.workflows import human_input

                    continuation = human_input.list_continuations(wait_id)[0]
                    watchdog.forget(wait_id)
                    state._sessions.clear()
                    directory = store.run_dir(run_id)
                    assert directory.exists()
                    await private_runs.reconcile(watchdog)
                    assert store.get(run_id) is None and not directory.exists()
                    assert registry.peek(key) is None
                    assert (
                        store.get(wait_id) is None
                        and not store.run_dir(wait_id).exists()
                    )
                    assert (
                        human_input.load_continuation(wait_id, continuation.token)
                        is None
                    )
                    assert "dashboard:mine" not in watchdog._private_receipts
                    with pytest.raises(HypermidRemoteError):
                        await asyncio.to_thread(
                            provider._loop.call,
                            lambda client: NativePrivateScopes(client).resolve(
                                receipt, trace=_trace()
                            ),
                        )
        finally:
            await watchdog.stop()
            end_turn(other)
            end_turn(credential)
            trace_recorder.reset_for_test()
            await asyncio.to_thread(provider.close)
            process.terminate()
            await asyncio.to_thread(process.wait, 10)

    asyncio.run(run_test())
