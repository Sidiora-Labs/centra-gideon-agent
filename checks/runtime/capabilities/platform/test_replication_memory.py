import copy
import json
import struct

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.cognition.memory_record import MemoryKind, MemoryRecord, MemoryScope, MemoryTier
from gideon.cognition.vector_memory import SemanticArchive
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.platform import replication_memory as adapter
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import RECEIVE_PATH, ReplicationError, ReplicationService


CREATED = "2026-09-25T08:00:00+00:00"


def archive(home):
    value = SemanticArchive(db_path=home / "memory.db")
    value.init()
    return value


def semantic(home, identity, value, *, updated=CREATED, deleted=False, confidence=1.0, category="profile"):
    store = archive(home)
    try:
        result = store.import_semantic(MemoryRecord(
            id=identity, kind=MemoryKind.SEMANTIC, value=value, confidence=confidence,
            source="user_explicit", tier=MemoryTier.SEMANTIC, scope=MemoryScope.GLOBAL,
            category=category, is_deleted=deleted, created_at=CREATED, updated_at=updated,
        ))
        assert result.accepted
    finally:
        store.close()


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def peer_record(identity, endpoint, enabled=True):
    categories = [adapter.SCOPE] if enabled else []
    return {
        "label": identity["peer_id"][-8:], "endpoint": endpoint, "public_key": identity["public_key"],
        "enabled": True, "send_categories": categories, "receive_categories": categories, "revision": 0,
    }


def pair(first_home, second_home, enabled=True, second_endpoint="https://second.example"):
    first, second = PeerStore(first_home), PeerStore(second_home)
    first_id, second_id = first.snapshot()["self"], second.snapshot()["self"]
    first.put(second_id["peer_id"], peer_record(second_id, second_endpoint, enabled))
    second.put(first_id["peer_id"], peer_record(first_id, "https://first.example", enabled))
    return first_id, second_id


def test_projection_is_exact_semantic_only_with_tombstones_and_no_derived_state(tmp_path):
    home = tmp_path / "home"
    semantic(home, "pref.interface.theme", "coral")
    semantic(home, "project.gideon.status", {"phase": "source"}, updated="2026-09-25T08:05:00+00:00", deleted=True)
    store = archive(home)
    try:
        assert store.set_semantic("lesson.private", "do not replicate", 1.0, "user_explicit") is None
        assert store.set_semantic("user.persona.operator", "private persona", 1.0, "user_explicit") is None
        store.db.execute(
            "UPDATE semantic_memory SET embedding=?,recall_count=9,visit_count=4 WHERE key=?",
            (struct.pack("f", 1.0), "pref.interface.theme"),
        )
        store.db.commit()
    finally:
        store.close()
    projected = rows(home)
    assert [row["id"] for row in projected] == ["pref.interface.theme", "project.gideon.status"]
    assert projected[1]["data"]["is_deleted"] is True
    encoded = json.dumps(projected)
    for forbidden in ("embedding", "recall_count", "visit_count", "lesson.private", "private persona"):
        assert forbidden not in encoded


@pytest.mark.asyncio
async def test_signed_two_home_route_conflict_restore_and_exact_tombstone(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    semantic(source, "project.shared.fact", "baseline")
    semantic(source, "pref.retired.option", "enabled")
    receiver = ReplicationService(target)

    async def receive(request):
        envelope = await request.json()
        assert envelope["proof"]["scope"] == adapter.SCOPE
        peer = PeerStore(target).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    application = web.Application()
    application.router.add_post(RECEIVE_PATH, receive)
    async with TestServer(application) as server:
        source_id, target_id = pair(source, target, second_endpoint=str(server.make_url("/")))
        accepted = await ReplicationService(source).push(target_id["peer_id"], adapter.SCOPE)
    assert accepted["accepted"] is True
    assert rows(target) == rows(source)

    semantic(target, "project.shared.fact", "local", updated="2026-09-25T08:10:00+00:00", category="local")
    semantic(source, "project.shared.fact", "peer", updated="2026-09-25T08:15:00+00:00", category="peer")
    semantic(source, "pref.retired.option", "enabled", updated="2026-09-25T08:15:00+00:00", deleted=True)
    incoming = ReplicationService(source).export_batch(target_id["peer_id"], adapter.SCOPE)
    result = receiver.apply_batch(source_id["peer_id"], incoming)
    assert result["entries"][0]["conflicts"] == 1
    assert result["entries"][0]["removed"] == 1
    pending = conflicts.ConflictQueue(target).items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1
    receiver.restore_fields(pending[0].id, ["value"])
    current = {row["id"]: row["data"] for row in rows(target)}
    assert current["project.shared.fact"]["value"] == "peer"
    assert current["project.shared.fact"]["category"] == "local"
    assert current["pref.retired.option"]["is_deleted"] is True


def test_policy_and_complete_preflight_reject_private_authority_and_derived_shapes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first_id, second_id = pair(first, second, enabled=False)
    semantic(first, "pref.safe.value", "safe")
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(first).export_batch(second_id["peer_id"], adapter.SCOPE)
    canonical = rows(first)[0]
    invalid = []
    value = copy.deepcopy(canonical); value["data"]["id"] = "user.persona.private"; value["id"] = value["data"]["id"]; invalid.append(value)
    value = copy.deepcopy(canonical); value["data"]["credential_ref"] = "SECRET"; invalid.append(value)
    value = copy.deepcopy(canonical); value["data"]["value"] = {"access_token": "token-value"}; invalid.append(value)
    value = copy.deepcopy(canonical); value["data"]["value"] = "operator@example.test"; invalid.append(value)
    value = copy.deepcopy(canonical); value["data"]["embedding"] = [0.1]; invalid.append(value)
    value = copy.deepcopy(canonical); value["data"]["updated_at"] = "yesterday"; invalid.append(value)
    for row in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [row]}])
        assert rows(second) == []
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([])
    assert first_id["peer_id"] == PeerStore(second).get(first_id["peer_id"])["id"]


def test_owner_protected_conflict_is_held_before_mutation(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    semantic(first, "project.protected.fact", "local", confidence=1.0)
    remote = copy.deepcopy(rows(first)[0])
    remote["data"].update(
        value="automated peer", confidence=0.8, source="service",
        updated_at="2026-09-25T08:30:00+00:00",
    )
    queue = conflicts.ConflictQueue(first)
    result = adapter.apply_rows(
        first, adapter.ENTRY_ID, [remote], {}, queue, "2026-09-25T08:31:00+00:00",
    )
    assert result.conflicts == 1 and result.updated == 0
    assert rows(first)[0]["data"]["value"] == "local"
    assert len(queue.items(status=conflicts.STATUS_NEEDS_REVIEW)) == 1
    assert not (second / "memory.db").exists()
