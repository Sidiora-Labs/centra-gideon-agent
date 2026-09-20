from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    registry._providers.clear()
    with (
        patch("gideon.engine.tasks.native.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
@pytest.mark.parametrize("bad_provider", ["", "missing"])
async def test_bad_provider_aborts_entire_bulk_write(tmp_path, operation, bad_provider):
    async with _client(tmp_path) as client:
        original = await (
            await client.post("/api/tasks", json={"title": "Original"})
        ).json()
        if operation == "create":
            items = [
                {"title": "Would persist"},
                {"title": "Bad", "provider": bad_provider},
            ]
        elif operation == "update":
            items = [
                {"id": original["id"], "title": "Would change"},
                {"id": original["id"], "title": "Bad", "provider": bad_provider},
            ]
        else:
            items = [
                {"id": original["id"]},
                {"id": original["id"], "provider": bad_provider},
            ]

        response = await client.post(
            "/api/tasks/bulk", json={"op": operation, "items": items}
        )

        assert response.status == 400
        receipt = await response.json()
        assert receipt["succeeded"] == 0
        assert receipt["errors"] == [
            {"index": 1, "error": f"Unknown task provider: {bad_provider}"}
        ]
        stored = await client.get(f"/api/tasks/{original['id']}")
        assert stored.status == 200
        assert (await stored.json())["title"] == "Original"
        listed = await (await client.get("/api/tasks")).json()
        assert listed["total"] == 1
