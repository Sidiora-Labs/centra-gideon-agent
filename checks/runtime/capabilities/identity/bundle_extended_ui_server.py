"""Two real isolated canonical stores for encrypted identity console transfers."""

import asyncio
import sys
from pathlib import Path

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_identity_bundles import register
from gideon.workspace.capabilities.identity.store import StoryStore
from gideon.workspace.capabilities.identity.twin import TwinStore


async def main():
    home = Path(sys.argv[1])
    twin = TwinStore(home / "source/capabilities/identity/twin.sqlite3")
    twin.save_document(
        title="Source private story",
        text="Private formative event",
        private=True,
        expected_revision=0,
    )
    stories = StoryStore(home / "source/capabilities/identity/stories.sqlite3")
    story = stories.create(
        prompt="Origin?", theme="past", text="Original answer", request_id="origin"
    )
    stories.update(
        story["id"],
        expected_revision=1,
        prompt="Origin?",
        theme="past",
        text="Edited answer",
    )
    runners, endpoints = [], []
    for name in ("source", "destination"):
        app = web.Application()
        register(app, home=home / name)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        endpoints.append(
            f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/identity/bundles"
        )
    print("|".join(endpoints), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        for runner in runners:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
