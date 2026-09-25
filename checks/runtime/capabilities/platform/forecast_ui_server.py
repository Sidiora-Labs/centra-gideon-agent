import asyncio

from aiohttp import web

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers.capabilities_forecast import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    TriggerStore().upsert(
        Trigger(
            id="schedule:forecast",
            name="Forecast audit",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 600},
            workflow={"ref": "audit-workflow"},
        )
    )
    TriggerStore().upsert(
        Trigger(
            id="manual-one",
            name="Manual task",
            kind="manual",
            workflow={"ref": "audit-workflow"},
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
