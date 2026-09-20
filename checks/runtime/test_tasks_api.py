"""HTTP-level tests for the task API: project/task-list CRUD, ready, search,
bulk ops, repeatable reset, and the exit-criteria complete-gate."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    """A test client over the task routes with isolated filesystem stores."""
    registry._providers.clear()
    with (
        patch("gideon.engine.tasks.native.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


@pytest.mark.asyncio
async def test_default_projects_listed(tmp_path):
    async with _client(tmp_path) as client:
        resp = await client.get("/api/projects")
        assert resp.status == 200
        names = {p["name"] for p in (await resp.json())["projects"]}
        assert {"Personal", "Repeatable"} <= names


@pytest.mark.asyncio
async def test_project_create_get_update_delete(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/projects", json={"name": "Website"})
        assert r.status == 201
        pid = (await r.json())["id"]
        assert (await client.get(f"/api/projects/{pid}")).status == 200
        r = await client.put(f"/api/projects/{pid}", json={"name": "Site"})
        assert (await r.json())["name"] == "Site"
        assert (await client.delete(f"/api/projects/{pid}")).status == 200
        assert (await client.get(f"/api/projects/{pid}")).status == 404


@pytest.mark.asyncio
async def test_project_linked_lists_bound_loops_and_code(tmp_path):
    from gideon.automation.loop import store as loop_store
    from gideon.automation.loop.loop import Loop

    with patch("gideon.automation.loop.files.config_dir", return_value=tmp_path):
        async with _client(tmp_path) as client:
            pid = (
                await (
                    await client.post("/api/projects", json={"name": "Effort"})
                ).json()
            )["id"]
            loop_store.create(
                Loop(id="", kind="goal", name="Loopy", task="g" * 30, project_id=pid)
            )
            loop_store.create(
                Loop(
                    id="",
                    kind="code",
                    name="Codey",
                    task="t" * 20,
                    tasks_project_id=pid,
                )
            )
            loop_store.create(Loop(id="", kind="goal", name="Other", task="x" * 30))
            from gideon.workspace.artifacts import registry as art_reg
            from gideon.workspace.artifacts.native import NativeArtifactProvider

            prov = NativeArtifactProvider(root=tmp_path / "artifacts")
            prov.create(name="Spec", content="<p>x</p>", project_id=pid)
            own_loop_id = next(
                lp.id for lp in loop_store.list_all() if lp.project_id == pid
            )
            code_id = next(
                lp.id for lp in loop_store.list_all() if lp.tasks_project_id == pid
            )
            other_id = next(
                lp.id
                for lp in loop_store.list_all()
                if pid not in (lp.project_id, lp.tasks_project_id)
            )
            prov.create(
                name="Deliverable", content="x", tags=["loop", f"loop:{own_loop_id}"]
            )
            prov.create(name="CodeOut", content="x", tags=[f"loop:{code_id}"])
            prov.create(
                name="ForeignDeliverable", content="x", tags=[f"loop:{other_id}"]
            )
            prov.create(
                name="Both", content="x", project_id=pid, tags=[f"loop:{own_loop_id}"]
            )
            with patch.object(art_reg, "get_provider", lambda name=None: prov):
                r = await client.get(f"/api/projects/{pid}/linked")
            assert r.status == 200
            body = await r.json()
            assert [e["name"] for e in body["loops"]] == ["Loopy"]
            assert [c["name"] for c in body["code"]] == ["Codey"]
            got = sorted(a["name"] for a in body["artifacts"])
            assert got == ["Both", "CodeOut", "Deliverable", "Spec"]
            assert (
                "error_message" in body["loops"][0]
                and "error_message" in body["code"][0]
            )


@pytest.mark.asyncio
async def test_project_linked_missing_404(tmp_path):
    async with _client(tmp_path) as client:
        assert (await client.get("/api/projects/p-nope/linked")).status == 404


@pytest.mark.asyncio
async def test_project_delete_blocked_by_bound_work_unless_forced(tmp_path):
    from gideon.automation.loop import store as loop_store
    from gideon.automation.loop.loop import Loop

    with patch("gideon.automation.loop.files.config_dir", return_value=tmp_path):
        async with _client(tmp_path) as client:
            pid = (
                await (await client.post("/api/projects", json={"name": "Busy"})).json()
            )["id"]
            lp = loop_store.create(
                Loop(id="", kind="goal", name="L", task="g" * 30, project_id=pid)
            )
            r = await client.delete(f"/api/projects/{pid}")
            assert r.status == 409
            body = await r.json()
            assert body["loops"] == 1 and body["code"] == 0
            assert (
                await client.delete(f"/api/projects/{pid}?force=true")
            ).status == 200
            assert (await client.get(f"/api/projects/{pid}")).status == 404
            assert loop_store.get(lp.id) is None


@pytest.mark.asyncio
async def test_project_delete_guard_counts_chats_and_force_unbinds_them(tmp_path):
    class _FakeChat:
        def __init__(self, key, pid):
            self.key = key
            self.project_id = pid
            self._app = ""
            self.title = key

    async with _client(tmp_path) as client:
        chat = _FakeChat("chat-1-999", None)
        client.app["state"] = type("S", (), {"_sessions": {}})()
        pid = (
            await (await client.post("/api/projects", json={"name": "Chatty"})).json()
        )["id"]
        chat.project_id = pid
        client.app["state"]._sessions["chat-1-999"] = chat
        r = await client.delete(f"/api/projects/{pid}")
        assert r.status == 409
        body = await r.json()
        assert body["chats"] == 1 and body["loops"] == 0 and body["code"] == 0
        assert (await client.delete(f"/api/projects/{pid}?force=true")).status == 200
        assert (await client.get(f"/api/projects/{pid}")).status == 404
        assert chat.project_id == ""
        assert "chat-1-999" in client.app["state"]._sessions


@pytest.mark.asyncio
async def test_project_delete_clean_when_no_bound_work(tmp_path):
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Free"})).json()
        )["id"]
        assert (await client.delete(f"/api/projects/{pid}")).status == 200


@pytest.mark.asyncio
async def test_project_payload_includes_context_dir_and_counts(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/projects", json={"name": "Rich", "workspace_dir": "/tmp/repo"}
        )
        body = await r.json()
        assert body["workspace_dir"] == "/tmp/repo"
        assert body["context_dir"].endswith(f"/projects/{body['id']}/context")
        assert body["task_list_count"] == 0
        await client.post(
            "/api/task-lists", json={"name": "L1", "project_id": body["id"]}
        )
        projects = (await (await client.get("/api/projects")).json())["projects"]
        rich = next(p for p in projects if p["id"] == body["id"])
        assert rich["task_list_count"] == 1 and "context_dir" in rich


@pytest.mark.asyncio
async def test_project_duplicate_name_400(tmp_path):
    async with _client(tmp_path) as client:
        await client.post("/api/projects", json={"name": "Dup"})
        r = await client.post("/api/projects", json={"name": "Dup"})
        assert r.status == 400


@pytest.mark.asyncio
async def test_default_project_undeletable(tmp_path):
    async with _client(tmp_path) as client:
        projects = (await (await client.get("/api/projects")).json())["projects"]
        personal = next(p for p in projects if p["name"] == "Personal")
        assert (await client.delete(f"/api/projects/{personal['id']}")).status == 400


@pytest.mark.asyncio
async def test_default_project_rename_refused_no_duplicate(tmp_path):
    async with _client(tmp_path) as client:
        projects = (await (await client.get("/api/projects")).json())["projects"]
        personal = next(p for p in projects if p["name"] == "Personal")
        r = await client.put(
            f"/api/projects/{personal['id']}", json={"name": "Renamed"}
        )
        assert r.status == 400
        assert "cannot be renamed" in (await r.json())["error"]
        r = await client.put(
            f"/api/projects/{personal['id']}", json={"brief": "Catch-all"}
        )
        assert r.status == 200 and (await r.json())["brief"] == "Catch-all"
        after = (await (await client.get("/api/projects")).json())["projects"]
        assert len([p for p in after if p["name"] == "Personal"]) == 1


@pytest.mark.asyncio
async def test_project_update_refuses_sensitive_workspace_dir(tmp_path):
    async with _client(tmp_path) as client:
        pid = (
            await (
                await client.post(
                    "/api/projects",
                    json={"name": "Bound", "workspace_dir": "/tmp/repo"},
                )
            ).json()
        )["id"]
        for unsafe in (
            str(Path.home() / ".ssh"),
            str(Path.home() / ".aws"),
            "/etc",
            "/tmp",
        ):
            r = await client.put(f"/api/projects/{pid}", json={"workspace_dir": unsafe})
            assert r.status == 403, unsafe
            assert "system or sensitive" in (await r.json())["error"]
            stored = (await (await client.get(f"/api/projects/{pid}")).json())[
                "workspace_dir"
            ]
            assert stored == "/tmp/repo", unsafe
        r = await client.put(
            f"/api/projects/{pid}", json={"workspace_dir": "/tmp/other"}
        )
        assert r.status == 200 and (await r.json())["workspace_dir"] == "/tmp/other"
        r = await client.put(f"/api/projects/{pid}", json={"workspace_dir": ""})
        assert r.status == 200 and (await r.json())["workspace_dir"] == ""


@pytest.mark.asyncio
async def test_project_create_refuses_sensitive_workspace_dir(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/projects",
            json={"name": "Creds", "workspace_dir": str(Path.home() / ".ssh")},
        )
        assert r.status == 403
        names = {
            p["name"]
            for p in (await (await client.get("/api/projects")).json())["projects"]
        }
        assert "Creds" not in names


@pytest.mark.asyncio
async def test_project_update_rejects_unknown_and_reserved_keys(tmp_path):
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Strict"})).json()
        )["id"]
        for key, value in (
            ("id", "p-hijack"),
            ("is_builtin", True),
            ("created_at", "1999-01-01T00:00:00Z"),
            ("nope", "x"),
            ("self", "x"),
            ("project_id", "x"),
        ):
            r = await client.put(f"/api/projects/{pid}", json={key: value})
            assert r.status == 400, key
            assert key in (await r.json())["error"], key
        assert (await (await client.get(f"/api/projects/{pid}")).json())[
            "name"
        ] == "Strict"
        r = await client.put(
            f"/api/projects/{pid}",
            json={
                "name": "Renamed",
                "name_locked": True,
                "status": "archived",
                "brief": "the why",
                "agent_instructions_template": "be brief",
                "workspace_dir": "/tmp/repo",
            },
        )
        assert r.status == 200
        body = await r.json()
        assert body["name"] == "Renamed" and body["name_locked"] is True
        assert body["status"] == "archived" and body["brief"] == "the why"
        assert body["agent_instructions_template"] == "be brief"
        assert body["workspace_dir"] == "/tmp/repo"


@pytest.mark.asyncio
async def test_task_list_update_rejects_unknown_and_reserved_keys(tmp_path):
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Holder"})).json()
        )["id"]
        tl = await (
            await client.post("/api/task-lists", json={"name": "L", "project_id": pid})
        ).json()
        for key in ("self", "list_id", "id", "created_at", "nope"):
            r = await client.put(f"/api/task-lists/{tl['id']}", json={key: "x"})
            assert r.status == 400, key
            assert key in (await r.json())["error"], key
        assert (await (await client.get(f"/api/task-lists/{tl['id']}")).json())[
            "name"
        ] == "L"
        r = await client.put(
            f"/api/task-lists/{tl['id']}",
            json={
                "name": "Renamed",
                "project_id": pid,
                "agent_instructions_template": "go",
            },
        )
        assert r.status == 200
        body = await r.json()
        assert body["name"] == "Renamed" and body["agent_instructions_template"] == "go"


@pytest.mark.asyncio
async def test_task_list_routes_to_personal_by_default(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (await client.post("/api/task-lists", json={"name": "Misc"})).json()
        projects = (await (await client.get("/api/projects")).json())["projects"]
        personal = next(p for p in projects if p["name"] == "Personal")
        assert tl["project_id"] == personal["id"]


@pytest.mark.asyncio
async def test_task_list_under_project_and_filter(tmp_path):
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "P"})).json())[
            "id"
        ]
        await client.post("/api/task-lists", json={"name": "L1", "project_id": pid})
        await client.post("/api/task-lists", json={"name": "L2", "project_id": pid})
        r = await client.get(f"/api/task-lists?project_id={pid}")
        assert len((await r.json())["task_lists"]) == 2


@pytest.mark.asyncio
async def test_create_with_project_id_attaches_general_list(tmp_path):
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Proj"})).json()
        )["id"]
        t1 = await (
            await client.post("/api/tasks", json={"title": "T1", "project_id": pid})
        ).json()
        assert t1["project"] == "Proj"
        assert t1["task_list_id"]
        t2 = await (
            await client.post("/api/tasks", json={"title": "T2", "project_id": pid})
        ).json()
        assert t2["task_list_id"] == t1["task_list_id"]
        lists = await (await client.get(f"/api/task-lists?project_id={pid}")).json()
        assert [tl["name"] for tl in lists["task_lists"]] == ["General"]
        named = await (
            await client.post(
                "/api/task-lists", json={"name": "Named", "project_id": pid}
            )
        ).json()
        t3 = await (
            await client.post(
                "/api/tasks",
                json={"title": "T3", "project_id": pid, "task_list_id": named["id"]},
            )
        ).json()
        assert t3["task_list_id"] == named["id"]


@pytest.mark.asyncio
async def test_task_writes_reject_unknown_parent_ids_without_creating_orphans(tmp_path):
    async with _client(tmp_path) as client:
        for payload in (
            {"title": "Bad project", "project_id": "p-missing"},
            {"title": "Bad list", "task_list_id": "tl-missing"},
        ):
            response = await client.post("/api/tasks", json=payload)
            assert response.status == 400
        assert (await (await client.get("/api/tasks")).json())["total"] == 0


@pytest.mark.asyncio
async def test_task_update_rejects_unknown_parent_without_changing_task(tmp_path):
    async with _client(tmp_path) as client:
        task = await (await client.post("/api/tasks", json={"title": "Safe"})).json()
        response = await client.put(
            f"/api/tasks/{task['id']}", json={"task_list_id": "tl-missing"}
        )
        assert response.status == 400
        stored = await (await client.get(f"/api/tasks/{task['id']}")).json()
        assert stored["task_list_id"] == ""


@pytest.mark.asyncio
async def test_bulk_parent_validation_aborts_before_any_task_write(tmp_path):
    async with _client(tmp_path) as client:
        response = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [
                    {"title": "Would otherwise persist"},
                    {"title": "Orphan", "task_list_id": "tl-missing"},
                ],
            },
        )
        assert response.status == 400
        assert (await (await client.get("/api/tasks")).json())["total"] == 0


@pytest.mark.asyncio
async def test_bulk_writes_resolve_project_ids(tmp_path):
    async with _client(tmp_path) as client:
        project = await (
            await client.post("/api/projects", json={"name": "Bulk project"})
        ).json()
        receipt = await (
            await client.post(
                "/api/tasks/bulk",
                json={
                    "op": "create",
                    "items": [{"title": "Bulk child", "project_id": project["id"]}],
                },
            )
        ).json()
        task = await (
            await client.get(f"/api/tasks/{receipt['results'][0]['task_id']}")
        ).json()
        assert task["project"] == "Bulk project"
        assert task["task_list_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", [12345, True, 1.5, [1], {"nested": "obj"}, None])
async def test_non_string_title_is_400_not_500(tmp_path, shape):
    """`body.get("title", "").strip()` raised AttributeError on every non-string
    title — a 500 for what is plainly a client bug (same class as the comment-body
    guard). A coerced-but-truthy value (e.g. str(12345)) must NOT be accepted as a
    title either: it falls through to the emptiness guard and 400s without creating
    a task."""
    async with _client(tmp_path) as client:
        r = await client.post("/api/tasks", json={"title": shape})
        assert r.status == 400
        assert (await r.json())["error"] == {
            "code": "invalid_request",
            "message": "title required",
        }
        assert (await (await client.get("/api/tasks")).json())["total"] == 0


@pytest.mark.asyncio
async def test_ready_excludes_blocked_then_includes_after_done(tmp_path):
    async with _client(tmp_path) as client:
        a = await (await client.post("/api/tasks", json={"title": "A"})).json()
        b = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "B",
                    "dependencies": [
                        {"depends_on_task_id": a["id"], "dependency_type": "BLOCKS"}
                    ],
                },
            )
        ).json()
        ready_ids = {
            t["id"]
            for t in (await (await client.get("/api/tasks/ready")).json())["tasks"]
        }
        assert a["id"] in ready_ids
        assert b["id"] not in ready_ids
        await client.put(f"/api/tasks/{a['id']}", json={"status": "done"})
        ready_ids = {
            t["id"]
            for t in (await (await client.get("/api/tasks/ready")).json())["tasks"]
        }
        assert b["id"] in ready_ids


@pytest.mark.asyncio
async def test_search_query_and_priority_filter(tmp_path):
    async with _client(tmp_path) as client:
        await client.post(
            "/api/tasks",
            json={"title": "Migrate database schema", "priority": "critical"},
        )
        await client.post("/api/tasks", json={"title": "Write docs", "priority": "low"})
        body = await (
            await client.post("/api/tasks/search", json={"query": "database"})
        ).json()
        assert body["total"] == 1
        body = await (
            await client.post("/api/tasks/search", json={"priority": ["critical"]})
        ).json()
        assert body["total"] == 1
        assert body["tasks"][0]["priority"] == "critical"


@pytest.mark.asyncio
async def test_task_pagination_clamps_and_reports_the_applied_window(tmp_path):
    async with _client(tmp_path) as client:
        for title in ("A", "B", "C"):
            await client.post("/api/tasks", json={"title": title})

        listed = await (await client.get("/api/tasks?limit=0&offset=-20")).json()
        assert (listed["limit"], listed["offset"]) == (1, 0)
        assert len(listed["tasks"]) == 1 and listed["total"] == 3

        searched = await (
            await client.post(
                "/api/tasks/search", json={"limit": 100_000, "offset": -20}
            )
        ).json()
        assert (searched["limit"], searched["offset"]) == (500, 0)
        assert len(searched["tasks"]) == 3 and searched["total"] == 3


@pytest.mark.asyncio
async def test_task_list_default_page_is_not_truncated_at_fifty(tmp_path):
    async with _client(tmp_path) as client:
        for index in range(51):
            await client.post("/api/tasks", json={"title": f"Task {index}"})

        listed = await (await client.get("/api/tasks")).json()
        assert listed["total"] == len(listed["tasks"]) == 51


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/tasks?provider=missing", None),
        ("get", "/api/tasks/graph?provider=missing", None),
        ("post", "/api/tasks", {"title": "No", "provider": "missing"}),
        ("get", "/api/tasks/t-nope?provider=missing", None),
        ("put", "/api/tasks/t-nope", {"title": "No", "provider": "missing"}),
        ("delete", "/api/tasks/t-nope?provider=missing", None),
        ("get", "/api/tasks/t-nope/comments?provider=missing", None),
        (
            "post",
            "/api/tasks/t-nope/comments",
            {"body": "No", "provider": "missing"},
        ),
        ("delete", "/api/tasks/t-nope/comments/c-nope?provider=missing", None),
    ],
)
async def test_unknown_task_providers_are_400(tmp_path, method, path, payload):
    async with _client(tmp_path) as client:
        request = getattr(client, method)
        response = (
            await request(path, json=payload)
            if payload is not None
            else await request(path)
        )
        assert response.status == 400
        assert (await response.json())["error"] == {
            "code": "invalid_request",
            "message": "Unknown task provider: missing",
        }


@pytest.mark.asyncio
async def test_bulk_task_provider_selection_matches_single_write_semantics(tmp_path):
    async with _client(tmp_path) as client:
        refused = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [{"title": "No", "provider": "missing"}],
            },
        )
        assert refused.status == 400
        receipt = await refused.json()
        assert receipt["succeeded"] == 0
        assert receipt["errors"][0]["error"] == "Unknown task provider: missing"


@pytest.mark.asyncio
async def test_comment_count_in_list_and_get(tmp_path):
    async with _client(tmp_path) as client:
        t = await (
            await client.post("/api/tasks", json={"title": "Has comments"})
        ).json()
        listed = await (await client.get("/api/tasks")).json()
        assert listed["tasks"][0]["comment_count"] == 0
        assert (await (await client.get(f"/api/tasks/{t['id']}")).json())[
            "comment_count"
        ] == 0
        await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "one"})
        await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "two"})
        listed = await (await client.get("/api/tasks")).json()
        assert listed["tasks"][0]["comment_count"] == 2
        assert (await (await client.get(f"/api/tasks/{t['id']}")).json())[
            "comment_count"
        ] == 2


def _comments_on_disk(tmp_path, task_id):
    """The comment sidecar as persisted — the store, not the response echo."""
    f = tmp_path / "tasks" / f"_comments_{task_id}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


@pytest.mark.asyncio
async def test_comment_author_is_server_derived_not_caller_supplied(tmp_path):
    """The whole point of #554: the stored author is the server's handle."""
    with patch(
        "gideon.engine.tasks.handlers._owner_username", return_value="keyur-golani"
    ):
        async with _client(tmp_path) as client:
            t = await (await client.post("/api/tasks", json={"title": "T"})).json()
            r = await client.post(
                f"/api/tasks/{t['id']}/comments", json={"body": "mine"}
            )
            assert r.status == 201
            assert (await r.json())["author"] == "keyur-golani"
            stored = _comments_on_disk(tmp_path, t["id"])
            assert [c["author"] for c in stored] == ["keyur-golani"]


