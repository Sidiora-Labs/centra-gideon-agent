"""Native authenticated browser-session server for the assistant route test."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-browser-client-") as directory:
        home = Path(directory)
        os.environ["GIDEON_HOME"] = str(home)
        (home / "config.json").write_text(
            json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
        )

        from gideon.cognition.history import ConversationLog
        from gideon.interfaces.dashboard import token_auth
        from gideon.interfaces.dashboard.handlers import auth
        from gideon.interfaces.dashboard.handlers.browser_sessions import register_browser_session_routes
        from gideon.interfaces.dashboard.state import ConsoleState
        from gideon.security.auth import credentials

        credentials.set_password("browser-owner", "correct-horse-battery-staple")
        token_auth.use_persistent_secret()
        token_auth.revoke_all_sessions()
        auth.reset_lockouts()
        other_token = token_auth.generate_token("browser-other", ttl_seconds=3600)

        log = ConversationLog(base_dir=home / "history")
        log.append("dashboard:browser-chat", "user", "Open my browser")
        log.append("channel-thread", "user", "Channel conversation")
        log.append("collision", "user", "Bare conversation")
        log.append("dashboard:collision", "user", "Dashboard conversation")
        log.append("dashboard:lost-reply", "user", "Lost reservation reply")
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10021)])
        app["port"] = 10021
        app["allowed_origins"] = {origin}
        app["state"] = ConsoleState(None, time.time(), conversation_log=log)
        app.router.add_get("/api/auth/session", auth.api_auth_session)
        app.router.add_post("/api/auth/login", auth.api_auth_login)
        app.router.add_post("/api/auth/logout", auth.api_auth_logout)
        register_browser_session_routes(app, store_path=home / "browser.sqlite3")

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({"api_port": port, "other_token": other_token}), flush=True)

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
