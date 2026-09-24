from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.loop import store as legacy_store
from gideon.automation.workflows import defs, service, store
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.loop_routes import api_loop_create
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.sel import sel


@asynccontextmanager
async def loop_server(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("gideon.automation.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr(defs, "_providers", {})
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state.workflows = WorkflowWatchdog()
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/loops", api_loop_create)
    try:
        async with TestClient(TestServer(app)) as client:
            yield client, state
    finally:
        await state.workflows.stop()


def audit_entries():
    return [
        json.loads(line)
        for line in sel()._path.read_text().splitlines()
        if json.loads(line).get("operation") == "workflow_run_start"
    ]


@pytest.mark.asyncio
async def test_ported_general_returns_run_contract_and_records_audit(
    tmp_path, monkeypatch
):
    assert service.PORTED_LOOP_KINDS == {"general"}
    async with loop_server(tmp_path, monkeypatch) as (client, state):
        response = await client.post(
            "/api/loops",
            json={
                "kind": "general",
                "task": "Review the onboarding checklist",
                "success_criteria": "Every checklist item has evidence",
                "skip_preflight": True,
            },
        )
        assert response.status == 202
        body = await response.json()
        assert set(body) == {"run_id", "status", "blocking", "kind"}
        assert body["kind"] == "general" and body["blocking"] is False
        assert body["status"] == "running"
        run = store.get(body["run_id"])
        assert run.workflow_name == "general-project"
        assert run.inputs["exit_condition"] == "Every checklist item has evidence"
        assert state.workflows.controller(run.id) is not None
        assert legacy_store.list_redacted() == []
        audit = audit_entries()[-1]
        assert audit["outcome"] == "success"
        assert audit["resources"] == run.id


@pytest.mark.asyncio
async def test_ported_permission_refusal_is_audited_before_creating_run(
    tmp_path, monkeypatch
):
    async with loop_server(tmp_path, monkeypatch) as (client, state):
        state._restricted_keys.add("dashboard:restricted")
        response = await client.post(
            "/api/loops",
            headers={"X-Session-Key": "dashboard:restricted"},
            json={
                "kind": "general",
                "task": "Review the onboarding checklist",
                "skip_preflight": True,
            },
        )
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "restricted_session"
        assert store.list_runs() == ([], 0)
        audit = audit_entries()[-1]
        assert audit["outcome"] == "denied"
        assert audit["caller_identity"] == "dashboard:restricted"


@pytest.mark.asyncio
async def test_ported_preflight_error_remains_error_and_is_audited(
    tmp_path, monkeypatch
):
    async with loop_server(tmp_path, monkeypatch) as (client, state):
        response = await client.post(
            "/api/loops",
            json={
                "kind": "general",
                "task": "Review the onboarding checklist",
            },
        )
        assert response.status == 422
        assert (await response.json())["error"][
            "service_code"
        ] == "WF_RUN_PREFLIGHT_FAILED"
        assert audit_entries()[-1]["outcome"] == "failure"
        assert store.list_runs() == ([], 0)


@pytest.mark.asyncio
async def test_legacy_goal_still_creates_ready_loop(tmp_path, monkeypatch):
    async with loop_server(tmp_path, monkeypatch) as (client, state):
        response = await client.post(
            "/api/loops",
            json={
                "kind": "goal",
                "task": "Review the onboarding checklist",
            },
        )
        assert response.status == 201
        body = await response.json()
        assert body["kind"] == "goal" and body["status"] == "ready"
        assert legacy_store.get(body["id"]) is not None
        assert store.list_runs() == ([], 0)