@pytest.mark.asyncio
async def test_supplied_comment_author_is_refused_and_stores_nothing(tmp_path):
    """A body-supplied author is rejected outright rather than silently dropped: a 201
    that stored a different author than asked would tell a forging client it worked."""
    with patch(
        "gideon.engine.tasks.handlers._owner_username", return_value="keyur-golani"
    ):
        async with _client(tmp_path) as client:
            t = await (await client.post("/api/tasks", json={"title": "T"})).json()
            r = await client.post(
                f"/api/tasks/{t['id']}/comments",
                json={
                    "body": "looks good",
                    "author": "Keyur Golani <keyurrgolani@gmail.com>",
                },
            )
            assert r.status == 400
            assert "author" in (await r.json())["error"]["message"]
            assert _comments_on_disk(tmp_path, t["id"]) == []


@pytest.mark.asyncio
async def test_supplied_comment_author_refused_even_when_it_matches_the_owner(tmp_path):
    """The field is refused on principle. Accepting the ones that happen to match would
    make the endpoint's contract depend on the current username."""
    with patch(
        "gideon.engine.tasks.handlers._owner_username", return_value="keyur-golani"
    ):
        async with _client(tmp_path) as client:
            t = await (await client.post("/api/tasks", json={"title": "T"})).json()
            r = await client.post(
                f"/api/tasks/{t['id']}/comments",
                json={"body": "hi", "author": "keyur-golani"},
            )
            assert r.status == 400
            assert _comments_on_disk(tmp_path, t["id"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", [42, {}, [], True, 1.5, {"nested": "obj"}])
async def test_non_string_comment_body_is_400_not_500(tmp_path, shape):
    """`body.get("body", "").strip()` raised AttributeError on every non-string —
    a 500 for what is plainly a client bug (same class as #591/#441)."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "T"})).json()
        r = await client.post(f"/api/tasks/{t['id']}/comments", json={"body": shape})
        assert r.status == 400
        assert "string" in (await r.json())["error"]["message"]
        assert _comments_on_disk(tmp_path, t["id"]) == []


@pytest.mark.asyncio
async def test_null_and_absent_comment_body_stay_400_body_required(tmp_path):
    """null/absent are legitimately "you forgot the body", not a type error — they must
    keep reaching the emptiness guard rather than being swallowed by the shape check."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "T"})).json()
        for payload in ({"body": None}, {}, {"body": "   "}):
            r = await client.post(f"/api/tasks/{t['id']}/comments", json=payload)
            assert r.status == 400
            assert (await r.json())["error"] == {
                "code": "invalid_request",
                "message": "body required",
            }


@pytest.mark.asyncio
async def test_non_object_comment_body_is_400(tmp_path):
    """A top-level array/scalar has no .get() at all."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "T"})).json()
        for payload in ([1, 2, 3], "hello", 5):
            r = await client.post(f"/api/tasks/{t['id']}/comments", json=payload)
            assert r.status == 400
            assert (await r.json())["error"]["code"] == "invalid_body"


@pytest.mark.asyncio
async def test_comment_delete_removes_it_from_the_store(tmp_path):
    """DELETE is the recoverability half: without it a bad comment was permanent
    through the API and only removable by hand-editing the sidecar."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "T"})).json()
        first = await (
            await client.post(
                f"/api/tasks/{t['id']}/comments", json={"body": "keep me"}
            )
        ).json()
        doomed = await (
            await client.post(
                f"/api/tasks/{t['id']}/comments", json={"body": "delete me"}
            )
        ).json()
        r = await client.delete(f"/api/tasks/{t['id']}/comments/{doomed['id']}")
        assert r.status == 200
        assert (await r.json()) == {"ok": True, "id": doomed["id"]}
        stored = _comments_on_disk(tmp_path, t["id"])
        assert [c["id"] for c in stored] == [first["id"]]
        listed = (await (await client.get(f"/api/tasks/{t['id']}/comments")).json())[
            "comments"
        ]
        assert [c["body"] for c in listed] == ["keep me"]
        assert (await (await client.get(f"/api/tasks/{t['id']}")).json())[
            "comment_count"
        ] == 1


@pytest.mark.asyncio
async def test_comment_delete_unknown_id_is_404(tmp_path):
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "T"})).json()
        await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "one"})
        r = await client.delete(f"/api/tasks/{t['id']}/comments/c-nope")
        assert r.status == 404
        assert (await r.json()) == {
            "error": {"code": "not_found", "message": "not found"}
        }
        assert len(_comments_on_disk(tmp_path, t["id"])) == 1


