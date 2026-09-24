import copy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_experience import PREFIX, STORE, register
from gideon.workspace.capabilities.experience import Conflict, ExperienceStore, NotFound
from gideon.workspace.capabilities.experience.graph import validate_graph


@pytest.fixture
def authored():
    return {
        "title": "The river crossing",
        "start_node": "bank",
        "nodes": [
            {"id": "bank", "kind": "scene", "text": "The bridge and ferry lead across.", "choices": [
                {"id": "bridge", "label": "Take the bridge", "target": "hill"},
                {"id": "ferry", "label": "Take the ferry", "target": "harbor"},
            ]},
            {"id": "hill", "kind": "ending", "text": "You arrive on the hill.", "choices": []},
            {"id": "harbor", "kind": "ending", "text": "You arrive at the harbor.", "choices": []},
        ],
    }


def start_body(story, request_id="start"):
    return {"story_id": story["id"], "story_revision": story["revision"], "request_id": request_id}


def choice_body(choice="bridge", request_id="pick", revision=1):
    return {"choice_id": choice, "request_id": request_id, "revision": revision}


def test_alternate_endings_survive_store_and_process_restart(tmp_path, authored):
    store = ExperienceStore(tmp_path)
    story = store.save(authored)
    first = store.start(start_body(story, "first"))
    second = store.start(start_body(story, "second"))
    assert first["node"]["text"] == authored["nodes"][0]["text"]
    assert first["session"]["story_revision"] == 1
    assert first["session"]["revision"] == 1
    assert first["session"]["history"] == []
    left = store.choose(first["session"]["id"], choice_body())
    right = store.choose(second["session"]["id"], choice_body("ferry"))
    assert left["node"]["kind"] == right["node"]["kind"] == "ending"
    assert left["node"]["text"] == "You arrive on the hill."
    assert right["node"]["text"] == "You arrive at the harbor."
    assert left["session"]["history"] == [{"request_id": "pick", "choice_id": "bridge", "from_node": "bank", "to_node": "hill", "revision": 2}]
    assert ExperienceStore(tmp_path).session(first["session"]["id"]) == left
    command = "from pathlib import Path; import json,sys; from gideon.workspace.capabilities.experience import ExperienceStore; print(json.dumps(ExperienceStore(Path(sys.argv[1])).session(sys.argv[2])))"
    result = subprocess.run([sys.executable, "-c", command, str(tmp_path), second["session"]["id"]], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == right
    assert len(store.sessions()) == 2


def test_story_revisions_and_deleted_source_remain_available_to_sessions(tmp_path, authored):
    store = ExperienceStore(tmp_path)
    original = store.save(authored)
    session = store.start(start_body(original))["session"]
    changed = copy.deepcopy(authored)
    changed.update(revision=1, title="A revised river")
    changed["nodes"][1]["text"] = "The hill is different now."
    updated = store.save(changed, original["id"])
    assert updated["revision"] == 2
    assert updated["id"] == original["id"]
    assert store.story(original["id"])["title"] == "A revised river"
    assert store.session(session["id"])["story"] == original
    with pytest.raises(Conflict, match="story revision"):
        store.save(changed, original["id"])
    with pytest.raises(Conflict):
        store.start(start_body(original, "stale-start"))
    with pytest.raises(Conflict):
        store.delete(original["id"], 1)
    store.delete(original["id"], 2)
    assert store.stories() == []
    with pytest.raises(NotFound):
        store.story(original["id"])
    with pytest.raises(NotFound):
        store.start(start_body(updated, "deleted-start"))
    completed = store.choose(session["id"], choice_body())
    assert completed["node"]["text"] == "You arrive on the hill."
    assert ExperienceStore(tmp_path).session(session["id"]) == completed


def test_idempotent_start_choice_and_conflicting_reuse(tmp_path, authored):
    store = ExperienceStore(tmp_path)
    story = store.save(authored)
    first = store.start(start_body(story))
    assert store.start(start_body(story)) == first
    assert len(store.sessions()) == 1
    other = store.save({**authored, "title": "Other"})
    with pytest.raises(Conflict, match="different input"):
        store.start(start_body(other))
    key = first["session"]["id"]
    chosen = store.choose(key, choice_body())
    assert store.choose(key, choice_body()) == chosen
    assert ExperienceStore(tmp_path).choose(key, choice_body()) == chosen
    with pytest.raises(Conflict, match="different input"):
        store.choose(key, choice_body("ferry"))
    with pytest.raises(Conflict, match="session revision"):
        store.choose(key, choice_body("ferry", "another", 1))
    with pytest.raises(ValueError, match="unavailable"):
        store.choose(key, choice_body("ferry", "ending", 2))
    assert len(store.session(key)["session"]["history"]) == 1


def test_concurrent_choices_have_one_winner_and_exact_replay(tmp_path, authored):
    store = ExperienceStore(tmp_path)
    story = store.save(authored)
    key = store.start(start_body(story))["session"]["id"]
    def submit(choice):
        try:
            return ExperienceStore(tmp_path).choose(key, choice_body(choice, choice))
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(submit, ["bridge", "ferry"]))
    winners = [answer for answer in answers if answer is not None]
    assert len(winners) == 1
    assert store.session(key) == winners[0]
    assert winners[0]["session"]["revision"] == 2
    winner = winners[0]["session"]["history"][0]["choice_id"]
    assert store.choose(key, choice_body(winner, winner)) == winners[0]


