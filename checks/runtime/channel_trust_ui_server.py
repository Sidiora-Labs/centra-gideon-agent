"""Authenticated native sender-trust fixture for Settings."""
import asyncio
import json
from aiohttp import web
from gideon.integrations import channel_trust
from gideon.interfaces.dashboard.handlers.channel_trust import api_channel_trust
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
async def main():
    channel_trust.allow_sender("telegram", "trusted-ui-sender", "Trusted operator")
    app = web.Application(middlewares=[token_auth_middleware()])
    app.router.add_get("/api/channels/trust", api_channel_trust)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"port": site._server.sockets[0].getsockname()[1], "token": generate_token("trust-test-owner")}), flush=True)
    await asyncio.Event().wait()
if __name__ == "__main__":
    asyncio.run(main())
