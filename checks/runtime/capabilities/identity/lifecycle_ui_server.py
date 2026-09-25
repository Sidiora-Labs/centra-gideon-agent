"""Isolated actual loop engine and HTTP application for console qualification."""
import asyncio
import sys
import time
from pathlib import Path
from aiohttp import web
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.automation.loop import store as loops
from gideon.automation.loop.loop import Loop
from gideon.automation.triggers.nudge import AutoNudgeService
from gideon.interfaces.dashboard.handlers.capabilities_identity_lifecycle import register


async def main():
    home = Path(sys.argv[1])
    loop = loops.create(Loop(id="", name="Identity research", kind="general", task="Review current anchors", agent="default"))
    service = AutoNudgeService(base_dir=home)
    await service.start()
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    app = web.Application()
    app["state"] = state
    register(app, home=home)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/identity/lifecycle#{loop.id}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        service.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
