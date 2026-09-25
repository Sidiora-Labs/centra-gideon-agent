import asyncio

from aiohttp import web

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.interfaces.dashboard.handlers.capabilities_peers import (
    register as register_peers,
)
from gideon.interfaces.dashboard.handlers.capabilities_replication import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.workspace.capabilities.platform.peers import PeerStore


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    register_peers(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    identity = PeerStore().snapshot()["self"]
    PeerStore().put(
        identity["peer_id"],
        {
            "label": "Local integration peer",
            "endpoint": f"http://127.0.0.1:{port}",
            "public_key": identity["public_key"],
            "enabled": True,
            "send_categories": ["workspace.records"],
            "receive_categories": ["workspace.records"],
            "revision": 0,
        },
    )
    HierarchyStore().create_project(
        "Replicated project", brief="Canonical project source"
    )
    print(port, flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
