import json
import os
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, web

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.workspace.capabilities.workspace.storage import StorageDiagnosis


def tree_snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        result[str(path.relative_to(root))] = (
            stat.st_mode,
            stat.st_size,
            stat.st_mtime_ns,
            path.is_symlink(),
        )
    return result


class StorageRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_home = os.environ.get("GIDEON_HOME")
        self.home = self.root / "gideon"
        self.home.mkdir()
        os.environ["GIDEON_HOME"] = str(self.home)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.project = self.workspaces / "alpha"
        self.project.mkdir()
        self.record = HierarchyStore().create_project(
            "Alpha", workspace_dir=str(self.project)
        )
        self.service = StorageDiagnosis(
            self.home, allowed_roots=[self.workspaces], entry_limit=50
        )

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = self.old_home
        self.temp.cleanup()

    def test_real_bytes_hardlinks_and_canonical_metadata_are_attributed(self):
        source = self.project / "source.bin"
        source.write_bytes(b"actual-storage-bytes")
        os.link(source, self.project / "same-inode.bin")
        nested = self.project / "nested"
        nested.mkdir()
        (nested / "more.txt").write_text("more", encoding="utf-8")
        report = self.service.report(self.record.id)
        self.assertEqual(len(report["projects"]), 1)
        row = report["projects"][0]
        self.assertEqual(row["project_id"], self.record.id)
        self.assertEqual(row["name"], "Alpha")
        self.assertEqual(row["workspace"], str(self.project))
        self.assertEqual(
            row["workspace_usage"]["bytes"], len(b"actual-storage-bytes") + 4
        )
        self.assertEqual(row["workspace_usage"]["files"], 3)
        self.assertEqual(row["workspace_usage"]["directories"], 1)
        self.assertTrue(row["workspace_usage"]["complete"])
        self.assertGreater(row["metadata_usage"]["bytes"], 0)
        self.assertGreater(report["runtime_usage"]["bytes"], 0)
        self.assertEqual(report["entry_limit"], 50)
        self.assertTrue(report["generated_at"].endswith("+00:00"))

    def test_symlinks_are_counted_but_never_followed(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "secret.bin").write_bytes(b"secret-outside-canonical-project")
        (self.project / "foreign-link").symlink_to(foreign, target_is_directory=True)
        (self.project / "file-link").symlink_to(foreign / "secret.bin")
        (self.project / "owned.txt").write_bytes(b"owned")
        result = self.service.scan({"scope": "project", "project_id": self.record.id})
        self.assertEqual(result["scope"], "project")
        self.assertEqual(result["usage"]["bytes"], 5)
        self.assertEqual(result["usage"]["files"], 1)
        self.assertEqual(result["usage"]["skipped_symlinks"], 2)
        self.assertTrue(result["usage"]["complete"])
        self.assertNotIn("secret-outside", json.dumps(result))

    def test_entry_bound_returns_truthful_partial_without_walking_rest(self):
        for index in range(12):
            folder = self.project / f"folder-{index:02d}"
            folder.mkdir()
            (folder / "payload").write_bytes(b"x" * 20)
        result = self.service.scan(
            {"scope": "project", "project_id": self.record.id, "max_entries": 3}
        )
        usage = result["usage"]
        self.assertFalse(usage["complete"])
        self.assertEqual(usage["entries_scanned"], 3)
        self.assertEqual(usage["partial_reasons"], ["entry_limit"])
        self.assertLess(usage["bytes"], 240)

    def test_runtime_scope_is_bound_to_home_and_scan_makes_no_changes(self):
        (self.home / "runtime.dat").write_bytes(b"runtime-owned")
        (self.project / "project.dat").write_bytes(b"project-owned")
        before_home = tree_snapshot(self.home)
        before_project = tree_snapshot(self.project)
        result = self.service.scan({"scope": "runtime"})
        self.assertEqual(result["scope"], "runtime")
        self.assertGreaterEqual(result["usage"]["bytes"], len(b"runtime-owned"))
        self.assertNotIn(str(self.project), json.dumps(result))
        self.assertEqual(tree_snapshot(self.home), before_home)
        self.assertEqual(tree_snapshot(self.project), before_project)

    def test_only_fixed_scopes_limits_and_canonical_ids_are_accepted(self):
        invalid = [
            {},
            {"scope": "host"},
            {"scope": "runtime", "project_id": self.record.id},
            {"scope": "runtime", "path": "/"},
            {"scope": "runtime", "max_entries": 0},
            {"scope": "runtime", "max_entries": 51},
            {"scope": "runtime", "max_entries": True},
            {"scope": "project"},
            {"scope": "project", "project_id": ""},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.scan(payload)
        with self.assertRaises(FileNotFoundError):
            self.service.scan({"scope": "project", "project_id": "p-missing"})

    def test_canonical_project_outside_allowed_roots_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        project = HierarchyStore().create_project("Outside", workspace_dir=str(outside))
        with self.assertRaisesRegex(ValueError, "outside allowed roots"):
            self.service.scan({"scope": "project", "project_id": project.id})
        link = self.workspaces / "linked"
        link.symlink_to(outside, target_is_directory=True)
        linked = HierarchyStore().create_project("Linked", workspace_dir=str(link))
        with self.assertRaisesRegex(ValueError, "safe directory"):
            self.service.scan({"scope": "project", "project_id": linked.id})

    def test_filesystem_root_cannot_be_a_canonical_project(self):
        project = HierarchyStore().create_project(
            "Root", workspace_dir=str(self.project)
        )
        record = self.home / "projects" / project.id / "project.json"
        payload = json.loads(record.read_text())
        payload["workspace_dir"] = "/"
        record.write_text(json.dumps(payload))
        service = StorageDiagnosis(self.home, allowed_roots=[Path("/")])
        with self.assertRaisesRegex(ValueError, "safe directory"):
            service.scan({"scope": "project", "project_id": project.id})


class StorageHttpAndTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_home = os.environ.get("GIDEON_HOME")
        os.environ["GIDEON_HOME"] = str(self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "measured.txt").write_text(
            "measured through real HTTP", encoding="utf-8"
        )
        self.project = HierarchyStore().create_project(
            "HTTP project", workspace_dir=str(self.repo)
        )
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )

        reset_secret_cache()
        self.token = generate_token("storage-owner")
        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/workspace/storage"
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        response = await self.client.get(self.url + "?token=" + self.token)
        self.assertEqual(response.status, 200, await response.text())

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        if self.old_home is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = self.old_home
        self.temp.cleanup()

    async def test_owner_http_aggregate_and_bounded_scan_share_real_state(self):
        aggregate = await self.client.get(self.url)
        self.assertEqual(aggregate.status, 200)
        body = await aggregate.json()
        row = next(
            item for item in body["projects"] if item["project_id"] == self.project.id
        )
        self.assertEqual(
            row["workspace_usage"]["bytes"], len("measured through real HTTP")
        )
        scan = await self.client.post(
            self.url + "/scan",
            json={"scope": "project", "project_id": self.project.id, "max_entries": 1},
        )
        self.assertEqual(scan.status, 200)
        selected = await scan.json()
        self.assertEqual(selected["project_id"], self.project.id)
        self.assertEqual(selected["usage"]["entries_scanned"], 1)

    async def test_http_refuses_apps_unknown_queries_paths_and_unknown_projects(self):
        bad_query = await self.client.get(self.url + "?path=/")
        self.assertEqual(bad_query.status, 400)
        bad_body = await self.client.post(
            self.url + "/scan", json={"scope": "runtime", "path": "/"}
        )
        self.assertEqual(bad_body.status, 400)
        missing = await self.client.get(self.url + "?project_id=p-missing")
        self.assertEqual(missing.status, 404)
        self.client.cookie_jar.clear()
        denied = await self.client.get(self.url)
        self.assertEqual(denied.status, 403)

    async def test_native_tool_is_safe_and_uses_same_canonical_project(self):
        from gideon.workspace.capabilities.workspace.tools import create_provider

        provider = create_provider()
        definitions = {item.name: item for item in await provider.list_tools()}
        definition = definitions["workspace_storage_diagnosis"]
        self.assertFalse(definition.requires_approval)
        result = await provider.invoke(
            "workspace_storage_diagnosis", {"project_id": self.project.id}
        )
        self.assertTrue(result.success, result.error)
        body = json.loads(result.output)
        row = next(
            item for item in body["projects"] if item["project_id"] == self.project.id
        )
        self.assertEqual(
            row["workspace_usage"]["bytes"], len("measured through real HTTP")
        )
        invalid = await provider.invoke("workspace_storage_diagnosis", {"path": "/"})
        self.assertFalse(invalid.success)


if __name__ == "__main__":
    unittest.main()
