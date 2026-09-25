import asyncio
from datetime import datetime, timedelta, timezone

from aiohttp import web

from gideon.integrations.llm.registry import ProviderEntry, get_default_registry
from gideon.interfaces.dashboard.handlers.capabilities_accounting import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.operations import usage_ledger as ledger


async def main():
    get_default_registry().register_entry(
        ProviderEntry(
            name="Work API", type="openai", model="model-a", credential="work-account"
        )
    )
    now = datetime.now(timezone.utc)
    ledger.record_turn(
        ledger.TurnUsage(
            ts=now.isoformat(),
            session_key="dashboard:ui",
            source="chat",
            agent="",
            provider="Work API",
            model="model-a",
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.25,
        )
    )
    ledger.record_turn(
        ledger.TurnUsage(
            ts=(now - timedelta(days=10)).isoformat(),
            session_key="dashboard:ui",
            source="loop",
            agent="",
            provider="Work API",
            model="model-a",
            input_tokens=50,
            output_tokens=10,
            priced=False,
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
