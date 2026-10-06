from __future__ import annotations

import asyncio
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from gideon.interfaces.dashboard.handlers.sessions import (
    api_approval_resolve,
    api_approvals,
)


async def _wait_for_pending(state, approval_id: str) -> dict:
    for _ in range(200):
        pending = state._pending_approvals.get(approval_id)
        if pending is not None:
            return pending
        await asyncio.sleep(0.005)
    raise AssertionError(f"Native approval {approval_id!r} was not registered")


@pytest.mark.asyncio
async def test_revision_targets_one_native_approval_and_rejects_reused_id(tmp_path):
    state = _make_state(tmp_path)
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/approvals", api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)

    async with TestClient(TestServer(app)) as client:
        first_request = asyncio.create_task(
            state.request_approval(
                "native-approval-7",
                "dashboard",
                "send_message",
                tool_input='{"to":"room-a","text":"first request"}',
                tool_purpose="Send the requested first message",
                session="chat-native-7",
            )
        )
        first_native = await _wait_for_pending(state, "native-approval-7")
        first_revision = first_native["revision"]
        assert re.fullmatch(r"[a-f0-9]{32}", first_revision)
        listed = await (await client.get("/api/approvals")).json()
        assert listed == [first_native]

        resolved = await client.post(
            "/api/approvals/native-approval-7/approve",
            json={"expected_revision": first_revision},
        )
        assert resolved.status == 200
        assert await resolved.json() == {"ok": True}
        assert await first_request is True

        duplicate = await client.post(
            "/api/approvals/native-approval-7/approve",
            json={"expected_revision": first_revision},
        )
        assert duplicate.status == 404

        second_request = asyncio.create_task(
            state.request_approval(
                "native-approval-7",
                "dashboard",
                "delete_file",
                tool_input='{"path":"/workspace/second.txt"}',
                tool_purpose="Delete the second request target",
                session="chat-native-8",
            )
        )
        second_native = await _wait_for_pending(state, "native-approval-7")
        second_revision = second_native["revision"]
        assert second_revision != first_revision
        assert second_native["tool"] == "delete_file"
        assert second_native["tool_input"] == '{"path":"/workspace/second.txt"}'

        concurrent_duplicate = await state.request_approval(
            "native-approval-7",
            "dashboard",
            "overwrite_file",
            tool_input='{"path":"/workspace/other.txt"}',
            session="chat-native-9",
        )
        assert concurrent_duplicate is False
        assert state._pending_approvals["native-approval-7"] is second_native

        stale = await client.post(
            "/api/approvals/native-approval-7/approve",
            json={"expected_revision": first_revision},
        )
        assert stale.status == 409
        assert await stale.json() == {
            "error": {
                "code": "revision_conflict",
                "message": "Approval request changed. Reload before deciding.",
            }
        }
        assert not second_request.done()
        assert state._pending_approvals["native-approval-7"] is second_native

        rejected = await client.post(
            "/api/approvals/native-approval-7/reject",
            json={"expected_revision": second_revision},
        )
        assert rejected.status == 200
        assert await rejected.json() == {"ok": True}
        assert await second_request is False
        assert "native-approval-7" not in state._pending_approvals
