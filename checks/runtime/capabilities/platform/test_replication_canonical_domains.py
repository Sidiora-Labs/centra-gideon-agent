import copy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.experience.store import ExperienceStore
from gideon.workspace.capabilities.identity.store import StoryStore
from gideon.workspace.capabilities.platform import replication_adapters
from gideon.workspace.capabilities.platform import replication_commissions
from gideon.workspace.capabilities.platform import replication_experience_stories
from gideon.workspace.capabilities.platform import replication_identity_stories
from gideon.workspace.capabilities.platform import replication_knowledge_collections
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import DOMAINS, RECEIVE_PATH, ReplicationError, ReplicationService


ADAPTERS = (
    replication_identity_stories,
    replication_knowledge_collections,
    replication_experience_stories,
    replication_commissions,
)


def peer_record(identity, endpoint, scope, send=True, receive=True):
    return {
        "label": identity["peer_id"][-8:], "endpoint": endpoint, "public_key": identity["public_key"],
        "enabled": True, "send_categories": [scope] if send else [],
        "receive_categories": [scope] if receive else [], "revision": 0,
    }


def pair(first_home, second_home, scope, second_endpoint="https://second.example"):
    first, second = PeerStore(first_home), PeerStore(second_home)
    first_id, second_id = first.snapshot()["self"], second.snapshot()["self"]
    first.put(second_id["peer_id"], peer_record(second_id, second_endpoint, scope))
    second.put(first_id["peer_id"], peer_record(first_id, "https://first.example", scope))
    return first_id, second_id


def knowledge(home):
    return KnowledgeStore(str(knowledge_db_path(home)))


def story_graph(title):
    return {
        "title": title,
        "start_node": "crossroads",
        "nodes": [
            {"id": "crossroads", "text": "Choose a road.", "kind": "scene", "choices": [
                {"id": "east", "label": "Walk east", "target": "orchard"},
            ]},
            {"id": "orchard", "text": "You reach the orchard.", "kind": "ending", "choices": []},
        ],
    }


def commission_record():
    return {
        "id": "commission-one", "revision": 1, "name": "Harbor treatment", "target_ability": "series",
        "brief": {"intent": "Develop the pinned source.", "genre": "mystery", "category": "treatment", "style": "Precise.", "constraints": {}, "seed_refs": []},
        "sources": [{"kind": "work", "id": "source-work", "revision": 1}],
        "steps": [{"id": "verify", "title": "Verify source", "operation": "source.verify", "depends_on": []}],
        "created_at": "2026-09-25T12:00:00+00:00", "updated_at": "2026-09-25T12:00:00+00:00",
    }


def prepare(adapter, source, target):
    if adapter is replication_identity_stories:
        StoryStore(source / "capabilities/identity/stories.sqlite3").create(
            prompt="Where did this begin?", theme="Origins", text="The harbor lights marked the route.",
            request_id="story-one",
        )
    elif adapter is replication_knowledge_collections:
        source_store = knowledge(source)
        try:
            item_id = source_store.create_typed_item(
                item_type="note", title="Field note", content="A canonical note.", tags=["shared"],
            )
            collection_id = source_store.create_collection(name="Research shelf", icon="archive")
            assert source_store.add_to_collection(collection_id, item_id)
        finally:
            source_store.db.close()
        item_rows = replication_adapters.read_rows(source, "knowledge.items")
        replication_adapters.apply_rows(
            target, "knowledge.items", item_rows, {}, conflicts.ConflictQueue(target),
            "2026-09-25T12:00:00+00:00",
        )
    elif adapter is replication_experience_stories:
        ExperienceStore(source).save(story_graph("The divided road"))
    else:
        value = commission_record()
        adapter.write_row(source, adapter.ENTRY_ID, {"id": value["id"], "data": value}, value["id"])
    return [{"entry_id": entry_id, "rows": adapter.read_rows(source, entry_id)} for entry_id in adapter.ENTRIES]