def test_isolated_homes_and_filtered_sessions(tmp_path, authored):
    first = ExperienceStore(tmp_path / "first")
    second = ExperienceStore(tmp_path / "second")
    a = first.save(authored)
    b = first.save({**authored, "title": "Second story"})
    first.start(start_body(a, "a"))
    first.start(start_body(b, "b"))
    assert len(first.sessions(a["id"])) == 1
    assert first.sessions(a["id"])[0]["story_id"] == a["id"]
    assert second.stories() == []
    assert second.sessions() == []
    with pytest.raises(NotFound):
        second.story(a["id"])
    with pytest.raises(ValueError):
        first.sessions("../escape")
    with pytest.raises(NotFound):
        first.session("missing")
    with pytest.raises(ValueError):
        first.story("../escape")


def test_derived_transitions_and_explicit_consistency(authored):
    graph = validate_graph(authored)
    assert graph["transitions"] == [
        {"source": "bank", "choice_id": "bridge", "target": "hill"},
        {"source": "bank", "choice_id": "ferry", "target": "harbor"},
    ]
    assert validate_graph(graph) == graph
    with pytest.raises(ValueError, match="transitions"):
        validate_graph({**authored, "transitions": []})
    assert "transitions" not in authored


def test_graph_refuses_dangling_unreachable_and_trapped_cycles(authored):
    dangling = copy.deepcopy(authored)
    dangling["nodes"][0]["choices"][0]["target"] = "absent"
    with pytest.raises(ValueError, match="target"):
        validate_graph(dangling)
    unreachable = copy.deepcopy(authored)
    unreachable["nodes"].append({"id": "island", "kind": "ending", "text": "An island", "choices": []})
    with pytest.raises(ValueError, match="reachable"):
        validate_graph(unreachable)
    trapped = {"title": "Trap", "start_node": "a", "nodes": [{"id": "a", "text": "Loop", "kind": "scene", "choices": [{"id": "again", "label": "Again", "target": "a"}]}]}
    with pytest.raises(ValueError, match="ending"):
        validate_graph(trapped)
    reachable_cycle = copy.deepcopy(authored)
    reachable_cycle["nodes"][0]["choices"].append({"id": "wait", "label": "Wait", "target": "bank"})
    assert len(validate_graph(reachable_cycle)["transitions"]) == 3


@pytest.mark.parametrize("field,value", [("title", ""), ("title", "a" * 201), ("start_node", "absent"), ("nodes", []), ("nodes", "invalid"), ("nodes", [None]), ("unexpected", True)])
def test_malformed_story_fields(field, value, authored, tmp_path):
    store = ExperienceStore(tmp_path)
    with pytest.raises(ValueError):
        store.save({**authored, field: value})
    assert store.stories() == []


@pytest.mark.parametrize("field,value", [("text", ""), ("kind", "other"), ("choices", None), ("id", "../node"), ("extra", True)])
def test_malformed_node_fields(field, value, authored):
    authored["nodes"][0][field] = value
    with pytest.raises(ValueError):
        validate_graph(authored)


