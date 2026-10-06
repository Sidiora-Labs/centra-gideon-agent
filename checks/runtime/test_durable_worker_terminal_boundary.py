from __future__ import annotations

import asyncio
import json
import shutil
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.skipif(
    shutil.which("tmux") is None, reason="requires the real tmux executable"
)
def test_real_worker_is_hidden_from_terminal_list_and_survives_terminal_delete(
    tmp_path, monkeypatch
):
    from gideon.engine import tmux_substrate
    from gideon.interfaces.dashboard.handlers import terminal

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"dashboard": {"terminal": {"persist": True, "shell": "/bin/sh"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    terminal._enabled_cache[:] = [True, time.monotonic()]
    worker_name = tmux_substrate.terminal_session_name("deadbeef0000@none")

    @web.middleware
    async def signed_in(request, handler):
        request["user"] = "terminal-worker-test"
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
        try:
            assert await tmux_substrate.new_session(
                worker_name,
                workspace=str(tmp_path),
                command=["/bin/sleep", "30"],
            )
            kinds = dict(await tmux_substrate.list_session_kinds())
            assert kinds[worker_name] == tmux_substrate.WORKER_KIND
            async with TestClient(TestServer(app)) as client:
                created = await client.post(
                    "/api/terminal/sessions", json={"sandbox": "none"}
                )
                terminal_id = (await created.json())["session_id"]
                async with client.ws_connect(f"/api/ws/terminal/{terminal_id}"):
                    listed = await client.get("/api/terminal/sessions")
                    rows = (await listed.json())["sessions"]
                    assert all(
                        row["session_id"] != worker_name.removeprefix("gideon-")
                        for row in rows
                    )
                    refused = await client.delete(
                        "/api/terminal/sessions/deadbeef0000@none"
                    )
                    assert refused.status == 409
                assert (
                    await client.delete(f"/api/terminal/sessions/{terminal_id}")
                ).status == 200
                assert await tmux_substrate.has_session(worker_name)
        finally:
            await tmux_substrate.kill_session(worker_name)

    asyncio.run(exercise())


@pytest.mark.skipif(
    shutil.which("tmux") is None, reason="requires the real tmux executable"
)
def test_completed_durable_step_is_not_repeated_by_bare_fallback(tmp_path, monkeypatch):
    from gideon.automation.workflows.provisioning import run_step
    from gideon.engine import tmux_substrate

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"agent": {"durable_sessions": True}}), encoding="utf-8"
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "completed-once"
    name = tmux_substrate.durable_session_name("project", "one-shot", "step")

    async def exercise():
        try:
            result = await run_step(
                f"/bin/sh -c 'printf x >> {marker}'",
                tmp_path,
                durable_session=name,
                timeout=10,
            )
            assert result[0] is True
            assert marker.read_text(encoding="utf-8") == "x"
        finally:
            await tmux_substrate.kill_session(name)

    asyncio.run(exercise())
