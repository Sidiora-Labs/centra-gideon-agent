"""Persisted Inbox GET projections under real credentials and canonical app decisions."""

import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.manager import InstalledApp, _write_installed
from gideon.extensions.apps.permissions import app_request_denial
from gideon.integrations.inbox import InboxItem, InboxStore
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers_inbox import (
    api_inbox_kinds,
    api_inbox_list,
    api_inbox_open_items,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.approval_answer import OWNER, Principal
from gideon.security.session_credentials import begin_turn, end_turn


@pytest.mark.asyncio
async def test_persisted_get_scopes_before_totals_and_current_route_permission(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    store = InboxStore(tmp_path / "inbox.json")
    for identity, refs, kind, status in [
        ("own", {"chat": "one"}, "agent_request", "pending"),
        ("shared", {}, "email", "seen"),
        ("foreign", {"chat": "two"}, "proposal", "pending"),
        ("unresolved", {"workflow": "missing"}, "needs_input", "pending"),
        ("handled", {}, "system", "handled"),
    ]:
        store.add(
            InboxItem(
                id=identity,
                channel="channel",
                channel_name="Channel",
                thread_ts=None,
                message=identity,
                sender_id="actual",
                sender_name="Morgan",
                source="channel:mail",
                refs=refs,
                item_kind=kind,
                status=status,
                created_at=len(store.items),
            )
        )
    store.flush()
    before = (tmp_path / "inbox.json").read_bytes()
    # Read a fresh persisted store, not a canned projection or fake authority rows.
    state._inbox_store = InboxStore(tmp_path / "inbox.json")
    state._inbox_store.load()
    manifest = tmp_path / "apps" / "get-reader" / "app.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": "get-reader",
                "version": "1.0.0",
                "permissions": {"api": ["/api/inbox*"]},
            }
        )
    )
    _write_installed(
        "get-reader", InstalledApp(name="get-reader", version="1.0.0", enabled=True)
    )

    @web.middleware
    async def app_permissions(request, handler):
        if request.get("app"):
            reason = app_request_denial(
                request["app"],
                request.path,
                method=request.method,
                route=request.match_info.route.resource.canonical,
            )
            if reason:
                return web.json_response({"error": reason}, status=403)
        return await handler(request)

    token_auth.use_ephemeral_secret(b"inbox-get-local")
    token_auth.revoke_all_sessions()
    owner = token_auth.generate_token("get-owner", kind="desktop")
    app_token = token_auth.generate_token("get-owner", app="get-reader")
    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(
                port=0,
                internal_secret="get-secret",
                mixed_internal_routes=frozenset(
                    {"GET /api/inbox", "GET /api/inbox/open", "GET /api/inbox/kinds"}
                ),
            ),
            app_permissions,
        ]
    )
    app["state"] = state
    app["local_secret"] = "get-secret"
    app.router.add_get("/api/inbox", api_inbox_list)
    app.router.add_get("/api/inbox/open", api_inbox_open_items)
    app.router.add_get("/api/inbox/kinds", api_inbox_kinds)
    one = begin_turn(
        "dashboard:one",
        Principal(OWNER, "get-owner"),
        turn_id="one",
        memory_mode="incognito",
    )
    two = begin_turn(
        "dashboard:two",
        Principal(OWNER, "get-owner"),
        turn_id="two",
        memory_mode="temporary",
    )
    try:
        async with TestClient(TestServer(app)) as client:
            headers = {
                "X-Internal-Secret": "get-secret",
                "X-Session-Key": "dashboard:one",
                "X-Session-Proof": one.bearer,
            }
            response = await client.get("/api/inbox", headers=headers)
            assert response.status == 200
            assert {x["id"] for x in await response.json()} == {
                "own",
                "shared",
                "handled",
            }
            response = await client.get("/api/inbox/open", headers=headers)
            assert {x["id"] for x in await response.json()} == {"own", "shared"}
            response = await client.get("/api/inbox/kinds", headers=headers)
            kinds = (await response.json())["kinds"]
            assert {x["kind"] for x in kinds} == {"agent_request", "email", "system"}
            assert (
                sum(x["open"] for x in kinds) == 2
                and sum(x["total"] for x in kinds) == 3
            )
            response = await client.get(
                "/api/inbox?kind=proposal&mine=true", headers=headers
            )
            assert await response.json() == []
            response = await client.get(
                "/api/inbox", headers={"Authorization": "Bearer " + owner}
            )
            assert len(await response.json()) == 5
            response = await client.get(
                "/api/inbox",
                headers={
                    "X-Internal-Secret": "get-secret",
                    "X-Session-Key": "dashboard:ui",
                },
            )
            assert await response.json() == []  # naming the pages is not owner proof
            response = await client.get("/api/inbox")
            assert response.status == 403
            response = await client.get(
                "/api/inbox",
                headers={
                    "Authorization": "Bearer " + app_token,
                    "Cookie": f"gideon_token_{client.server.port}=" + owner,
                },
            )
            assert (
                response.status == 403
                and "not declared for app access" in (await response.json())["error"]
            )
        assert (tmp_path / "inbox.json").read_bytes() == before
    finally:
        end_turn(two)
        end_turn(one)
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()
