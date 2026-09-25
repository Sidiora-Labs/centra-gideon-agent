import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, web

from gideon.integrations.llm.credentials import CredentialStore
from gideon.workspace.capabilities.platform.remote_sessions import RemoteSessionBridge, RemoteSessionError, RemoteSessionStore, _text


class ProtocolRuntime:
    def __init__(self):
        self.requests = []
        self.stream_closed = asyncio.Event()

    async def tool(self, request):
        self.requests.append(("tool", request.headers.get("Authorization"), await request.json()))
        if request.headers.get("Authorization") != "Bearer actual-secret":
            return web.json_response({"error": "denied"}, status=401)
        body = self.requests[-1][2]
        if body["tool"] == "sessions_list":
            return web.json_response({"ok": True, "result": {"details": {"sessions": [{"key": "remote-1", "title": "Actual remote", "status": "idle", "updatedAt": "2026-09-25T00:00:00Z"}]}}})
        if body["tool"] == "sessions_history":
            return web.json_response({"ok": True, "result": {"details": {"messages": [{"id": "m1", "role": "user", "content": [{"text": "real history"}], "createdAt": "2026-09-25T00:00:00Z"}, {"id": "m2", "role": "assistant", "content": "retained remotely"}]}}})
        return web.json_response({"ok": False})

    async def responses(self, request):
        body = await request.json()
        self.requests.append(("stream", request.headers.get("Authorization"), request.headers.get("x-openclaw-session-key"), body))
        if request.headers.get("Authorization") != "Bearer actual-secret":
            return web.json_response({}, status=401)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            await response.write(b'event: response.output_text.delta\ndata: {"delta":"remote "}\n\n')
            await response.write(b'event: response.output_text.delta\ndata: {"delta":"reply"}\n\n')
            await response.write(b'event: response.completed\ndata: {}\n\n')
            await response.write_eof()
        finally:
            self.stream_closed.set()
        return response

    async def malformed(self, request):
        self.requests.append(("malformed", request.headers.get("Authorization"), await request.json()))
        return web.Response(text="not-json", content_type="text/plain")

    async def unavailable(self, request):
        self.requests.append(("unavailable", request.headers.get("Authorization"), await request.json()))
        return web.json_response({"error": {"message": "downstream stopped"}}, status=503)


class RemoteSessionProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        credentials = CredentialStore(self.home)
        credentials.save({"openclaw-token": {"type": "static_token", "value": "actual-secret"}})
        self.runtime = ProtocolRuntime()
        app = web.Application()
        app.router.add_post("/tools/invoke", self.runtime.tool)
        app.router.add_post("/v1/responses", self.runtime.responses)
        app.router.add_post("/malformed/tools/invoke", self.runtime.malformed)
        app.router.add_post("/unavailable/tools/invoke", self.runtime.unavailable)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.store = RemoteSessionStore(self.home)
        self.store.save_connection({"id": "primary", "label": "OpenClaw", "base_url": self.origin, "credential_ref": "openclaw-token", "agent_id": "main"})
        self.bridge = RemoteSessionBridge(self.home, store=self.store, credentials=credentials)

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_actual_http_protocol_lists_history_and_retains_provenance(self):
        sessions = await self.bridge.sessions("primary")
        self.assertEqual(sessions["sessions"][0]["id"], "remote-1")
        self.assertEqual(sessions["sessions"][0]["provenance"], "remote")
        self.assertEqual(self.store.retained("primary")[0]["title"], "Actual remote")
        reloaded = RemoteSessionStore(self.home)
        self.assertEqual(reloaded.retained("primary")[0]["connection_id"], "primary")
        history = await self.bridge.history("primary", "remote-1", 12)
        self.assertEqual([row["content"] for row in history["messages"]], ["real history", "retained remotely"])
        self.assertEqual(history["messages"][0]["source"], {"connection_id": "primary", "session_id": "remote-1", "kind": "remote"})
        self.assertEqual(self.runtime.requests[0][1], "Bearer actual-secret")
        self.assertNotIn("actual-secret", json.dumps(self.store.connections()))

    async def test_real_sse_request_carries_session_attachment_and_closes_transport(self):
        chunks = bytearray()
        async for chunk in self.bridge.stream("primary", "remote-1", {"message": "inspect", "attachments": [{"filename": "note.txt", "media_type": "text/plain", "data": "bm90ZQ=="}]}):
            chunks.extend(chunk)
        self.assertIn(b'remote ', chunks)
        self.assertIn(b'reply', chunks)
        await asyncio.wait_for(self.runtime.stream_closed.wait(), 1)
        sent = self.runtime.requests[-1]
        self.assertEqual(sent[2], "remote-1")
        self.assertEqual(sent[3]["model"], "openclaw:main")
        self.assertEqual(sent[3]["input"][1]["source"]["filename"], "note.txt")
        self.assertTrue(sent[3]["stream"])

    async def test_credentials_validation_and_protocol_fail_closed(self):
        with self.assertRaises(ValueError):
            self.store.save_connection({"id": "bad", "label": "bad", "base_url": "file:///tmp/a", "credential_ref": "openclaw-token"})
        with self.assertRaises(ValueError):
            self.store.save_connection({"id": "bad", "label": "bad", "base_url": "https://secret@example.test", "credential_ref": "openclaw-token"})
        self.store.save_connection({"id": "missing", "label": "Missing", "base_url": self.origin, "credential_ref": "not-there"})
        with self.assertRaisesRegex(RemoteSessionError, "reference") as caught:
            await self.bridge.sessions("missing")
        self.assertEqual(caught.exception.code, "credential_missing")
        with self.assertRaises(ValueError):
            async for _ in self.bridge.stream("primary", "remote-1", {"message": "", "attachments": []}):
                pass

    async def test_actual_unauthorized_malformed_and_unavailable_responses_are_distinct(self):
        credentials = CredentialStore(self.home)
        credentials.save({
            "openclaw-token": {"type": "static_token", "value": "actual-secret"},
            "wrong-token": {"type": "static_token", "value": "wrong-secret"},
        })
        self.store.save_connection({"id": "denied", "label": "Denied", "base_url": self.origin, "credential_ref": "wrong-token"})
        denied = RemoteSessionBridge(self.home, store=self.store, credentials=credentials)
        with self.assertRaises(RemoteSessionError) as caught:
            await denied.sessions("denied")
        self.assertEqual(caught.exception.code, "unauthorized")
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("wrong-secret", str(caught.exception))
        self.store.save_connection({"id": "malformed", "label": "Malformed", "base_url": self.origin + "/malformed", "credential_ref": "openclaw-token"})
        with self.assertRaises(RemoteSessionError) as caught:
            await denied.sessions("malformed")
        self.assertEqual(caught.exception.code, "protocol_error")
        self.assertEqual(str(caught.exception), "Remote runtime returned invalid JSON")
        self.store.save_connection({"id": "down", "label": "Down", "base_url": self.origin + "/unavailable", "credential_ref": "openclaw-token"})
        with self.assertRaises(RemoteSessionError) as caught:
            await denied.sessions("down")
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertIn("503", str(caught.exception))

    async def test_attachment_contract_rejects_ambiguous_or_oversized_payloads_before_network(self):
        before = len(self.runtime.requests)
        invalid = [
            {"message": "x", "attachments": "not-a-list"},
            {"message": "x", "attachments": [{}]},
            {"message": "x", "attachments": [{"data": "eA==", "path": "/tmp/secret"}]},
            {"message": "x", "attachments": [{"data": "x" * 13_333_334}]},
            {"message": "x", "attachments": [{"data": "eA=="}] * 9},
        ]
        for payload in invalid:
            with self.subTest(payload_type=type(payload["attachments"]).__name__), self.assertRaises(ValueError):
                async for _ in self.bridge.stream("primary", "remote-1", payload):
                    pass
        self.assertEqual(len(self.runtime.requests), before)

    def test_connection_updates_and_retained_remote_identity_survive_store_reload(self):
        original = self.store.connection("primary")
        self.assertEqual(original.label, "OpenClaw")
        self.assertEqual(original.agent_id, "main")
        updated = self.store.save_connection({"id": "primary", "label": "Operations runtime", "base_url": self.origin + "/api", "credential_ref": "openclaw-token", "agent_id": "operator"})
        self.assertEqual(updated["label"], "Operations runtime")
        self.assertEqual(updated["agent_id"], "operator")
        reloaded = RemoteSessionStore(self.home)
        connection = reloaded.connection("primary")
        self.assertEqual(connection.base_url, self.origin + "/api/")
        self.assertEqual(connection.credential_ref, "openclaw-token")
        retained = reloaded.retain("primary", [
            {"id": "s1", "title": "First", "status": "idle", "last_message_at": "2026-01-01T00:00:00Z"},
            {"id": "s2", "title": "Second", "status": "running", "last_message_at": "2026-01-02T00:00:00Z"},
        ])
        self.assertEqual(len(retained), 2)
        rows = RemoteSessionStore(self.home).retained("primary")
        self.assertEqual([row["id"] for row in rows], ["s2", "s1"])
        self.assertEqual(rows[0]["status"], "running")
        self.assertEqual(rows[0]["provenance"], "remote")
        self.assertEqual(rows[1]["connection_id"], "primary")

    def test_connection_schema_rejects_secret_and_unknown_transport_fields(self):
        invalid = [
            {},
            {"id": "x", "label": "X", "base_url": self.origin, "credential_ref": ""},
            {"id": "x", "label": "X", "base_url": self.origin, "credential_ref": "openclaw-token", "secret": "forbidden"},
            {"id": "x", "label": "X", "base_url": "ftp://example.test", "credential_ref": "openclaw-token"},
            {"id": "x", "label": "X", "base_url": "https://example.test/?token=x", "credential_ref": "openclaw-token"},
            {"id": "x", "label": "X", "base_url": "https://example.test/#fragment", "credential_ref": "openclaw-token"},
        ]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.store.save_connection(row)
        self.assertEqual([row["id"] for row in self.store.connections()], ["primary"])

    def test_remote_message_content_normalization_preserves_order_without_raw_objects(self):
        self.assertEqual(_text("plain"), "plain")
        self.assertEqual(_text({"text": "nested"}), "nested")
        self.assertEqual(_text({"content": [{"text": "first"}, {"body": "second"}]}), "first\n\nsecond")
        self.assertEqual(_text({"parts": [{"message": "part"}]}), "part")
        self.assertEqual(_text({"output": [{"output_text": "answer"}]}), "answer")
        self.assertEqual(_text(None), "")
        self.assertEqual(_text({"unknown": "not exposed"}), "")


