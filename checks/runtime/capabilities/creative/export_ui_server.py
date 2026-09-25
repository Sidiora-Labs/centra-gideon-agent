import json
import os

from aiohttp import web

from gideon.interfaces.dashboard.handlers.capabilities_creative_exports import register
from gideon.workspace.capabilities.creative.exports import ManuscriptExports
from gideon.workspace.capabilities.creative.works import WorkStore


async def main():
    home = os.environ["GIDEON_HOME"]
    works = WorkStore(home)
    work = works.create(
        {
            "request_id": "ui-work",
            "title": "UI Manuscript",
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
            "request_id": "ui-draft",
            "revision": work["revision"],
            "text": "# Opening\n\nActual UI export manuscript.",
            "note": "selected",
        },
    )["work"]
    exports = ManuscriptExports(home, works=works)

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["creative_exports_factory"] = lambda: exports
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "port": site._server.sockets[0].getsockname()[1],
                "work_id": work["id"],
                "revision": work["revision"],
            }
        ),
        flush=True,
    )
    try:
        await __import__("asyncio").Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    __import__("asyncio").run(main())
