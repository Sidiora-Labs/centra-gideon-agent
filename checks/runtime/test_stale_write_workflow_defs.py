"""Workflow-definition saves retain integer revisions and refuse stale editor copies."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_workflow_editor_requires_captured_revision_and_preserves_versioned_copy(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))

    from gideon.automation.workflows import defs
    from gideon.automation.workflows.handlers import register_workflow_routes
    from gideon.automation.workflows.native_defs import (
        NativeWorkflowDefProvider,
        defs_root,
    )
    from gideon.stale_write import revision_of

    previous_provider = defs.get_provider("native")
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    app = web.Application()
    register_workflow_routes(app)
    path = defs_root() / "weekly-note" / "workflow.json"
    root = {"kind": "transform", "id": "draft", "config": {"expr": "one"}}
    initial = {
        "name": "weekly-note",
        "description": "Original definition",
        "root": root,
        "provenance": "user",
    }

    try:
        async with TestClient(TestServer(app)) as client:
            created = await client.post("/api/workflows", json=initial)
            assert created.status == 201, await created.text()
            assert path.is_file()

            opened_response = await client.get("/api/workflows/weekly-note")
            assert opened_response.status == 200
            opened = await opened_response.json()
            base_revision = opened["revision"]
            assert base_revision == revision_of(opened["definition"])
            assert opened["definition"]["version"] == 1

            stale_document = {
                **initial,
                "description": "Tab A's update",
                "root": {"kind": "transform", "id": "draft", "config": {"expr": "two"}},
            }
            missing_base = await client.post("/api/workflows", json=stale_document)
            assert missing_base.status == 428
            assert (await missing_base.json())["error"]["code"] == "revision_required"
            assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1

            first = await client.post(
                "/api/workflows",
                json=stale_document,
                headers={"If-Match": f'"{base_revision}"'},
            )
            assert first.status == 201, await first.text()

            stale_document["description"] = "Tab B's stale update"
            stale_document["root"] = {
                "kind": "transform",
                "id": "draft",
                "config": {"expr": "stale"},
            }
            stale = await client.post(
                "/api/workflows",
                json=stale_document,
                headers={"If-Match": f'"{base_revision}"'},
            )
            assert stale.status == 409
            assert (await stale.json())["error"]["code"] == "stale_write"
            stored = json.loads(path.read_text(encoding="utf-8"))
            assert stored["version"] == 2
            assert stored["description"] == "Tab A's update"
            assert stored["root"]["config"]["expr"] == "two"

            copied = await client.post(
                "/api/workflows",
                json={
                    "name": "weekly-copy",
                    "description": "Copy of the first version",
                    "root": root,
                    "create_only": True,
                    "based_on": "weekly-note",
                    "based_on_version": 1,
                },
            )
            assert copied.status == 201, await copied.text()
            copied_document = json.loads(
                (defs_root() / "weekly-copy" / "workflow.json").read_text(
                    encoding="utf-8"
                )
            )
            assert copied_document["version"] == 1
            assert copied_document["root"]["config"]["expr"] == "one"
    finally:
        defs.unregister_provider("native")
        if previous_provider is not None:
            defs.register_provider(previous_provider)