@pytest.mark.asyncio
async def test_comment_delete_unknown_task_is_404(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.delete("/api/tasks/t-nope/comments/c-nope")
        assert r.status == 404
        assert (await r.json()) == {
            "error": {"code": "not_found", "message": "not found"}
        }


@pytest.mark.asyncio
async def test_comment_delete_is_not_captured_by_the_task_id_matcher(tmp_path):
    """The new route sits under the dynamic /{task_id} prefix; assert aiohttp resolves
    it rather than treating "comments" as a task id (the ordering trap this file's
    register_task_routes comment warns about)."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "T"})).json()
        c = await (
            await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "x"})
        ).json()
        assert (
            await client.delete(f"/api/tasks/{t['id']}/comments/{c['id']}")
        ).status == 200
        assert (await client.get(f"/api/tasks/{t['id']}")).status == 200


@pytest.mark.asyncio
async def test_bulk_create(tmp_path):
    async with _client(tmp_path) as client:
        body = await (
            await client.post(
                "/api/tasks/bulk",
                json={"op": "create", "items": [{"title": "A"}, {"title": "B"}]},
            )
        ).json()
        assert body["succeeded"] == 2
        assert (await (await client.get("/api/tasks")).json())["total"] == 2


@pytest.mark.asyncio
async def test_bulk_validate_all_aborts(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/tasks/bulk",
            json={"op": "create", "items": [{"title": "A"}, {"title": ""}]},
        )
        assert r.status == 400
        assert (await (await client.get("/api/tasks")).json())["total"] == 0


@pytest.mark.asyncio
async def test_reset_requires_repeatable_project(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (await client.post("/api/task-lists", json={"name": "L"})).json()
        r = await client.post(
            f"/api/task-lists/{tl['id']}/reset", json={"confirm": True}
        )
        assert r.status == 400
        assert "Repeatable" in (await r.text())


@pytest.mark.asyncio
async def test_reset_repeatable_list(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "repeatable": True}
            )
        ).json()
        t = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "step",
                    "task_list_id": tl["id"],
                    "exit_criteria": [
                        {
                            "description": "verified",
                            "status": "complete",
                            "comment": "old proof",
                        }
                    ],
                    "action_plan": [{"content": "run it", "completed": True}],
                    "execution_notes": [{"content": "old run"}],
                    "evidence": [{"kind": "gate", "ref": "old"}],
                    "attempts": [{"status": "passed"}],
                },
            )
        ).json()
        await client.put(f"/api/tasks/{t['id']}", json={"status": "done"})
        r = await client.post(
            f"/api/task-lists/{tl['id']}/reset", json={"confirm": True}
        )
        assert r.status == 200
        reloaded = await (await client.get(f"/api/tasks/{t['id']}")).json()
        assert reloaded["status"] == "open"
        assert reloaded["exit_criteria"] == [
            {
                "description": "verified",
                "status": "incomplete",
                "comment": "",
                "met": False,
            }
        ]
        assert reloaded["action_plan"][0]["completed"] is False
        assert reloaded["execution_notes"] == []
        assert reloaded["evidence"] == [] and reloaded["attempts"] == []


@pytest.mark.asyncio
async def test_reopening_repeatable_task_resets_previous_run_state(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "repeatable": True}
            )
        ).json()
        task = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "step",
                    "task_list_id": tl["id"],
                    "status": "done",
                    "exit_criteria": [
                        {"description": "verified", "met": True, "comment": "old"}
                    ],
                    "action_plan": [{"content": "run it", "completed": True}],
                    "execution_notes": [{"content": "old run"}],
                    "evidence": [{"kind": "gate", "ref": "old"}],
                    "attempts": [{"status": "passed"}],
                },
            )
        ).json()

        response = await client.put(f"/api/tasks/{task['id']}", json={"status": "open"})

        assert response.status == 200
        reopened = await response.json()
        assert reopened["status"] == "open"
        assert reopened["exit_criteria"][0]["met"] is False
        assert reopened["exit_criteria"][0]["comment"] == ""
        assert reopened["action_plan"][0]["completed"] is False
        assert reopened["execution_notes"] == []
        assert reopened["evidence"] == [] and reopened["attempts"] == []


@pytest.mark.asyncio
async def test_reset_blocked_when_incomplete(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "repeatable": True}
            )
        ).json()
        await client.post(
            "/api/tasks", json={"title": "step", "task_list_id": tl["id"]}
        )
        assert (
            await client.post(f"/api/task-lists/{tl['id']}/reset", json={})
        ).status == 400


@pytest.mark.asyncio
async def test_done_blocked_by_incomplete_criteria(tmp_path):
    async with _client(tmp_path) as client:
        t = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "Ship",
                    "exit_criteria": [{"description": "tests pass", "met": False}],
                },
            )
        ).json()
        r = await client.put(f"/api/tasks/{t['id']}", json={"status": "done"})
        assert r.status == 400
        assert "exit criteria" in (await r.json())["error"]["message"]


@pytest.mark.asyncio
async def test_invalid_status_is_400_not_silent_noop(tmp_path):
    """PUT with a bad status ("completed" is the natural guess — the board column
    is even labeled Completed) must 400 with the valid set named, not 200 with the
    task silently unchanged."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "A"})).json()
        r = await client.put(f"/api/tasks/{t['id']}", json={"status": "completed"})
        assert r.status == 400
        err = (await r.json())["error"]
        assert err["code"] == "invalid_request"
        err = err["message"]
        assert "completed" in err and "done" in err
        got = await (await client.get(f"/api/tasks/{t['id']}")).json()
        assert got["status"] == "open"


@pytest.mark.asyncio
async def test_picking_a_project_while_EDITING_moves_the_task(tmp_path):
    """🔴 issue 2142. `PUT /api/tasks/{id}` dropped `project_id` on the floor.

    A task's `project` label derives solely from its task list, so a `project_id` with no
    `task_list_id` has to be resolved into one. `_attach_project_general_list` does that and was
    called by the create handler only — so `project_id` reached `update_task`, which has no such
    field, and the edit answered **200 having changed nothing**. The dropdown reverted on the next
    load, with no indication at any point.

    `TaskForm.draftToPayload` is shared by the create page and the detail page, so the identical
    payload worked on one surface and was a no-op on the other.
    """
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Website"})).json()
        )["id"]
        task_id = (await (await client.post("/api/tasks", json={"title": "t"})).json())[
            "id"
        ]

        r = await client.put(
            f"/api/tasks/{task_id}", json={"title": "t", "project_id": pid}
        )
        assert r.status == 200

        lists = (await (await client.get(f"/api/task-lists?project_id={pid}")).json())[
            "task_lists"
        ]
        assert [tl["name"] for tl in lists] == ["General"]
        assert (await r.json())["task_list_id"] == lists[0]["id"]