def edit_first(adapter, home, title):
    entry_id = adapter.ENTRIES[0]
    row = copy.deepcopy(adapter.read_rows(home, entry_id)[0])
    if adapter is replication_knowledge_collections:
        store = knowledge(home)
        try:
            assert store.update_collection(row["id"], name=title)
        finally:
            store.db.close()
        return row["id"], "name"
    field = "theme" if adapter is replication_identity_stories else "title" if adapter is replication_experience_stories else "name"
    row["data"][field] = title
    row["data"]["revision"] += 1
    if adapter is replication_identity_stories or adapter is replication_commissions:
        row["data"]["updated_at"] = "2026-09-25T12:10:00+00:00" if title == "Local title" else "2026-09-25T12:11:00+00:00"
    adapter.write_row(home, entry_id, row, row["id"])
    return row["id"], field


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_canonical_domains_are_declared_and_default_denied(tmp_path, adapter):
    first, second = tmp_path / "first", tmp_path / "second"
    first_store, second_store = PeerStore(first), PeerStore(second)
    second_id = second_store.snapshot()["self"]
    first_store.put(second_id["peer_id"], peer_record(second_id, "https://second.example", adapter.SCOPE, send=False))
    assert DOMAINS[adapter.SCOPE].entries == adapter.ENTRIES
    assert adapter.SCOPE in first_store.snapshot()["categories"]
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(first).export_batch(second_id["peer_id"], adapter.SCOPE)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_signed_scope_dispatches_complete_canonical_batch(tmp_path, adapter):
    first, second = tmp_path / "first", tmp_path / "second"
    expected = prepare(adapter, first, second)
    receiver = ReplicationService(second)

    async def receive(request):
        envelope = await request.json()
        assert envelope["proof"]["scope"] == adapter.SCOPE
        peer = PeerStore(second).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    app = web.Application()
    app.router.add_post(RECEIVE_PATH, receive)
    async with TestServer(app) as server:
        first_id, second_id = pair(first, second, adapter.SCOPE, str(server.make_url("/")))
        malformed = ReplicationService(first).export_batch(second_id["peer_id"], adapter.SCOPE)
        malformed = copy.deepcopy(malformed)
        malformed["entries"][0]["rows"][0]["data"]["request_id"] = "private-authority"
        with pytest.raises(ReplicationError) as rejected:
            receiver.apply_batch(first_id["peer_id"], malformed)
        assert rejected.value.status == 422
        response = await ReplicationService(first).push(second_id["peer_id"], adapter.SCOPE)

    assert response["accepted"] is True
    assert [item["entry_id"] for item in response["entries"]] == list(adapter.ENTRIES)
    assert [
        {"entry_id": entry_id, "rows": adapter.read_rows(second, entry_id)}
        for entry_id in adapter.ENTRIES
    ] == expected


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_central_conflict_restore_dispatch_preserves_unselected_fields(tmp_path, adapter):
    first, second = tmp_path / "first", tmp_path / "second"
    prepare(adapter, first, second)
    first_id, second_id = pair(first, second, adapter.SCOPE)
    first_service, second_service = ReplicationService(first), ReplicationService(second)
    baseline = first_service.export_batch(second_id["peer_id"], adapter.SCOPE)
    second_service.apply_batch(first_id["peer_id"], baseline)
    first_service._record_sent(second_id["peer_id"], baseline)

    identity, field = edit_first(adapter, first, "Local title")
    edit_first(adapter, second, "Peer title")
    incoming = second_service.export_batch(first_id["peer_id"], adapter.SCOPE)
    result = first_service.apply_batch(second_id["peer_id"], incoming)
    assert sum(item["conflicts"] for item in result["entries"]) == 1
    pending = conflicts.ConflictQueue(first).items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1 and pending[0].entity_id == identity
    restored = first_service.restore_fields(pending[0].id, [field])
    current = next(row for row in adapter.read_rows(first, pending[0].entry_id) if row["id"] == identity)
    assert restored["fields"] == [field]
    assert current["data"][field] == "Peer title"


def test_knowledge_collection_membership_failure_is_atomic_at_central_boundary(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    prepare(replication_knowledge_collections, first, second)
    first_id, second_id = pair(first, second, replication_knowledge_collections.SCOPE)
    batch = ReplicationService(first).export_batch(second_id["peer_id"], replication_knowledge_collections.SCOPE)
    missing_item = knowledge(second)
    try:
        missing_item.db.execute("DELETE FROM items")
        missing_item.db.commit()
    finally:
        missing_item.db.close()
    with pytest.raises(ReplicationError, match="missing canonical item") as rejected:
        ReplicationService(second).apply_batch(first_id["peer_id"], batch)
    assert rejected.value.status == 422
    assert replication_knowledge_collections.read_rows(second, replication_knowledge_collections.COLLECTION_ENTRY) == []
    assert replication_knowledge_collections.read_rows(second, replication_knowledge_collections.MEMBERSHIP_ENTRY) == []
