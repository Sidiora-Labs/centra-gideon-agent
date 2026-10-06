"""Real authenticated customer browser lifecycle and native Chromium control."""

from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import subprocess
import time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_owned_browser_readiness_preview_and_exclusive_control(
    tmp_path, monkeypatch
):
    if not any(
        shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome")
    ):
        pytest.fail("Real Chromium is required for customer browser control acceptance")
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    from gideon.cognition.history import ConversationLog
    from gideon.engine.agents.native.approval import ApprovalGate
    from gideon.integrations.browse.customer_control import ControlDenied
    from gideon.integrations.browse.grant import request_grant
    from gideon.integrations.browse.killswitch import engage, release
    from gideon.interfaces.dashboard import token_auth
    from gideon.interfaces.dashboard.handlers.browser_sessions import (
        CONTROL_KEY,
        register_browser_session_routes,
    )
    from gideon.interfaces.dashboard.state import ConsoleState

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "auth": {"login_enabled": True},
                "security": {"egress": {"allow_hosts": ["127.0.0.1"]}},
            }
        )
    )
    hits: list[str] = []
    slow_seen, slow_release, slow_completed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def page_response(request):
        hits.append(request.path)
        if request.path == "/slow":
            slow_seen.set()
            await slow_release.wait()
            response = web.StreamResponse(headers={"Content-Type": "text/html"})
            try:
                await response.prepare(request)
                await response.write(b"<title>Late page</title>")
                await response.write_eof()
            except ConnectionResetError:
                pass
            finally:
                slow_completed.set()
            return response
        if request.path == "/redirect":
            raise web.HTTPFound(f"http://127.0.0.2:{blocked_port}/redirect-blocked")
        if request.path == "/socket-page":
            return web.Response(
                text=(
                    "<title>Socket page</title><script>"
                    f'let socket = new WebSocket("ws://127.0.0.1:{owned_port}/ws");'
                    "socket.onmessage = event => { window.socketReply = event.data; };"
                    "</script>"
                ),
                content_type="text/html",
            )
        return web.Response(
            text=(
                f'<title>Owned page</title><input id="entry">'
                f'<img src="http://127.0.0.2:{blocked_port}/blocked">'
            ),
            content_type="text/html",
        )

    async def socket_response(request):
        hits.append("/ws")
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_str("real websocket")
        await socket.close()
        return socket

    async def blocked_response(request):
        hits.append("blocked")
        return web.Response(text="forbidden destination")

    owned_app = web.Application()
    owned_app.router.add_get("/ws", socket_response)
    owned_app.router.add_get("/{tail:.*}", page_response)
    blocked_app = web.Application()
    blocked_app.router.add_get("/{tail:.*}", blocked_response)
    owned_runner, blocked_runner = web.AppRunner(owned_app), web.AppRunner(blocked_app)
    await owned_runner.setup()
    await blocked_runner.setup()
    owned_site = web.TCPSite(owned_runner, "127.0.0.1", 0)
    blocked_site = web.TCPSite(blocked_runner, "127.0.0.2", 0)
    await owned_site.start()
    await blocked_site.start()
    owned_port = owned_site._server.sockets[0].getsockname()[1]
    blocked_port = blocked_site._server.sockets[0].getsockname()[1]
    cert, key = tmp_path / "browser.crt", tmp_path / "browser.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    secure_site = web.TCPSite(owned_runner, "127.0.0.1", 0, ssl_context=tls)
    await secure_site.start()
    secure_port = secure_site._server.sockets[0].getsockname()[1]
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    log = ConversationLog(base_dir=tmp_path / "conversations")
    log.append("dashboard:browser-chat", "user", "Open my browser")
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10129)])
    app["state"] = ConsoleState(None, time.time(), conversation_log=log)
    app["port"] = 10129
    app["allowed_origins"] = {"http://localhost:10129"}
    register_browser_session_routes(app, store_path=tmp_path / "sessions.sqlite3")
    alice = token_auth.generate_token("alice", ttl_seconds=3600)
    bob = token_auth.generate_token("bob", ttl_seconds=3600)
    cookie = lambda token: {"gideon_token_10129": token}

    async with TestClient(
        TestServer(app), cookie_jar=aiohttp.DummyCookieJar()
    ) as client:
        created = await client.post(
            "/api/browser/sessions",
            json={"conversation_id": "browser-chat"},
            cookies=cookie(alice),
        )
        assert created.status == 201
        row = (await created.json())["session"]
        session_id = row["id"]
        assert row["status"] == "reserved"
        assert not (tmp_path / "profiles" / session_id).exists()
        base = f"/api/browser/sessions/{session_id}"
        cross_preview = await client.get(base + "/preview", cookies=cookie(bob))
        absent_preview = await client.get(
            "/api/browser/sessions/absent/preview", cookies=cookie(bob)
        )
        assert cross_preview.status == absent_preview.status == 404
        assert await cross_preview.text() == await absent_preview.text()
        denied = await client.post(
            base + "/start", json={"expected_version": 1}, cookies=cookie(bob)
        )
        assert denied.status == 404

        started = await client.post(
            base + "/start", json={"expected_version": 1}, cookies=cookie(alice)
        )
        assert started.status == 200, await started.text()
        active = (await started.json())["session"]
        assert active["status"] == "active" and active["version"] == 2
        assert active["control_holder"] == "assistant"
        assert not any(
            key in str(active).lower() for key in ("cdp", "profile", "websocket")
        )
        assert (tmp_path / "profiles" / session_id / "DevToolsActivePort").exists()
        preview = await client.get(base + "/preview", cookies=cookie(alice))
        assert preview.status == 200
        assert (await preview.read()).startswith(b"\x89PNG\r\n\x1a\n")
        assert preview.headers["X-Browser-Control"] == "assistant"
        assert preview.headers["X-Browser-Version"] == "2"
        assert float(preview.headers["X-Browser-Timestamp"]) > 0
        assert preview.headers["Cache-Control"] == "no-store"

        grant_gate = ApprovalGate()
        grant_task = asyncio.create_task(
            request_grant(
                task="Read browser page",
                scope=("127.0.0.1",),
                gate=grant_gate,
                request_id="customer-browser-test",
                timeout=5,
                bound_device_id=session_id,
            )
        )
        while not grant_gate.approve("customer-browser-test"):
            await asyncio.sleep(0.005)
        live_grant = await grant_task
        assert live_grant.granted
        controller = app[CONTROL_KEY]
        navigated = await controller.navigate(
            session_id,
            "local",
            "alice",
            2,
            f"http://127.0.0.1:{owned_port}/page",
            actor="assistant",
            grant=live_grant,
        )
        assert navigated.version == 3
        engine = controller._engines[session_id]
        unauthenticated_reader, unauthenticated_writer = await asyncio.open_connection(
            "127.0.0.1",
            engine.proxy.port,
        )
        unauthenticated_writer.write(
            f"GET http://127.0.0.1:{owned_port}/unrelated HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{owned_port}\r\n\r\n".encode()
        )
        await unauthenticated_writer.drain()
        assert (await unauthenticated_reader.readline()).startswith(b"HTTP/1.1 407 ")
        unauthenticated_writer.close()
        await unauthenticated_writer.wait_closed()
        assert "/unrelated" not in hits
        for _ in range(100):
            if await engine.page._eval("document.title") == "Owned page":
                break
            await asyncio.sleep(0.05)
        assert await engine.page._eval("document.title") == "Owned page"
        assert "/page" in hits and "blocked" not in hits
        for _ in range(100):
            audit_path = tmp_path / "security_events.jsonl"
            if audit_path.exists() and any(
                row.get("operation") == "proxy_connect"
                and row.get("metadata", {}).get("host") == "127.0.0.2"
                for row in (
                    json.loads(line) for line in audit_path.read_text().splitlines()
                )
            ):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Denied image subresource did not reach the enforcing proxy")
        await engine.page._eval("document.querySelector('#entry').focus()")
        entered = await controller.input(
            session_id,
            "local",
            "alice",
            3,
            "text",
            "approved",
            actor="assistant",
            grant=live_grant,
        )
        assert entered.version == 4
        assert (
            await engine.page._eval("document.querySelector('#entry').value")
            == "approved"
        )
        await engine.transport.send(
            "Security.setIgnoreCertificateErrors", {"ignore": True}
        )
        secure_url = f"https://127.0.0.1:{secure_port}/secure"
        assert (await engine.gate.navigate(secure_url)).ok
        for _ in range(100):
            if "/secure" in hits and await engine.page.current_url() == secure_url:
                break
            await asyncio.sleep(0.05)
        assert "/secure" in hits and await engine.page.current_url() == secure_url
        assert (
            await engine.gate.navigate(f"http://127.0.0.1:{owned_port}/socket-page")
        ).ok
        for _ in range(100):
            if await engine.page._eval("window.socketReply || ''") == "real websocket":
                break
            await asyncio.sleep(0.05)
        assert "/ws" in hits
        assert await engine.page._eval("window.socketReply") == "real websocket"
        denied_target = f"http://127.0.0.2:{blocked_port}/blocked"
        with pytest.raises(ControlDenied):
            await controller.navigate(
                session_id,
                "local",
                "alice",
                4,
                denied_target,
                actor="assistant",
                grant=live_grant,
            )
        assert "blocked" not in hits

        prior_url = await engine.page.current_url()
        prior_title = await engine.page._eval("document.title")
        slow_action = asyncio.create_task(
            controller.navigate(
                session_id,
                "local",
                "alice",
                4,
                f"http://127.0.0.1:{owned_port}/slow",
                actor="assistant",
                grant=live_grant,
            )
        )
        await asyncio.wait_for(slow_seen.wait(), 5)
        assert not slow_action.done()
        takeover = await asyncio.wait_for(
            client.post(
                base + "/control/takeover",
                json={"expected_version": 4},
                cookies=cookie(alice),
            ),
            1,
        )
        assert await engine.page.current_url() == prior_url
        slow_release.set()
        await asyncio.wait_for(slow_completed.wait(), 5)
        with pytest.raises(ControlDenied, match="changed"):
            await slow_action
        assert takeover.status == 200
        taken = (await takeover.json())["session"]
        assert (taken["control_holder"], taken["version"]) == ("customer", 5)
        assert await engine.page.current_url() == prior_url
        assert await engine.page._eval("document.title") == prior_title
        with pytest.raises(ControlDenied, match="control"):
            await controller.navigate(
                session_id,
                "local",
                "alice",
                5,
                f"http://127.0.0.1:{owned_port}/page",
                actor="assistant",
                grant=live_grant,
            )
        stale = await client.post(
            base + "/input",
            json={"expected_version": 4, "command": "scroll", "value": "down"},
            cookies=cookie(alice),
        )
        assert stale.status == 409
        no_grant = await client.post(
            base + "/control/handback",
            json={"expected_version": 4},
            cookies=cookie(alice),
        )
        assert no_grant.status == 409
        scroll = await client.post(
            base + "/input",
            json={"expected_version": 5, "command": "scroll", "value": "down"},
            cookies=cookie(alice),
        )
        assert scroll.status == 200, await scroll.text()
        assert (await scroll.json())["session"]["version"] == 6
        egress = await client.post(
            base + "/navigate",
            json={"expected_version": 6, "url": denied_target},
            cookies=cookie(alice),
        )
        assert egress.status == 409
        assert (await (await client.get(base, cookies=cookie(alice))).json())[
            "session"
        ]["version"] == 6
        redirect = await client.post(
            base + "/navigate",
            json={
                "expected_version": 6,
                "url": f"http://127.0.0.1:{owned_port}/redirect",
            },
            cookies=cookie(alice),
        )
        assert redirect.status == 200, await redirect.text()
        assert (await redirect.json())["session"]["version"] == 7
        redirect_target = f"http://127.0.0.2:{blocked_port}/redirect-blocked"
        for _ in range(100):
            audit_path = tmp_path / "security_events.jsonl"
            if audit_path.exists() and any(
                event.get("operation") == "proxy_connect"
                and event.get("outcome") == "denied"
                and event.get("metadata", {}).get("url") == redirect_target
                and event.get("metadata", {}).get("phase") == "proxy"
                for event in (
                    json.loads(line) for line in audit_path.read_text().splitlines()
                )
            ):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Redirect destination produced no enforcing proxy SEL denial")
        assert "/redirect" in hits and "blocked" not in hits
        handback = await client.post(
            base + "/control/handback",
            json={"expected_version": 7},
            cookies=cookie(alice),
        )
        assert handback.status == 200
        returned = (await handback.json())["session"]
        assert returned["control_holder"] == "assistant" and returned["version"] == 8
        with pytest.raises(ControlDenied) as denied_navigation:
            await controller.navigate(
                session_id,
                "local",
                "alice",
                8,
                denied_target,
                actor="assistant",
                grant=live_grant,
            )
        assert "control" not in str(denied_navigation.value).lower()
        engage("test stop")
        try:
            with pytest.raises(ControlDenied):
                await controller.input(
                    session_id,
                    "local",
                    "alice",
                    8,
                    "text",
                    "stopped",
                    actor="assistant",
                    grant=live_grant,
                )
        finally:
            release()
        user_after_handback = await client.post(
            base + "/input",
            json={"expected_version": 8, "command": "scroll", "value": "up"},
            cookies=cookie(alice),
        )
        assert user_after_handback.status == 409
        closed = await client.post(
            base + "/close", json={"expected_version": 8}, cookies=cookie(alice)
        )
        assert closed.status == 200
        assert (await closed.json())["session"]["status"] == "closed"
        assert (
            await client.get(base + "/preview", cookies=cookie(alice))
        ).status == 503
        reopened = await client.post(
            base + "/reopen", json={"expected_version": 9}, cookies=cookie(alice)
        )
        assert reopened.status == 200
        assert (await reopened.json())["session"]["id"] == session_id
        restarted = await client.post(
            base + "/start", json={"expected_version": 10}, cookies=cookie(alice)
        )
        assert restarted.status == 200, await restarted.text()
        restarted_session = (await restarted.json())["session"]
        assert (
            restarted_session["status"] == "active"
            and restarted_session["version"] == 11
        )
        new_preview = await client.get(base + "/preview", cookies=cookie(alice))
        assert new_preview.status == 200
        assert (await new_preview.read()).startswith(b"\x89PNG\r\n\x1a\n")
    token_auth.revoke_all_sessions()
    await owned_runner.cleanup()
    await blocked_runner.cleanup()


def test_existing_reservation_schema_upgrades_for_active_state(tmp_path):
    import sqlite3

    from gideon.integrations.browse.customer_sessions import CustomerBrowserSessionStore

    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE customer_browser_sessions (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL, owner_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL, canonical_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('reserved', 'closed', 'error')),
        version INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(account_id, canonical_key))""")
    db.execute(
        "INSERT INTO customer_browser_sessions VALUES ('s','local','alice','c','c','reserved',1,1,1)"
    )
    db.commit()
    db.close()
    store = CustomerBrowserSessionStore(path)
    assert (
        store.transition(
            "s", "local", "alice", expected_version=1, action="activate"
        ).status
        == "active"
    )
