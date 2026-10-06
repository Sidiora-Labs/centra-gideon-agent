"""Native task edits preserve current checklist state when an older form is saved."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_native_task_stale_save_preserves_atomic_checklist_toggle(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))

    from gideon.engine.tasks import registry
    from gideon.engine.tasks.handlers import register_task_routes
    from gideon.engine.tasks.native import NativeTaskProvider

    previous_provider = registry.get_provider("native")
    provider = NativeTaskProvider()
    registry.register_provider(provider)
    app = web.Application()
    register_task_routes(app)

    try:
        async with TestClient(TestServer(app)) as client:
            created = await client.post(
                "/api/tasks",
                json={
                    "title": "Prepare review",
                    "provider": "native",
                    "action_plan": [{"content": "Read the draft", "completed": False}],
                },
            )
            assert created.status == 201, await created.text()
            task_id = (await created.json())["id"]

            opened_response = await client.get(
                f"/api/tasks/{task_id}", params={"provider": "native"}
            )
            assert opened_response.status == 200
            opened = await opened_response.json()
            base_revision = opened["revision"]
            assert opened["action_plan"][0]["completed"] is False

            missing_base = await client.put(
                f"/api/tasks/{task_id}",
                json={"provider": "native", "title": "Blind edit"},
            )
            assert missing_base.status == 428
            assert (await missing_base.json())["error"]["code"] == "revision_required"

            toggled_response = await client.put(
                f"/api/tasks/{task_id}",
                json={
                    "provider": "native",
                    "checklist_toggle": {"kind": "step", "index": 0},
                },
            )
            assert toggled_response.status == 200, await toggled_response.text()
            toggled = await toggled_response.json()
            assert toggled["action_plan"][0]["completed"] is True
            assert toggled["revision"] != base_revision

            stale = await client.put(
                f"/api/tasks/{task_id}",
                json={
                    "provider": "native",
                    "expected_revision": base_revision,
                    "title": "Stale edit",
                    "action_plan": opened["action_plan"],
                },
            )
            assert stale.status == 409
            assert (await stale.json())["error"]["code"] == "version_conflict"

            current_response = await client.get(
                f"/api/tasks/{task_id}", params={"provider": "native"}
            )
            current = await current_response.json()
            assert current["title"] == "Prepare review"
            assert current["action_plan"][0]["completed"] is True
    finally:
        registry.unregister_provider("native")
        if previous_provider is not None:
            registry.register_provider(previous_provider)
