import copy
import json
import struct
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.archive_episode_import import AuthoredEpisodeImport
from gideon.cognition.vector_memory import SemanticArchive
from gideon.interfaces.dashboard.handlers.capabilities_replication import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.platform import replication_episodic_memory as adapter
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import ReplicationError, ReplicationService
CREATED = "2026-09-25T08:00:00+00:00"
PREFIX = "/api/capabilities/platform/replication"


def archive(home):
    store = SemanticArchive(db_path=home / "memory.db")
    store.init()
    return store


def authored(home, text, *, identity=None, conversation="journal-2026-09-25", contributor="owner",
             source="user_explicit", deleted=False, updated=CREATED):
    record = {"id": identity or str(uuid4()), "conversation_id": conversation, "text": text,
              "tags": ["journal", "decision"], "importance": 0.8, "source": source,
              "contributor": contributor, "created_at": CREATED, "updated_at": updated,
              "is_deleted": deleted}
    store = archive(home)
    try:
        assert AuthoredEpisodeImport(store).apply(record).accepted
    finally:
        store.close()
    return record


def rows(home):
    return adapter.read_rows(home, adapter.ENTRY_ID)


def pair(first_home, second_home):
    first_home.mkdir(parents=True, exist_ok=True)
    second_home.mkdir(parents=True, exist_ok=True)
    first, second = PeerStore(first_home), PeerStore(second_home)
    first.create({"label": "Second", "endpoint": "https://second.example",
                  "public_key": second.identity["public_key"], "credential": "PEER_CONNECTION"})
    second.create({"label": "First", "endpoint": "https://first.example",
                   "public_key": first.identity["public_key"], "credential": "PEER_CONNECTION"})
    return first.identity, second.identity


def application():
    app = web.Application(middlewares=[token_auth_middleware(port=8014)])
    register(app)
    return app


def test_projection_is_authored_exact_and_excludes_model_vectors_access_and_events(tmp_path):
    home = tmp_path / "home"
    one = authored(home, "I chose the staged migration after reviewing the rollback evidence.")
    two = authored(home, "I retired the obsolete option after the replacement was verified.",
                   deleted=True, updated="2026-09-25T09:00:00+00:00")
    store = archive(home)
    try:
        assert store.write_episodic("A model-generated conversation summary that must remain local.",
                                    conversation_id="runtime-session", source="consolidation",
                                    contributor="agent")
        store.db.execute("UPDATE episodic_memories SET embedding=?,last_accessed_at=? WHERE id=?",
                         (struct.pack("f", 1.0), "2026-09-25T10:00:00+00:00", one["id"]))
        store.db.execute(
            "INSERT INTO memory_events(event_type,memory_type,memory_key,old_value,new_value,source,created_at) "
            "VALUES('recall','episodic',?,NULL,NULL,'runtime',?)",
            (one["id"], "2026-09-25T11:00:00+00:00"),
        )
        store.db.commit()
    finally:
        store.close()
    projected = rows(home)
    assert [row["id"] for row in projected] == sorted([one["id"], two["id"]])
    values = {row["id"]: row["data"] for row in projected}
    assert values[one["id"]] == one
    assert values[two["id"]] == two
    encoded = json.dumps(projected, sort_keys=True)
    for forbidden in ("model-generated", "embedding", "last_accessed", "memory_events", "permission", "safe_to_act"):
        assert forbidden not in encoded