@pytest.mark.asyncio
async def test_the_two_write_paths_resolve_a_project_IDENTICALLY(tmp_path):
    """The rail. One shared form payload, two endpoints — they must agree.

    This is the third instance of that shape I have hit (the trigger form's dropped fields, the
    knowledge second write path, this), so the assertion is not "update works" but "update does
    what create does", derived by running the same body through both.
    """
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Website"})).json()
        )["id"]
        body = {"title": "shared payload", "task_list_id": "", "project_id": pid}

        created = await (await client.post("/api/tasks", json=dict(body))).json()
        other_id = (
            await (await client.post("/api/tasks", json={"title": "x"})).json()
        )["id"]
        updated = await (
            await client.put(f"/api/tasks/{other_id}", json=dict(body))
        ).json()

        assert created["task_list_id"], "create stopped resolving the project"
        assert updated["task_list_id"] == created["task_list_id"]


@pytest.mark.asyncio
async def test_an_explicit_task_list_WINS_over_project_id(tmp_path):
    """🪤 The floor. "Resolve the project" one step too far would overwrite a list the user picked
    deliberately, which is a worse bug than the one being fixed — it would move tasks.
    """
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Website"})).json()
        )["id"]
        chosen = await (
            await client.post(
                "/api/task-lists", json={"name": "Backlog", "project_id": pid}
            )
        ).json()
        task_id = (await (await client.post("/api/tasks", json={"title": "t"})).json())[
            "id"
        ]

        r = await client.put(
            f"/api/tasks/{task_id}",
            json={"title": "t", "task_list_id": chosen["id"], "project_id": pid},
        )
        assert (await r.json())["task_list_id"] == chosen["id"]
        lists = (await (await client.get(f"/api/task-lists?project_id={pid}")).json())[
            "task_lists"
        ]
        assert [tl["name"] for tl in lists] == ["Backlog"]


