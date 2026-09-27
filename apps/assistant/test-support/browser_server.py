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
            json.dumps({"auth": {"login_enabled": True},
                        "security": {"egress": {"allow_hosts": ["127.0.0.1"]}}}), encoding="utf-8"
        )

        from gideon.cognition.history import ConversationLog
        from gideon.interfaces.dashboard import token_auth
        from gideon.interfaces.dashboard.handlers import auth
        from gideon.interfaces.dashboard.handlers.browser_sessions import CONTROL_KEY, register_browser_session_routes
        from gideon.interfaces.dashboard.state import ConsoleState
        from gideon.integrations.browse.customer_control import ControlDenied
        from gideon.integrations.browse.grant import request_grant
        from gideon.integrations.browse.killswitch import engage, release
        from gideon.engine.agents.native.approval import ApprovalGate
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

        hits: list[str] = []
        async def owned_page(request: web.Request) -> web.Response:
            hits.append(request.path)
            return web.Response(text='<title>Owned browser page</title><input id="entry" autofocus><p>Real local page</p>', content_type="text/html")

        async def blocked_page(request: web.Request) -> web.Response:
            hits.append("blocked")
            return web.Response(text="Blocked destination", content_type="text/html")

        owned = web.AppRunner(web.Application())
        owned.app.router.add_get("/{tail:.*}", owned_page)
        blocked = web.AppRunner(web.Application())
        blocked.app.router.add_get("/{tail:.*}", blocked_page)
        await owned.setup()
        await blocked.setup()
        owned_site = web.TCPSite(owned, "127.0.0.1", 0)
        blocked_site = web.TCPSite(blocked, "127.0.0.2", 0)
        await owned_site.start()
        await blocked_site.start()
        owned_port = owned_site._server.sockets[0].getsockname()[1]
        blocked_port = blocked_site._server.sockets[0].getsockname()[1]

        async def fixture_state(request: web.Request) -> web.Response:
            if request.get("user") != "browser-owner":
                raise web.HTTPUnauthorized()
            audit = home / "security_events.jsonl"
            events = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
            return web.json_response({"hits": hits, "events": events})

        async def assistant_action(request: web.Request) -> web.Response:
            if request.get("user") != "browser-owner":
                raise web.HTTPUnauthorized()
            body = await request.json()
            session_id = body["session_id"]
            version = body["expected_version"]
            gate = ApprovalGate()
            request_id = f"ui-{time.time_ns()}"
            grant_task = asyncio.create_task(request_grant(
                task="Read browser page", scope=("127.0.0.1",), gate=gate,
                request_id=request_id, timeout=5, bound_device_id=session_id,
            ))
            while not gate.approve(request_id):
                await asyncio.sleep(0.005)
            grant = await grant_task
            try:
                session = await app[CONTROL_KEY].navigate(
                    session_id, "local", "browser-owner", version,
                    f"http://127.0.0.1:{owned_port}/page", actor="assistant", grant=grant,
                )
            except ControlDenied as exc:
                raise web.HTTPConflict(text=str(exc)) from None
            return web.json_response({"session": session.public()})

        async def kill(request: web.Request) -> web.Response:
            if request.get("user") != "browser-owner":
                raise web.HTTPUnauthorized()
            body = await request.json()
            if body.get("enabled") is True:
                engage("browser UI test")
            elif body.get("enabled") is False:
                release()
            else:
                raise web.HTTPBadRequest()
            return web.json_response({"stopped": bool(body["enabled"])})

        async def disconnect(request: web.Request) -> web.Response:
            if request.get("user") != "browser-owner":
                raise web.HTTPUnauthorized()
            body = await request.json()
            engine = app[CONTROL_KEY]._engines.get(body["session_id"])
            if engine is None:
                raise web.HTTPNotFound()
            await engine.close()
            return web.json_response({"disconnected": True})

        app.router.add_get("/test/browser/state", fixture_state)
        app.router.add_post("/test/browser/assistant-action", assistant_action)
        app.router.add_post("/test/browser/kill", kill)
        app.router.add_post("/test/browser/disconnect", disconnect)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({"api_port": port, "other_token": other_token,
                          "owned_url": f"http://127.0.0.1:{owned_port}/page",
                          "blocked_url": f"http://127.0.0.2:{blocked_port}/blocked"}), flush=True)

        try:
            await asyncio.Event().wait()
        finally:
            release()
            await runner.cleanup()
            await owned.cleanup()
            await blocked.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
