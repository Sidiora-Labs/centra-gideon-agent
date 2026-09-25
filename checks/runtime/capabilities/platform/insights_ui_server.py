import asyncio

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.interfaces.dashboard.handlers.capabilities_insights import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.workspace.capabilities.wellbeing.store import MeasurementStore


async def main():
    store = MeasurementStore(config_dir())
    for index, value in enumerate([80, 79], 1):
        store.create(
            dict(
                request_id=f"weight-{index}",
                kind="body_weight",
                observed_at=f"2026-09-0{index}T12:00:00Z",
                unit="kg",
                values={"weight": value},
                source="scale",
            )
        )
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
