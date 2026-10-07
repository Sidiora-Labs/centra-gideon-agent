import asyncio
import json
import os

from aiohttp import web
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware

from gideon.interfaces.dashboard.handlers.capabilities_creative_direction import (
    register,
)
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.works import WorkStore


async def main():
    home = os.environ["GIDEON_HOME"]
    works = WorkStore(home)
    work = works.create(
        {
            "request_id": "direction-ui-work",
            "title": "Source manuscript",
            "kind": "work",
            "prompt": "",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    work = works.draft(
        work["id"],
        {
            "request_id": "direction-ui-draft",
            "revision": 1,
            "text": "A real manuscript source.",
            "note": "",
        },
    )["work"]
    store = DirectionStore(home)

    app = web.Application(middlewares=[token_auth_middleware()])
    app["creative_direction_factory"] = lambda: store
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{runner.addresses[0][1]}",
                "token": generate_token("direction_ui_server-owner"),
                "work_id": work["id"],
                "revision": work["revision"],
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
