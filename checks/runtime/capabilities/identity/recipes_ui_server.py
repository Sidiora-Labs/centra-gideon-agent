"""Real application used by the console interaction test."""
import asyncio
from pathlib import Path
import sys
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_identity_recipes import register


async def main():
    from gideon.workspace.capabilities.identity.goals import GoalStore
    GoalStore(Path(sys.argv[1]) / "capabilities/identity/goals.sqlite3").save_goal(title="Build telescope", request_id="seed-goal")
    app = web.Application()
    register(app, home=Path(sys.argv[1]))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(f"http://127.0.0.1:{port}/api/capabilities/identity/recipes", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
