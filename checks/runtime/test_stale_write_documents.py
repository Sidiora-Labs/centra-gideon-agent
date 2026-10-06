"""Knowledge document edits refuse stale bases while tag operations preserve neighbors."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_knowledge_tag_operation_survives_stale_document_save(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))

    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.handlers.knowledge import get_item, update_item
    from gideon.interfaces.dashboard.state import ConsoleState

    sessions = ConversationDirectory(AppConfig())
    state = ConsoleState(sessions=sessions, start_time=0.0)
    store = state.knowledge_store
    item_id = store.create_typed_item(
        item_type="note", title="Original", content="Body", tags=["kept"]
    )
    slow_handler_entered = asyncio.Event()

    @web.middleware
    async def observe_slow_handler(request, handler):
        if request.headers.get("X-Test-Slow-Body") == "1":
            slow_handler_entered.set()
        return await handler(request)

    app = web.Application(middlewares=[observe_slow_handler])
    app["state"] = state
    app.router.add_get("/api/knowledge/items/{id}", get_item)
    app.router.add_patch("/api/knowledge/items/{id}", update_item)

    try:
        async with TestClient(TestServer(app)) as client:
            opened_response = await client.get(f"/api/knowledge/items/{item_id}")
            assert opened_response.status == 200
            opened = await opened_response.json()
            base_revision = opened["revision"]

            missing_base = await client.patch(
                f"/api/knowledge/items/{item_id}", json={"title": "Blind title"}
            )
            assert missing_base.status == 428
            assert (await missing_base.json())["error"]["code"] == "revision_required"
            assert store.get_item(item_id)["title"] == "Original"

            operation = await client.patch(
                f"/api/knowledge/items/{item_id}", json={"tag_add": ["added-elsewhere"]}
            )
            assert operation.status == 200
            operation_result = await operation.json()
            assert len(operation_result["revision"]) == 16
            assert operation_result["revision"] != base_revision

            stale = await client.patch(
                f"/api/knowledge/items/{item_id}",
                json={"title": "Stale title"},
                headers={"If-Match": f'"{base_revision}"'},
            )
            assert stale.status == 409
            assert (await stale.json())["error"]["code"] == "stale_write"

            current_response = await client.get(f"/api/knowledge/items/{item_id}")
            assert current_response.status == 200
            concurrent_base = (await current_response.json())["revision"]
            first_chunk_sent = asyncio.Event()
            release_slow_body = asyncio.Event()

            async def slow_json_body():
                yield b'{"title":'
                first_chunk_sent.set()
                await release_slow_body.wait()
                yield b'"Slow request"}'

            slow_request = asyncio.create_task(
                client.patch(
                    f"/api/knowledge/items/{item_id}",
                    data=slow_json_body(),
                    headers={
                        "Content-Type": "application/json",
                        "If-Match": f'"{concurrent_base}"',
                        "X-Test-Slow-Body": "1",
                    },
                )
            )
            try:
                await asyncio.wait_for(slow_handler_entered.wait(), timeout=5)
                await asyncio.wait_for(first_chunk_sent.wait(), timeout=5)
                accepted = await client.patch(
                    f"/api/knowledge/items/{item_id}",
                    json={"title": "Accepted concurrent save"},
                    headers={"If-Match": f'"{concurrent_base}"'},
                )
                assert accepted.status == 200
                release_slow_body.set()
                slow_stale = await asyncio.wait_for(slow_request, timeout=5)
                assert slow_stale.status == 409
                assert (await slow_stale.json())["error"]["code"] == "stale_write"
            finally:
                release_slow_body.set()
                if not slow_request.done():
                    await slow_request

        current = store.get_item(item_id)
        assert current["title"] == "Accepted concurrent save"
        assert set(current["tags"]) == {"kept", "added-elsewhere"}
    finally:
        store.close()
        await sessions.close_all()
