import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, web

from gideon.workspace.capabilities.workspace.iterm import (
    ExternalTerminalMirror,
    pane_id,
)
from gideon.workspace.capabilities.workspace.iterm_worker import text


class MirrorValidation(unittest.IsolatedAsyncioTestCase):
    def test_identity_bounds_and_no_command_or_endpoint_syntax(self):
        for value in ("abc-123", "w0t1p2:1234", "a" * 256):
            self.assertEqual(pane_id(value), value)
        for value in (
            "",
            "a" * 257,
            "../pane",
            "http://localhost",
            "$(id)",
            "pane\n",
            None,
            1,
            {},
            [],
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    pane_id(value)

    def test_plain_text_retains_unicode_and_removes_terminal_controls(self):
        self.assertEqual(text("α你好\t✓", 100), "α你好\t✓")
        self.assertEqual(text("a\x00\x07\x1bb\x7f\rc", 100), "abc")
        self.assertEqual(text("abcdef", 3), "abc")
        self.assertEqual(text("<script>x</script>", 100), "<script>x</script>")
        self.assertEqual(text("", 100), "")

    @unittest.skipIf(sys.platform == "darwin", "Requires the real non-Mac platform")
    async def test_actual_platform_refuses_native_observation(self):
        mirror = ExternalTerminalMirror()
        state = mirror.availability()
        self.assertFalse(state["available"])
        self.assertEqual(state["reason"], "macOS required")
        self.assertTrue(state["read_only"])
        self.assertEqual(state["transport"], "native-iterm2")
        with self.assertRaisesRegex(OSError, "macOS"):
            await mirror.inventory()
        with self.assertRaisesRegex(OSError, "macOS"):
            await mirror.screen("real-pane")
        with self.assertRaises(ValueError):
            await mirror.screen("../escape")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(
                Path(__file__).parents[4]
                / "runtime/gideon/workspace/capabilities/workspace/iterm_worker.py"
            ),
            "pane",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await proc.communicate()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(output, b"")
        self.assertEqual(error, b"")


@unittest.skipIf(
    sys.platform == "darwin", "Exercises real unavailable platform boundary"
)
class MirrorHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from gideon.interfaces.dashboard.token_auth import reset_secret_cache

        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = self.temp.name
        reset_secret_cache()
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            token_auth_middleware,
        )

        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.base = self.origin + "/api/capabilities/workspace/external-terminals"
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        response = await self.client.get(
            self.origin
            + "/api/capabilities/workspace?token="
            + generate_token("mirror-owner")
        )
        self.assertEqual(response.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        if self.old is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = self.old
        self.temp.cleanup()

    async def test_authenticated_availability_and_unavailable_reads(self):
        response = await self.client.get(self.base + "/availability")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertFalse((await response.json())["available"])
        for suffix in ("", "/pane"):
            response = await self.client.get(self.base + suffix)
            self.assertEqual(response.status, 503)
            value = await response.json()
            self.assertIn("authorization", value["error"])
            self.assertNotIn(self.temp.name, json.dumps(value))
            self.assertNotIn("panes", value)

    async def test_no_input_resize_or_lifecycle_routes(self):
        for method in ("post", "put", "delete", "patch"):
            response = await getattr(self.client, method)(
                self.base + "/pane", json={"command": "id", "columns": 80}
            )
            self.assertEqual(response.status, 405)
        response = await self.client.get(
            self.base + "/availability?endpoint=http://localhost"
        )
        self.assertEqual(response.status, 400)
        response = await self.client.get(self.base + "/availability?env=secret")
        self.assertEqual(response.status, 400)
        async with ClientSession() as anonymous:
            response = await anonymous.get(self.base)
            self.assertNotEqual(response.status, 200)
            self.assertNotIn("panes", await response.text())

    async def test_real_native_tools_share_read_only_failure(self):
        from gideon.workspace.capabilities.workspace.tools import create_provider

        provider = create_provider()
        definitions = {t.name: t for t in await provider.list_tools()}
        for name in (
            "workspace_external_terminals",
            "workspace_external_terminal_screen",
        ):
            self.assertFalse(definitions[name].requires_approval)
            self.assertFalse(definitions[name].parameters["additionalProperties"])
        result = await provider.invoke("workspace_external_terminals", {})
        self.assertFalse(result.success)
        result = await provider.invoke(
            "workspace_external_terminal_screen", {"id": "pane"}
        )
        self.assertFalse(result.success)
        result = await provider.invoke(
            "workspace_external_terminals", {"endpoint": "http://localhost"}
        )
        self.assertFalse(result.success)


@unittest.skipUnless(
    sys.platform == "darwin"
    and importlib.util.find_spec("iterm2")
    and os.environ.get("GIDEON_TEST_NATIVE_PANE")
    and os.environ.get("GIDEON_TEST_NATIVE_TEXT"),
    "Needs an existing authorized native Mac pane; no fixture substitute",
)
class NativeMirror(unittest.IsolatedAsyncioTestCase):
    async def test_existing_native_pane_survives_readers_and_retains_native_grid(self):
        mirror = ExternalTerminalMirror()
        identity = os.environ["GIDEON_TEST_NATIVE_PANE"]
        initial = await mirror.inventory()
        matches = [pane for pane in initial["panes"] if pane["id"] == identity]
        self.assertEqual(len(matches), 1)
        pane = matches[0]
        self.assertGreater(pane["columns"], 0)
        self.assertGreater(pane["rows"], 0)
        screen = await mirror.screen(identity)
        self.assertEqual(screen["id"], identity)
        self.assertEqual(screen["columns"], pane["columns"])
        self.assertEqual(screen["rows"], pane["rows"])
        self.assertIsInstance(screen["lines"], list)
        self.assertLessEqual(len(screen["lines"]), 500)
        self.assertIn(os.environ["GIDEON_TEST_NATIVE_TEXT"], "\n".join(screen["lines"]))
        second = ExternalTerminalMirror()
        after = await second.inventory()
        self.assertIn(identity, [item["id"] for item in after["panes"]])
        again = await second.screen(identity)
        self.assertEqual(again["id"], identity)
        self.assertEqual(again["window_id"], pane["window_id"])
        self.assertEqual(again["tab_id"], pane["tab_id"])
        with self.assertRaises(FileNotFoundError):
            await second.screen("gideon-missing-native-pane")
        final = await mirror.inventory()
        self.assertIn(identity, [item["id"] for item in final["panes"]])
