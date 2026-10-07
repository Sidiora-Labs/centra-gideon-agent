"""Actual dashboard handlers served on an isolated loopback port for UI journeys."""

import asyncio
import json
import signal
import time

from aiohttp import web

from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState

from gideon.interfaces.dashboard.handlers.capabilities_comparisons import (
    register as register_comparisons,
)
from gideon.interfaces.dashboard.handlers.capabilities_gsd import (
    register as register_gsd,
)
from gideon.interfaces.dashboard.handlers.capabilities_harnesses import (
    register as register_harnesses,
)
from gideon.interfaces.dashboard.handlers.capabilities_ownership import (
    register as register_ownership,
)
from gideon.interfaces.dashboard.handlers.capabilities_platform import register
from gideon.interfaces.dashboard.handlers.capabilities_references import (
    register as register_references,
)
from gideon.interfaces.dashboard.handlers.prompts import api_prompt_syntax
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    app = web.Application(middlewares=[token_auth_middleware()])
    app["state"] = state
    register(app)
    register_harnesses(app)
    register_comparisons(app)
    register_references(app)
    register_ownership(app)
    register_gsd(app)
    app.router.add_get("/api/prompts/syntax", api_prompt_syntax)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps({"url": f"http://127.0.0.1:{runner.addresses[0][1]}", "token": generate_token("platform-owner")}),
        flush=True,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        pending = list(state._background_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await state.sessions.close_all()
        if state._knowledge_store is not None:
            state._knowledge_store.close()
        loop.remove_signal_handler(signal.SIGTERM)


asyncio.run(main())
