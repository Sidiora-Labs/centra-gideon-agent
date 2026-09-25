import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.sqlite_compat import sqlite3
from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.workspace.capabilities.creative.store import CatalogError, IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider
from gideon.workspace.capabilities.creative.works import WorkStore


def create(store, **changes):
    return store.create(
        {
            "request_id": str(uuid4()),
            "title": "Night journey",
            "kind": "exercise",
            "prompt": "Describe a train station.",
            **changes,
        }
    )


def draft_payload(revision=1, **changes):
    return {
        "request_id": str(uuid4()),
        "revision": revision,
        "text": "  The last train arrived.\n\n",
        "note": "First attempt",
        **changes,
    }


def test_manuscript_drafts_are_real_readonly_artifacts_with_exact_text(tmp_path):
    store = WorkStore(tmp_path)
    work = create(store)
    assert work["kind"] == "exercise"
    assert work["active_draft_id"] is None
    request = draft_payload()
    first = store.draft(work["id"], request)
    assert first["work"]["revision"] == 2
    assert first["work"]["active_draft_id"] == first["draft"]["id"]
    artifact = store.artifacts.get(first["draft"]["artifact_id"], version=1)
    assert artifact.content == request["text"]
    assert artifact.readonly
    assert artifact.kind == "markdown"
    assert first["draft"]["characters"] == len(request["text"])
    assert store.get(work["id"])["text"] == request["text"]
    assert store.read_draft(work["id"], first["draft"]["id"])["text"] == request["text"]
    assert store.read_draft(work["id"], first["draft"]["id"])["missing"] is False
    with pytest.raises(PermissionError):
        store.artifacts.update(
            artifact.slug, content="Overwrite forbidden", snapshot=True
        )
    second = store.draft(
        work["id"], draft_payload(2, text="A revised ending.", note="Second attempt")
    )
    assert second["work"]["revision"] == 3
    assert second["draft"]["artifact_id"] != first["draft"]["artifact_id"]
    assert store.drafts(work["id"])["items"] == [second["draft"], first["draft"]]
    assert store.get(work["id"])["text"] == "A revised ending."
    restored = store.restore(work["id"], {"revision": 3, "target_revision": 2})
    assert restored["active_draft_id"] == first["draft"]["id"]
    assert store.get(work["id"])["text"] == request["text"]
    assert len(store.drafts(work["id"])["items"]) == 2
    assert store.export(work["id"], 3) == second["work"]
    reopened = WorkStore(tmp_path)
    assert reopened.get(work["id"])["text"] == request["text"]
    assert reopened.drafts(work["id"]) == store.drafts(work["id"])
    with store.connection() as db:
        persisted = " ".join(
            str(row) for row in db.execute("SELECT record FROM work_revisions")
        )
    assert request["text"].strip() not in persisted


def test_pinned_author_universe_context_remains_at_selected_revision(tmp_path):
    store = WorkStore(tmp_path)
    author = store.authors.create(
        {"request_id": "author", "title": "Mira", "voice": {"tone": "Quiet"}}
    )
    universe = store.universes.create(
        {
            "request_id": "universe",
            "title": "City",
            "canon": [{"id": "law", "title": "Night"}],
        }
    )
    work = create(
        store,
        author_ref={"id": author["id"], "revision": 1},
        universe_ref={"id": universe["id"], "revision": 1},
    )
    store.authors.update(author["id"], {"revision": 1, "voice": {"tone": "Loud"}})
    store.universes.update(universe["id"], {"revision": 1, "title": "Changed world"})
    context = store.context(work["id"])
    assert context["author"]["voice"]["tone"] == "Quiet"
    assert context["author"]["revision"] == 1
    assert context["universe"]["title"] == "City"
    assert context["universe"]["canon"][0]["title"] == "Night"
    assert context["prompt"] == "Describe a train station."
    assert context["missing"] == []
    with store.connection() as db:
        db.execute("DELETE FROM author_revisions WHERE id=?", (author["id"],))
        db.execute("DELETE FROM universe_revisions WHERE id=?", (universe["id"],))
    missing = store.context(work["id"])
    assert missing["author"] is None
    assert missing["universe"] is None
    assert missing["missing"] == ["author_ref", "universe_ref"]
    changed = store.update(work["id"], {"revision": 1, "title": "Retained context"})
    assert changed["author_ref"] == work["author_ref"]
    with pytest.raises(CatalogError):
        create(store, author_ref=work["author_ref"])


def test_retry_recovers_real_artifact_after_sqlite_rollback(tmp_path):
    store = WorkStore(tmp_path)
    work = create(store)
    request = draft_payload()
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_work_revision BEFORE INSERT ON work_revisions BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.draft(work["id"], request)
    assert store.get(work["id"])["revision"] == 1
    assert store.drafts(work["id"])["items"] == []
    artifacts = store.artifacts.list()
    assert len(artifacts) == 1
    assert artifacts[0].readonly
    assert store.artifacts.get(artifacts[0].slug, version=1).content == request["text"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM work_draft_requests").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_work_revision")
    recovered = store.draft(work["id"], request)
    assert recovered["draft"]["artifact_id"] == artifacts[0].slug
    assert len(store.artifacts.list()) == 1
    assert len(store.revisions(work["id"])) == 2
    assert store.draft(work["id"], request) == recovered


def test_deterministic_artifact_collision_is_rejected_without_deleting_it(tmp_path):
    store = WorkStore(tmp_path)
    work = create(store)
    request = draft_payload()
    draft_id = hashlib.sha256(
        (work["id"] + ":" + request["request_id"]).encode()
    ).hexdigest()
    existing = store.artifacts.create(
        name="Existing source",
        slug="creative-draft-" + draft_id,
        content="Keep me",
        kind="markdown",
    )
    with pytest.raises(CatalogError) as error:
        store.draft(work["id"], request)
    assert error.value.status == 409
    assert store.artifacts.get(existing.slug, version=1).content == "Keep me"
    assert store.drafts(work["id"])["items"] == []
    assert store.get(work["id"])["revision"] == 1


