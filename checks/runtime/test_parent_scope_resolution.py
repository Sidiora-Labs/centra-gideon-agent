from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.engine.tasks.handlers import register_task_routes
from gideon.interfaces.dashboard.handlers.knowledge import remove_collection_item


@asynccontextmanager
async def _client(tmp_path):
    with (
        patch("gideon.engine.tasks.native.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


@pytest.mark.asyncio
async def test_task_list_scope_resolves_supplied_project(tmp_path):
    async with _client(tmp_path) as client:
        created = await client.post("/api/task-lists", json={"name": "Global-looking"})
        assert created.status == 201

        response = await client.get("/api/task-lists?project_id=missing")

        assert response.status == 404
        assert await response.json() == {"error": "project not found"}


@pytest.mark.asyncio
async def test_collection_item_scope_resolves_supplied_collection(tmp_path):
    app = web.Application()
    app["state"] = SimpleNamespace(
        knowledge_store=KnowledgeStore(tmp_path / "knowledge.db")
    )
    app.router.add_delete(
        "/api/knowledge/collections/{id}/items/{item_id}", remove_collection_item
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.delete(
            "/api/knowledge/collections/missing/items/missing-item"
        )

        assert response.status == 404
        assert await response.json() == {"error": "collection not found"}