class OwnerHTTPAndNative(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        from gideon.interfaces.dashboard.handlers.capabilities_platform_remote_sessions import register
        credentials = CredentialStore(self.home)
        credentials.save({"remote-token": {"type": "static_token", "value": "actual-secret"}})
        self.protocol = ProtocolRuntime()
        remote = web.Application()
        remote.router.add_post("/tools/invoke", self.protocol.tool)
        remote.router.add_post("/v1/responses", self.protocol.responses)
        self.remote_runner = web.AppRunner(remote)
        await self.remote_runner.setup()
        remote_site = web.TCPSite(self.remote_runner, "127.0.0.1", 0)
        await remote_site.start()
        self.remote_origin = f"http://127.0.0.1:{remote_site._server.sockets[0].getsockname()[1]}"
        self.bridge = RemoteSessionBridge(self.home, credentials=credentials)
        @web.middleware
        async def owner(request, handler):
            request["user"] = "owner" if request.headers.get("x-owner") == "yes" else ""
            request["app"] = request.headers.get("x-app", "")
            return await handler(request)
        app = web.Application(middlewares=[owner])
        app["remote_session_bridge"] = self.bridge
        register(app)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/platform/remote-sessions"
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True), headers={"x-owner": "yes"})

    async def asyncTearDown(self):
        await self.client.close(); await self.runner.cleanup(); await self.remote_runner.cleanup(); self.temp.cleanup()

    async def test_owner_routes_persist_metadata_and_project_protocol_results(self):
        created = await self.client.post(self.url, json={"id": "remote", "label": "Remote", "base_url": self.remote_origin, "credential_ref": "remote-token", "agent_id": "ops"})
        self.assertEqual(created.status, 201)
        self.assertNotIn("secret", await created.text())
        collection = await (await self.client.get(self.url)).json()
        self.assertEqual(collection["connections"][0]["credential_ref"], "remote-token")
        sessions = await self.client.get(self.url + "/remote/sessions")
        self.assertEqual(sessions.status, 200)
        self.assertEqual((await sessions.json())["sessions"][0]["provenance"], "remote")
        history = await self.client.get(self.url + "/remote/sessions/remote-1/history?limit=4")
        self.assertEqual(history.status, 200)
        self.assertEqual((await history.json())["messages"][0]["source"]["kind"], "remote")
        streamed = await self.client.post(self.url + "/remote/sessions/remote-1/messages/stream", json={"message": "hello"})
        self.assertIn("response.completed", await streamed.text())

    async def test_owner_auth_and_query_refusals(self):
        denied = await self.client.get(self.url, headers={"x-owner": "no"})
        self.assertEqual(denied.status, 403)
        app_denied = await self.client.get(self.url, headers={"x-owner": "yes", "x-app": "external"})
        self.assertEqual(app_denied.status, 403)
        unknown = await self.client.get(self.url + "?path=/")
        self.assertEqual(unknown.status, 400)

    async def test_native_manifest_and_approval_contract(self):
        from gideon.workspace.capabilities.platform.remote_sessions_tools import create_provider
        provider = create_provider()
        definitions = {row.name: row for row in await provider.list_tools()}
        self.assertFalse(definitions["remote_agent_sessions"].requires_approval)
        self.assertFalse(definitions["remote_agent_history"].requires_approval)
        self.assertTrue(definitions["remote_agent_message"].requires_approval)
        invalid = await provider.invoke("remote_agent_history", {"path": "/"})
        self.assertFalse(invalid.success)
        manifest = json.loads((Path(__file__).parents[4] / "runtime/gideon/extensions/apps/native/remote-agent-sessions/app.json").read_text())
        self.assertEqual(manifest["provider"]["implementation"], "gideon.workspace.capabilities.platform.remote_sessions_tools:create_provider")


if __name__ == "__main__":
    unittest.main()
