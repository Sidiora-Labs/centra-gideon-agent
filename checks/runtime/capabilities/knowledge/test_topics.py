import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.mcp_core import (
    reset_current_session_key,
    set_current_session_key,
)
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_topics import register
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.workspace.capabilities.communications.store import PeopleStore
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools
from gideon.workspace.capabilities.knowledge.topics import SOURCE_TYPES, TrackedTopics
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy, BoundTasks


@pytest.fixture
def topics(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    result = TrackedTopics(store, home=tmp_path)
    yield result
    store.close()


def save(service, **changes):
    return service.save(
        {
            "request_id": "topic-original-save",
            "name": "Observatory",
            "query": "observatory",
            "source_types": list(SOURCE_TYPES),
            **changes,
        }
    )


def test_topic_mutations_are_revisioned_and_idempotent(topics):
    first = save(topics)
    assert first["revision"] == 1
    assert first["created_at"] == first["updated_at"]
    assert save(topics) == first
    assert topics.list()["total"] == 1
    second = save(
        topics,
        request_id="topic-edited-save",
        id=first["id"],
        revision=1,
        name="Telescope visits",
        query="telescope visit",
        source_types=["note", "person"],
    )
    assert second["revision"] == 2
    assert second["created_at"] == first["created_at"]
    assert topics.get(first["id"])["query"] == "telescope visit"
    assert save(topics) == first
    assert topics.get(first["id"]) == second
    with pytest.raises(CaptureError) as error:
        save(topics, request_id="stale-topic-save", id=first["id"], revision=1)
    assert error.value.status == 409
    with pytest.raises(CaptureError) as error:
        save(topics, name="Reuse request with different name")
    assert error.value.status == 409
    deleted = topics.delete(
        first["id"], {"request_id": "topic-delete-once", "revision": 2}
    )
    assert deleted == {"id": first["id"], "deleted": True}
    assert (
        topics.delete(first["id"], {"request_id": "topic-delete-once", "revision": 2})
        == deleted
    )
    assert topics.list()["total"] == 0
    with pytest.raises(CaptureError) as error:
        topics.get(first["id"])
    assert error.value.status == 404


def test_live_matches_use_all_seven_actual_canonical_sources(topics):
    identities = {}
    for kind in ("note", "journal", "fleeting"):
        identities[kind] = topics.store.create_typed_item(
            item_type=kind,
            title="Observatory " + kind,
            content="Visit the telescope tonight.",
        )
    people = PeopleStore(topics.home / "capabilities/communications")
    person = people.save(
        {"name": "Observatory guide", "notes": "Call before the visit"}
    )
    identities["person"] = person["id"]
    project = BoundHierarchy(topics.home).create_project(
        "Observatory expedition", brief="Arrange a visit"
    )
    identities["project"] = project.id
    task = asyncio.run(
        BoundTasks(topics.home).create_task(
            title="Observatory booking", description="Reserve telescope time"
        )
    )
    identities["task"] = task.id
    archive = SemanticArchive(db_path=topics.home / "memory.db")
    archive.init()
    try:
        assert archive.write_episodic(
            "We visited the mountain observatory together last winter."
        )
        topics.memory = MemoryService.over_vector_store(archive)
        identities["memory"] = topics.memory.episodic_list()[0]["id"]
        topic = save(topics)
        result = topics.matches(topic["id"])
        assert result["total"] == 7
        assert result["total_is_complete"]
        assert result["truncated"] == []
        assert all(state == "available" for state in result["sources"].values())
        assert {
            row["source_type"]: row["source_id"] for row in result["items"]
        } == identities
        links = {row["source_type"]: row["source_link"] for row in result["items"]}
        assert links["task"] == "#/tasks?open=" + task.id
        assert links["project"] == "#/projects/" + project.id
        assert links["person"].endswith(person["id"])
        assert links["memory"].endswith(identities["memory"])
        assert all(row["matched_terms"] == ["observatory"] for row in result["items"])
        assert all(result["scanned"][kind] == 1 for kind in SOURCE_TYPES)
        topics.store.delete_item(identities["note"])
        topics.store.update_item(identities["journal"], is_archived=1)
        assert archive.delete_episodic(identities["memory"])
        refreshed = topics.matches(topic["id"])
        assert refreshed["total"] == 4
        assert {row["source_type"] for row in refreshed["items"]} == {
            "fleeting",
            "person",
            "project",
            "task",
        }
        assert topics.get(topic["id"])["revision"] == 1
    finally:
        archive.close()


def test_query_all_terms_casefold_and_scanned_bounds(topics):
    first = topics.store.create_typed_item(
        item_type="note", title="OBSERVATORY plans", content="Book the telescope."
    )
    topics.store.create_typed_item(
        item_type="note", title="Observatory schedule", content="No equipment noted."
    )
    topics.store.create_typed_item(
        item_type="note", title="Elsewhere", content="Telescope maintenance."
    )
    topic = save(topics, query="ObSeRvAtOrY TELESCOPE", source_types=["note"])
    result = topics.matches(topic["id"], limit=1)
    assert result["total"] == 1
    assert result["items"][0]["source_id"] == first
    assert result["items"][0]["matched_terms"] == ["observatory", "telescope"]
    assert result["scanned"] == {"note": 3}
    assert result["total_is_complete"]
    assert result["next_offset"] is None
    assert topics.matches(topic["id"], offset=1)["items"] == []
    assert topics.store.get_item(first)["title"] == "OBSERVATORY plans"


def test_empty_sources_do_not_create_canonical_domains(topics):
    topic = save(topics)
    before = sorted(path.relative_to(topics.home) for path in topics.home.rglob("*"))
    result = topics.matches(topic["id"])
    assert result["total"] == 0
    assert result["sources"]["memory"] == "unavailable"
    assert not result["total_is_complete"]
    assert result["sources"]["person"] == "available"
    assert result["sources"]["project"] == "available"
    assert result["sources"]["task"] == "available"
    assert not (topics.home / "projects").exists()
    assert not (topics.home / "tasks").exists()
    assert not (topics.home / "capabilities").exists()
    assert (
        sorted(path.relative_to(topics.home) for path in topics.home.rglob("*"))
        == before
    )
    assert topics.store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_project_topic_reads_do_not_create_builtin_defaults(topics):
    project = BoundHierarchy(topics.home).create_project("Observatory archive")
    topic = save(topics, source_types=["project"])
    paths = sorted(path.relative_to(topics.home) for path in topics.home.rglob("*"))
    result = topics.matches(topic["id"])
    assert [row["source_id"] for row in result["items"]] == [project.id]
    assert (
        sorted(path.relative_to(topics.home) for path in topics.home.rglob("*"))
        == paths
    )
    assert len(BoundHierarchy(topics.home)._all_projects_raw()) == 1


def test_home_drift_reads_stay_bound_and_mutations_fail_without_artifacts(
    topics, monkeypatch, tmp_path
):
    identity = topics.store.create_typed_item(
        item_type="note", title="Observatory memory", content="Original allocation"
    )
    topic = save(topics, source_types=["note"])
    foreign = tmp_path / "foreign"
    monkeypatch.setenv("GIDEON_HOME", str(foreign))
    assert topics.matches(topic["id"])["items"][0]["source_id"] == identity
    assert save(topics, source_types=["note"]) == topic
    with pytest.raises(CaptureError) as error:
        save(topics, request_id="drift-topic-save", name="Changed")
    assert error.value.status == 409
    with pytest.raises(CaptureError):
        topics.delete(topic["id"], {"request_id": "drift-topic-delete", "revision": 1})
    assert not foreign.exists()
    assert topics.list()["total"] == 1
    monkeypatch.setenv("GIDEON_HOME", str(topics.home))
    assert topics.delete(
        topic["id"], {"request_id": "drift-topic-delete", "revision": 1}
    )["deleted"]
    assert topics.store.get_item(identity)["content"] == "Original allocation"


@pytest.mark.parametrize(
    "change",
    [
        {"source_types": []},
        {"source_types": ["note", "note"]},
        {"source_types": ["runtime"]},
        {"source_types": [None]},
        {"query": "---"},
        {"name": ""},
        {"query": "x" * 301},
        {"id": "without-revision"},
        {"id": [], "revision": 1},
        {"id": None, "revision": 1},
        {"id": "", "revision": 1},
        {"home": "/tmp/other"},
    ],
)
def test_invalid_topic_shapes_have_no_persistence(topics, change):
    with pytest.raises(CaptureError):
        save(topics, **change)
    assert topics.list()["total"] == 0
    assert (
        topics.db.execute(
            "SELECT count(*) FROM capability_knowledge_topic_mutations"
        ).fetchone()[0]
        == 0
    )


def test_pagination_and_delete_revision_preserve_sources(topics):
    source = topics.store.create_typed_item(
        item_type="note", title="Observatory source"
    )
    created = [
        save(
            topics,
            request_id=f"topic-page-{index}",
            name=f"Topic {index}",
            source_types=["note"],
        )
        for index in range(3)
    ]
    first = topics.list(limit=2)
    assert first["total"] == 3
    assert first["next_offset"] == 2
    assert [row["id"] for row in first["items"]] == [created[2]["id"], created[1]["id"]]
    assert topics.list(limit=2, offset=2)["next_offset"] is None
    with pytest.raises(CaptureError) as error:
        topics.delete(
            created[0]["id"], {"request_id": "stale-topic-delete", "revision": 2}
        )
    assert error.value.status == 409
    assert topics.store.get_item(source)
    for limit, offset in ((0, 0), (101, 0), (20, -1), (True, 0)):
        with pytest.raises(CaptureError):
            topics.matches(created[0]["id"], limit, offset)


def test_native_topics_read_and_write_actual_data_with_session_guards(topics):
    source = topics.store.create_typed_item(
        item_type="note", title="Observatory native source"
    )
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = topics.store
    provider = KnowledgeCapabilityTools(
        ActionServices(state=state, spawn_background=asyncio.create_task)
    )
    token = set_current_session_key("dashboard:topic-native")
    try:
        arguments = {
            "request_id": "native-topic-save",
            "name": "Native topic",
            "query": "observatory",
            "source_types": ["note"],
        }
        state._sessions["topic-native"] = _ChatSession(
            "topic-native", memory_mode="incognito"
        )
        denied = asyncio.run(provider.invoke("knowledge_topic_save", arguments))
        assert not denied.success
        assert denied.metadata["status"] == 403
        state._sessions["topic-native"].memory_mode = "persistent"
        saved = asyncio.run(provider.invoke("knowledge_topic_save", arguments))
        assert saved.success
        topic = json.loads(saved.output)
        result = asyncio.run(
            provider.invoke("knowledge_topic_matches", {"id": topic["id"]})
        )
        assert result.success
        assert json.loads(result.output)["items"][0]["source_id"] == source
        state._sessions["topic-native"].memory_mode = "temporary"
        assert not asyncio.run(provider.invoke("knowledge_topic_list", {})).success
        state._sessions["topic-native"].memory_mode = "persistent"
        deleted = asyncio.run(
            provider.invoke(
                "knowledge_topic_delete",
                {"id": topic["id"], "revision": 1, "request_id": "native-topic-delete"},
            )
        )
        assert deleted.success
        assert topics.store.get_item(source)
        tools = {tool.name: tool for tool in asyncio.run(provider.list_tools())}
        assert tools["knowledge_topic_save"].requires_approval
        assert tools["knowledge_topic_delete"].requires_approval
        assert not tools["knowledge_topic_matches"].requires_approval
    finally:
        reset_current_session_key(token)


def test_real_http_topic_refresh_isolation_and_delete(topics, tmp_path):
    async def journey():
        state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        state._knowledge_store = topics.store
        app = web.Application()
        app["state"] = state
        register(app)
        other_store = KnowledgeStore(str(tmp_path / "other.db"))
        other_state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
        other_state._knowledge_store = other_store
        other = web.Application()
        other["state"] = other_state
        register(other)
        secret = "sk-" + "a" * 48
        source = topics.store.create_typed_item(
            item_type="note",
            title="Observatory HTTP source",
            content="Credential " + secret,
        )
        root = "/api/capabilities/knowledge/topics"
        async with (
            TestClient(TestServer(app)) as client,
            TestClient(TestServer(other)) as isolated,
        ):
            body = {
                "request_id": "http-topic-save",
                "name": "HTTP topic",
                "query": "observatory",
                "source_types": ["note", "memory"],
            }
            response = await client.post(root, json=body)
            assert response.status == 200
            topic = await response.json()
            response = await client.get(root + "/" + topic["id"] + "/matches")
            result = await response.json()
            assert result["total"] == 1
            assert not result["total_is_complete"]
            assert result["items"][0]["source_id"] == source
            assert secret not in result["items"][0]["excerpt"]
            assert secret in topics.store.get_item(source)["content"]
            assert (
                await isolated.get(root + "/" + topic["id"] + "/matches")
            ).status == 404
            topics.store.update_item(source, title="Removed keyword")
            assert (
                await (await client.get(root + "/" + topic["id"] + "/matches")).json()
            )["total"] == 0
            for query in ("?home=/tmp/other", "?model=x", "?limit=1&limit=2"):
                assert (await client.get(root + query)).status == 400
            assert (
                await client.post(root, json=body | {"runtime": "other"})
            ).status == 400
            response = await client.delete(
                root + "/" + topic["id"],
                json={"request_id": "http-topic-delete", "revision": 1},
            )
            assert response.status == 200
            assert (await response.json())["deleted"]
            assert topics.store.get_item(source)
            assert (await (await client.get(root)).json())["total"] == 0
        other_store.close()

    asyncio.run(journey())


def test_source_scan_bound_is_reported_without_false_total(topics):
    for index in range(1001):
        topics.store.create_typed_item(
            item_type="note", title=f"Observatory record {index}"
        )
    topic = save(topics, source_types=["note"])
    result = topics.matches(topic["id"], limit=100)
    assert result["scanned"] == {"note": 1000}
    assert result["truncated"] == ["note"]
    assert not result["total_is_complete"]
    assert result["total"] == 1000
    assert len(result["items"]) == 100
    assert result["next_offset"] == 100
    assert topics.matches(topic["id"], offset=1000)["items"] == []
    assert topics.store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1001
