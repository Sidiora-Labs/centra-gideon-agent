import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import os

from aiohttp import web

from checks.runtime.capabilities.platform.test_remote_sessions import ProtocolRuntime
from gideon.integrations.llm.credentials import CredentialStore
from gideon.interfaces.dashboard.handlers.capabilities_platform_remote_sessions import (
    register,
)
from gideon.workspace.capabilities.platform.remote_sessions import RemoteSessionBridge


async def main():
    home = Path(os.environ["GIDEON_HOME"])
    credentials = CredentialStore(home)
    credentials.save(
        {"browser-token": {"type": "static_token", "value": "actual-secret"}}
    )
    protocol = ProtocolRuntime()
    remote = web.Application()
    remote.router.add_post("/tools/invoke", protocol.tool)
    remote.router.add_post("/v1/responses", protocol.responses)
    remote_runner = web.AppRunner(remote)
    await remote_runner.setup()
    remote_site = web.TCPSite(remote_runner, "127.0.0.1", 0)
    await remote_site.start()
    remote_origin = (
        f"http://127.0.0.1:{remote_site._server.sockets[0].getsockname()[1]}"
    )
    bridge = RemoteSessionBridge(home, credentials=credentials)
    bridge.store.save_connection(
        {
            "id": "browser",
            "label": "Browser runtime",
            "base_url": remote_origin,
            "credential_ref": "browser-token",
            "agent_id": "main",
        }
    )

    @web.middleware
    async def owner(request, handler):
        request["user"] = "browser-owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["remote_session_bridge"] = bridge
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "origin": f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}",
                "remote_origin": remote_origin,
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await remote_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
