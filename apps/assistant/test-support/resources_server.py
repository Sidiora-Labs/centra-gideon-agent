"""Single-owner native resource fixture for assistant resource delivery journeys."""

import asyncio
import json
import os
import sys
from pathlib import Path

from aiohttp import web


PASSWORD = "correct-horse-battery-staple"


async def main(origin: str) -> None:
    home = Path(os.environ["GIDEON_HOME"]).resolve()
    home.mkdir(parents=True, exist_ok=True)

    from gideon.cognition.history import ConversationLog
    from gideon.core.config.loader import AppConfig, outbox_dir
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard import token_auth
    from gideon.interfaces.dashboard.chat import (
        api_chat,
        api_chat_session_create,
        api_chat_session_detail,
        api_chat_sessions,
    )
    from gideon.interfaces.dashboard.handlers import auth, files
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.interfaces.dashboard.ws import api_ws
    from gideon.security.auth import credentials
    from gideon.workspace.artifacts.handlers import register_artifact_routes

    (home / "config.json").write_text(json.dumps({
        "auth": {"login_enabled": True},
        "dashboard": {"username": "resource-owner"},
    }), encoding="utf-8")
    credentials.set_password("resource-owner", PASSWORD)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()

    outbox = outbox_dir()
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "owned-download.txt").write_text(
        "Native owner-scoped download fixture.\n", encoding="utf-8"
    )

    config = AppConfig()
    state = ConsoleState(
        sessions=ConversationDirectory(config),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "history"),
        owner_id="resource-owner",
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
    app.router.add_get("/api/outbox/{filename}", files.api_outbox_download)
    register_artifact_routes(app)

    control = web.Application()

    async def fixture_state(_request: web.Request) -> web.Response:
        return web.json_response({"owner": "resource-owner", "download": "owned-download.txt"})

    control.router.add_get("/state", fixture_state)
    api_runner = web.AppRunner(app)
    control_runner = web.AppRunner(control)
    await api_runner.setup()
    await control_runner.setup()
    api_site = web.TCPSite(api_runner, "127.0.0.1", 0)
    control_site = web.TCPSite(control_runner, "127.0.0.1", 0)
    await api_site.start()
    await control_site.start()
    print(json.dumps({
        "api_port": api_site._server.sockets[0].getsockname()[1],
        "control_port": control_site._server.sockets[0].getsockname()[1],
        "username": "resource-owner",
        "password": PASSWORD,
        "download": "owned-download.txt",
    }), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await api_runner.cleanup()
        await control_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
