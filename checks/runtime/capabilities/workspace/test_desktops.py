import asyncio
import io
import json
import os
import signal
import socket
import sys
import tempfile
import termios
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, web
from PIL import Image

from checks.runtime.capabilities.workspace.test_workspace import repository
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.workspace.capabilities.workspace.desktop import (
    DesktopRegistry,
    close_desktop_registry,
    get_desktop_registry,
)
from gideon.workspace.capabilities.workspace.store import ConflictError


class Desktops(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = str(self.root)
        self.repo = repository(self.root / "repo")
        self.project = HierarchyStore().create_project(
            name="Desktop source", workspace_dir=str(self.repo)
        )
        self.registry = DesktopRegistry(self.root / "state", allowed_roots=[self.root])

    async def asyncTearDown(self):
        await self.registry.close()
        await close_desktop_registry(self.root / "capabilities/workspace")
        if self.old is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = self.old
        self.temp.cleanup()

    def payload(self, request_id="desktop"):
        return {
            "project_id": self.project.id,
            "request_id": request_id,
            "width": 800,
            "height": 600,
        }

    async def terminal(self, registry, sid, command):
        await registry.input(sid, {"kind": "text", "text": command})
        await registry.input(sid, {"kind": "key", "key": "Return"})

    async def wait_file(self, path):
        for _ in range(100):
            if path.exists():
                return path.read_text()
            await asyncio.sleep(0.05)
        self.fail("Real desktop terminal did not create expected file")

    async def test_dedicated_display_survives_last_client_disconnect(self):
        import secrets

        display = 1000 + secrets.randbelow(30000)
        folder = self.root / "probe"
        folder.mkdir(mode=0o700)
        authority = folder / "authority"
        authority.touch(mode=0o600)
        handle = {
            "display": f":{display}",
            "auth": authority,
            "home": folder,
            "cwd": str(self.repo),
        }
        self.assertFalse(Path(f"/tmp/.X11-unix/X{display}").exists())
        self.assertFalse(Path(f"/tmp/.X{display}-lock").exists())
        await self.registry._command(
            handle,
            [
                "xauth",
                "-f",
                str(authority),
                "add",
                handle["display"],
                "MIT-MAGIC-COOKIE-1",
                secrets.token_hex(16),
            ],
        )
        proc, profile = await self.registry._spawn(
            handle,
            [
                "Xvfb",
                handle["display"],
                "-screen",
                "0",
                "800x600x24",
                "-nolisten",
                "tcp",
                "-noreset",
                "-auth",
                str(authority),
            ],
        )
        try:
            for attempt in range(30):
                try:
                    await self.registry._command(handle, ["xdpyinfo"], 262144)
                    break
                except ValueError:
                    if attempt == 29:
                        raise
                    await asyncio.sleep(0.1)
            await self.registry._command(
                handle,
                [
                    "xprop",
                    "-root",
                    "-f",
                    "GIDEON_LIFETIME",
                    "8s",
                    "-set",
                    "GIDEON_LIFETIME",
                    "owned-server",
                ],
            )
            for _ in range(2):
                await asyncio.sleep(0.2)
                value = await self.registry._command(
                    handle, ["xprop", "-root", "GIDEON_LIFETIME"]
                )
                self.assertIn(b"owned-server", value)
                self.assertIsNone(proc.returncode)
        finally:
            from gideon.core.cancellation import terminate_and_reap

            await terminate_and_reap(proc)
            if profile:
                Path(profile).unlink(missing_ok=True)
        self.assertIsNotNone(proc.returncode)

    async def test_real_frames_terminal_input_and_clean_stop(self):
        self.assertTrue(self.registry.availability()["available"])
        record = await self.registry.start(self.payload())
        self.assertEqual(record["status"], "running")
        self.assertEqual(record["revision"], 2)
        self.assertIsNotNone(record["started_at"])
        self.assertIsNone(record["ended_at"])
        self.assertFalse({"pid", "display", "cookie", "auth"} & set(record))
        handle = self.registry.handles[record["id"]]
        self.assertEqual(handle["auth"].stat().st_mode & 0o777, 0o600)
        self.assertEqual(handle["home"].stat().st_mode & 0o777, 0o700)
        master, slave = handle["pty"]
        self.assertTrue(os.isatty(master))
        self.assertTrue(os.isatty(slave))
        self.assertEqual(os.fstat(slave).st_uid, os.getuid())
        tty_name = os.ttyname(slave)
        self.assertEqual(termios.tcgetattr(slave)[6][termios.VERASE], b"\x08")
        focus = (
            (
                await self.registry._command(
                    handle, ["xdotool", "getwindowfocus", "getwindowname"], 4096
                )
            )
            .decode()
            .strip()
        )
        self.assertEqual(focus, "Workspace desktop")
        visible = (
            (
                await self.registry._command(
                    handle,
                    ["xdotool", "search", "--onlyvisible", "--class", "XTerm"],
                    1024,
                )
            )
            .decode()
            .splitlines()
        )
        self.assertEqual(len(visible), 1)
        pending = asyncio.create_task(self.registry.frame(record["id"]))
        await asyncio.sleep(0)
        with self.assertRaisesRegex(ConflictError, "frame"):
            await self.registry.frame(record["id"])
        image = Image.open(io.BytesIO(await pending))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (800, 600))
        self.assertGreater(len(image.convert("RGB").getcolors(1000000)), 10)
        await self.registry.input(record["id"], {"kind": "click", "x": 100, "y": 100})
        position = await self.registry._command(
            handle, ["xdotool", "getmouselocation", "--shell"]
        )
        self.assertIn(b"X=100", position)
        self.assertIn(b"Y=100", position)
        await self.terminal(
            self.registry, record["id"], "printf desktop-proof > desktop-proof.txt"
        )
        self.assertEqual(
            await self.wait_file(self.repo / "desktop-proof.txt"), "desktop-proof"
        )
        await self.terminal(self.registry, record["id"], "tty > tty-name.txt")
        self.assertEqual(
            (await self.wait_file(self.repo / "tty-name.txt")).strip(), tty_name
        )
        await self.terminal(
            self.registry,
            record["id"],
            "test -t 0 && printf interactive > interactive.txt",
        )
        self.assertEqual(
            await self.wait_file(self.repo / "interactive.txt"), "interactive"
        )
        with self.assertRaises(ConflictError):
            await self.registry.stop(record["id"], 1)
        processes = [proc for proc, _ in handle["processes"]]
        stopped = await self.registry.stop(record["id"], record["revision"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["revision"], 3)
        self.assertIsNotNone(stopped["ended_at"])
        self.assertTrue(all(p.returncode is not None for p in processes))
        self.assertFalse(handle["auth"].exists())
        for fd in (master, slave):
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertEqual(await self.registry.stop(record["id"], 2), stopped)
        with self.assertRaises(ConflictError):
            await self.registry.frame(record["id"])
        with self.assertRaises(ConflictError):
            await self.registry.input(record["id"], {"kind": "key", "key": "Return"})
        self.assertEqual(self.registry.list(), [stopped])

    async def test_idempotency_exclusive_owner_and_reopen(self):
        first = await self.registry.start(self.payload())
        self.assertEqual(await self.registry.start(self.payload()), first)
        self.assertEqual(len(self.registry.handles), 1)
        with self.assertRaises(ConflictError):
            await self.registry.start({**self.payload(), "width": 900})
        with self.assertRaises(ConflictError):
            DesktopRegistry(self.root / "state", allowed_roots=[self.root])
        await self.registry.close()
        self.registry = DesktopRegistry(self.root / "state", allowed_roots=[self.root])
        self.assertEqual(self.registry.get(first["id"])["status"], "stopped")
        self.assertEqual(self.registry.handles, {})
        replay = await self.registry.start(self.payload())
        self.assertEqual(replay["id"], first["id"])
        self.assertEqual(replay["status"], "stopped")
        self.assertEqual(self.registry.handles, {})

    async def test_two_sessions_remain_isolated_and_capacity_refused(self):
        first = await self.registry.start(self.payload("first"))
        second = await self.registry.start(self.payload("second"))
        one = self.registry.handles[first["id"]]
        two = self.registry.handles[second["id"]]
        self.assertNotEqual(one["display"], two["display"])
        self.assertNotEqual(one["auth"].read_bytes(), two["auth"].read_bytes())
        self.assertNotEqual(one["home"], two["home"])
        wrong_credential = {**one, "auth": two["auth"]}
        with self.assertRaises(ValueError):
            await self.registry._command(wrong_credential, ["xdpyinfo"], 262144)
        genuine = await self.registry._command(one, ["xdpyinfo"], 262144)
        self.assertIn(b"dimensions:    800x600", genuine)
        with self.assertRaises(ConflictError):
            await self.registry.start(self.payload("third"))
        await self.registry.stop(first["id"], first["revision"])
        self.assertEqual(self.registry.get(second["id"])["status"], "running")
        self.assertTrue(two["auth"].exists())
        await self.terminal(self.registry, second["id"], "printf second > second.txt")
        self.assertEqual(await self.wait_file(self.repo / "second.txt"), "second")
        data = await self.registry.frame(second["id"])
        self.assertTrue(data.startswith(b"\x89PNG"))
        with self.assertRaises(FileNotFoundError):
            await self.registry.stop("other-home-session", 2)
        self.assertEqual(self.registry.get(second["id"])["revision"], 2)

    async def test_input_validation_and_keyboard_editing(self):
        row = await self.registry.start(self.payload())
        invalid = [
            {"kind": "click", "x": -1, "y": 5},
            {"kind": "click", "x": 800, "y": 0},
            {"kind": "click", "x": True, "y": 0},
            {"kind": "key", "key": "ctrl+alt+F1"},
            {"kind": "text", "text": "\n"},
            {"kind": "text", "text": "x" * 2049},
            {"kind": "text", "text": "safe", "DISPLAY": ":0"},
            {"kind": "shell", "command": "touch escape"},
            {},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    await self.registry.input(row["id"], payload)
        await self.registry.input(row["id"], {"kind": "text", "text": "printf wrongX"})
        await self.registry.input(row["id"], {"kind": "key", "key": "BackSpace"})
        await self.registry.input(row["id"], {"kind": "text", "text": " > edited.txt"})
        await self.registry.input(row["id"], {"kind": "key", "key": "Return"})
        self.assertEqual(await self.wait_file(self.repo / "edited.txt"), "wrong")
        self.assertEqual(self.registry.get(row["id"])["status"], "running")

    async def test_concurrent_start_reuses_one_live_terminal_and_closes_pty(self):
        results = await asyncio.gather(
            self.registry.start(self.payload()), self.registry.start(self.payload())
        )
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.registry.handles), 1)
        row = results[0]
        handle = self.registry.handles[row["id"]]
        descriptors = handle["pty"]
        self.assertEqual(len(descriptors), 2)
        self.assertEqual(len(handle["processes"]), 4)
        self.assertEqual(len(self.registry.list()), 1)
        await self.terminal(
            self.registry, row["id"], 'printf "$DISPLAY" > display-name.txt'
        )
        self.assertEqual(
            await self.wait_file(self.repo / "display-name.txt"), handle["display"]
        )
        await self.terminal(
            self.registry, row["id"], 'printf "$HOME" > private-home.txt'
        )
        self.assertEqual(
            await self.wait_file(self.repo / "private-home.txt"), str(handle["home"])
        )
        await self.registry.close()
        self.assertEqual(self.registry.handles, {})
        self.assertTrue(self.registry.closed)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        self.assertEqual(self.registry.get(row["id"])["status"], "stopped")

    async def test_missing_dependencies_are_truthfully_unavailable(self):
        old = os.environ["PATH"]
        empty = self.root / "empty-bin"
        empty.mkdir()
        os.environ["PATH"] = str(empty)
        try:
            state = self.registry.availability()
            self.assertFalse(state["available"])
            self.assertIn("Xvfb", state["missing"])
            with self.assertRaisesRegex(ValueError, "dependencies unavailable"):
                await self.registry.start(self.payload())
            self.assertEqual(self.registry.list(), [])
            self.assertEqual(self.registry.handles, {})
        finally:
            os.environ["PATH"] = old

    async def test_validation_does_not_create_sessions(self):
        for change in (
            {"width": True},
            {"width": 639},
            {"height": 901},
            {"request_id": ""},
            {"project_id": "missing"},
            {"display": ":0"},
            {"command": "bash"},
        ):
            with self.subTest(change=change):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    await self.registry.start({**self.payload(), **change})
        self.assertEqual(self.registry.list(), [])
        self.assertEqual(self.registry.handles, {})
        with self.assertRaises(FileNotFoundError):
            self.registry.get("missing")
        with self.assertRaises(FileNotFoundError):
            await self.registry.input("missing", {})
        await self.registry.close()
        with self.assertRaises(ConflictError):
            await self.registry.start(self.payload())

    async def test_owned_child_exit_updates_failed_state(self):
        row = await self.registry.start(self.payload())
        handle = self.registry.handles[row["id"]]
        terminal = handle["processes"][-1][0]
        terminal.terminate()
        for _ in range(100):
            if (
                self.registry.get(row["id"])["status"] == "failed"
                and row["id"] not in self.registry.handles
            ):
                break
            await asyncio.sleep(0.05)
        self.assertEqual(self.registry.get(row["id"])["status"], "failed")
        self.assertIsNotNone(self.registry.get(row["id"])["ended_at"])
        self.assertNotIn(row["id"], self.registry.handles)
        for proc, _ in handle["processes"]:
            await asyncio.wait_for(proc.wait(), 5)
            self.assertIsNotNone(proc.returncode)
        self.assertFalse(handle["auth"].exists())

    async def test_actual_registry_crash_cleans_display_and_recovery_does_not_adopt(
        self,
    ):
        await self.registry.close()
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("desktop_worker.py")),
            str(self.root),
            self.project.id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            line = await asyncio.wait_for(worker.stdout.readline(), 30)
            self.assertTrue(
                line,
                (
                    await worker.stderr.read()
                    if worker.returncode is not None
                    else "Worker did not report ready"
                ),
            )
            ready = json.loads(line)
            socket_path = "/tmp/.X11-unix/X" + ready["display"][1:]
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(socket_path)
            worker.kill()
            await worker.wait()
            for _ in range(100):
                try:
                    with socket.socket(
                        socket.AF_UNIX, socket.SOCK_STREAM
                    ) as connection:
                        connection.connect(socket_path)
                except OSError:
                    break
                await asyncio.sleep(0.05)
            else:
                self.fail("Owned X server survived registry process crash")
            self.registry = DesktopRegistry(
                self.root / "state", allowed_roots=[self.root]
            )
            recovered = self.registry.get(ready["record"]["id"])
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual(recovered["revision"], 3)
            self.assertIsNotNone(recovered["ended_at"])
            self.assertEqual(self.registry.handles, {})
            with self.assertRaises(ConflictError):
                await self.registry.frame(recovered["id"])
        finally:
            if worker.returncode is None:
                worker.kill()
                await worker.wait()


