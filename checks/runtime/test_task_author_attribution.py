"""Attribution integrity on the task write paths (#554).

Issue #554 was filed against the COMMENT endpoint: it took `author` from the request
body, so any caller could sign a comment as anyone. That half was closed (the POST route
refuses a supplied `author` and derives it from the owner handle) and is pinned in
`test_tasks_api.py`. This file covers the identical defect on the sibling field the fixed
comment code explicitly claims agreement with — "the same handle `Task.author` is stamped
with on create" — which was still taking `author` straight from the request body on FOUR
paths: `POST /api/tasks`, `PUT /api/tasks/{id}`, and both bulk ops.

Two things make it more than cosmetic:

* `Task.belongs_to` reads `author`, so it decides `?mine=1` and `/api/tasks/ready`. A
  forged author removes a row from the owner's own views — measured below.
* The UPDATE path re-attributed rows that were already honestly signed, which
  `identity.py` forbids in as many words.

Every assertion here reads STORED state (the task JSON on disk), not the response echo —
a handler that answered 400 and wrote anyway would pass a status-only test.

The refusal is deliberately loud rather than a silent drop: a 201 that stored a different
author than the caller asked for tells a forging client it worked and never tells an
honest one its attribution was discarded. So each refusal test is paired with a vacuity
floor — the honest write on the same path still records the acting identity — because
"everything is refused" would otherwise satisfy the refusal half on its own.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes

OWNER = "keyur-golani"
FORGED = "root@evil"
REFUSAL = "author is server-derived and must not be supplied"


@asynccontextmanager
async def _client(tmp_path):
    """Task routes over an isolated store, with a KNOWN acting identity.

    `_current_username` is patched in `tasks.native` (where create stamps the author)
    and `_owner_username` in `tasks.handlers` (where the list route reports the owner),
    so "the acting identity" is one fixed string across both layers.
    """
    registry._providers.clear()
    with (
        patch("gideon.engine.tasks.native.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.native._current_username", return_value=OWNER),
        patch("gideon.engine.tasks.handlers._owner_username", return_value=OWNER),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


def _task_files(tmp_path):
    """Task records as PERSISTED — the store, not the response echo."""
    return sorted((tmp_path / "tasks").glob("t-*.json"))


def _stored(tmp_path, task_id):
    return json.loads(
        (tmp_path / "tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_create_refuses_a_supplied_author_and_writes_nothing(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/tasks", json={"title": "forged", "author": FORGED})
        assert r.status == 400
        assert (await r.json())["error"] == REFUSAL
        assert _task_files(tmp_path) == []


@pytest.mark.asyncio
async def test_create_derives_the_author_from_the_acting_identity(tmp_path):
    """VACUITY FLOOR for create: the honest write still records, and records the
    acting identity rather than an empty string or a placeholder."""
    async with _client(tmp_path) as client:
        r = await client.post("/api/tasks", json={"title": "honest"})
        assert r.status == 201
        created = await r.json()
        assert created["author"] == OWNER
        assert _stored(tmp_path, created["id"])["author"] == OWNER


@pytest.mark.asyncio
async def test_create_refuses_an_author_that_matches_the_owner(tmp_path):
    """Refused on PRESENCE, not on disagreement. A caller that happens to guess the
    owner handle is still asserting its own attribution, and accepting it would make the
    rule "we check the value", which is the check that rots the moment the handle
    changes."""
    async with _client(tmp_path) as client:
        r = await client.post("/api/tasks", json={"title": "t", "author": OWNER})
        assert r.status == 400
        assert _task_files(tmp_path) == []


@pytest.mark.asyncio
async def test_create_refuses_an_empty_author(tmp_path):
    """`author: ""` is the quiet version of the same forgery: it would blank the
    attribution the server was about to stamp, leaving a row that reads as
    pre-attribution history. Presence, not truthiness, is the trigger."""
    async with _client(tmp_path) as client:
        r = await client.post("/api/tasks", json={"title": "t", "author": ""})
        assert r.status == 400
        assert _task_files(tmp_path) == []


@pytest.mark.asyncio
async def test_update_refuses_a_supplied_author_and_leaves_provenance_intact(tmp_path):
    """The worse half of the bug: this rewrote the author of a row that was already
    honestly signed."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "honest"})).json()
        r = await client.put(f"/api/tasks/{t['id']}", json={"author": FORGED})
        assert r.status == 400
        assert (await r.json())["error"] == REFUSAL
        assert _stored(tmp_path, t["id"])["author"] == OWNER


