import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_identity import PREFIX, register
from gideon.workspace.capabilities.identity.store import ConflictError, StoryStore


def answer(store, request_id="first", **changes):
    return store.create(
        prompt="What did you learn?",
        theme="School",
        text="I learned to listen.",
        request_id=request_id,
        **changes,
    )


def edit(story, **changes):
    return {
        "prompt": story["prompt"],
        "theme": story["theme"],
        "text": story["text"],
        "parent_id": story["parent_id"],
        "expected_revision": story["revision"],
        **changes,
    }


def test_original_answer_and_revision_chronology(tmp_path):
    store = StoryStore(tmp_path / "stories.sqlite3")
    original = answer(store)
    updated = store.update(
        original["id"], **edit(original, text="I learned to listen carefully.")
    )
    reopened = StoryStore(store.path)
    assert reopened.get(original["id"]) == updated
    assert updated["created_at"] == original["created_at"]
    assert datetime.fromisoformat(updated["updated_at"]) >= datetime.fromisoformat(
        original["created_at"]
    )
    assert (
        datetime.fromisoformat(original["created_at"]).utcoffset().total_seconds() == 0
    )
    assert updated["revision"] == 2
    assert reopened.history(original["id"]) == [original, updated]
    assert reopened.export() == {
        "schema_version": 1,
        "stories": [updated],
        "history": [original, updated],
    }
    assert original["text"] == "I learned to listen."
    assert original["revision"] == 1


def test_create_replay_is_durable_and_does_not_duplicate(tmp_path):
    store = StoryStore(tmp_path / "stories.sqlite3")
    original = answer(store)
    updated = store.update(original["id"], **edit(original, theme="Learning"))
    reopened = StoryStore(store.path)
    assert answer(reopened) == original
    assert reopened.list() == [updated]
    assert len(reopened.history(original["id"])) == 2
    with pytest.raises(ConflictError, match="different answer"):
        reopened.create(
            prompt="Other",
            theme="Learning",
            text="A different answer",
            request_id="first",
        )
    assert reopened.list() == [updated]


def test_branching_family_is_complete_and_independent(tmp_path):
    store = StoryStore(tmp_path / "stories.sqlite3")
    root = answer(store)
    left = answer(store, "left", parent_id=root["id"])
    right = answer(store, "right", parent_id=root["id"])
    leaf = answer(store, "leaf", parent_id=left["id"])
    other = answer(store, "other")
    assert store.chain(leaf["id"]) == [root, left, right, leaf]
    assert store.chain(right["id"]) == [root, left, right, leaf]
    assert store.chain(other["id"]) == [other]
    assert store.list() == [root, left, right, leaf, other]
    assert len(store.export()["history"]) == 5


def test_cycle_rejection_preserves_both_records(tmp_path):
    store = StoryStore(tmp_path / "stories.sqlite3")
    root = answer(store)
    child = answer(store, "child", parent_id=root["id"])
    with pytest.raises(ConflictError, match="ancestor"):
        store.update(root["id"], **edit(root, parent_id=child["id"]))
    with pytest.raises(ConflictError, match="ancestor"):
        store.update(child["id"], **edit(child, parent_id=child["id"]))
    assert store.chain(root["id"]) == [root, child]
    assert store.history(root["id"]) == [root]
    assert store.history(child["id"]) == [child]


def test_independent_stores_reject_foreign_parents(tmp_path):
    first = StoryStore(tmp_path / "first" / "stories.sqlite3")
    second = StoryStore(tmp_path / "second" / "stories.sqlite3")
    root = answer(first)
    with pytest.raises(KeyError):
        answer(second, parent_id=root["id"])
    assert second.list() == []
    local = answer(second)
    with pytest.raises(KeyError):
        second.update(local["id"], **edit(local, parent_id=root["id"]))
    assert second.list() == [local]
    assert first.list() == [root]
    assert local["id"] != root["id"]
    assert second.history(local["id"]) == [local]


def test_delete_rejects_parent_and_retains_original_answer_history(tmp_path):
    store = StoryStore(tmp_path / "stories.sqlite3")
    root = answer(store)
    child = answer(store, "child", parent_id=root["id"])
    with pytest.raises(ConflictError, match="follow-ups"):
        store.delete(root["id"], expected_revision=1)
    assert store.list() == [root, child]
    with pytest.raises(ConflictError, match="changed"):
        store.delete(child["id"], expected_revision=2)
    store.delete(child["id"], expected_revision=1)
    assert store.chain(root["id"]) == [root]
    assert store.history(child["id"]) == [child]
    store.delete(root["id"], expected_revision=1)
    assert store.list() == []
    assert len(store.export()["history"]) == 2
    with pytest.raises(KeyError):
        store.get(root["id"])
    assert answer(store) == root
    assert store.list() == []


def test_reparenting_moves_whole_subtree(tmp_path):
    store = StoryStore(tmp_path / "stories.sqlite3")
    first = answer(store)
    second = answer(store, "second")
    child = answer(store, "child", parent_id=first["id"])
    grandchild = answer(store, "grandchild", parent_id=child["id"])
    moved = store.update(child["id"], **edit(child, parent_id=second["id"]))
    assert store.chain(first["id"]) == [first]
    assert store.chain(grandchild["id"]) == [second, moved, grandchild]
    assert store.history(child["id"]) == [child, moved]


def test_concurrent_edits_have_one_winner(tmp_path):
    path = tmp_path / "stories.sqlite3"
    store = StoryStore(path)
    original = answer(store)

    def attempt(text):
        try:
            return StoryStore(path).update(original["id"], **edit(original, text=text))
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["First edit", "Second edit"]))
    winner = [result for result in results if result]
    assert len(winner) == 1
    assert store.list() == winner
    assert store.history(original["id"]) == [original, winner[0]]
    assert winner[0]["text"] in {"First edit", "Second edit"}


