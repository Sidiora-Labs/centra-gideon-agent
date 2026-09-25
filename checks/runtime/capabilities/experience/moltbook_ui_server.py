import asyncio
import json
import os

from aiohttp import web

from gideon.integrations.llm.credentials import CredentialStore
from gideon.interfaces.dashboard.handlers.capabilities_experience_moltbook import (
    register,
)
from gideon.workspace.capabilities.experience.moltbook import MoltbookAdapter

KEY = "moltbook_ui_protocol_secret"


async def main():
    @web.middleware
    async def protocol_auth(request, handler):
        if request.headers.get("Authorization") != "Bearer " + KEY:
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    protocol = web.Application(middlewares=[protocol_auth])
    protocol.router.add_get(
        "/api/v1/agents/me",
        lambda request: web.json_response(
            {"id": "ui-agent", "name": "UI Molty", "status": "claimed"}
        ),
    )
    protocol.router.add_get(
        "/api/v1/agents/status",
        lambda request: web.json_response({"status": "claimed"}),
    )
    protocol.router.add_get(
        "/api/v1/feed",
        lambda request: web.json_response(
            {
                "posts": [
                    {
                        "id": "ui-post",
                        "title": "Wire protocol post",
                        "content": "Fetched from the local Moltbook protocol server.",
                        "author": {"name": "ProtocolMolty"},
                    }
                ]
            }
        ),
    )

    async def post(request):
        return web.json_response(
            {
                "post": {"id": "created-post"},
                "verification_required": True,
                "verification": {
                    "code": "ui-verify",
                    "challenge": "not retained",
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            },
            status=201,
        )

    protocol.router.add_post("/api/v1/posts", post)
    protocol.router.add_post(
        "/api/v1/posts/{post}/comments",
        lambda request: web.json_response({"id": "created-comment"}, status=201),
    )
    protocol_runner = web.AppRunner(protocol)
    await protocol_runner.setup()
    protocol_site = web.TCPSite(protocol_runner, "127.0.0.1", 0)
    await protocol_site.start()
    protocol_port = protocol_site._server.sockets[0].getsockname()[1]
    home = os.environ["GIDEON_HOME"]
    credentials = CredentialStore(home)
    credentials.put("moltbook-ui", {"type": "api_key", "value": KEY})
    adapter = MoltbookAdapter(home, f"http://127.0.0.1:{protocol_port}/api/v1")
    adapter.configure({"credential_ref": "moltbook-ui"})

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        return await handler(request)

    gateway = web.Application(middlewares=[owner])
    gateway["moltbook_factory"] = lambda: adapter
    register(gateway)
    runner = web.AppRunner(gateway)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"port": site._server.sockets[0].getsockname()[1]}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await protocol_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
