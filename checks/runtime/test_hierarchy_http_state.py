import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.loop import store as loop_store
from gideon.automation.loop.loop import Loop
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@asynccontextmanager
async def hierarchy_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    saved = dict(registry._providers)
    registry._providers.clear()
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    try:
        app = web.Application()
        app["state"] = state
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client, state
    finally:
        registry._providers.clear()
        registry._providers.update(saved)


@pytest.mark.asyncio
async def test_force_retirement_detaches_real_chat_and_deletes_owned_records(
    tmp_path, monkeypatch
):
    async with hierarchy_server(tmp_path, monkeypatch) as (client, state):
        project = await (
            await client.post("/api/projects", json={"name": "Retire me"})
        ).json()
        task = await (
            await client.post(
                "/api/tasks", json={"title": "Owned", "project_id": project["id"]}
            )
        ).json()
        loop = loop_store.create(
            Loop(
                id="",
                kind="goal",
                name="Owned work",
                task="Do work under the actual project",
                project_id=project["id"],
            )
        )
        chat = _ChatSession(key="chat-retained", project_id=project["id"])
        worker = _ChatSession(key="worker-retained", project_id=project["id"])
        worker._app = "loop"
        state._sessions[chat.key] = chat
        state._sessions[worker.key] = worker
        url = f"/api/projects/{project['id']}"
        guarded = await client.delete(url)
        assert guarded.status == 409
        assert await guarded.json() == {
            "error": "project has bound work",
            "loops": 1,
            "code": 0,
            "chats": 1,
        }
        assert (await client.get(f"/api/tasks/{task['id']}")).status == 200
        deleted = await client.delete(url + "?force=true")
        assert deleted.status == 200 and await deleted.json() == {"ok": True}
        assert chat.project_id == "" and state._sessions[chat.key] is chat
        assert worker.project_id == project["id"]
        assert loop_store.get(loop.id) is None
        assert (await client.get(f"/api/tasks/{task['id']}")).status == 404
        assert (
            await client.get(f"/api/task-lists/{task['task_list_id']}")
        ).status == 404
        assert (await client.get(url)).status == 404


@pytest.mark.asyncio
async def test_repeatable_reset_requires_intent_then_reopens_persisted_criteria(
    tmp_path, monkeypatch
):
    async with hierarchy_server(tmp_path, monkeypatch) as (client, _):
        task_list = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "repeatable": True}
            )
        ).json()
        task = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "Ship",
                    "task_list_id": task_list["id"],
                    "status": "done",
                    "exit_criteria": [{"description": "Verified", "met": True}],
                    "execution_notes": ["Actual evidence"],
                },
            )
        ).json()
        path = tmp_path / "tasks" / f"{task['id']}.json"
        before = path.read_bytes()
        url = f"/api/task-lists/{task_list['id']}/reset"
        refusal = await client.post(url, json={})
        assert (
            refusal.status == 400
            and (await refusal.json())["error"]["code"] == "confirm_required"
        )
        assert path.read_bytes() == before
        response = await client.post(url, json={"confirm": True})
        assert await response.json() == {"ok": True, "reset_task_ids": [task["id"]]}
        record = json.loads(path.read_text())
        assert record["status"] == "open" and record["execution_notes"] == []
        assert record["exit_criteria"][0]["status"] == "incomplete"
        assert record["exit_criteria"][0]["met"] is False
        assert (await client.post(url, json={"confirm": True})).status == 400


@pytest.mark.asyncio
async def test_hierarchy_patch_admission_preserves_file_on_refusal(
    tmp_path, monkeypatch
):
    async with hierarchy_server(tmp_path, monkeypatch) as (client, _):
        project = await (
            await client.post(
                "/api/projects", json={"name": "Original", "brief": "Before"}
            )
        ).json()
        url = f"/api/projects/{project['id']}"
        for payload in (
            {"brief": "Changed", "id": "forged"},
            [],
            {"workspace_dir": "/etc"},
        ):
            response = await client.put(url, json=payload)
            assert response.status in (400, 403)
            loaded = await (await client.get(url)).json()
            assert loaded["brief"] == "Before" and loaded["name"] == "Original"
        changed = await (
            await client.put(
                url, json={"name": "Renamed", "brief": "After", "name_locked": True}
            )
        ).json()
        assert (
            changed["name"] == "Renamed"
            and changed["brief"] == "After"
            and changed["name_locked"]
        )
        task_list = await (
            await client.post(
                "/api/task-lists", json={"name": "Old", "project_id": project["id"]}
            )
        ).json()
        list_url = f"/api/task-lists/{task_list['id']}"
        assert (await client.put(list_url, json={"list_id": "forged"})).status == 400
        assert (await (await client.put(list_url, json={"name": "New"})).json())[
            "name"
        ] == "New"
        assert (await client.delete(list_url)).status == 200
        assert (await client.get(list_url)).status == 404


@pytest.mark.asyncio
async def test_project_archive_lifecycle_is_writable_and_reversible(
    tmp_path, monkeypatch
):
    async with hierarchy_server(tmp_path, monkeypatch) as (client, _):
        project = await (
            await client.post("/api/projects", json={"name": "Archive me"})
        ).json()
        url = f"/api/projects/{project['id']}"

        archived = await client.put(url, json={"status": "archived"})
        assert archived.status == 200
        assert (await archived.json())["status"] == "archived"
        assert (await (await client.get(url)).json())["status"] == "archived"
        listed = await (await client.get("/api/projects")).json()
        listed_project = next(
            row for row in listed["projects"] if row["id"] == project["id"]
        )
        assert listed_project["status"] == "archived"

        restored = await client.put(url, json={"status": "active"})
        assert restored.status == 200
        assert (await restored.json())["status"] == "active"
