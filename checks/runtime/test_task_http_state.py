import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes


@asynccontextmanager
async def task_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    previous = dict(registry._providers)
    registry._providers.clear()
    try:
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    finally:
        registry._providers.clear()
        registry._providers.update(previous)


@pytest.mark.asyncio
async def test_bulk_admission_then_partial_mutation_uses_real_records(
    tmp_path, monkeypatch
):
    async with task_server(tmp_path, monkeypatch) as client:
        rejected = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [{"title": "Honest"}, {"title": "", "author": ""}],
            },
        )
        receipt = await rejected.json()
        assert rejected.status == 400 and receipt["failed"] == 2
        assert len(receipt["errors"]) == 2 and receipt["results"] == []
        assert not list((tmp_path / "tasks").glob("t-*.json"))
        response = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [
                    {"title": "Stored", "provider": "ignored"},
                    {"title": "Refused", "status": "invalid"},
                ],
            },
        )
        receipt = await response.json()
        assert response.status == 400
        assert receipt["succeeded"] == 0 and receipt["failed"] == 2
        assert "Unknown task provider" in receipt["errors"][0]["error"]
        assert not list((tmp_path / "tasks").glob("t-*.json"))
        response = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [
                    {"title": "Stored", "provider": "native"},
                    {"title": "Refused", "status": "invalid"},
                ],
            },
        )
        receipt = await response.json()
        assert response.status == 200
        assert receipt["succeeded"] == receipt["failed"] == 1
        task_id = receipt["results"][0]["task_id"]
        stored = json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text())
        assert stored["title"] == "Stored" and stored["provider"] == "native"
        update = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "update",
                "items": [{"id": task_id, "title": "Updated"}, {"id": "t-missing"}],
            },
        )
        receipt = await update.json()
        assert receipt["succeeded"] == 2 and receipt["failed"] == 0
        assert [row["status"] for row in receipt["results"]] == ["updated", "not_found"]
        assert (
            json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text())["title"]
            == "Updated"
        )
        deleted = await client.post(
            "/api/tasks/bulk", json={"op": "delete", "items": [task_id, "t-missing"]}
        )
        assert [row["status"] for row in (await deleted.json())["results"]] == [
            "deleted",
            "not_found",
        ]
        assert not (tmp_path / "tasks" / f"{task_id}.json").exists()


@pytest.mark.asyncio
async def test_cycle_refusal_and_completion_receipt_match_persisted_cascade(
    tmp_path, monkeypatch
):
    async with task_server(tmp_path, monkeypatch) as client:
        first = await (await client.post("/api/tasks", json={"title": "First"})).json()
        second = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "Second",
                    "dependencies": [{"depends_on_task_id": first["id"]}],
                },
            )
        ).json()
        before = (tmp_path / "tasks" / f"{first['id']}.json").read_bytes()
        cycle = await client.put(
            f"/api/tasks/{first['id']}",
            json={"dependencies": [{"depends_on_task_id": second["id"]}]},
        )
        assert cycle.status == 400
        assert set((await cycle.json())["cycle"]) == {first["id"], second["id"]}
        assert (tmp_path / "tasks" / f"{first['id']}.json").read_bytes() == before
        completed = await client.put(
            f"/api/tasks/{first['id']}", json={"status": "done"}
        )
        changed = (await completed.json())["reconciled"]
        assert {row["id"]: row["status"] for row in changed} == {
            first["id"]: "done",
            second["id"]: "open",
        }
        ready = await (await client.get("/api/tasks/ready?everyone=yes")).json()
        assert [row["id"] for row in ready["tasks"]] == [second["id"]]
        graph = await (await client.get("/api/tasks/graph")).json()
        assert isinstance(graph, dict)
        assert (await client.get("/api/tasks/providers")).status == 200


@pytest.mark.asyncio
async def test_invalid_json_admission_and_real_comment_lifecycle(tmp_path, monkeypatch):
    async with task_server(tmp_path, monkeypatch) as client:
        task = await (await client.post("/api/tasks", json={"title": "Notes"})).json()
        task_url = f"/api/tasks/{task['id']}"
        for method, url in [
            ("POST", "/api/tasks"),
            ("PUT", task_url),
            ("POST", "/api/tasks/search"),
            ("POST", "/api/tasks/bulk"),
            ("POST", task_url + "/comments"),
        ]:
            response = await client.request(method, url, data="{")
            assert response.status == 400
            assert await response.json() == {
                "error": {"code": "invalid_json", "message": "invalid JSON"}
            }
        response = await client.post(
            task_url + "/comments", json={"body": "  Actual note  "}
        )
        comment = await response.json()
        assert response.status == 201 and comment["body"] == "Actual note"
        assert (await (await client.get(task_url)).json())["comment_count"] == 1
        response = await client.delete(task_url + f"/comments/{comment['id']}")
        assert await response.json() == {"ok": True, "id": comment["id"]}
        assert (await (await client.get(task_url + "/comments")).json())[
            "comments"
        ] == []
        assert (await client.delete(task_url)).status == 200
        missing = await client.get(task_url)
        assert missing.status == 404 and await missing.json() == {
            "error": {"code": "not_found", "message": "not found"}
        }