@pytest.mark.asyncio
async def test_update_refuses_the_whole_edit_rather_than_applying_the_rest(tmp_path):
    """An edit carrying `author` alongside legitimate fields is refused WHOLE. Applying
    the honest fields and dropping the author would answer 200 for a payload the server
    only partly honored."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "before"})).json()
        r = await client.put(
            f"/api/tasks/{t['id']}", json={"title": "after", "author": FORGED}
        )
        assert r.status == 400
        stored = _stored(tmp_path, t["id"])
        assert stored["title"] == "before"
        assert stored["author"] == OWNER


@pytest.mark.asyncio
async def test_update_still_edits_every_other_field(tmp_path):
    """VACUITY FLOOR for update: the path is not inert — an edit without `author`
    still applies, so the refusal above is about attribution and nothing else."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "before"})).json()
        r = await client.put(
            f"/api/tasks/{t['id']}", json={"title": "after", "priority": "high"}
        )
        assert r.status == 200
        stored = _stored(tmp_path, t["id"])
        assert stored["title"] == "after"
        assert stored["priority"] == "high"
        assert stored["author"] == OWNER


@pytest.mark.asyncio
async def test_bulk_create_refuses_a_supplied_author_and_aborts_the_batch(tmp_path):
    """Bulk is the cheapest way to mint many rows, and it reaches `create_task` with
    `**item` exactly as the single handler reaches it with `**body`. The refusal is in
    phase 1, so one forging item aborts the batch instead of landing beside honest ones.
    """
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [{"title": "ok"}, {"title": "bad", "author": FORGED}],
            },
        )
        assert r.status == 400
        payload = await r.json()
        assert {"index": 1, "error": REFUSAL} in payload["errors"]
        assert payload["succeeded"] == 0
        assert _task_files(tmp_path) == []


@pytest.mark.asyncio
async def test_bulk_update_refuses_a_supplied_author(tmp_path):
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "honest"})).json()
        r = await client.post(
            "/api/tasks/bulk",
            json={"op": "update", "items": [{"id": t["id"], "author": FORGED}]},
        )
        assert r.status == 400
        assert {"index": 0, "error": REFUSAL} in (await r.json())["errors"]
        assert _stored(tmp_path, t["id"])["author"] == OWNER


@pytest.mark.asyncio
async def test_bulk_still_creates_and_updates_without_an_author(tmp_path):
    """VACUITY FLOOR for bulk, both ops."""
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/tasks/bulk",
            json={"op": "create", "items": [{"title": "a"}, {"title": "b"}]},
        )
        assert r.status == 200
        created = await r.json()
        assert created["succeeded"] == 2
        ids = [row["task_id"] for row in created["results"]]
        assert [_stored(tmp_path, i)["author"] for i in ids] == [OWNER, OWNER]

        r = await client.post(
            "/api/tasks/bulk",
            json={"op": "update", "items": [{"id": ids[0], "title": "a2"}]},
        )
        assert r.status == 200
        assert _stored(tmp_path, ids[0])["title"] == "a2"


