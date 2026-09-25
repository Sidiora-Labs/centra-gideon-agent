import asyncio
import json
import os
import shlex
import sqlite3
import sys
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, web

from checks.runtime.capabilities.workspace.test_processes import (
    Processes,
    output,
    settled,
)
from gideon.workspace.capabilities.workspace.processes import (
    ProcessRegistry,
    close_registry,
    get_registry,
)


class LogWindows(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Processes.asyncSetUp
    asyncTearDown = Processes.asyncTearDown

    async def launch(self, source, request="log"):
        path = self.repo / (request + ".py")
        path.write_text(source)
        return await self.registry.start(
            {
                **self.payload,
                "command": shlex.quote(sys.executable) + " " + shlex.quote(str(path)),
                "request_id": request,
            }
        )

    async def test_incremental_cursor_and_real_delayed_stdout_stderr(self):
        row = await self.launch(
            "import sys,time\nprint('first',flush=True)\ntime.sleep(.3)\nprint('second',file=sys.stderr,flush=True)\ntime.sleep(.3)\nprint('third',flush=True)"
        )
        await output(self.registry, row["id"], "first")
        first = self.registry.log_window(row["id"])
        self.assertEqual(first["text"], "first\n")
        self.assertEqual(first["start"], 0)
        self.assertEqual(first["next"], 6)
        self.assertEqual(first["dropped"], 0)
        self.assertEqual(first["status"], "running")
        self.assertEqual(first["cursor_unit"], "redacted_unicode_characters")
        await settled(self.registry, row["id"])
        second = self.registry.log_window(row["id"], after=first["next"])
        self.assertEqual(second["text"], "second\nthird\n")
        self.assertEqual(second["start"], first["next"])
        self.assertEqual(second["status"], "exited")
        self.assertEqual(second["next"], second["end"])
        empty = self.registry.log_window(row["id"], after=second["next"])
        self.assertEqual(empty["text"], "")
        self.assertEqual(empty["next"], second["next"])
        self.assertEqual(
            self.registry.logs(row["id"])["text"], first["text"] + second["text"]
        )
        await self.registry.close()
        self.registry = ProcessRegistry(self.root / "state", allowed_roots=[self.root])
        self.assertEqual(
            self.registry.log_window(row["id"], after=first["next"]), second
        )

    async def test_real_output_retention_gap_and_paging(self):
        row = await self.launch("import sys\nsys.stdout.write('a'*70000+'final\\n')")
        await settled(self.registry, row["id"])
        page = self.registry.log_window(row["id"], limit=4000)
        self.assertEqual(page["end"], 70006)
        self.assertEqual(page["dropped"], 70006 - 65536)
        self.assertEqual(page["start"], page["dropped"])
        self.assertEqual(len(page["text"]), 4000)
        retained = page["text"]
        cursor = page["next"]
        while cursor < page["end"]:
            part = self.registry.log_window(row["id"], after=cursor, limit=10000)
            self.assertEqual(part["start"], cursor)
            self.assertEqual(part["dropped"], 0)
            self.assertGreater(part["next"], cursor)
            cursor = part["next"]
            retained += part["text"]
        self.assertEqual(len(retained), 65536)
        self.assertEqual(retained, "a" * (65536 - 6) + "final\n")
        self.assertEqual(retained, self.registry.logs(row["id"])["text"])
        self.assertEqual(self.registry.log_window(row["id"], after=cursor)["text"], "")
        with self.assertRaises(ValueError):
            self.registry.log_window(row["id"], after=cursor + 1)

    async def test_split_utf8_is_preserved_and_incomplete_final_byte_is_replaced(self):
        row = await self.launch(
            "import os,time\nos.write(1,b'\\xe2')\ntime.sleep(.1)\nos.write(1,b'\\x82')\ntime.sleep(.1)\nos.write(1,b'\\xac\\n\\xe2')"
        )
        await settled(self.registry, row["id"])
        page = self.registry.log_window(row["id"])
        self.assertEqual(page["text"], "€\n�")
        self.assertEqual(page["end"], 3)
        self.assertEqual(self.registry.log_window(row["id"], after=1)["text"], "\n�")
        self.assertEqual(self.registry.logs(row["id"])["text"], "€\n�")

    async def test_existing_database_tail_migrates_without_losing_records(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        payload = {"id": "old", "status": "exited", "revision": 2, "exit_code": 0}
        with sqlite3.connect(legacy / "processes.sqlite3") as db:
            db.execute(
                'CREATE TABLE processes(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,input TEXT NOT NULL,payload TEXT NOT NULL,log TEXT NOT NULL DEFAULT "")'
            )
            db.execute(
                "INSERT INTO processes VALUES(?,?,?,?,?)",
                ("old", "old", "{}", json.dumps(payload), "saved€"),
            )
        migrated = ProcessRegistry(legacy, allowed_roots=[self.root])
        try:
            self.assertEqual(migrated.get("old"), payload)
            window = migrated.log_window("old")
            self.assertEqual(window["text"], "saved€")
            self.assertEqual(window["end"], 6)
            self.assertEqual(window["dropped"], 0)
            self.assertEqual(window["status"], "exited")
        finally:
            await migrated.close()
        reopened = ProcessRegistry(legacy, allowed_roots=[self.root])
        try:
            self.assertEqual(reopened.log_window("old"), window)
        finally:
            await reopened.close()

    async def test_invalid_windows_and_wrong_home_are_refused(self):
        row = await self.launch("print('private')")
        await settled(self.registry, row["id"])
        for args in (
            {"after": -1},
            {"after": True},
            {"limit": 0},
            {"limit": 65537},
            {"limit": False},
            {"after": "0"},
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.registry.log_window(row["id"], **args)
        with self.assertRaises(FileNotFoundError):
            self.registry.log_window("../processes.sqlite3")
        other = ProcessRegistry(self.root / "other", allowed_roots=[self.root])
        try:
            with self.assertRaises(FileNotFoundError):
                other.log_window(row["id"])
            self.assertEqual(other.list(), [])
        finally:
            await other.close()

    async def test_owner_http_and_native_cursor_use_same_registry(self):
        old = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = str(self.root)
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )
        from gideon.workspace.capabilities.workspace.tools import create_provider

        reset_secret_cache()
        registry = get_registry(
            self.root / "capabilities/workspace", allowed_roots=[self.root]
        )
        row = await registry.start({**self.payload, "command": "printf hello"})
        await settled(registry, row["id"])
        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace"
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                await client.get(base + "?token=" + generate_token("log-owner"))
                response = await client.get(
                    base + "/processes/" + row["id"] + "/log-window?after=1&limit=2"
                )
                self.assertEqual(response.status, 200)
                value = await response.json()
                self.assertEqual(value["text"], "el")
                self.assertEqual(value["next"], 3)
                tool = await create_provider().invoke(
                    "workspace_process_log_window",
                    {"id": row["id"], "after": 1, "limit": 2},
                )
                self.assertTrue(tool.success)
                self.assertEqual(json.loads(tool.output), value)
                response = await client.get(
                    base + "/processes/" + row["id"] + "/log-window?after=-1"
                )
                self.assertEqual(response.status, 400)
                response = await client.get(base + "/processes/missing/log-window")
                self.assertEqual(response.status, 404)
            async with ClientSession() as anonymous:
                response = await anonymous.get(
                    base + "/processes/" + row["id"] + "/log-window"
                )
                self.assertNotEqual(response.status, 200)
                self.assertNotIn("hello", await response.text())
        finally:
            await runner.cleanup()
            await close_registry(self.root / "capabilities/workspace")
            if old is None:
                os.environ.pop("GIDEON_HOME", None)
            else:
                os.environ["GIDEON_HOME"] = old