def test_duplicate_ids_and_ending_choices_are_rejected(authored):
    duplicate = copy.deepcopy(authored)
    duplicate["nodes"].append(copy.deepcopy(duplicate["nodes"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_graph(duplicate)
    duplicate = copy.deepcopy(authored)
    duplicate["nodes"][0]["choices"][1]["id"] = "bridge"
    with pytest.raises(ValueError, match="duplicate"):
        validate_graph(duplicate)
    authored["nodes"][0]["kind"] = "ending"
    with pytest.raises(ValueError, match="ending"):
        validate_graph(authored)


@pytest.mark.parametrize("bad", [None, [], "text", 1])
def test_nonobject_tool_payloads_fail_without_writes(tmp_path, authored, bad):
    store = ExperienceStore(tmp_path)
    story = store.save(authored)
    session = store.start(start_body(story))
    with pytest.raises(ValueError):
        store.start(bad)
    with pytest.raises(ValueError):
        store.choose(session["session"]["id"], bad)
    assert store.session(session["session"]["id"]) == session


@pytest.mark.asyncio
async def test_http_author_play_edit_conflict_delete_and_resume(tmp_path, authored):
    app = web.Application()
    app[STORE] = ExperienceStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(PREFIX + "/stories", json=authored)
        assert response.status == 201
        story = (await response.json())["story"]
        response = await client.get(PREFIX + "/stories")
        assert (await response.json())["stories"] == [story]
        response = await client.get(PREFIX + "/stories/" + story["id"])
        assert (await response.json())["story"] == story
        response = await client.post(PREFIX + "/sessions", json=start_body(story))
        assert response.status == 201
        opened = await response.json()
        key = opened["session"]["id"]
        response = await client.post(PREFIX + f"/sessions/{key}/choices", json=choice_body())
        assert response.status == 200
        played = await response.json()
        assert played["node"]["id"] == "hill"
        response = await client.get(PREFIX + f"/sessions/{key}")
        assert await response.json() == played
        response = await client.post(PREFIX + f"/sessions/{key}/choices", json=choice_body("ferry"))
        assert response.status == 409
        assert "different input" in (await response.json())["error"]
        response = await client.put(PREFIX + "/stories/" + story["id"], json={**authored, "revision": 1, "title": "Edited"})
        assert (await response.json())["story"]["revision"] == 2
        response = await client.put(PREFIX + "/stories/" + story["id"], json={**authored, "revision": 1})
        assert response.status == 409
        response = await client.delete(PREFIX + "/stories/" + story["id"] + "?revision=2")
        assert await response.json() == {"deleted": True}
        response = await client.get(PREFIX + "/stories/" + story["id"])
        assert response.status == 404
        response = await client.get(PREFIX + f"/sessions/{key}")
        assert await response.json() == played


@pytest.mark.asyncio
async def test_http_invalid_bodies_do_not_bypass_graph_or_revision_checks(tmp_path, authored):
    app = web.Application()
    app[STORE] = ExperienceStore(tmp_path)
    register(app)
    async with TestClient(TestServer(app)) as client:
        for body in (None, [], {"home": "/tmp/other"}, {**authored, "start_node": "missing"}):
            response = await client.post(PREFIX + "/stories", json=body)
            assert response.status == 400
            assert "error" in await response.json()
        response = await client.post(PREFIX + "/stories", data="{broken", headers={"Content-Type": "application/json"})
        assert response.status == 400
        response = await client.post(PREFIX + "/stories", json=authored)
        story = (await response.json())["story"]
        response = await client.delete(PREFIX + "/stories/" + story["id"])
        assert response.status == 400
        response = await client.delete(PREFIX + "/stories/" + story["id"] + "?revision=99")
        assert response.status == 409
        response = await client.post(PREFIX + "/sessions", json={**start_body(story), "story_revision": True})
        assert response.status == 400
        response = await client.get(PREFIX + "/sessions/missing")
        assert response.status == 404
        response = await client.get(PREFIX + "/sessions?story_id=../escape")
        assert response.status == 400
        response = await client.get(PREFIX + "/sessions")
        assert await response.json() == {"sessions": []}
        assert len(app[STORE].stories()) == 1