@pytest.mark.asyncio
async def test_bulk_delete_is_unaffected(tmp_path):
    """`delete` carries no attribution, so the phase-1 rule must not reach it — a
    guard applied to all three ops would break deletion of a row whose dict happens to
    round-trip an `author` field."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "doomed"})).json()
        r = await client.post(
            "/api/tasks/bulk",
            json={"op": "delete", "items": [{"id": t["id"], "author": OWNER}]},
        )
        assert r.status == 200
        assert _task_files(tmp_path) == []


@pytest.mark.asyncio
async def test_a_caller_cannot_hide_a_task_from_the_owners_mine_view(tmp_path):
    """Why this is integrity and not cosmetics. `Task.belongs_to` falls back to
    `author` for an unassigned task, so before the fix a task created with
    `author: "root@evil"` was absent from `?mine=1` and from `/api/tasks/ready` —
    the owner's own board silently omitted a row on their own machine. Every task a
    caller can now create is one the owner can see."""
    async with _client(tmp_path) as client:
        assert (
            await client.post("/api/tasks", json={"title": "hidden", "author": FORGED})
        ).status == 400
        await client.post("/api/tasks", json={"title": "visible"})

        mine = await (await client.get("/api/tasks?mine=1")).json()
        assert [t["title"] for t in mine["tasks"]] == ["visible"]
        assert mine["owner"] == OWNER
        ready = await (await client.get("/api/tasks/ready")).json()
        assert [t["title"] for t in ready["tasks"]] == ["visible"]


@pytest.mark.asyncio
async def test_the_store_will_not_rewrite_an_author_on_update(tmp_path):
    """Defense in depth at the layer that owns the record. The HTTP refusal closes
    today's four callers; this closes the next in-process one, and states
    `identity.py`'s rule as behavior: existing records keep the string they were
    written with."""
    async with _client(tmp_path):
        created = await registry.create_task(title="honest")
        assert created.author == OWNER
        updated = await registry.update_task(
            created.id, author="in-process-forge", title="edited"
        )
        assert updated is not None
        assert updated.title == "edited"
        assert updated.author == OWNER
        assert _stored(tmp_path, created.id)["author"] == OWNER


@pytest.mark.asyncio
async def test_the_store_still_lets_a_trusted_caller_sign_its_own_creation(tmp_path):
    """The in-process CREATE contract is deliberately untouched: `selfqa` files findings
    as `author="self-qa"`, and attribution is write-once rather than unwritable. Pinned
    so a later tightening of the store cannot silently take an honest attribution away
    from the one caller that legitimately is not the owner."""
    async with _client(tmp_path):
        task = await registry.create_task(title="finding", author="self-qa")
        assert task.author == "self-qa"
        assert _stored(tmp_path, task.id)["author"] == "self-qa"


@pytest.mark.asyncio
async def test_ids_and_timestamps_stay_server_owned_on_both_write_paths(tmp_path):
    """`author` was the ONE provenance field a request body could still set. The other
    three were already server-owned; pinned here so the class stays closed rather than
    closed for one field."""
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/tasks",
            json={
                "title": "t",
                "id": "t-forged",
                "created_at": "1999-01-01T00:00:00Z",
                "updated_at": "1999-01-01T00:00:00Z",
            },
        )
        assert r.status == 201
        created = await r.json()
        assert created["id"] != "t-forged"
        assert not created["created_at"].startswith("1999")

        r = await client.put(
            f"/api/tasks/{created['id']}",
            json={
                "id": "t-forged",
                "created_at": "1999-01-01T00:00:00Z",
                "provider": "evil",
            },
        )
        assert r.status == 200
        stored = _stored(tmp_path, created["id"])
        assert stored["id"] == created["id"]
        assert stored["created_at"] == created["created_at"]
        assert stored["provider"] == "native"


@pytest.mark.asyncio
async def test_the_comment_route_refuses_with_the_same_message(tmp_path):
    """#554's original half, asserted against the SAME constant the task paths use, so
    the two cannot drift into refusing on different grounds or saying different things.
    """
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "carrier"})).json()
        r = await client.post(
            f"/api/tasks/{t['id']}/comments", json={"body": "forged", "author": FORGED}
        )
        assert r.status == 400
        assert (await r.json())["error"] == REFUSAL

        r = await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "honest"})
        assert r.status == 201
        assert (await r.json())["author"] == OWNER
        sidecar = tmp_path / "tasks" / f"_comments_{t['id']}.json"
        assert [
            c["author"] for c in json.loads(sidecar.read_text(encoding="utf-8"))
        ] == [OWNER]


@asynccontextmanager
async def _client_with_the_real_identity(tmp_path):
    """The same routes over an isolated home, but with the REAL identity lookup.

    `_current_username` / `_owner_username` are left unpatched and only `config_dir` is
    redirected, so both write paths read their handle the way production does — from
    `AppConfig.load().dashboard.username` through `identity.current_username`. That is the
    only way to see what the two paths do when there is no handle to read.
    """
    registry._providers.clear()
    with (
        patch("gideon.engine.tasks.native.config_dir", return_value=tmp_path),
        patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("gideon.core.config.loader.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


def _comment_authors(tmp_path, task_id):
    sidecar = tmp_path / "tasks" / f"_comments_{task_id}.json"
    return [c["author"] for c in json.loads(sidecar.read_text(encoding="utf-8"))]


@pytest.mark.asyncio
async def test_tasks_and_comments_share_one_empty_author_default(tmp_path):
    """A home with no configured handle — every install before onboarding.

    Both write paths fall back through the same `identity.current_username()`, so a task
    and a comment written in that state must carry the SAME author string. A second default
    (a literal `"unknown"`, `"local"`, the OS user) on either path would split the
    attribution of two rows created by one person in one session, and `Task.belongs_to`
    reads that string.
    """
    async with _client_with_the_real_identity(tmp_path) as client:
        from gideon.cognition.identity import current_username

        assert (
            current_username() == ""
        ), "the premise is a home with no configured handle"

        task = await (await client.post("/api/tasks", json={"title": "t"})).json()
        comment = await (
            await client.post(f"/api/tasks/{task['id']}/comments", json={"body": "b"})
        ).json()

        assert task["author"] == comment["author"] == ""
        assert _stored(tmp_path, task["id"])["author"] == ""
        assert _comment_authors(tmp_path, task["id"]) == [""]


@pytest.mark.asyncio
async def test_a_configured_handle_is_preserved_on_both_paths(tmp_path):
    """VACUITY FLOOR for the shared default: with a handle configured, both paths record
    it (slugified by `identity`), so the agreement above is about the fallback and not
    about attribution being empty everywhere."""
    (tmp_path / "config.json").write_text(
        json.dumps({"dashboard": {"username": "Ada Lovelace"}}), encoding="utf-8"
    )
    async with _client_with_the_real_identity(tmp_path) as client:
        from gideon.cognition.identity import current_username

        handle = current_username()
        assert handle == "ada-lovelace", handle

        task = await (await client.post("/api/tasks", json={"title": "t"})).json()
        await client.post(f"/api/tasks/{task['id']}/comments", json={"body": "b"})

        assert _stored(tmp_path, task["id"])["author"] == handle
        assert _comment_authors(tmp_path, task["id"]) == [handle]


@pytest.mark.asyncio
async def test_an_explicit_trusted_identity_survives_an_empty_handle(tmp_path):
    """The in-process create contract, asserted in the state that would otherwise erase it:
    with no configured handle the default is `""`, and a trusted caller's own attribution
    must still win over that default rather than being normalized into it."""
    async with _client_with_the_real_identity(tmp_path):
        from gideon.cognition.identity import current_username

        assert current_username() == ""
        task = await registry.create_task(title="finding", author="self-qa")
        assert task.author == "self-qa"
        assert _stored(tmp_path, task.id)["author"] == "self-qa"


@pytest.mark.asyncio
async def test_bulk_update_refuses_before_ANY_row_in_the_batch_is_mutated(tmp_path):
    """req 100 ac_2 — the refusal precedes the batch, not each item in turn.

    The single-item bulk-update test above cannot see the ordering: with one item there is
    no "before". Here an honest edit sits at index 0 and the forging item at index 1, so a
    guard applied per item inside the apply loop would land the first edit and then refuse —
    a 400 that half happened, which is the worst answer a batch can give. Both rows are read
    off disk afterwards: unchanged titles are what "before any mutation" means.
    """
    async with _client(tmp_path) as client:
        first = await (await client.post("/api/tasks", json={"title": "first"})).json()
        second = await (
            await client.post("/api/tasks", json={"title": "second"})
        ).json()

        r = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "update",
                "items": [
                    {"id": first["id"], "title": "first-edited"},
                    {"id": second["id"], "title": "second-edited", "author": FORGED},
                ],
            },
        )

        assert r.status == 400
        payload = await r.json()
        assert {"index": 1, "error": REFUSAL} in payload["errors"]
        assert payload["succeeded"] == 0
        assert _stored(tmp_path, first["id"])["title"] == "first", (
            "the honest edit ahead of the forgery was applied — the batch mutated before it "
            "refused"
        )
        assert _stored(tmp_path, second["id"])["title"] == "second"
        assert _stored(tmp_path, first["id"])["author"] == OWNER
        assert _stored(tmp_path, second["id"])["author"] == OWNER


@pytest.mark.asyncio
async def test_bulk_create_refuses_before_ANY_row_is_written(tmp_path):
    """The create half of the same ordering, with the forging item LAST so the refusal has
    to be reached before the honest items ahead of it are minted."""
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "create",
                "items": [
                    {"title": "one"},
                    {"title": "two"},
                    {"title": "three", "author": FORGED},
                ],
            },
        )
        assert r.status == 400
        assert (await r.json())["succeeded"] == 0
        assert _task_files(tmp_path) == [], "rows ahead of the forgery were written"