def test_concurrent_idempotent_drafts_and_stale_revision_are_safe(tmp_path):
    store = WorkStore(tmp_path)
    work = create(store)
    request = draft_payload()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.draft(work["id"], request), range(2)))
    assert results[0] == results[1]
    assert len(store.artifacts.list()) == 1
    assert len(store.drafts(work["id"])["items"]) == 1
    with pytest.raises(CatalogError) as reused:
        store.draft(work["id"], {**request, "text": "Different"})
    assert reused.value.status == 409
    with pytest.raises(CatalogError) as stale:
        store.draft(work["id"], draft_payload(1))
    assert stale.value.status == 409
    assert store.get(work["id"])["text"] == request["text"]
    assert len(store.revisions(work["id"])) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"text": ""},
        {"text": "   "},
        {"text": 123},
        {"text": "x" * 1000001},
        {"note": "x" * 2001},
        {"home": "other"},
        {"model": "override"},
        {"session_id": "private"},
        {"revision": True},
    ],
)
def test_invalid_drafts_do_not_create_metadata_or_artifacts(tmp_path, changes):
    store = WorkStore(tmp_path)
    work = create(store)
    with pytest.raises(CatalogError):
        store.draft(work["id"], draft_payload(**changes))
    assert store.artifacts.list() == []
    assert store.drafts(work["id"])["items"] == []
    assert store.export(work["id"]) == work


def test_cross_home_work_context_and_draft_access_is_rejected(tmp_path):
    one = WorkStore(tmp_path / "one")
    two = WorkStore(tmp_path / "two")
    work = create(one)
    manuscript = one.draft(work["id"], draft_payload())
    other_work = create(two)
    for action in (
        lambda: two.get(work["id"]),
        lambda: two.context(work["id"]),
        lambda: two.drafts(work["id"]),
        lambda: two.read_draft(other_work["id"], manuscript["draft"]["id"]),
    ):
        with pytest.raises(CatalogError) as error:
            action()
        assert error.value.status == 404
    assert two.artifacts.list() == []
    author = one.authors.create({"request_id": "author", "title": "Author"})
    with pytest.raises(CatalogError):
        create(two, author_ref={"id": author["id"], "revision": 1})
    with pytest.raises(CatalogError):
        two.update(
            other_work["id"],
            {"revision": 1, "active_draft_id": manuscript["draft"]["id"]},
        )


def test_missing_draft_artifact_is_explicit_without_rewriting_snapshot(tmp_path):
    store = WorkStore(tmp_path)
    work = create(store)
    result = store.draft(work["id"], draft_payload())
    artifact = store.artifacts.get(result["draft"]["artifact_id"], version=1)
    version = tmp_path / "artifacts" / artifact.slug / "versions" / "v1.html"
    version.unlink()
    assert store.get(work["id"])["draft_missing"] is True
    assert store.get(work["id"])["text"] == ""
    assert store.read_draft(work["id"], result["draft"]["id"])["missing"] is True
    assert store.export(work["id"]) == result["work"]


def test_actual_http_and_all_native_work_operations(tmp_path):
    async def run():
        app = web.Application()
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        base = "/api/capabilities/creative/works"
        async with TestClient(TestServer(app)) as client:
            created = await client.post(
                base,
                json={
                    "request_id": "http",
                    "title": "HTTP exercise",
                    "kind": "exercise",
                },
            )
            assert created.status == 201
            work = await created.json()
            path = base + "/" + work["id"]
            written = await client.post(path + "/drafts", json=draft_payload())
            assert written.status == 200
            draft = (await written.json())["draft"]
            read = await client.get(path + "/drafts/" + draft["id"])
            assert read.status == 200
            assert (await read.json())["text"] == "  The last train arrived.\n\n"
            context = await client.get(path + "/context")
            assert (await context.json())["missing"] == []
            listing = await client.get(base + "?q=HTTP")
            assert (await listing.json())["total"] == 1
            invalid = await client.post(
                path + "/drafts", json=draft_payload(2, provider="override")
            )
            assert invalid.status == 400
        provider = CreativeToolProvider(tmp_path)

        async def call(name, args):
            result = await provider.invoke("creative_work_" + name, args)
            assert result.success, result.error
            return json.loads(result.output)

        native = await call(
            "create", {"payload": {"request_id": "native", "title": "Native work"}}
        )
        result = await call("draft", {"id": native["id"], "payload": draft_payload()})
        assert (await call("get", {"id": native["id"]}))["active_draft_id"] == result[
            "draft"
        ]["id"]
        assert (
            await call(
                "read_draft", {"id": native["id"], "draft_id": result["draft"]["id"]}
            )
        )["text"].startswith("  The")
        assert (await call("drafts", {"id": native["id"]}))["items"] == [
            result["draft"]
        ]
        assert (await call("context", {"id": native["id"]}))["author"] is None
        updated = await call(
            "update",
            {"id": native["id"], "payload": {"revision": 2, "title": "Updated work"}},
        )
        assert (await call("list", {"q": "Updated work"}))["items"] == [updated]
        assert (await call("revisions", {"id": native["id"]}))["total"] == 3
        assert (await call("export", {"id": native["id"], "revision": 1})) == native
        restored = await call(
            "restore",
            {"id": native["id"], "payload": {"revision": 3, "target_revision": 1}},
        )
        assert restored["active_draft_id"] is None
        assert len((await call("drafts", {"id": native["id"]}))["items"]) == 1

    asyncio.run(run())
