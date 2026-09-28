from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def test_terminal_creation_and_legacy_refusal_use_real_http_and_pty(tmp_path, monkeypatch):
    from gideon.interfaces.dashboard.handlers import terminal

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"dashboard": {"terminal": {"persist": False, "shell": "/bin/sh"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    terminal._enabled_cache[:] = [True, time.monotonic()]

    @web.middleware
    async def signed_in(request, handler):
        request["user"] = "terminal-tier-test"
        return await handler(request)

    app = web.Application(middlewares=[signed_in])
    app["state"] = SimpleNamespace(_terminal_sessions={})
    app.router.add_post("/api/terminal/sessions", terminal.api_terminal_create)
    app.router.add_get("/api/terminal/sessions", terminal.api_terminal_list)
    app.router.add_delete(
        "/api/terminal/sessions/{session_id}", terminal.api_terminal_delete
    )
    app.router.add_get("/api/ws/terminal/{session_id}", terminal.api_terminal_ws)

    async def exercise():
        async with TestClient(TestServer(app)) as client:
            created = await client.post("/api/terminal/sessions", json={"sandbox": "none"})
            assert created.status == 200
            session_id = (await created.json())["session_id"]
            assert session_id.endswith("@none")

            async with client.ws_connect(f"/api/ws/terminal/{session_id}"):
                listed = await client.get("/api/terminal/sessions")
                rows = (await listed.json())["sessions"]
                assert any(row["session_id"] == session_id for row in rows)

            legacy = await client.ws_connect("/api/ws/terminal/legacy-session")
            refusal = await legacy.receive_json(timeout=3)
            assert refusal["type"] == "error"
            assert "open a new one" in refusal["message"]
            await legacy.close()

            unavailable = await client.post(
                "/api/terminal/sessions", json={"sandbox": "removed-tier"}
            )
            assert unavailable.status == 409

            deleted = await client.delete(f"/api/terminal/sessions/{session_id}")
            assert deleted.status == 200

    asyncio.run(exercise())


def test_unknown_provider_resolution_refuses_instead_of_selecting_host():
    from gideon.integrations.sandbox_providers import (
        SandboxUnavailableError,
        resolve_provider,
    )

    assert resolve_provider("none").name == "none"
    try:
        resolve_provider("not-registered-tier")
    except SandboxUnavailableError as error:
        assert "not registered" in str(error)
    else:
        raise AssertionError("an unknown named tier resolved to a provider")
