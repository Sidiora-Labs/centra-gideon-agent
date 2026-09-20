from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes
from gideon.engine.tasks.models import WorkflowTaskBinding


@asynccontextmanager
async def _doors(tmp_path):
    registry._providers.clear()
    with (
        patch("gideon.engine.tasks.native.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        task = await registry.create_task(
            title="managed",
            workflow_binding=WorkflowTaskBinding(run_id="run-8", node_id="node-8"),
        )
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield task, client, NativeBuiltinToolProvider(tmp_path)
    registry._providers.clear()


@pytest.mark.asyncio
async def test_all_user_write_doors_reject_engine_owned_fields(tmp_path):
    async with _doors(tmp_path) as (task, client, tools):
        single = await client.put(f"/api/tasks/{task.id}", json={"status": "done"})
        bulk = await client.post(
            "/api/tasks/bulk",
            json={"op": "update", "items": [{"id": task.id, "status": "done"}]},
        )
        tool = await tools.invoke("task_update", {"id": task.id, "status": "done"})

        assert single.status == bulk.status == 409
        assert (await single.json())["error"]["code"] == "engine_owned_field"
        assert (await bulk.json())["error"]["code"] == "engine_owned_field"
        assert not tool.success
        assert tool.metadata["error"]["code"] == "engine_owned_field"
        assert "workflow_skip" in tool.error
        assert (await registry.get_task(task.id)).status.value == "open"


@pytest.mark.asyncio
async def test_update_facade_remains_available_to_the_engine(tmp_path):
    async with _doors(tmp_path) as (task, _client, _tools):
        changed = await registry.update_task(task.id, status="done")
        assert changed is not None and changed.status.value == "done"