def test_batch_owner_rejection_rolls_back_all_prior_imports(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    identities = sorted([str(uuid4()), str(uuid4())])
    first = authored(source, "I authored the first atomic episode for the receiving archive.", identity=identities[0])
    authored(source, "I authored the second atomic episode for the receiving archive.", identity=identities[1])
    store = archive(target)
    try:
        store.db.execute(
            "INSERT INTO episodic_memories(id,conversation_id,text,tags,importance,created_at,is_deleted,contributor) "
            "VALUES(?,?,?,?,?,?,0,?)",
            (identities[1], "local-runtime", "A locally generated summary occupying this identity.", "[]", 0.5,
             CREATED, "agent"),
        )
        store.db.execute(
            "INSERT INTO memory_events(event_type,memory_type,memory_key,old_value,new_value,source,created_at) "
            "VALUES('create','episodic',?,NULL,NULL,'consolidation',?)", (identities[1], CREATED),
        )
        store.db.commit()
    finally:
        store.close()
    with pytest.raises(ValueError, match="immutable"):
        adapter.apply_rows(target, adapter.ENTRY_ID, rows(source), {}, conflicts.ConflictQueue(target), CREATED)
    assert all(row["id"] != first["id"] for row in rows(target))


def test_real_two_home_import_preserves_identity_provenance_and_clears_derived_state(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    record = authored(source, "I approved the exact incident response plan and preserved its evidence.",
                      conversation="journal-incident-42", contributor="alice")
    queue = conflicts.ConflictQueue(target)
    result = adapter.apply_rows(target, adapter.ENTRY_ID, rows(source), {}, queue, CREATED)
    assert (result.added, result.removed, result.conflicts) == (1, 0, 0)
    assert rows(target) == rows(source)
    reopened = archive(target)
    try:
        stored = reopened.db.execute("SELECT * FROM episodic_memories WHERE id=?", (record["id"],)).fetchone()
        assert stored["conversation_id"] == "journal-incident-42"
        assert stored["contributor"] == "alice"
        assert stored["embedding"] is None
        assert stored["last_accessed_at"] is None
        assert reopened.db.execute("SELECT count(*) FROM mem_links WHERE from_ref=?", (record["id"],)).fetchone()[0] == 0
    finally:
        reopened.close()
    assert rows(target)[0]["data"] == record


@pytest.mark.asyncio
async def test_default_denied_then_authenticated_two_home_receive_uses_canonical_owner(tmp_path, monkeypatch):
    target, source = tmp_path / "target", tmp_path / "source"
    target_id, source_id = pair(target, source)
    record = authored(source, "I explicitly approved this episode for my configured peer archive.",
                      conversation="journal-approved", contributor="owner")
    sender = ReplicationService(source)
    with pytest.raises(ReplicationError, match="policy denies"):
        sender.export_batch(target_id["peer_id"], adapter.SCOPE)
    policy = {"revision": 1, "enabled": True, "send_categories": [adapter.SCOPE],
              "receive_categories": [adapter.SCOPE]}
    PeerStore(source).policy(target_id["peer_id"], policy)
    PeerStore(target).policy(source_id["peer_id"], policy)
    batch = sender.export_batch(target_id["peer_id"], adapter.SCOPE)
    envelope = PeerStore(source).signed(target_id["peer_id"], adapter.SCOPE)
    envelope["payload"] = batch
    body = json.dumps(envelope)
    monkeypatch.setenv("GIDEON_HOME", str(target))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    async with TestClient(TestServer(application())) as client:
        denied = await client.post(PREFIX + "/receive", data=body,
                                   headers={"Content-Type": "application/json"})
        assert denied.status == 403
        accepted = await client.post(
            PREFIX + "/receive", data=body,
            headers={"Content-Type": "application/json",
                     "Cookie": "gideon_token_8014=" + generate_token("dashboard:episodic-replication")},
        )
        assert accepted.status == 200
        response = await accepted.json()
        assert response["accepted"] is True
        assert response["domain"] == adapter.SCOPE
        assert response["entries"] == [{"entry_id": adapter.ENTRY_ID, "added": 1,
                                         "updated": 0, "removed": 0, "conflicts": 0}]
    assert rows(target)[0]["data"] == record


def test_explicit_tombstone_converges_only_from_common_unchanged_ancestor(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    record = authored(source, "I selected the canonical archive policy after reviewing privacy constraints.")
    initial = rows(source)
    first = adapter.apply_rows(target, adapter.ENTRY_ID, initial, {}, conflicts.ConflictQueue(target), CREATED)
    ancestor = first.new_ancestors[record["id"]]
    tombstone = {**record, "is_deleted": True, "updated_at": "2026-09-25T09:00:00+00:00"}
    store = archive(source)
    try:
        assert AuthoredEpisodeImport(store).apply(tombstone).code == "tombstoned"
    finally:
        store.close()
    result = adapter.apply_rows(target, adapter.ENTRY_ID, rows(source), {record["id"]: ancestor},
                                conflicts.ConflictQueue(target), "2026-09-25T09:01:00+00:00")
    assert (result.removed, result.conflicts) == (1, 0)
    assert rows(target)[0]["data"]["is_deleted"] is True
    assert rows(target)[0]["data"]["updated_at"] == tombstone["updated_at"]


def test_same_identity_different_immutable_episode_is_held_without_mutation_or_restore(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    identity = str(uuid4())
    remote = authored(source, "I recorded the remote authored decision with its exact context.", identity=identity)
    local = authored(target, "I recorded a different local decision under this colliding identity.", identity=identity)
    queue = conflicts.ConflictQueue(target)
    result = adapter.apply_rows(target, adapter.ENTRY_ID, rows(source), {}, queue, "2026-09-25T10:00:00+00:00")
    assert (result.added, result.removed, result.conflicts) == (0, 0, 1)
    assert rows(target)[0]["data"] == local
    pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
    assert len(pending) == 1
    assert pending[0].local_row["data"]["text"] == local["text"]
    assert pending[0].remote_row["data"]["text"] == remote["text"]
    with pytest.raises(ValueError, match="immutable"):
        adapter.restore_fields(target, pending[0].id, ["text"], "2026-09-25T10:01:00+00:00")


@pytest.mark.parametrize("mutation", [
    lambda row: row["data"].__setitem__("source", "consolidation"),
    lambda row: row["data"].__setitem__("text", "Authorization: Bearer secret-value"),
    lambda row: row["data"].__setitem__("conversation_id", "../runtime/session"),
    lambda row: row["data"].__setitem__("contributor", ""),
    lambda row: row["data"].__setitem__("embedding", [0.1]),
    lambda row: row["data"].__setitem__("safe_to_act", {"shell": True}),
    lambda row: row["data"].__setitem__("updated_at", "before"),
])
def test_preflight_rejects_private_generated_derived_or_execution_shapes_before_owner_mutation(tmp_path, mutation):
    source, target = tmp_path / "source", tmp_path / "target"
    authored(source, "I wrote this sufficiently detailed private-free episode for replication.")
    value = copy.deepcopy(rows(source)[0])
    mutation(value)
    with pytest.raises(ValueError):
        adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [value]}])
    assert not (target / "memory.db").exists()


def test_owner_import_is_idempotent_and_refuses_resurrection_or_immutable_rewrite(tmp_path):
    home = tmp_path / "home"
    record = authored(home, "I authored this immutable episode with a stable identity and provenance.")
    store = archive(home)
    try:
        owner = AuthoredEpisodeImport(store)
        assert owner.apply(record).code == "already_current"
        changed = {**record, "text": "I attempted to rewrite immutable episode content after creation."}
        with pytest.raises(ValueError, match="immutable"):
            owner.apply(changed)
        tombstone = {**record, "is_deleted": True, "updated_at": "2026-09-25T11:00:00+00:00"}
        assert owner.apply(tombstone).code == "tombstoned"
        with pytest.raises(ValueError, match="immutable"):
            owner.apply(record)
    finally:
        store.close()
    assert rows(home)[0]["data"]["is_deleted"] is True


def test_absence_is_not_a_delete_and_unknown_coverage_is_refused(tmp_path):
    home = tmp_path / "home"
    record = authored(home, "I preserved this episode unless an explicit authored tombstone arrives.")
    result = adapter.apply_rows(home, adapter.ENTRY_ID, [], {}, conflicts.ConflictQueue(home), CREATED)
    assert (result.added, result.removed, result.conflicts) == (0, 0, 0)
    assert rows(home)[0]["id"] == record["id"]
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([])
    with pytest.raises(ValueError, match="Unknown"):
        adapter.read_rows(home, "memory.other")
