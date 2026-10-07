"""Real application used by the console interaction test."""

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_identity_goal_plans import (
    register,
)
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    from gideon.workspace.capabilities.identity.goals import GoalStore

    goals = GoalStore(Path(sys.argv[1]) / "capabilities/identity/goals.sqlite3")
    goals.save_goal(title="Learn astronomy", request_id="parent")
    goals.save_goal(title="Build telescope", request_id="child")
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app, home=Path(sys.argv[1]))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(
        json.dumps(
            {
                "endpoint": f"http://127.0.0.1:{port}/api/capabilities/identity/goal-plans",
                "token": generate_token("identity-test-owner"),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