@pytest.mark.parametrize(
    "changes",
    [
        {"prompt": ""},
        {"prompt": " "},
        {"prompt": 1},
        {"prompt": "x" * 4001},
        {"theme": ""},
        {"theme": None},
        {"theme": "x" * 201},
        {"text": ""},
        {"text": []},
        {"text": "x" * 100001},
        {"parent_id": ""},
        {"parent_id": 3},
        {"request_id": ""},
        {"request_id": "x" * 129},
    ],
)
def test_validation_is_non_mutating(tmp_path, changes):
    store = StoryStore(tmp_path / "stories.sqlite3")
    body = {
        "prompt": "Question",
        "theme": "Theme",
        "text": "Answer",
        "request_id": "request",
        **changes,
    }
    with pytest.raises(ValueError):
        store.create(**body)
    assert store.export() == {"schema_version": 1, "stories": [], "history": []}


@pytest.mark.parametrize("revision", [0, -1, True, "1", None, 1.5])
def test_revision_requires_integer(tmp_path, revision):
    store = StoryStore(tmp_path / "stories.sqlite3")
    original = answer(store)
    with pytest.raises(ValueError, match="positive integer"):
        store.update(original["id"], **edit(original, expected_revision=revision))
    with pytest.raises(ValueError, match="positive integer"):
        store.delete(original["id"], expected_revision=revision)
    assert store.get(original["id"]) == original


@pytest.mark.asyncio
async def test_http_author_edit_chain_conflict_and_export(tmp_path):
    path = tmp_path / "http.sqlite3"
    app = web.Application()
    register(app, store_path=path)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(PREFIX + "/stories")
        assert response.status == 200
        assert await response.json() == []
        body = {
            "prompt": "Where did you grow up?",
            "theme": "Childhood",
            "text": "Near a river.",
            "request_id": "root",
        }
        response = await client.post(PREFIX + "/stories", json=body)
        assert response.status == 200
        root = await response.json()
        assert root["text"] == body["text"]
        child_body = {
            **body,
            "prompt": "What was the river like?",
            "parent_id": root["id"],
            "request_id": "child",
        }
        response = await client.post(PREFIX + "/stories", json=child_body)
        assert response.status == 200
        child = await response.json()
        response = await client.put(
            PREFIX + "/stories/" + child["id"], json=edit(child, text="Wide and quiet.")
        )
        assert response.status == 200
        edited = await response.json()
        assert edited["revision"] == 2
        response = await client.put(
            PREFIX + "/stories/" + child["id"], json=edit(child, text="Stale answer")
        )
        assert response.status == 409
        assert "reload" in (await response.json())["error"]
        response = await client.get(PREFIX + "/stories/" + child["id"] + "/chain")
        assert await response.json() == [root, edited]
        response = await client.get(PREFIX + "/stories/" + child["id"] + "/history")
        assert await response.json() == [child, edited]
        response = await client.get(PREFIX + "/export")
        exported = await response.json()
        assert exported["schema_version"] == 1
        assert exported["stories"] == [root, edited]
        assert len(exported["history"]) == 3
        response = await client.delete(
            PREFIX + "/stories/" + root["id"] + "?expected_revision=1"
        )
        assert response.status == 409
        assert "follow-ups" in (await response.json())["error"]
        response = await client.delete(
            PREFIX + "/stories/" + child["id"] + "?expected_revision=2"
        )
        assert response.status == 200
        assert (await response.json())["deleted"] == child["id"]
    reopened = StoryStore(path)
    assert reopened.list() == [root]
    assert reopened.history(child["id"]) == [child, edited]


@pytest.mark.asyncio
async def test_http_rejects_bad_inputs_and_cross_store_references(tmp_path):
    app = web.Application()
    register(app, store_path=tmp_path / "local.sqlite3")
    foreign = answer(StoryStore(tmp_path / "foreign.sqlite3"))
    body = {
        "prompt": "Question",
        "theme": "Theme",
        "text": "Answer",
        "request_id": "request",
    }
    async with TestClient(TestServer(app)) as client:
        for payload in (
            [],
            {},
            {**body, "generated_narrative": "Disallowed"},
            {**body, "revision": 9},
        ):
            response = await client.post(PREFIX + "/stories", json=payload)
            assert response.status == 400
            assert isinstance((await response.json())["error"], str)
        response = await client.post(
            PREFIX + "/stories",
            data="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400
        response = await client.post(
            PREFIX + "/stories", json={**body, "parent_id": foreign["id"]}
        )
        assert response.status == 404
        response = await client.get(PREFIX + "/stories/" + foreign["id"])
        assert response.status == 404
        response = await client.get(PREFIX + "/stories/" + foreign["id"] + "/chain")
        assert response.status == 404
        response = await client.post(PREFIX + "/stories", json=body)
        assert response.status == 200
        root = await response.json()
        response = await client.put(
            PREFIX + "/stories/" + root["id"], json=edit(root, parent_id=root["id"])
        )
        assert response.status == 409
        response = await client.delete(PREFIX + "/stories/" + root["id"])
        assert response.status == 400
        response = await client.delete(
            PREFIX + "/stories/" + root["id"] + "?expected_revision=true"
        )
        assert response.status == 400
        response = await client.post(PREFIX + "/stories", json=body)
        assert await response.json() == root
        response = await client.post(
            PREFIX + "/stories", json={**body, "text": "Changed"}
        )
        assert response.status == 409
        response = await client.get(PREFIX + "/stories")
        assert await response.json() == [root]
