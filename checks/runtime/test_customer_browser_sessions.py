"""Owner-bound browser reservations through real dashboard authentication and SQLite."""

from __future__ import annotations

import asyncio
import json
import time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import ConversationLog
from gideon.security.auth import credentials
from gideon.integrations.browse.customer_sessions import (
    CustomerBrowserSessionStore,
    SessionNotFound,
)
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth as auth_h
from gideon.interfaces.dashboard.handlers.browser_sessions import (
    KEY,
    register_browser_session_routes,
)
from gideon.interfaces.dashboard.state import ConsoleState

PORT = 10117
COOKIE = f"gideon_token_{PORT}"


@pytest.mark.asyncio
async def test_owner_conversation_binding(tmp_path, monkeypatch):
    from gideon.core.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(credentials, "config_dir", lambda: tmp_path)
    for variable in ("GIDEON_DEV_NO_AUTH", "GIDEON_BYPASS_LOCAL_NETWORKS"):
        monkeypatch.delenv(variable, raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    credentials.set_password("api_key", "correct-horse-battery-staple")
    (tmp_path / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )
    log = ConversationLog(base_dir=tmp_path / "conversations")
    log.append("dashboard:chat-one", "user", "Open my browser")
    log.append("channel-thread", "user", "A separate channel conversation")
    log.append("chat-two", "user", "Channel conversation")
    log.append("dashboard:chat-two", "user", "Dashboard conversation")
    log.append("dashboard:login-chat", "user", "Credential owner conversation")
    state = ConsoleState(None, time.time(), conversation_log=log)
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["state"] = state
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    app.router.add_post("/api/auth/login", auth_h.api_auth_login)
    path = tmp_path / "browser.sqlite3"
    register_browser_session_routes(app, store_path=path)
    alice = token_auth.generate_token("alice", ttl_seconds=3600)
    rotated_owner = token_auth.generate_token("rotated-owner", ttl_seconds=3600)

    async with TestClient(TestServer(app), cookie_jar=aiohttp.DummyCookieJar()) as client:
        def owned(token):
            return {COOKIE: token}

        unauthorized = await client.post(
            "/api/browser/sessions", json={"conversation_id": "chat-one"}
        )
        assert unauthorized.status == 403

        missing = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "fabricated"},
            cookies=owned(alice),
        )
        assert missing.status == 404
        channel_only = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "channel-thread"},
            cookies=owned(alice),
        )
        assert channel_only.status == 201
        channel_row = (await channel_only.json())["session"]
        assert channel_row["conversation_id"] == "channel-thread"
        assert app[KEY].get(channel_row["id"], "local", "alice").canonical_key == "channel-thread"

        bare_collision = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "chat-two"},
            cookies=owned(alice),
        )
        dashboard_collision = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "dashboard:chat-two"},
            cookies=owned(alice),
        )
        assert (bare_collision.status, dashboard_collision.status) == (201, 201)
        bare_row = (await bare_collision.json())["session"]
        dashboard_row = (await dashboard_collision.json())["session"]
        assert bare_row["id"] != dashboard_row["id"]
        assert bare_row["conversation_id"] == "chat-two"
        assert dashboard_row["conversation_id"] == "dashboard:chat-two"
        assert app[KEY].get(bare_row["id"], "local", "alice").canonical_key == "chat-two"
        assert app[KEY].get(dashboard_row["id"], "local", "alice").canonical_key == "dashboard:chat-two"

        async def create():
            response = await client.post(
                "/api/browser/sessions",
                json={"conversation_id": "chat-one", "account_id": "forged"},
                cookies=owned(alice),
            )
            return response.status, (await response.json())["session"]

        created = await asyncio.gather(*(create() for _ in range(8)))
        assert {status for status, _ in created} <= {200, 201}
        assert sum(status == 201 for status, _ in created) == 1
        assert len({row["id"] for _, row in created}) == 1
        first = created[0][1]
        assert first["conversation_id"] == "chat-one"
        assert first["status"] == "reserved"
        assert first["version"] == 1
        session_id = first["id"]

        alias = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "dashboard:chat-one"},
            cookies=owned(alice),
        )
        assert alias.status == 200
        assert (await alias.json())["session"] == first

        app[KEY] = CustomerBrowserSessionStore(path)
        retried = await create()
        assert retried[0] == 200
        assert retried[1] == first
        alias_after_restart = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "dashboard:chat-one"},
            cookies=owned(alice),
        )
        assert alias_after_restart.status == 200
        assert (await alias_after_restart.json())["session"] == first
        for name, original in (
            ("channel-thread", channel_row),
            ("chat-two", bare_row),
            ("dashboard:chat-two", dashboard_row),
        ):
            retry = await client.post(
                "/api/browser/sessions",
                json={"conversation_id": name}, cookies=owned(alice),
            )
            assert retry.status == 200
            assert (await retry.json())["session"] == original

        own_read = await client.get(
            f"/api/browser/sessions/{session_id}", cookies=owned(alice)
        )
        assert own_read.status == 200
        assert (await own_read.json())["session"] == first

        login = await client.post(
            "/api/auth/login",
            json={"username": "api_key", "password": "correct-horse-battery-staple"},
        )
        assert login.status == 200
        login_cookie = login.cookies.get(COOKIE)
        assert login_cookie is not None
        valid, login_owner, _ = token_auth.validate_token(
            login_cookie.value, use_session_exp=True
        )
        assert valid and login_owner == "api_key"
        logged_in_create = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "login-chat"},
            cookies=owned(login_cookie.value),
        )
        assert logged_in_create.status == 201
        logged_in_row = (await logged_in_create.json())["session"]
        assert logged_in_row["conversation_id"] == "login-chat"
        logged_in_read = await client.get(
            f"/api/browser/sessions/{logged_in_row['id']}",
            cookies=owned(login_cookie.value),
        )
        assert logged_in_read.status == 200
        assert (await logged_in_read.json())["session"] == logged_in_row

        app_token = token_auth.generate_token("alice", ttl_seconds=3600, app="notes")
        app_headers = {"Authorization": f"Bearer {app_token}"}
        app_create = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "chat-one"},
            cookies=owned(alice), headers=app_headers,
        )
        app_read = await client.get(
            f"/api/browser/sessions/{session_id}",
            cookies=owned(alice), headers=app_headers,
        )
        app_mutation = await client.post(
            f"/api/browser/sessions/{session_id}/close",
            json={"expected_version": 1},
            cookies=owned(alice), headers=app_headers,
        )
        assert (app_create.status, app_read.status, app_mutation.status) == (401, 401, 401)

        other_existing = await client.get(
            f"/api/browser/sessions/{session_id}", cookies=owned(rotated_owner)
        )
        other_absent = await client.get(
            "/api/browser/sessions/absent", cookies=owned(rotated_owner)
        )
        assert (other_existing.status, await other_existing.text()) == (
            other_absent.status, await other_absent.text()
        )
        assert other_existing.status == 404
        cross_create = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "chat-one"},
            cookies=owned(rotated_owner),
        )
        assert cross_create.status == 404

        other_mutation = await client.post(
            f"/api/browser/sessions/{session_id}/close",
            json={"expected_version": 1}, cookies=owned(rotated_owner),
        )
        absent_mutation = await client.post(
            "/api/browser/sessions/absent/close",
            json={"expected_version": 1}, cookies=owned(rotated_owner),
        )
        assert (other_mutation.status, await other_mutation.text()) == (
            absent_mutation.status, await absent_mutation.text()
        )

        closed = await client.post(
            f"/api/browser/sessions/{session_id}/close",
            json={"expected_version": 1}, cookies=owned(alice),
        )
        assert closed.status == 200
        closed_row = (await closed.json())["session"]
        assert (closed_row["id"], closed_row["conversation_id"], closed_row["status"], closed_row["version"]) == (
            session_id, "chat-one", "closed", 2
        )
        stale = await client.post(
            f"/api/browser/sessions/{session_id}/reopen",
            json={"expected_version": 1}, cookies=owned(alice),
        )
        assert stale.status == 409
        reopened = await client.post(
            f"/api/browser/sessions/{session_id}/reopen",
            json={"expected_version": 2}, cookies=owned(alice),
        )
        assert reopened.status == 200
        reopened_row = (await reopened.json())["session"]
        assert (reopened_row["id"], reopened_row["conversation_id"], reopened_row["status"], reopened_row["version"]) == (
            session_id, "chat-one", "reserved", 3
        )

        store = CustomerBrowserSessionStore(path)
        with pytest.raises(SessionNotFound):
            store.get(session_id, "different-account", "alice")

        token_auth.revoke_all_sessions()
        revoked = await client.get(
            f"/api/browser/sessions/{session_id}", cookies=owned(alice)
        )
        assert revoked.status in {401, 403}
        new_owner = token_auth.generate_token("new-owner", ttl_seconds=3600)
        rotated_existing = await client.get(
            f"/api/browser/sessions/{session_id}", cookies=owned(new_owner)
        )
        rotated_absent = await client.get(
            "/api/browser/sessions/absent", cookies=owned(new_owner)
        )
        assert (rotated_existing.status, await rotated_existing.text()) == (
            rotated_absent.status, await rotated_absent.text()
        )
        assert rotated_existing.status == 404

    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
