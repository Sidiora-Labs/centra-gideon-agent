"""Actual link-study HTTP application for console behavior tests."""

import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_links import register
from gideon.interfaces.dashboard.state import ConsoleState


async def main():
    home = Path(os.environ["GIDEON_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(str(home / "knowledge.db"))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    listener = web.TCPSite(runner, "127.0.0.1", 0)
    await listener.start()
    print(
        json.dumps({"port": listener._server.sockets[0].getsockname()[1]}), flush=True
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