class DesktopHttp(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Desktops.asyncSetUp
    asyncTearDown = Desktops.asyncTearDown
    payload = Desktops.payload
    wait_file = Desktops.wait_file

    async def test_owner_http_native_png_input_and_authentication(self):
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )
        from gideon.workspace.capabilities.workspace.tools import create_provider

        reset_secret_cache()
        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace/desktops"
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                response = await client.get(
                    base + "?token=" + generate_token("desktop-owner")
                )
                self.assertEqual(response.status, 200)
                response = await client.get(base + "/availability")
                self.assertTrue((await response.json())["available"])
                response = await client.post(base, json=self.payload())
                self.assertEqual(response.status, 200, await response.text())
                row = await response.json()
                response = await client.get(base + "/" + row["id"] + "/frame")
                self.assertEqual(response.content_type, "image/png")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(
                    Image.open(io.BytesIO(await response.read())).size, (800, 600)
                )
                for payload in (
                    {"kind": "text", "text": "printf http-proof > http-proof.txt"},
                    {"kind": "key", "key": "Return"},
                ):
                    response = await client.post(
                        base + "/" + row["id"] + "/input", json=payload
                    )
                    self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(
                    await self.wait_file(self.repo / "http-proof.txt"), "http-proof"
                )
                provider = create_provider()
                result = await provider.invoke("workspace_desktops", {})
                self.assertTrue(result.success, result.error)
                self.assertEqual(json.loads(result.output)[0]["id"], row["id"])
                definitions = {x.name: x for x in await provider.list_tools()}
                self.assertTrue(
                    definitions["workspace_desktop_start"].requires_approval
                )
                self.assertTrue(definitions["workspace_desktop_stop"].requires_approval)
                self.assertNotIn("workspace_desktop_input", definitions)
                stopped = await provider.invoke(
                    "workspace_desktop_stop",
                    {"id": row["id"], "revision": row["revision"]},
                )
                self.assertTrue(stopped.success, stopped.error)
                self.assertEqual(json.loads(stopped.output)["status"], "stopped")
                response = await client.get(base + "/" + row["id"])
                self.assertEqual((await response.json())["status"], "stopped")
                response = await client.get(base + "/" + row["id"] + "/frame")
                self.assertEqual(response.status, 409)
            async with ClientSession() as anonymous:
                for suffix in ("", "/" + row["id"] + "/frame"):
                    response = await anonymous.get(base + suffix)
                    self.assertIn(response.status, (401, 403))
                response = await anonymous.post(
                    base + "/" + row["id"] + "/input",
                    json={"kind": "key", "key": "Return"},
                )
                self.assertIn(response.status, (401, 403))
        finally:
            await runner.cleanup()
