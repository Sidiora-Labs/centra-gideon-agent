import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, CookieJar, web

from gideon.workspace.capabilities.workspace import ConflictError, SnapshotStore


def git(path, *args):
    return (
        subprocess.check_output(
            ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )


def repository(path):
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Workspace Test")
    (path / "note.txt").write_text("original\n")
    git(path, "add", "note.txt")
    git(path, "commit", "-m", "Initial context")
    return path


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = repository(self.root / "repo")
        self.store = SnapshotStore(self.root / "state", allowed_roots=[self.root])
        self.input = {
            "project_id": "project-one",
            "workspace": str(self.repo),
            "terminal_ids": [],
            "task_ids": [],
            "request_id": uuid4().hex,
        }

    def capture(self, **changes):
        return self.store.capture(
            {**self.input, **changes},
            terminal_ids=["shell-a", "shell-b"],
            task_ids=["task-a"],
        )

    def test_persistence_and_real_process_reconciliation(self):
        one = subprocess.Popen(["sleep", "120"])
        two = subprocess.Popen(["sleep", "120"])
        self.addCleanup(lambda: one.poll() is None and one.terminate())
        self.addCleanup(lambda: two.poll() is None and two.terminate())
        self.input["terminal_ids"] = [str(one.pid), str(two.pid)]
        saved = self.store.capture(
            self.input, terminal_ids=[str(one.pid), str(two.pid)], task_ids=[]
        )
        self.assertEqual(saved["branch"], "main")
        self.assertIs(saved["dirty"], False)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["project_id"], "project-one")
        self.assertTrue(saved["captured_at"].endswith("+00:00"))
        reopened = SnapshotStore(self.root / "state", allowed_roots=[self.root])
        self.assertEqual(reopened.get(saved["id"]), saved)
        git(self.repo, "checkout", "-b", "next")
        (self.repo / "note.txt").write_text("unfinished\n")
        one.terminate()
        one.wait(timeout=5)
        current = [str(p.pid) for p in [one, two] if p.poll() is None]
        result = reopened.reconcile(saved["id"], terminal_ids=current, task_ids=[])
        self.assertEqual(result["surviving_terminal_ids"], [str(two.pid)])
        self.assertEqual(result["missing_terminal_ids"], [str(one.pid)])
        self.assertFalse(result["branch_matches"])
        self.assertTrue(result["live"]["dirty"])
        self.assertEqual(result["live"]["branch"], "next")
        self.assertEqual(result["snapshot"], saved)
        self.assertEqual((self.repo / "note.txt").read_text(), "unfinished\n")
        self.assertEqual(git(self.repo, "branch", "--show-current"), "next")
        two.terminate()
        two.wait(timeout=5)

    def test_detached_head_and_missing_task(self):
        git(self.repo, "checkout", "--detach")
        saved = self.capture(task_ids=["task-a"])
        self.assertIsNone(saved["branch"])
        compared = self.store.reconcile(saved["id"], terminal_ids=[], task_ids=[])
        self.assertIsNone(compared["branch_matches"])
        self.assertEqual(compared["missing_task_ids"], ["task-a"])
        self.assertEqual(compared["surviving_task_ids"], [])
        self.assertIsNone(compared["live"]["branch"])

    def test_retry_preserves_original_after_workspace_changes(self):
        saved = self.capture()
        git(self.repo, "checkout", "-b", "later")
        self.assertEqual(self.capture(), saved)
        self.assertEqual(len(self.store.list()), 1)
        with self.assertRaises(ConflictError):
            self.capture(project_id="other")
        self.assertEqual(self.store.get(saved["id"]), saved)

    def test_concurrent_retry_one_record(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(lambda _: self.capture(), range(8)))
        self.assertEqual(len({r["id"] for r in records}), 1)
        self.assertEqual(self.store.list(), [records[0]])

    def test_deletion_conflict_and_retry(self):
        saved = self.capture()
        with self.assertRaises(ConflictError):
            self.store.delete(saved["id"], 2)
        self.assertEqual(self.store.get(saved["id"]), saved)
        self.assertTrue(self.store.delete(saved["id"], 1)["deleted"])
        self.assertTrue(self.store.delete(saved["id"], 1)["deleted"])
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(FileNotFoundError):
            self.store.get(saved["id"])
        with self.assertRaises(ConflictError):
            self.capture()
        with self.assertRaises(ValueError):
            self.store.delete(saved["id"], True)

    def test_workspace_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as other:
            foreign = repository(Path(other) / "outside")
            with self.assertRaises(ValueError):
                self.capture(workspace=str(foreign))
            (self.root / "link").symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.capture(workspace=str(self.root / "link"))
        with self.assertRaises(ValueError):
            self.capture(workspace="relative")
        with self.assertRaises(FileNotFoundError):
            self.capture(workspace=str(self.root / "missing"))
        with self.assertRaises(ValueError):
            self.capture(workspace=str(self.repo / "note.txt"))

    def test_git_metadata_escape(self):
        linked = self.root / "linked"
        git(self.repo, "worktree", "add", str(linked), "-b", "linked")
        restricted = SnapshotStore(self.root / "restricted", allowed_roots=[linked])
        with self.assertRaises(ValueError):
            restricted.capture(
                {**self.input, "workspace": str(linked)}, terminal_ids=[], task_ids=[]
            )
        saved = self.capture(workspace=str(linked))
        self.assertEqual(saved["branch"], "linked")

    def test_nonrepository_and_removed_workspace(self):
        plain = self.root / "plain"
        plain.mkdir()
        with self.assertRaises(ValueError):
            self.capture(workspace=str(plain))
        saved = self.capture()
        self.repo.rename(self.root / "moved")
        with self.assertRaises(FileNotFoundError):
            self.store.reconcile(saved["id"], terminal_ids=[], task_ids=[])
        self.assertEqual(self.store.get(saved["id"]), saved)

    def test_capture_cannot_invent_reference_or_state(self):
        for patch in (
            {"task_ids": ["unknown"]},
            {"terminal_ids": ["unknown"]},
            {"terminal_ids": ["shell-a", "shell-a"]},
            {"task_ids": "task-a"},
            {"branch": "invented"},
            {"dirty": False},
            {"project_id": ""},
            {"request_id": ""},
            {"task_ids": [1]},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.capture(**patch)
        self.assertEqual(self.store.list(), [])
        saved = self.capture(terminal_ids=["shell-a"], task_ids=["task-a"])
        self.assertEqual(saved["terminal_ids"], ["shell-a"])
        self.assertEqual(saved["task_ids"], ["task-a"])

    def test_pagination_and_independent_homes(self):
        records = [self.capture(request_id=f"request-{i}") for i in range(4)]
        self.assertEqual(self.store.list(limit=2), list(reversed(records))[:2])
        self.assertEqual(
            self.store.list(offset=2, limit=2), list(reversed(records))[2:]
        )
        self.assertEqual(self.store.list(offset=4), [])
        for kwargs in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": True}):
            with self.assertRaises(ValueError):
                self.store.list(**kwargs)
        other = SnapshotStore(self.root / "other-home", allowed_roots=[self.root])
        self.assertEqual(other.list(), [])
        with self.assertRaises(FileNotFoundError):
            other.get(records[0]["id"])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_git_read_does_not_run_fsmonitor_or_inherit_git_dir(self):
        marker = self.root / "executed"
        hook = self.root / "monitor"
        hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
        hook.chmod(0o700)
        git(self.repo, "config", "core.fsmonitor", str(hook))
        old = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = str(self.root / "nonexistent")
        try:
            saved = self.capture()
        finally:
            if old is None:
                os.environ.pop("GIT_DIR")
            else:
                os.environ["GIT_DIR"] = old
        self.assertEqual(saved["branch"], "main")
        self.assertFalse(marker.exists())


class HttpJourney(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_home = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = str(self.root)
        self.repo = repository(self.root / "repo")
        (self.root / "config.json").write_text(
            json.dumps({"dashboard": {"terminal": {"enabled": False}}})
        )
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )

        reset_secret_cache()
        self.app = web.Application(middlewares=[token_auth_middleware()])
        register(self.app)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.token = generate_token("workspace-owner")
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        response = await self.client.get(
            self.url + "/api/capabilities/workspace?token=" + self.token
        )
        self.assertEqual(response.status, 200, await response.text())
        self.body = {
            "project_id": "real-project",
            "workspace": str(self.repo),
            "request_id": uuid4().hex,
            "terminal_ids": [],
            "task_ids": [],
        }

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        if self.old_home is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = self.old_home
        self.temp.cleanup()

    async def test_authenticated_capture_compare_delete(self):
        response = await self.client.post(
            self.url + "/api/capabilities/workspace", json=self.body
        )
        self.assertEqual(response.status, 200, await response.text())
        record = await response.json()
        self.assertEqual(record["project_id"], "real-project")
        self.assertEqual(record["branch"], "main")
        self.assertFalse(record["dirty"])
        path = self.url + "/api/capabilities/workspace/" + record["id"]
        response = await self.client.get(path)
        self.assertEqual(await response.json(), record)
        git(self.repo, "checkout", "-b", "http-branch")
        response = await self.client.post(path + "/reconcile", json={})
        self.assertEqual(response.status, 200, await response.text())
        compared = await response.json()
        self.assertFalse(compared["branch_matches"])
        self.assertEqual(compared["live"]["branch"], "http-branch")
        self.assertEqual(compared["missing_terminal_ids"], [])
        response = await self.client.delete(path + "?revision=2")
        self.assertEqual(response.status, 409)
        response = await self.client.delete(path + "?revision=1")
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["deleted"])
        response = await self.client.get(path)
        self.assertEqual(response.status, 404)
        response = await self.client.get(self.url + "/api/capabilities/workspace")
        self.assertEqual(await response.json(), [])

    async def test_http_bad_inputs_and_owner_boundary(self):
        async with ClientSession() as anonymous:
            response = await anonymous.get(self.url + "/api/capabilities/workspace")
            self.assertIn(response.status, [401, 403])
        response = await self.client.post(
            self.url + "/api/capabilities/workspace",
            json={**self.body, "workspace": "/etc"},
        )
        self.assertEqual(response.status, 400)
        response = await self.client.post(
            self.url + "/api/capabilities/workspace",
            json={**self.body, "terminal_ids": ["invented"]},
        )
        self.assertEqual(response.status, 400)
        response = await self.client.get(
            self.url + "/api/capabilities/workspace?limit=1000"
        )
        self.assertEqual(response.status, 400)
        response = await self.client.post(
            self.url + "/api/capabilities/workspace", json=[]
        )
        self.assertEqual(response.status, 400)
        response = await self.client.get(self.url + "/api/capabilities/workspace")
        self.assertEqual(await response.json(), [])


if __name__ == "__main__":
    unittest.main()
