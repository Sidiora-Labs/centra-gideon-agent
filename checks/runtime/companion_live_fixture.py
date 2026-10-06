"""Loopback-only native task/WS fixture for rendered companion liveness checks."""

import asyncio
import json
import time

from aiohttp import web

from gideon.automation.loop import store as loop_store
from gideon.automation.loop.loop import Loop
from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import api_tasks_list
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.integrations.inbox_providers.native_source import set_dashboard_state
from gideon.interfaces.dashboard.handlers.loop_routes import api_loop_list
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.guardrails import incident


async def main():
    registry.register_provider(NativeTaskProvider())
    state = ConsoleState(sessions=None, start_time=time.time())
    set_dashboard_state(state)
    task = await registry.create_task(title="Original native task")
    loop_store.create(
        Loop(
            id="abcd1234",
            name="Native companion loop",
            kind="goal",
            task="Controlled local status",
            status="running",
            started_at=time.time(),
        )
    )
    reads = 0
    sockets = 0
    holding = False
    release = asyncio.Event()
    release.set()
    stopped = asyncio.Event()

    async def listing(request):
        nonlocal reads, holding
        reads += 1
        response = await api_tasks_list(request)
        if holding and request.query.get("status") == "open":
            holding = False
            await release.wait()
        return response

    async def websocket(request):
        nonlocal sockets
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.register_ws(ws)
        sockets += 1
        try:
            async for message in ws:
                pass
        finally:
            state.unregister_ws(ws)
        return ws

    async def control(request):
        nonlocal holding
        body = await request.json()
        action = body["action"]
        if action == "change":
            await registry.update_task(task.id, title=body["title"])
        elif action == "quiet-change":
            set_dashboard_state(None)
            try:
                await registry.update_task(task.id, title=body["title"])
            finally:
                set_dashboard_state(state)
        elif action == "burst":
            for _ in range(25):
                state.push_refresh("tasks")
        elif action == "drop":
            await state.close_all_ws()
        elif action == "incident-hold":
            incident.activate("Controlled local incident")
        elif action == "incident-resume":
            incident.resume()
        elif action == "hold":
            holding = True
            release.clear()
        elif action == "release":
            release.set()
        elif action == "stop":
            stopped.set()
        return web.json_response({"reads": reads, "sockets": sockets})

    app = web.Application()
    app.router.add_get("/api/tasks", listing)
    app.router.add_get("/api/loops", api_loop_list)
    app.router.add_get("/api/ws", websocket)
    app.router.add_post("/fixture/control", control)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"port": site._server.sockets[0].getsockname()[1]}), flush=True)
    try:
        await stopped.wait()
    finally:
        release.set()
        await state.close_all_ws()
        set_dashboard_state(None)
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
