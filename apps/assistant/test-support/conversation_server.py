import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aiohttp import web

import gideon.core.config.loader as loader
from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard import session_store, token_auth
from gideon.interfaces.dashboard.chat import (
    api_chat,
    api_chat_session_create,
    api_chat_session_detail,
    api_chat_sessions,
)
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.ws import api_ws
from gideon.security.auth import credentials


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-conversation-") as directory:
        home = Path(directory)
        loader.config_dir = lambda: home
        credentials.config_dir = lambda: home
        session_store.config_dir = lambda: home
        (home / "config.json").write_text(
            json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
        )
        credentials.set_password("conversation-owner", "correct-horse-battery-staple")
        token_auth.use_persistent_secret()
        token_auth.revoke_all_sessions()

        state = ConsoleState(
            sessions=ConversationDirectory(AppConfig()),
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=home / "history"),
            owner_id="conversation-owner",
        )
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
        app["port"] = 10000
        app["allowed_origins"] = {origin}
        app["state"] = state
        app.router.add_get("/api/auth/status", auth.api_login_status)
        app.router.add_get("/api/auth/session", auth.api_auth_session)
        app.router.add_post("/api/auth/login", auth.api_auth_login)
        app.router.add_post("/api/auth/logout", auth.api_auth_logout)
        app.router.add_get("/api/ws", api_ws)
        app.router.add_get("/api/chat/sessions", api_chat_sessions)
        app.router.add_post("/api/chat/sessions", api_chat_session_create)
        app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
        app.router.add_post("/api/chat", api_chat)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({"api_port": port}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
