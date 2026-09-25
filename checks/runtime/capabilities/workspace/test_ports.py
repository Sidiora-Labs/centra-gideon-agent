import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, CookieJar, web

from gideon.workspace.capabilities.workspace.ports import (
    PortRegistry,
    close_port_registry,
    get_port_registry,
)
from gideon.workspace.capabilities.workspace.store import ConflictError


def free_ports(count=3):
    sockets = [socket.socket() for _ in range(count)]
    try:
        for handle in sockets:
            handle.bind(("127.0.0.1", 0))
        return [handle.getsockname()[1] for handle in sockets]
    finally:
        for handle in sockets:
            handle.close()


class Reservations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ports = free_ports()
        self.registry = PortRegistry(self.root / "ports", allowed_ports=self.ports)
        self.payload = {
            "project_id": "real-app",
            "request_id": uuid4().hex,
            "port": self.ports[0],
        }

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def test_real_socket_hold_release_and_reload(self):
        record = self.registry.reserve(self.payload)
        self.assertEqual(record["status"], "held")
        self.assertEqual(record["port"], self.ports[0])
        self.assertEqual(record["project_id"], "real-app")
        self.assertIsNone(record["released_at"])
        self.assertTrue(record["created_at"].endswith("+00:00"))
        with socket.socket() as outsider, self.assertRaises(OSError):
            outsider.bind(("127.0.0.1", record["port"]))
        self.assertEqual(self.registry.get(record["id"]), record)
        observed = {x["port"]: x for x in self.registry.inventory()["ports"]}
        self.assertFalse(observed[record["port"]]["available"])
        self.assertEqual(observed[record["port"]]["reservation_id"], record["id"])
        released = self.registry.release(record["id"], 1)
        self.assertEqual(released["status"], "released")
        self.assertEqual(released["revision"], 2)
        self.assertIsNotNone(released["released_at"])
        with socket.socket() as outsider:
            outsider.bind(("127.0.0.1", record["port"]))
        self.registry.close()
        self.registry = PortRegistry(self.root / "ports", allowed_ports=self.ports)
        self.assertEqual(self.registry.get(record["id"]), released)
        self.assertEqual(self.registry.list(), [released])

    def test_listener_conflict_no_side_effects(self):
        with socket.socket() as service:
            service.bind(("127.0.0.1", self.ports[0]))
            service.listen()
            with self.assertRaises(ConflictError):
                self.registry.reserve(self.payload)
            self.assertEqual(self.registry.list(), [])
            entries = {x["port"]: x for x in self.registry.inventory()["ports"]}
            self.assertFalse(entries[self.ports[0]]["available"])
            self.assertIsNone(entries[self.ports[0]]["reservation_id"])
            self.assertTrue(entries[self.ports[1]]["available"])
            self.assertEqual(
                service.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1
            )
        row = self.registry.reserve(self.payload)
        self.assertEqual(row["port"], self.ports[0])

    def test_retry_and_optimistic_release(self):
        row = self.registry.reserve(self.payload)
        self.assertEqual(self.registry.reserve(self.payload), row)
        self.assertEqual(len(self.registry.sockets), 1)
        with self.assertRaises(ConflictError):
            self.registry.reserve({**self.payload, "project_id": "changed"})
        with self.assertRaises(ConflictError):
            self.registry.release(row["id"], 0)
        with socket.socket() as outsider, self.assertRaises(OSError):
            outsider.bind(("127.0.0.1", row["port"]))
        released = self.registry.release(row["id"], 1)
        self.assertEqual(self.registry.release(row["id"], 1), released)
        self.assertEqual(self.registry.reserve(self.payload), released)
        self.assertEqual(self.registry.sockets, {})
        with self.assertRaises(ConflictError):
            self.registry.release(row["id"], True)

    def test_concurrent_reservations_and_exhaustion(self):
        def reserve(index):
            return self.registry.reserve(
                {"project_id": f"p-{index}", "request_id": f"r-{index}"}
            )

        with ThreadPoolExecutor(max_workers=3) as workers:
            rows = list(workers.map(reserve, range(3)))
        self.assertEqual({x["port"] for x in rows}, set(self.ports))
        self.assertEqual(len(self.registry.sockets), 3)
        with self.assertRaises(ConflictError):
            reserve(4)
        self.assertTrue(
            all(not x["available"] for x in self.registry.inventory()["ports"])
        )
        self.registry.release(rows[1]["id"], 1)
        self.assertEqual(reserve(5)["port"], rows[1]["port"])

    def test_concurrent_same_request_has_one_socket(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            rows = list(
                workers.map(lambda _: self.registry.reserve(self.payload), range(8))
            )
        self.assertEqual(len({x["id"] for x in rows}), 1)
        self.assertEqual(len(self.registry.sockets), 1)
        self.assertEqual(self.registry.list(), [rows[0]])

    def test_validation_and_config_bounds(self):
        for patch in (
            {"host": "0.0.0.0"},
            {"port": 80},
            {"port": True},
            {"port": "6000"},
            {"request_id": ""},
            {"project_id": None},
            {"port": self.ports[0] + 65536},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.registry.reserve({**self.payload, **patch})
        self.assertEqual(self.registry.list(), [])
        for configured in ([], [80], [True], range(1024, 1281), [65536]):
            with self.assertRaises(ValueError):
                PortRegistry(self.root / "invalid", allowed_ports=configured)
        self.assertFalse((self.root / "invalid").exists())

    def test_other_home_has_no_ownership(self):
        other = PortRegistry(self.root / "other", allowed_ports=self.ports)
        try:
            record = self.registry.reserve(self.payload)
            self.assertEqual(other.list(), [])
            with self.assertRaises(FileNotFoundError):
                other.release(record["id"], 1)
            with self.assertRaises(ConflictError):
                other.reserve(self.payload)
            entry = next(
                x for x in other.inventory()["ports"] if x["port"] == record["port"]
            )
            self.assertIsNone(entry["reservation_id"])
            self.assertFalse(entry["available"])
            other.close()
            with socket.socket() as outsider, self.assertRaises(OSError):
                outsider.bind(("127.0.0.1", record["port"]))
        finally:
            other.close()

    def test_exclusive_registry_and_close(self):
        record = self.registry.reserve(self.payload)
        with self.assertRaises(ConflictError):
            PortRegistry(self.root / "ports", allowed_ports=self.ports)
        self.registry.close()
        self.registry.close()
        with socket.socket() as client:
            client.bind(("127.0.0.1", record["port"]))
        self.assertEqual(self.registry.get(record["id"])["status"], "released")
        with self.assertRaises(ConflictError):
            self.registry.reserve({**self.payload, "request_id": "after-close"})
        with self.assertRaises(ConflictError):
            self.registry.inventory()

    def test_bounded_listing_and_inventory_identity(self):
        rows = []
        for index in range(3):
            row = self.registry.reserve({**self.payload, "request_id": f"list-{index}"})
            rows.append(self.registry.release(row["id"], 1))
        self.assertEqual(self.registry.list(limit=2), list(reversed(rows))[:2])
        self.assertEqual(self.registry.list(offset=2), [rows[0]])
        self.assertEqual(self.registry.list(offset=3), [])
        for args in ({"limit": 0}, {"limit": 101}, {"offset": True}, {"offset": -1}):
            with self.assertRaises(ValueError):
                self.registry.list(**args)
        inventory = self.registry.inventory()
        self.assertEqual(inventory["host"], "127.0.0.1")
        self.assertEqual(inventory["transport"], "tcp4")
        self.assertEqual({x["port"] for x in inventory["ports"]}, set(self.ports))
        self.assertTrue(
            all(
                set(x) == {"port", "available", "reservation_id"}
                for x in inventory["ports"]
            )
        )

    def test_singleton_same_owner_and_config_change_refused(self):
        root = self.root / "singleton"
        one = get_port_registry(root, allowed_ports=self.ports)
        try:
            two = get_port_registry(root, allowed_ports=reversed(self.ports))
            self.assertIs(one, two)
            with self.assertRaises(ConflictError):
                get_port_registry(root, allowed_ports=self.ports[:1])
        finally:
            close_port_registry(root)
        self.assertTrue(one.closed)
        close_port_registry(root)


class PortHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prior = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = str(self.root)
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )

        reset_secret_cache()
        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace/ports"
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        response = await self.client.get(
            self.url + "?token=" + generate_token("ports-owner")
        )
        self.assertEqual(response.status, 200, await response.text())

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        if self.prior is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = self.prior
        self.temp.cleanup()

    async def test_real_http_and_native_release_share_socket(self):
        from gideon.workspace.capabilities.workspace.tools import create_provider

        provider = create_provider()
        response = await self.client.post(
            self.url, json={"project_id": "http-app", "request_id": uuid4().hex}
        )
        self.assertEqual(response.status, 200, await response.text())
        row = await response.json()
        with socket.socket() as outsider, self.assertRaises(OSError):
            outsider.bind(("127.0.0.1", row["port"]))
        result = await provider.invoke("workspace_ports", {})
        self.assertTrue(result.success, result.error)
        self.assertEqual(json.loads(result.output), [row])
        inventory = await provider.invoke("workspace_port_inventory", {})
        self.assertTrue(inventory.success)
        self.assertTrue(
            any(
                x["reservation_id"] == row["id"]
                for x in json.loads(inventory.output)["ports"]
            )
        )
        released = await provider.invoke(
            "workspace_port_release", {"id": row["id"], "revision": row["revision"]}
        )
        self.assertTrue(released.success, released.error)
        self.assertEqual(json.loads(released.output)["status"], "released")
        with socket.socket() as outsider:
            outsider.bind(("127.0.0.1", row["port"]))
        started = await provider.invoke(
            "workspace_port_reserve",
            {"project_id": "tool-app", "request_id": uuid4().hex},
        )
        self.assertTrue(started.success, started.error)
        second = json.loads(started.output)
        response = await self.client.post(
            self.url + "/" + second["id"] + "/release",
            json={"revision": second["revision"]},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"], "released")

    async def test_auth_validation_and_conflict_envelopes(self):
        async with ClientSession() as anonymous:
            response = await anonymous.get(self.url + "/inventory")
            self.assertIn(response.status, (401, 403))
        response = await self.client.post(
            self.url, json={"project_id": "a", "request_id": "same"}
        )
        row = await response.json()
        response = await self.client.post(
            self.url, json={"project_id": "b", "request_id": "same"}
        )
        self.assertEqual(response.status, 409)
        response = await self.client.post(
            self.url + "/" + row["id"] + "/release", json={"revision": 0}
        )
        self.assertEqual(response.status, 409)
        response = await self.client.post(
            self.url, json={"project_id": "b", "request_id": "bad", "host": "0.0.0.0"}
        )
        self.assertEqual(response.status, 400)
        response = await self.client.get(self.url + "/missing")
        self.assertEqual(response.status, 404)
        from gideon.workspace.capabilities.workspace.tools import create_provider

        denied = await create_provider().invoke(
            "workspace_port_reserve",
            {"project_id": "bad", "request_id": "type", "port": True},
        )
        self.assertFalse(denied.success)
        self.assertIn("type", denied.error)

    async def test_actual_crash_releases_socket_and_marks_history(self):
        port = free_ports(1)[0]
        folder = self.root / "crashed"
        script = Path(__file__).with_name("port_worker.py")
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            str(folder),
            str(port),
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            row = json.loads(await asyncio.wait_for(worker.stdout.readline(), 10))
            with socket.socket() as outsider, self.assertRaises(OSError):
                outsider.bind(("127.0.0.1", port))
            worker.kill()
            await worker.wait()
            recovered = PortRegistry(folder, allowed_ports=[port])
            try:
                current = recovered.get(row["id"])
                self.assertEqual(current["status"], "interrupted")
                self.assertEqual(current["revision"], 2)
                self.assertIsNotNone(current["released_at"])
                self.assertEqual(recovered.sockets, {})
                self.assertTrue(recovered.inventory()["ports"][0]["available"])
                with socket.socket() as outsider:
                    outsider.bind(("127.0.0.1", port))
                    self.assertEqual(recovered.release(current["id"], 2), current)
                    self.assertEqual(outsider.getsockname()[1], port)
            finally:
                recovered.close()
        finally:
            if worker.returncode is None:
                worker.kill()
                await worker.wait()


if __name__ == "__main__":
    unittest.main()