@pytest.mark.asyncio
async def test_an_EMPTY_task_list_id_still_resolves_the_project(tmp_path):
    """The form always sends the key, so `task_list_id: ""` is the live case, not an edge one.

    It worked before only because `""` is falsy — luck rather than intent. The guard now states the
    rule ("an explicitly empty list means no list chosen"), and this pins it so a later
    `if "task_list_id" in body` refactor cannot invert it silently.
    """
    async with _client(tmp_path) as client:
        pid = (
            await (await client.post("/api/projects", json={"name": "Website"})).json()
        )["id"]
        task_id = (await (await client.post("/api/tasks", json={"title": "t"})).json())[
            "id"
        ]
        r = await client.put(
            f"/api/tasks/{task_id}",
            json={"title": "t", "task_list_id": "  ", "project_id": pid},
        )
        assert (await r.json())[
            "task_list_id"
        ], "a blank list defeated the project resolution"


@pytest.mark.asyncio
async def test_a_task_without_a_project_id_is_left_alone(tmp_path):
    """The other floor: the resolution must be a no-op for every edit that does not mention a
    project, which is nearly all of them."""
    async with _client(tmp_path) as client:
        task_id = (await (await client.post("/api/tasks", json={"title": "t"})).json())[
            "id"
        ]
        r = await client.put(f"/api/tasks/{task_id}", json={"title": "renamed"})
        body = await r.json()
        assert body["title"] == "renamed"
        assert not body["task_list_id"]
