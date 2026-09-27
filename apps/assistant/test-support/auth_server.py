import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from aiohttp import web

import gideon.core.config.loader as loader
from gideon.interfaces.dashboard import session_store, token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.security.auth import credentials, totp


PASSWORD = "correct-horse-battery-staple"
COOKIE_PORT = 10000


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-assistant-auth-") as temporary_home:
        home = Path(temporary_home)
        loader.config_dir = lambda: home
        credentials.config_dir = lambda: home
        session_store.config_dir = lambda: home
        token_auth.use_persistent_secret()
        token_auth.revoke_all_sessions()
        auth.reset_lockouts()
        secret = totp.new_secret()
        os.environ[credentials.TOTP_SECRET_KEY] = secret

        def set_mode(mode: str) -> None:
            if mode not in {"password", "totp", "local", "other"}:
                raise ValueError("Unsupported authentication mode")
            if mode != "local":
                credentials.set_password("owner-b" if mode == "other" else "owner-a", PASSWORD)
            config = {"auth": {"login_enabled": mode != "local", "require_totp": mode == "totp"}}
            (home / "config.json").write_text(json.dumps(config), encoding="utf-8")

        set_mode("password")
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=COOKIE_PORT)])
        app["port"] = COOKIE_PORT
        app["allowed_origins"] = {origin}
        app.router.add_get("/api/auth/status", auth.api_login_status)
        app.router.add_get("/api/auth/session", auth.api_auth_session)
        app.router.add_post("/api/auth/login", auth.api_auth_login)
        app.router.add_post("/api/auth/logout", auth.api_auth_logout)

        async def control_mode(request: web.Request) -> web.Response:
            payload = await request.json()
            try:
                set_mode(payload["mode"])
            except (KeyError, ValueError):
                return web.json_response({"error": "invalid mode"}, status=400)
            return web.json_response({"ok": True})

        async def control_code(_request: web.Request) -> web.Response:
            return web.json_response({"code": totp.code_now(secret)})

        async def control_token(_request: web.Request) -> web.Response:
            return web.json_response({"token": token_auth.generate_token("owner-local")})

        async def control_revoke(_request: web.Request) -> web.Response:
            token_auth.revoke_all_sessions()
            return web.json_response({"ok": True})

        control = web.Application()
        control.router.add_post("/mode", control_mode)
        control.router.add_get("/code", control_code)
        control.router.add_get("/token", control_token)
        control.router.add_post("/revoke", control_revoke)
        runner = web.AppRunner(app)
        control_runner = web.AppRunner(control)
        await runner.setup()
        await control_runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        control_site = web.TCPSite(control_runner, "127.0.0.1", 0)
        await site.start()
        await control_site.start()
        port = site._server.sockets[0].getsockname()[1]
        control_port = control_site._server.sockets[0].getsockname()[1]
        print(json.dumps({"api_port": port, "control_port": control_port}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await control_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
