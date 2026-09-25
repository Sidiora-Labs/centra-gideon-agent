import json
import os
import sqlite3
import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.interfaces.dashboard.handlers.capabilities_replication import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.operations.durability.conflicts import ConflictQueue, STATUS_NEEDS_REVIEW
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import DOMAINS, ReplicationError, ReplicationService
from gideon.workspace.capabilities.platform.replication_tools import create_provider
from gideon.workspace.capabilities.platform.replication_adapters import CREATIVE_TABLES
from gideon.workspace.capabilities.creative.store import IngredientStore
from gideon.workspace.capabilities.creative.direction import DirectionStore
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.progress import ProgressStore
from gideon.workspace.capabilities.identity.twin import TwinStore
from gideon.workspace.capabilities.communications.store import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications import social
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.library import MediaLibrary
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.decks import DeckStore
from gideon.workspace.capabilities.music.listening import ListeningStore
from gideon.workspace.capabilities.music.spotify import SpotifyBridge
from gideon.workspace.capabilities.music.store import RepertoireStore
from gideon.workspace.capabilities.wellbeing.apple_health import AppleHealthStore
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore
from gideon.workspace.capabilities.wellbeing.genome import GenomeStore
from gideon.workspace.capabilities.wellbeing.cognition import CognitiveStore
from gideon.workspace.capabilities.wellbeing.memory_practice import MemoryPracticeStore
from gideon.workspace.capabilities.wellbeing.life_calendar import LifeCalendarStore
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementStore
from gideon.workspace.capabilities.wellbeing.substances import ConsumptionStore

SCOPE = "workspace.records"
PREFIX = "/api/capabilities/platform/replication"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    use_ephemeral_secret()
    return tmp_path


def peer_record(identity, endpoint, revision=0, enabled=True, send=None, receive=None):
    return {"label": identity["peer_id"][-8:], "endpoint": endpoint, "public_key": identity["public_key"], "enabled": enabled, "send_categories": list([SCOPE] if send is None else send), "receive_categories": list([SCOPE] if receive is None else receive), "revision": revision}


def pair(a_home, b_home, a_endpoint="https://a.example", b_endpoint="https://b.example"):
    a, b = PeerStore(a_home), PeerStore(b_home)
    aid, bid = a.snapshot()["self"], b.snapshot()["self"]
    a.put(bid["peer_id"], peer_record(bid, b_endpoint))
    b.put(aid["peer_id"], peer_record(aid, a_endpoint))
    return aid, bid


def pair_for(a_home, b_home, scope):
    a, b = PeerStore(a_home), PeerStore(b_home)
    aid, bid = a.snapshot()["self"], b.snapshot()["self"]
    a.put(bid["peer_id"], peer_record(bid, "https://b.example", send=[scope], receive=[scope]))
    b.put(aid["peer_id"], peer_record(aid, "https://a.example", send=[scope], receive=[scope]))
    return aid, bid


def knowledge_store(home):
    return KnowledgeStore(str(knowledge_db_path(home)))


def create_domain_record(home, scope, title="base", content="local body"):
    if scope == "knowledge.records":
        store = knowledge_store(home)
        try:
            identity = store.create_typed_item(item_type="note", title=title, content=content, tags=["shared"])
            store.db.commit()
            return identity
        finally:
            store.db.close()
    return IngredientStore(home).create({"request_id": "create-" + title.replace(" ", "-"), "type": "concept", "title": title, "body": content, "tags": ["shared"], "source_refs": [], "relations": []})["id"]


def get_domain_record(home, scope, identity):
    if scope == "knowledge.records":
        store = knowledge_store(home)
        try: return store.get_item(identity)
        finally: store.db.close()
    try: return IngredientStore(home).get(identity)
    except Exception: return None


def update_domain_record(home, scope, identity, title, content):
    if scope == "knowledge.records":
        store = knowledge_store(home)
        try: store.update_item(identity, title=title, content=content); store.db.commit()
        finally: store.db.close()
    else:
        old = IngredientStore(home).get(identity)
        IngredientStore(home).update(identity, {"revision": old["revision"], "title": title, "body": content})


def delete_domain_record(home, scope, identity):
    if scope == "knowledge.records":
        store = knowledge_store(home)
        try: store.delete_item(identity)
        finally: store.db.close()
    else:
        with IngredientStore(home).connection() as db:
            db.execute("DELETE FROM ingredients WHERE id=?", (identity,))


def in_home(home, action):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(home)
    try:
        return action(HierarchyStore())
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous


def create_project(home, name="Alpha", brief="base"):
    return in_home(home, lambda store: store.create_project(name, brief=brief))


def project(home, identity):
    return in_home(home, lambda store: store.get_project(identity))


def update_project(home, identity, brief):
    return in_home(home, lambda store: store.update_project(identity, brief=brief))


def delete_project(home, identity):
    return in_home(home, lambda store: store.delete_project(identity))


def test_export_is_fixed_coverage_idempotent_versioned_and_secret_free(home, tmp_path):
    remote_home = tmp_path / "remote"
    _, remote = pair(home, remote_home)
    created = create_project(home)
    service = ReplicationService(home)
    first = service.export_batch(remote["peer_id"], SCOPE)
    second = ReplicationService(home).export_batch(remote["peer_id"], SCOPE)
    assert first == second
    assert first["schema_version"] == 1
    assert first["domain"] == SCOPE
    assert first["sequence"] == 1
    assert len(first["batch_id"]) == 32
    assert [item["entry_id"] for item in first["entries"]] == ["projects", "tasks"]
    rows = first["entries"][0]["rows"]
    assert rows[0]["id"] == created.id + "/project"
    wire = json.dumps(first)
    for forbidden in ("private_key", "identity.key", "peers.sqlite3", "replication.sqlite3", "nonce", "signature"):
        assert forbidden not in wire
    update_project(home, created.id, "changed")
    changed = service.export_batch(remote["peer_id"], SCOPE)
    assert changed["sequence"] == 2
    assert changed["batch_id"] != first["batch_id"]
    assert changed["entries"][0]["rows"][0]["data"]["brief"] == "changed"


def test_two_home_create_edit_delete_and_offline_restart(home, tmp_path):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair(a_home, b_home)
    created = create_project(a_home, brief="created on A")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    first = a.export_batch(bid["peer_id"], SCOPE)
    response = b.apply_batch(aid["peer_id"], first)
    assert response["accepted"] is True
    assert response["entries"][0]["added"] == 1
    assert project(b_home, created.id).brief == "created on A"
    assert ReplicationService(b_home).apply_batch(aid["peer_id"], first) == response
    update_project(a_home, created.id, "edited after offline interval")
    second = ReplicationService(a_home).export_batch(bid["peer_id"], SCOPE)
    assert second["sequence"] == 2
    assert ReplicationService(b_home).apply_batch(aid["peer_id"], second)["sequence"] == 2
    assert project(b_home, created.id).brief == "edited after offline interval"
    assert delete_project(a_home, created.id)
    third = ReplicationService(a_home).export_batch(bid["peer_id"], SCOPE)
    marker = next(row for row in third["entries"][0]["rows"] if row["id"] == created.id + "/project")
    assert "deleted_at" in marker
    removed = ReplicationService(b_home).apply_batch(aid["peer_id"], third)
    assert removed["entries"][0]["removed"] == 1
    assert project(b_home, created.id) is None
    with sqlite3.connect(b.path) as connection:
        assert connection.execute("SELECT sequence FROM cursors WHERE peer_id=? AND domain=?", (aid["peer_id"], SCOPE)).fetchone()[0] == 3


def test_independent_two_home_edits_queue_exact_conflict_and_hold_local(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair(a_home, b_home)
    created = create_project(a_home, brief="shared base")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], SCOPE)
    b.apply_batch(aid["peer_id"], baseline)
    a._record_sent(bid["peer_id"], baseline)
    update_project(a_home, created.id, "local A edit")
    update_project(b_home, created.id, "remote B edit")
    incoming = b.export_batch(aid["peer_id"], SCOPE)
    result = a.apply_batch(bid["peer_id"], incoming)
    assert result["entries"][0]["conflicts"] == 1
    assert project(a_home, created.id).brief == "local A edit"
    queued = ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW)
    assert len(queued) == 1
    conflict = queued[0]
    assert conflict.entry_id == "projects"
    assert conflict.entity_id == created.id + "/project"
    assert conflict.local_row["data"]["brief"] == "local A edit"
    assert conflict.remote_row["data"]["brief"] == "remote B edit"
    assert conflict.surface == "durability"
    assert a.apply_batch(bid["peer_id"], incoming) == result
    assert len(ConflictQueue(a_home).items()) == 1


def test_replay_sequence_gap_mixed_version_coverage_and_sender_denials(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair(a_home, b_home)
    create_project(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    batch = a.export_batch(bid["peer_id"], SCOPE)
    b.apply_batch(aid["peer_id"], batch)
    changed = json.loads(json.dumps(batch)); changed["entries"][0]["rows"][0]["data"]["brief"] = "tampered"
    with pytest.raises(ReplicationError, match="reused") as replay:
        b.apply_batch(aid["peer_id"], changed)
    assert replay.value.status == 409
    gap = json.loads(json.dumps(batch)); gap["sequence"] = 3; gap["batch_id"] = "3" * 32
    with pytest.raises(ReplicationError, match="expects sequence 2"):
        b.apply_batch(aid["peer_id"], gap)
    newer = json.loads(json.dumps(batch)); newer["schema_version"] = 2; newer["sequence"] = 2; newer["batch_id"] = "2" * 32
    with pytest.raises(ReplicationError, match="schema"):
        b.apply_batch(aid["peer_id"], newer)
    secret = json.loads(json.dumps(batch)); secret["sequence"] = 2; secret["batch_id"] = "4" * 32; secret["entries"][0]["entry_id"] = "auth"
    with pytest.raises(ReplicationError, match="coverage"):
        b.apply_batch(aid["peer_id"], secret)
    wrong = json.loads(json.dumps(batch)); wrong["sequence"] = 2; wrong["batch_id"] = "5" * 32; wrong["sender"] = bid["peer_id"]
    with pytest.raises(ReplicationError, match="sender") as sender:
        b.apply_batch(aid["peer_id"], wrong)
    assert sender.value.status == 403


def test_disabled_and_directional_policy_deny_before_export_or_apply(home, tmp_path):
    remote_home = tmp_path / "remote"
    local_id, remote_id = pair(home, remote_home)
    local_peer = PeerStore(home).get(remote_id["peer_id"])
    PeerStore(home).put(remote_id["peer_id"], peer_record(remote_id, local_peer["endpoint"], revision=1, send=[]))
    with pytest.raises(ReplicationError, match="export") as denied:
        ReplicationService(home).export_batch(remote_id["peer_id"], SCOPE)
    assert denied.value.status == 403
    remote_service = ReplicationService(remote_home)
    remote_peer = PeerStore(remote_home).get(local_id["peer_id"])
    PeerStore(remote_home).put(local_id["peer_id"], peer_record(local_id, remote_peer["endpoint"], revision=1, receive=[]))
    batch = {"schema_version": 1, "domain": SCOPE, "sender": local_id["peer_id"], "sequence": 1, "batch_id": "a" * 32, "entries": [{"entry_id": "projects", "rows": []}, {"entry_id": "tasks", "rows": []}]}
    with pytest.raises(ReplicationError, match="import") as inbound:
        remote_service.apply_batch(local_id["peer_id"], batch)
    assert inbound.value.status == 403
    assert not remote_service.path.exists()


@pytest.mark.asyncio
async def test_real_signed_guarded_push_between_two_homes(home):
    a_home, b_home = home / "a", home / "b"
    receiver = ReplicationService(b_home)

    async def receive(request):
        envelope = await request.json()
        peer = PeerStore(b_home).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    app = web.Application(); app.router.add_post("/api/capabilities/platform/replication/receive", receive)
    async with TestServer(app) as server:
        aid, bid = pair(a_home, b_home, b_endpoint=str(server.make_url("/")))
        created = create_project(a_home, brief="signed transport")
        result = await ReplicationService(a_home).push(bid["peer_id"], SCOPE)
    assert result["accepted"] is True
    assert result["sequence"] == 1
    assert project(b_home, created.id).brief == "signed transport"
    with sqlite3.connect(ReplicationService(a_home).path) as connection:
        assert connection.execute("SELECT count(*) FROM ancestors WHERE peer_id=?", (bid["peer_id"],)).fetchone()[0] >= 1
    assert aid["peer_id"] == PeerStore(b_home).get(aid["peer_id"])["id"]


@pytest.mark.asyncio
async def test_signed_creative_direction_scope_uses_canonical_owner_adapter(home):
    denied_a, denied_b = home / "direction-denied-a", home / "direction-denied-b"
    _, denied_peer = pair(denied_a, denied_b)
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(denied_a).export_batch(denied_peer["peer_id"], "creative.direction")

    a_home, b_home = home / "direction-a", home / "direction-b"
    receiver = ReplicationService(b_home)

    async def receive(request):
        envelope = await request.json()
        assert envelope["proof"]["scope"] == "creative.direction"
        peer = PeerStore(b_home).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    app = web.Application(); app.router.add_post("/api/capabilities/platform/replication/receive", receive)
    async with TestServer(app) as server:
        a_peers, b_peers = PeerStore(a_home), PeerStore(b_home)
        aid, bid = a_peers.snapshot()["self"], b_peers.snapshot()["self"]
        a_peers.put(bid["peer_id"], peer_record(bid, str(server.make_url("/")), send=["creative.direction"], receive=["creative.direction"]))
        b_peers.put(aid["peer_id"], peer_record(aid, "https://a.example", send=["creative.direction"], receive=["creative.direction"]))
        works = WorkStore(a_home)
        work = works.create({"request_id": "direction-work", "title": "Night signal", "kind": "work", "prompt": "", "author_ref": None, "universe_ref": None, "active_draft_id": None})
        draft = works.draft(work["id"], {"request_id": "direction-draft", "revision": 1, "text": "A lighthouse answers.", "note": "approved"})
        project = DirectionStore(a_home).create({"request_id": "direction-project", "name": "Harbor treatment", "treatment": "Cold blue light crosses the harbor.", "sources": [{"kind": "work", "id": draft["work"]["id"], "revision": draft["work"]["revision"]}], "steps": [{"id": "verify", "title": "Verify source", "operation": "source.verify", "depends_on": []}]})
        result = await ReplicationService(a_home).push(bid["peer_id"], "creative.direction")
    assert result["accepted"] is True and result["entries"][0]["entry_id"] == "creative.direction_projects"
    assert DirectionStore(b_home).get(project["id"]) == project
    assert WorkStore(b_home).list()["items"] == []
    source_pin = project["sources"][0]["chapters"][0]
    assert NativeArtifactProvider(b_home / "artifacts").get(source_pin["artifact_id"], version=source_pin["artifact_version"]) is None
    with sqlite3.connect(b_home / "capabilities/creative/direction.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM direction_requests").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_http_boundaries_status_native_and_signed_receive(home, tmp_path):
    remote_home = tmp_path / "remote"
    local_id, remote_id = pair(home, remote_home)
    create_project(remote_home)
    remote_batch = ReplicationService(remote_home).export_batch(local_id["peer_id"], SCOPE)
    proof = PeerStore(remote_home).create_proof(local_id["peer_id"], SCOPE)
    app = web.Application(middlewares=[token_auth_middleware(port=8000)]); register(app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status == 403
        headers = {"Cookie": "gideon_token_8000=" + generate_token("dashboard:replication")}
        status = await client.get(PREFIX, headers=headers)
        assert status.status == 200
        assert status.headers["Cache-Control"] == "no-store"
        denied = await client.post(f"{PREFIX}/receive", json={"proof": proof, "payload": remote_batch})
        assert denied.status == 403
        accepted = await client.post(f"{PREFIX}/receive", headers=headers, json={"proof": proof, "payload": remote_batch})
        assert accepted.status == 200
        assert (await accepted.json())["sequence"] == 1
        native = await create_provider().invoke("platform_replication_status", {})
        assert native.success
        projection = json.loads(native.output)
        assert projection["cursors"][0]["sequence"] == 1
        assert "private_key" not in native.output
        assert "rows" not in native.output
    assert not (home / "capabilities/platform/replication.sqlite3-wal").exists() or (home / "capabilities/platform/replication.sqlite3").exists()


def test_status_reports_declared_coverage_cursor_and_conflict_only(home, tmp_path):
    remote_home = tmp_path / "remote"
    local_id, remote_id = pair(home, remote_home)
    service = ReplicationService(home)
    value = service.status()
    assert value["domains"] == [
        {"scope": SCOPE, "entries": ["projects", "tasks"]},
        {"scope": "knowledge.records", "entries": ["knowledge.items"]},
        {"scope": "knowledge.collections", "entries": ["knowledge.collections", "knowledge.collection_items"]},
        {"scope": "creative.catalog", "entries": ["creative.ingredients", "creative.moodboards", "creative.universes", "creative.authors", "creative.works", "creative.stories", "creative.series"]},
        {"scope": "creative.commissions", "entries": ["creative.commission_records"]},
        {"scope": "creative.direction", "entries": ["creative.direction_projects"]},
        {"scope": "identity.goals", "entries": ["identity.goals", "identity.sessions", "identity.goal_plans", "identity.goal_checkins"]},
        {"scope": "identity.profile", "entries": ["identity.progress_profile", "identity.twin_profile", "identity.twin_documents"]},
        {"scope": "identity.stories", "entries": ["identity.life_stories"]},
        {"scope": "experience.stories", "entries": ["experience.story_graphs"]},
        {"scope": "memory.records", "entries": ["memory.semantic_records"]},
        {"scope": "communications.contacts", "entries": ["communications.people", "communications.touchpoints"]},
        {"scope": "music.library", "entries": ["music.artists", "music.tracks", "music.albums", "music.songs", "music.playlists", "music.decks"]},
        {"scope": "media.assets", "entries": ["media.library_metadata"]},
        {"scope": "media.video_projects", "entries": ["media.timelines"]},
        {"scope": "music.video", "entries": ["music.video_projects"]},
        {"scope": "wellbeing.health", "entries": ["wellbeing.measurements", "wellbeing.labs", "wellbeing.metrics"]},
        {"scope": "wellbeing.routines", "entries": ["wellbeing.substance_entries", "wellbeing.substance_presets", "wellbeing.intervention_plans", "wellbeing.intervention_records"]},
        {"scope": "wellbeing.genome", "entries": ["wellbeing.genome_sources", "wellbeing.genome_variants"]},
        {"scope": "wellbeing.practice", "entries": ["wellbeing.cognitive_sessions", "wellbeing.memory_cards"]},
        {"scope": "wellbeing.life_calendar", "entries": ["wellbeing.life_config", "wellbeing.life_events"]},
    ]
    assert value["cursors"] == []
    assert value["conflicts"] == []
    assert value["peers"][0]["id"] == remote_id["peer_id"]
    assert set(DOMAINS) == {SCOPE, "knowledge.records", "knowledge.collections", "creative.catalog", "creative.commissions", "creative.direction", "identity.goals", "identity.profile", "identity.stories", "experience.stories", "memory.records", "communications.contacts", "music.library", "media.assets", "media.video_projects", "music.video", "wellbeing.health", "wellbeing.routines", "wellbeing.genome", "wellbeing.practice", "wellbeing.life_calendar"}
    encoded = json.dumps(value)
    assert "private_key" not in encoded
    assert "identity.key" not in encoded
    invalid = pytest.raises(ReplicationError, match="Unsupported")
    with invalid:
        service.export_batch(remote_id["peer_id"], "identity.secrets")


@pytest.mark.parametrize("scope", ["knowledge.records", "creative.catalog"])
def test_sqlite_domain_two_home_create_edit_delete_restart_conflict_restore_and_disabled(scope, home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, scope)
    identity = create_domain_record(a_home, scope)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    first = a.export_batch(bid["peer_id"], scope)
    assert [row["entry_id"] for row in first["entries"]] == list(DOMAINS[scope].entries)
    assert b.apply_batch(aid["peer_id"], first)["entries"][0]["added"] == 1
    a._record_sent(bid["peer_id"], first)
    assert get_domain_record(b_home, scope, identity)["title"] == "base"

    update_domain_record(a_home, scope, identity, "edited", "A body")
    second = ReplicationService(a_home).export_batch(bid["peer_id"], scope)
    assert ReplicationService(b_home).apply_batch(aid["peer_id"], second)["sequence"] == 2
    assert get_domain_record(b_home, scope, identity)["title"] == "edited"
    ReplicationService(a_home)._record_sent(bid["peer_id"], second)

    update_domain_record(a_home, scope, identity, "A title", "A body kept")
    update_domain_record(b_home, scope, identity, "B title", "B body")
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], scope)
    conflict_result = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert conflict_result["entries"][0]["conflicts"] == 1
    conflict = ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW)[0]
    restored = ReplicationService(a_home).restore_fields(conflict.id, ["title"])
    assert restored["fields"] == ["title"]
    merged = get_domain_record(a_home, scope, identity)
    assert merged["title"] == "B title"
    assert (merged["content"] if scope == "knowledge.records" else merged["body"]) == "A body kept"

    c_home, d_home = home / "c", home / "d"
    cid, did = pair_for(c_home, d_home, scope)
    identity2 = create_domain_record(c_home, scope, title="delete me")
    fresh = ReplicationService(c_home).export_batch(did["peer_id"], scope)
    ReplicationService(d_home).apply_batch(cid["peer_id"], fresh)
    ReplicationService(c_home)._record_sent(did["peer_id"], fresh)
    delete_domain_record(c_home, scope, identity2)
    removal = ReplicationService(c_home).export_batch(did["peer_id"], scope)
    applied = ReplicationService(d_home).apply_batch(cid["peer_id"], removal)
    assert applied["entries"][0]["removed"] >= 1
    assert get_domain_record(d_home, scope, identity2) is None
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], removal) == applied

    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"):
        ReplicationService(a_home).export_batch(bid["peer_id"], scope)


def test_creative_catalog_covers_every_declared_authoritative_current_record_table(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, "creative.catalog")
    service = ReplicationService(a_home)
    initial = service.export_batch(bid["peer_id"], "creative.catalog")
    assert initial["sequence"] == 1
    assert [item["entry_id"] for item in initial["entries"]] == list(CREATIVE_TABLES)
    ReplicationService(b_home).apply_batch(aid["peer_id"], initial)
    path = a_home / "capabilities/creative/catalog.sqlite3"
    expected = {}
    with sqlite3.connect(path) as db:
        for number, (entry_id, table) in enumerate(CREATIVE_TABLES.items(), 1):
            identity = f"canonical-{number}"
            record = {"id": identity, "title": entry_id, "revision": 1,
                      "created_at": "2026-09-25T10:00:00+00:00",
                      "updated_at": "2026-09-25T10:00:00+00:00"}
            db.execute(f"INSERT INTO {table}(id,record) VALUES(?,?)", (identity, json.dumps(record)))
            expected[entry_id] = record
    batch = service.export_batch(bid["peer_id"], "creative.catalog")
    assert batch["sequence"] == 2
    receipt = ReplicationService(b_home).apply_batch(aid["peer_id"], batch)
    assert receipt["accepted"] is True
    assert sum(item["added"] for item in receipt["entries"]) == len(CREATIVE_TABLES)
    remote_path = b_home / "capabilities/creative/catalog.sqlite3"
    with sqlite3.connect(remote_path) as db:
        for entry_id, table in CREATIVE_TABLES.items():
            row = db.execute(f"SELECT record FROM {table}").fetchone()
            assert json.loads(row[0]) == expected[entry_id]
    service._record_sent(bid["peer_id"], batch)
    with sqlite3.connect(path) as db:
        for table in CREATIVE_TABLES.values():
            db.execute(f"DELETE FROM {table}")
    deletion = ReplicationService(a_home).export_batch(bid["peer_id"], "creative.catalog")
    removed = ReplicationService(b_home).apply_batch(aid["peer_id"], deletion)
    assert sum(item["removed"] for item in removed["entries"]) == len(CREATIVE_TABLES)
    with sqlite3.connect(remote_path) as db:
        assert all(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in CREATIVE_TABLES.values())


@pytest.mark.asyncio
async def test_field_restore_http_requires_dashboard_auth_and_refuses_unsafe_fields(home, tmp_path):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, "creative.catalog")
    identity = create_domain_record(a_home, "creative.catalog")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "creative.catalog")
    b.apply_batch(aid["peer_id"], baseline)
    a._record_sent(bid["peer_id"], baseline)
    update_domain_record(a_home, "creative.catalog", identity, "local", "keep")
    update_domain_record(b_home, "creative.catalog", identity, "remote", "replace")
    a.apply_batch(bid["peer_id"], b.export_batch(aid["peer_id"], "creative.catalog"))
    conflict = ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW)[0]
    monkey_home = os.environ["GIDEON_HOME"]
    os.environ["GIDEON_HOME"] = str(a_home)
    try:
        app = web.Application(middlewares=[token_auth_middleware(port=8001)]); register(app)
        async with TestClient(TestServer(app)) as client:
            route = f"{PREFIX}/conflicts/{conflict.id}/restore-fields"
            assert (await client.post(route, json={"fields": ["title"]})).status == 403
            headers = {"Cookie": "gideon_token_8001=" + generate_token("dashboard:restore")}
            unsafe = await client.post(route, headers=headers, json={"fields": ["id"]})
            assert unsafe.status == 409
            accepted = await client.post(route, headers=headers, json={"fields": ["title"]})
            assert accepted.status == 200
            assert (await accepted.json())["fields"] == ["title"]
    finally:
        os.environ["GIDEON_HOME"] = monkey_home
    merged = get_domain_record(a_home, "creative.catalog", identity)
    assert merged["title"] == "remote"
    assert merged["body"] == "keep"


def test_identity_goals_two_home_full_rows_conflict_restore_delete_restart_and_policy(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, "identity.goals")
    path_a = a_home / "capabilities/identity/goals.sqlite3"
    goals_a = GoalStore(path_a)
    goal = goals_a.save_goal(title="Shared goal", description="base", request_id="goal-create")
    session = goals_a.save_session(goal_id=goal["id"], title="Focus", start_at="2026-10-01T10:00:00+00:00", end_at="2026-10-01T11:00:00+00:00", request_id="session-create")
    plans_a = GoalPlanStore(path_a)
    plans_a.configure(goal_id=goal["id"], parent_id=None, horizon="long_term", milestones=[], links=[{"kind": "session", "id": session["id"]}], unit="pages", target_value=100, expected_revision=0, request_id="plan-create")
    checkin = plans_a.checkin(goal_id=goal["id"], value=10, observed_at="2026-10-01T12:00:00+00:00", notes="Started", request_id="checkin-create")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "identity.goals")
    assert [len(item["rows"]) for item in baseline["entries"]] == [1, 1, 1, 1]
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    remote = GoalPlanStore(b_home / "capabilities/identity/goals.sqlite3").get(goal["id"])
    assert remote["goal"]["title"] == "Shared goal"
    assert remote["checkins"][0]["id"] == checkin["id"]

    goals_b = GoalStore(b_home / "capabilities/identity/goals.sqlite3")
    goals_a.save_goal(id=goal["id"], title="Local title", description="keep local", status="active", target_date=None, expected_revision=1, request_id="local-edit")
    goals_b.save_goal(id=goal["id"], title="Peer title", description="peer body", status="active", target_date=None, expected_revision=1, request_id="peer-edit")
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "identity.goals")
    result = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert result["entries"][0]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "identity.goals")
    ReplicationService(a_home).restore_fields(conflict.id, ["title"])
    merged = GoalStore(path_a).get_goal(goal["id"])
    assert merged["title"] == "Peer title"
    assert merged["description"] == "keep local"

    c_home, d_home = home / "c", home / "d"
    cid, did = pair_for(c_home, d_home, "identity.goals")
    c_path = c_home / "capabilities/identity/goals.sqlite3"
    doomed = GoalStore(c_path).save_goal(title="Delete me", request_id="delete-create")
    first = ReplicationService(c_home).export_batch(did["peer_id"], "identity.goals")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    with sqlite3.connect(c_path) as db: db.execute("DELETE FROM goals WHERE id=?", (doomed["id"],))
    removal = ReplicationService(c_home).export_batch(did["peer_id"], "identity.goals")
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], removal)["entries"][0]["removed"] == 1
    with pytest.raises(KeyError): GoalStore(d_home / "capabilities/identity/goals.sqlite3").get_goal(doomed["id"])
    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "identity.goals")


def test_identity_profile_explicit_scope_excludes_private_and_syncs_public_conflicts_and_delete(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, "identity.profile")
    twin_a = TwinStore(a_home / "capabilities/identity/twin.sqlite3")
    public = twin_a.save_document(title="Public identity", text="shareable", expected_revision=0)
    public_id = public["documents"][0]["id"]
    private = twin_a.save_document(title="Private identity", text="never-export-this", private=True, expected_revision=1)
    twin_a.configure(expected_revision=2, enabled=True, traits={"openness": 0.8}, personas=[], active_persona_id=None)
    ProgressStore(a_home / "capabilities/identity/progress.sqlite3").configure(birth_date="1990-01-01", timezone="UTC", tracked_task_ids=[], expected_revision=0, request_id="profile-create")
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "identity.profile")
    wire = json.dumps(baseline)
    assert "never-export-this" not in wire
    assert [item["entry_id"] for item in baseline["entries"]] == ["identity.progress_profile", "identity.twin_profile", "identity.twin_documents"]
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    twin_b = TwinStore(b_home / "capabilities/identity/twin.sqlite3")
    assert [item["title"] for item in twin_b.snapshot()["documents"]] == ["Public identity"]
    assert ProgressStore(b_home / "capabilities/identity/progress.sqlite3").profile()["birth_date"] == "1990-01-01"

    twin_a.save_document(id=public_id, title="Local identity", text="local kept", expected_revision=3)
    twin_b.save_document(id=public_id, title="Peer identity", text="peer text", expected_revision=twin_b.snapshot()["revision"])
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "identity.profile")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][2]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "identity.twin_documents")
    ReplicationService(a_home).restore_fields(conflict.id, ["title"])
    state = TwinStore(a_home / "capabilities/identity/twin.sqlite3").snapshot()
    restored = next(item for item in state["documents"] if item["id"] == public_id)
    assert restored["title"] == "Peer identity" and restored["text"] == "local kept"
    assert any(item["private"] and item["text"] == "never-export-this" for item in state["documents"])

    c_home, d_home = home / "c", home / "d"
    cid, did = pair_for(c_home, d_home, "identity.profile")
    twin_c = TwinStore(c_home / "capabilities/identity/twin.sqlite3")
    created = twin_c.save_document(title="Temporary", text="delete", expected_revision=0)
    doomed = created["documents"][0]["id"]
    first = ReplicationService(c_home).export_batch(did["peer_id"], "identity.profile")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    twin_c.delete_document(doomed, expected_revision=1)
    deletion = ReplicationService(c_home).export_batch(did["peer_id"], "identity.profile")
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], deletion)["entries"][2]["removed"] == 1
    assert TwinStore(d_home / "capabilities/identity/twin.sqlite3").snapshot()["documents"] == []
    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "identity.profile")


def test_contacts_two_home_refs_conflict_restore_tombstones_credentials_and_policy(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, "communications.contacts")
    people_a = PeopleStore(a_home / "capabilities/communications")
    person = people_a.save({"name": "Ada", "identities": [{"kind": "email", "value": "ada@example.test"}], "ring": "core", "cadence_days": 14, "notes": "local base"})
    touchpoint, created = people_a.record(person["id"], {"source": "mail", "external_id": "message-1", "occurred_at": "2026-09-25T10:00:00+00:00", "direction": "mutual", "summary": "Project review"})
    assert created is True
    social.save(people_a, {"platform": "github", "handle": "ada", "label": "Ada", "profile_url": "https://github.com/ada", "credential_ref": "CONTACT_SOCIAL_TOKEN", "person_id": person["id"], "status": "active", "notes": "local only", "request_key": "social-create"})
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "communications.contacts")
    wire = json.dumps(baseline)
    assert "CONTACT_SOCIAL_TOKEN" not in wire and "credential_ref" not in wire
    assert [item["entry_id"] for item in baseline["entries"]] == ["communications.people", "communications.touchpoints"]
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    people_b = PeopleStore(b_home / "capabilities/communications")
    assert people_b.get(person["id"])["identities"] == [{"kind": "email", "value": "ada@example.test"}]
    assert people_b.touchpoints(person["id"])[0]["id"] == touchpoint["id"]
    assert people_b.touchpoints(person["id"])[0]["person_id"] == person["id"]
    with people_b.connect() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='social_accounts'").fetchone()[0] == 0

    people_a.save({"name": "Ada Local", "identities": person["identities"], "ring": "core", "cadence_days": 14, "notes": "preserve this", "revision": 1}, person["id"])
    people_b.save({"name": "Ada Peer", "identities": person["identities"], "ring": "core", "cadence_days": 30, "notes": "peer notes", "revision": 1}, person["id"])
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "communications.contacts")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][0]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "communications.people")
    ReplicationService(a_home).restore_fields(conflict.id, ["name"])
    merged = PeopleStore(a_home / "capabilities/communications").get(person["id"])
    assert merged["name"] == "Ada Peer" and merged["notes"] == "preserve this"

    c_home, d_home = home / "c", home / "d"
    cid, did = pair_for(c_home, d_home, "communications.contacts")
    people_c = PeopleStore(c_home / "capabilities/communications")
    doomed = people_c.save({"name": "Temporary", "identities": [{"kind": "handle", "value": "temporary"}]})
    people_c.record(doomed["id"], {"source": "chat", "external_id": "temporary-1", "occurred_at": "2026-09-25T11:00:00+00:00", "direction": "inbound", "summary": "Temporary"})
    first = ReplicationService(c_home).export_batch(did["peer_id"], "communications.contacts")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    with people_c.connect() as db:
        db.execute("DELETE FROM touchpoints WHERE person_id=?", (doomed["id"],))
        db.execute("DELETE FROM people WHERE id=?", (doomed["id"],))
    removal = ReplicationService(c_home).export_batch(did["peer_id"], "communications.contacts")
    receipt = ReplicationService(d_home).apply_batch(cid["peer_id"], removal)
    assert receipt["entries"][0]["removed"] == 1
    with pytest.raises(PeopleError): PeopleStore(d_home / "capabilities/communications").get(doomed["id"])

    bad_home, target_home = home / "bad", home / "target"
    bad_id, target_id = pair_for(bad_home, target_home, "communications.contacts")
    bad_people = PeopleStore(bad_home / "capabilities/communications")
    bad_person = bad_people.save({"name": "Broken ref", "identities": []})
    bad_people.record(bad_person["id"], {"source": "mail", "external_id": "broken", "occurred_at": "2026-09-25T12:00:00+00:00", "direction": "inbound", "summary": "Broken"})
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "communications.contacts")
    malformed["entries"][1]["rows"][0]["data"]["person_id"] = "missing-person"
    with pytest.raises(ReplicationError, match="missing canonical person") as rejected:
        ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422
    assert PeopleStore(target_home / "capabilities/communications").people() == []

    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "communications.contacts")


def seed_music(home):
    artifacts = NativeArtifactProvider(home / "artifacts")
    root = home / "capabilities/music"
    catalog = MusicCatalog(root, artifacts)
    artist = catalog.create("artists", {"name": "Signal Artist", "bio": "base"})
    track = catalog.create("tracks", {"title": "Signal Song", "artist_id": artist["id"], "notes": "metadata only"})
    album = catalog.create("albums", {"title": "Signal Album", "artist_id": artist["id"], "track_ids": [track["id"]]})
    score = artifacts.create(name="Pinned score", content="C G Am F", kind="markdown")
    song = RepertoireStore(root, artifacts).create({"title": "Practice Song", "instrument": "guitar", "body": "C G Am F", "attachment_refs": [{"slug": score.slug, "version": score.version}]})
    source = artifacts.create(name="Playlist source", content=json.dumps({"playlists": [{"name": "Focus", "lastModifiedDate": "2026-09-25", "items": [{"track": {"trackName": "Signal Song", "artistName": "Signal Artist", "albumName": "Signal Album", "trackUri": "spotify:track:signal"}}]}]}), kind="json")
    listening = ListeningStore(home, artifacts)
    listening.import_data({"request_id": "playlist-import", "account_label": "library", "format": "spotify_playlists", "artifact_ref": {"slug": source.slug, "version": source.version}})
    deck = DeckStore(home, artifacts).create({"name": "Reference Deck", "kind": "playing"})
    bridge = SpotifyBridge(home, listening, credential_resolver=lambda _: "never-export-token")
    bridge.configure({"enabled": True, "credential_name": "SPOTIFY_PRIVATE", "account_label": "private account", "revision": 0})
    return {"artist": artist, "track": track, "album": album, "song": song, "playlist": listening.playlists()[0], "deck": deck}


def test_music_library_two_home_refs_conflict_restore_tombstones_credentials_and_policy(home):
    a_home, b_home = home / "a", home / "b"
    aid, bid = pair_for(a_home, b_home, "music.library")
    records = seed_music(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "music.library")
    assert [item["entry_id"] for item in baseline["entries"]] == ["music.artists", "music.tracks", "music.albums", "music.songs", "music.playlists", "music.decks"]
    assert [len(item["rows"]) for item in baseline["entries"]] == [1, 1, 1, 1, 1, 1]
    assert baseline["entries"][-1]["rows"][0]["data"]["exports"] == []
    wire = json.dumps(baseline)
    assert "SPOTIFY_PRIVATE" not in wire and "never-export-token" not in wire and "credential_name" not in wire
    assert "private account" not in wire and "playlist-import" not in wire
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    artifacts_b = NativeArtifactProvider(b_home / "artifacts")
    catalog_b = MusicCatalog(b_home / "capabilities/music", artifacts_b)
    assert catalog_b.get("albums", records["album"]["id"])["track_ids"] == [records["track"]["id"]]
    assert catalog_b.get("tracks", records["track"]["id"])["artist_id"] == records["artist"]["id"]
    assert RepertoireStore(b_home / "capabilities/music", artifacts_b).get(records["song"]["id"])["attachment_refs"][0]["version"] == 1
    assert ListeningStore(b_home, artifacts_b).playlists()[0]["playlist_id"] == records["playlist"]["playlist_id"]
    assert DeckStore(b_home, artifacts_b).get(records["deck"]["id"])["name"] == "Reference Deck"
    assert artifacts_b.get(records["song"]["attachment_refs"][0]["slug"], version=1) is None

    catalog_a = MusicCatalog(a_home / "capabilities/music", NativeArtifactProvider(a_home / "artifacts"))
    catalog_a.update("artists", records["artist"]["id"], {"revision": 1, "name": "Local Artist", "bio": "preserve local"})
    catalog_b.update("artists", records["artist"]["id"], {"revision": 1, "name": "Peer Artist", "bio": "peer bio"})
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "music.library")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][0]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "music.artists")
    ReplicationService(a_home).restore_fields(conflict.id, ["name"])
    merged = catalog_a.get("artists", records["artist"]["id"])
    assert merged["name"] == "Peer Artist" and merged["bio"] == "preserve local" and merged["revision"] == 3

    c_home, d_home = home / "c", home / "d"
    cid, did = pair_for(c_home, d_home, "music.library")
    doomed = seed_music(c_home)
    first = ReplicationService(c_home).export_batch(did["peer_id"], "music.library")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    root = c_home / "capabilities/music"
    with sqlite3.connect(root / "music_catalog.sqlite3") as db: db.execute("DELETE FROM catalog")
    with sqlite3.connect(root / "repertoire.sqlite3") as db: db.execute("DELETE FROM items")
    with sqlite3.connect(root / "listening.sqlite3") as db: db.execute("DELETE FROM snapshots")
    with sqlite3.connect(root / "decks.sqlite3") as db: db.execute("DELETE FROM decks")
    deletion = ReplicationService(c_home).export_batch(did["peer_id"], "music.library")
    removed = ReplicationService(d_home).apply_batch(cid["peer_id"], deletion)
    assert sum(item["removed"] for item in removed["entries"]) == 6
    assert all(not item["rows"] for item in ReplicationService(d_home).export_batch(cid["peer_id"], "music.library")["entries"])

    bad_home, target_home = home / "bad", home / "target"
    bad_id, target_id = pair_for(bad_home, target_home, "music.library")
    seed_music(bad_home)
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "music.library")
    malformed["entries"][2]["rows"][0]["data"]["track_ids"] = ["missing-track"]
    with pytest.raises(ReplicationError, match="missing or repeated tracks") as rejected:
        ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422
    assert MusicCatalog(target_home / "capabilities/music", NativeArtifactProvider(target_home / "artifacts")).list("artists") == []
    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "music.library")


def seed_media(home):
    artifacts = NativeArtifactProvider(home / "artifacts")
    image = artifacts.create_binary(name="Reference image", slug="reference-image", data=b"private-image-bytes", mime="image/png", kind="image", source="import", tags=["reference"], event_metadata={"original_filename": "reference.png", "sha256": "a" * 64})
    video = artifacts.create_binary(name="Reference video", slug="reference-video", data=b"private-video-bytes", mime="video/mp4", kind="video", source="chat", tags=["motion"], event_metadata={"source_artifact_id": image.slug, "source_version": image.version})
    artifacts.update(image.slug, collection="Campaign", expect_updated_at=image.updated_at)
    artifacts.create(name="Private request note", content="credential_ref=MEDIA_PRIVATE", kind="markdown")
    return MediaLibrary(artifacts), image.slug, video.slug


def test_media_assets_current_metadata_conflict_tombstone_bytes_and_policy(home):
    none_a, none_b = home / "media-none-a", home / "media-none-b"
    _, none_peer = pair(none_a, none_b)
    seed_media(none_a)
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(none_a).export_batch(none_peer["peer_id"], "media.assets")

    a_home, b_home = home / "media-a", home / "media-b"
    aid, bid = pair_for(a_home, b_home, "media.assets")
    library_a, image_id, video_id = seed_media(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "media.assets")
    assert [item["entry_id"] for item in baseline["entries"]] == ["media.library_metadata"]
    assert [row["id"] for row in baseline["entries"][0]["rows"]] == [image_id, video_id]
    wire = json.dumps(baseline)
    assert "private-image-bytes" not in wire and "private-video-bytes" not in wire and "MEDIA_PRIVATE" not in wire
    assert "raw_url" not in wire and "source_path" not in wire and '"events"' not in wire
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    artifacts_b = NativeArtifactProvider(b_home / "artifacts")
    library_b = MediaLibrary(artifacts_b)
    assert library_b.get(image_id)["collection"] == "Campaign"
    assert library_b.get(video_id)["provenance"] == {"source_artifact_id": image_id, "source_version": 1}
    assert artifacts_b.raw_bytes(image_id) is None and artifacts_b.raw_bytes(video_id) is None

    local = library_a.get(image_id)
    peer = library_b.get(image_id)
    library_a.update(image_id, {"expected_updated_at": local["updated_at"], "name": "Local image", "tags": ["keep-local"]})
    library_b.update(image_id, {"expected_updated_at": peer["updated_at"], "name": "Peer image", "tags": ["peer-tag"]})
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "media.assets")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][0]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "media.library_metadata")
    ReplicationService(a_home).restore_fields(conflict.id, ["name"])
    merged = library_a.get(image_id)
    assert merged["name"] == "Peer image" and merged["tags"] == ["keep-local"]
    assert NativeArtifactProvider(a_home / "artifacts").raw_bytes(image_id)[0] == b"private-image-bytes"

    c_home, d_home = home / "media-c", home / "media-d"
    cid, did = pair_for(c_home, d_home, "media.assets")
    _, doomed, _ = seed_media(c_home)
    first = ReplicationService(c_home).export_batch(did["peer_id"], "media.assets")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    assert NativeArtifactProvider(c_home / "artifacts").delete(doomed)
    deletion = ReplicationService(c_home).export_batch(did["peer_id"], "media.assets")
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], deletion)["entries"][0]["removed"] == 1
    assert NativeArtifactProvider(d_home / "artifacts").get(doomed) is None

    bad_home, target_home = home / "media-bad", home / "media-target"
    bad_id, target_id = pair_for(bad_home, target_home, "media.assets")
    seed_media(bad_home)
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "media.assets")
    malformed["entries"][0]["rows"][0]["data"]["raw_url"] = "/private/raw"
    with pytest.raises(ReplicationError, match="exact non-binary") as rejected:
        ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422 and NativeArtifactProvider(target_home / "artifacts").list() == []
    configured = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, configured["endpoint"], revision=configured["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"):
        ReplicationService(a_home).export_batch(bid["peer_id"], "media.assets")


def seed_wellbeing(home):
    measurement = MeasurementStore(home).create({"request_id": "measure", "kind": "body_weight", "observed_at": "2026-09-25T08:00:00+02:00", "unit": "kg", "values": {"weight": 80}, "source": "manual", "notes": "baseline"})
    labs = LabStore(home)
    lab_input = {"filename": "labs.json", "format": "json", "content": json.dumps([{"analyte": "Glucose", "observed_at": "2026-09-25T08:30:00+02:00", "value": 92, "unit": "mg/dL", "reference_low": 70, "reference_high": 100, "notes": "fasting", "external_id": "lab-1"}]), "source": "clinic export"}
    lab = labs.commit({**lab_input, "preview_id": labs.preview(lab_input)["preview_id"], "request_id": "lab-import"})["records"][0]
    raw = b'<HealthData><Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" unit="count" value="125" startDate="2026-09-25 09:00:00 +0200" endDate="2026-09-25 09:05:00 +0200"/></HealthData>'
    apple = AppleHealthStore(home)
    apple_input = {"filename": "export.xml", "format": "xml", "content_base64": base64.b64encode(raw).decode(), "source": "personal export"}
    apple.commit({**apple_input, "preview_id": apple.preview(apple_input)["preview_id"], "request_id": "apple-import"})
    metric = apple.list_metrics()[0]
    habits = ConsumptionStore(home)
    preset = habits.create_preset({"request_id": "preset", "kind": "alcohol", "name": "Recorded serving", "details": {"volume_ml": 330, "abv_percent": 5}})
    entry = habits.create_entry({"request_id": "entry", "preset_id": preset["id"], "count": 1, "observed_at": "2026-09-25T18:00:00+02:00", "source": "manual", "notes": "recorded"})
    interventions = InterventionStore(home)
    plan = interventions.create_plan({"request_id": "plan", "name": "Evening walk", "kind": "activity", "instructions": "Walk outside", "source": "personal plan", "timezone": "Europe/Berlin", "start_date": "2026-09-20", "end_date": None, "weekdays": [0, 1, 2, 3, 4, 5, 6]})
    observation = interventions.record(plan["id"], {"request_id": "observation", "date": "2026-09-24", "status": "completed", "observed_at": "2026-09-24T20:00:00+02:00", "notes": "done"})
    with sqlite3.connect(home / "capabilities/wellbeing.sqlite3") as db:
        db.execute("INSERT INTO requests VALUES(?,?,?)", ("private-ledger", '{"access_token":"health-private-token","client_secret":"never-share","native_helper":"local-only"}', "{}"))
    return {"measurement": measurement, "lab": lab, "metric": metric, "preset": preset, "entry": entry, "plan": plan, "observation": observation}


def test_wellbeing_opt_in_scopes_current_refs_conflicts_tombstones_and_private_exclusions(home):
    none_a, none_b = home / "none-a", home / "none-b"
    _, none_peer = pair(none_a, none_b)
    seed_wellbeing(none_a)
    with pytest.raises(ReplicationError, match="policy denies"): ReplicationService(none_a).export_batch(none_peer["peer_id"], "wellbeing.health")

    a_home, b_home = home / "health-a", home / "health-b"
    aid, bid = pair_for(a_home, b_home, "wellbeing.health")
    records = seed_wellbeing(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    batch = a.export_batch(bid["peer_id"], "wellbeing.health")
    assert [item["entry_id"] for item in batch["entries"]] == ["wellbeing.measurements", "wellbeing.labs", "wellbeing.metrics"]
    wire = json.dumps(batch)
    assert "health-private-token" not in wire and "never-share" not in wire and "native_helper" not in wire and "requests" not in wire
    assert b.apply_batch(aid["peer_id"], batch)["accepted"] is True
    a._record_sent(bid["peer_id"], batch)
    assert MeasurementStore(b_home).get(records["measurement"]["id"])["values"] == {"weight": 80.0}
    assert LabStore(b_home).get(records["lab"]["id"])["analyte"] == "Glucose"
    assert AppleHealthStore(b_home).list_metrics()[0]["metric"] == "HKQuantityTypeIdentifierStepCount"
    assert LabStore(b_home).artifacts.get(records["lab"]["artifact"]["slug"], version=1) is None

    MeasurementStore(a_home).correct(records["measurement"]["id"], {"request_id": "local-health", "revision": 1, "values": {"weight": 79}, "notes": "keep local value"})
    MeasurementStore(b_home).correct(records["measurement"]["id"], {"request_id": "peer-health", "revision": 1, "notes": "peer note"})
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "wellbeing.health")
    result = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert result["entries"][0]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "wellbeing.measurements")
    ReplicationService(a_home).restore_fields(conflict.id, ["notes"])
    merged = MeasurementStore(a_home).get(records["measurement"]["id"])
    assert merged["notes"] == "peer note" and merged["values"] == {"weight": 79.0} and merged["revision"] == 3
    with sqlite3.connect(a_home / "capabilities/wellbeing.sqlite3") as db: db.execute("DELETE FROM lab_revisions WHERE id=?", (records["lab"]["id"],))
    deletion = ReplicationService(a_home).export_batch(bid["peer_id"], "wellbeing.health")
    assert ReplicationService(b_home).apply_batch(aid["peer_id"], deletion)["entries"][1]["removed"] == 1

    c_home, d_home = home / "routine-c", home / "routine-d"
    cid, did = pair_for(c_home, d_home, "wellbeing.routines")
    routines = seed_wellbeing(c_home)
    initial = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.routines")
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], initial)["accepted"] is True
    ReplicationService(c_home)._record_sent(did["peer_id"], initial)
    assert ConsumptionStore(d_home).get_entry(routines["entry"]["id"])["preset_id"] == routines["preset"]["id"]
    assert InterventionStore(d_home).get_record(routines["observation"]["id"])["plan_id"] == routines["plan"]["id"]
    ConsumptionStore(c_home).delete_entry(routines["entry"]["id"], {"request_id": "delete-entry", "revision": 1})
    tombstone = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.routines")
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], tombstone)["entries"][0]["removed"] == 1

    bad_home, target_home = home / "routine-bad", home / "routine-target"
    bad_id, target_id = pair_for(bad_home, target_home, "wellbeing.routines")
    seed_wellbeing(bad_home)
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "wellbeing.routines")
    malformed["entries"][3]["rows"][0]["data"]["plan_id"] = "missing-plan"
    with pytest.raises(ReplicationError, match="missing plan") as rejected: ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422 and InterventionStore(target_home).list_plans(include_archived=True) == []
    peer = PeerStore(c_home).get(did["peer_id"])
    PeerStore(c_home).put(did["peer_id"], peer_record(did, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.routines")


def seed_genome(home):
    store = GenomeStore(home)
    content = "rsid\tchromosome\tposition\tgenotype\nrs1\t1\t123\tAG\nrs2\tX\t456\t--\n"
    payload = {"filename": "sample.tsv", "format": "tsv", "content": content, "source": "personal export", "assembly": "GRCh37"}
    receipt = store.commit({**payload, "preview_id": store.preview(payload)["preview_id"], "request_id": "genome-import"})
    with sqlite3.connect(home / "capabilities/wellbeing.sqlite3") as db:
        db.execute("INSERT INTO requests VALUES(?,?,?)", ("genome-private", '{"private_key":"genome-private-key","access_grant":"local-only"}', "{}"))
    return receipt["source"], store.list_variants(receipt["source"]["id"])


def test_genome_scope_two_home_refs_conflict_restore_tombstones_and_privacy(home):
    none_a, none_b = home / "genome-none-a", home / "genome-none-b"
    _, none_peer = pair(none_a, none_b)
    seed_genome(none_a)
    with pytest.raises(ReplicationError, match="policy denies"): ReplicationService(none_a).export_batch(none_peer["peer_id"], "wellbeing.genome")

    a_home, b_home = home / "genome-a", home / "genome-b"
    aid, bid = pair_for(a_home, b_home, "wellbeing.genome")
    source, variants = seed_genome(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "wellbeing.genome")
    assert [item["entry_id"] for item in baseline["entries"]] == ["wellbeing.genome_sources", "wellbeing.genome_variants"]
    assert [len(item["rows"]) for item in baseline["entries"]] == [1, 2]
    wire = json.dumps(baseline)
    assert "genome-private-key" not in wire and "access_grant" not in wire and "requests" not in wire
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    genome_b = GenomeStore(b_home)
    assert genome_b.get_source(source["id"])["variant_count"] == 2
    assert [row["rsid"] for row in genome_b.list_variants(source["id"])] == ["rs1", "rs2"]
    assert genome_b.artifacts.get(source["artifact"]["slug"], version=1) is None

    GenomeStore(a_home).annotate(variants[0]["id"], {"request_id": "local-note", "revision": 1, "annotation": "local note", "annotation_source": "local review"})
    genome_b.annotate(variants[0]["id"], {"request_id": "peer-note", "revision": 1, "annotation": "peer note", "annotation_source": "peer review"})
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "wellbeing.genome")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][1]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "wellbeing.genome_variants")
    ReplicationService(a_home).restore_fields(conflict.id, ["annotation"])
    merged = GenomeStore(a_home).get_variant(variants[0]["id"])
    assert merged["annotation"] == "peer note" and merged["annotation_source"] == "local review" and merged["revision"] == 3

    c_home, d_home = home / "genome-c", home / "genome-d"
    cid, did = pair_for(c_home, d_home, "wellbeing.genome")
    doomed_source, doomed_variants = seed_genome(c_home)
    first = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.genome")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    with sqlite3.connect(c_home / "capabilities/wellbeing.sqlite3") as db:
        db.execute("DELETE FROM genome_sources WHERE id=?", (doomed_source["id"],))
        db.execute("DELETE FROM genome_variants WHERE source_id=?", (doomed_source["id"],))
    tombstones = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.genome")
    removed = ReplicationService(d_home).apply_batch(cid["peer_id"], tombstones)
    assert [item["removed"] for item in removed["entries"]] == [1, len(doomed_variants)]

    bad_home, target_home = home / "genome-bad", home / "genome-target"
    bad_id, target_id = pair_for(bad_home, target_home, "wellbeing.genome")
    seed_genome(bad_home)
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "wellbeing.genome")
    malformed["entries"][1]["rows"][0]["data"]["source_id"] = "missing-source"
    with pytest.raises(ReplicationError, match="missing source") as rejected: ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422 and GenomeStore(target_home).list_sources() == []
    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "wellbeing.genome")


def seed_practice(home):
    cognition = CognitiveStore(home)
    active = cognition.start({"request_id": "cognitive-start", "kind": "arithmetic", "planned_trials": 1, "time_limit_seconds": 60})
    stimulus = active["current_trial"]["stimulus"]
    answer = str(stimulus["left"] + stimulus["right"] if stimulus["operator"] == "+" else stimulus["left"] - stimulus["right"])
    session = cognition.answer(active["id"], {"request_id": "cognitive-answer", "revision": 1, "answer": answer})
    memory = MemoryPracticeStore(home)
    card = memory.create({"request_id": "memory-create", "front": "Capital of France?", "back": "Paris", "source": "personal study", "tags": ["geography"]})
    with sqlite3.connect(home / "capabilities/wellbeing.sqlite3") as db:
        db.execute("INSERT INTO requests VALUES(?,?,?)", ("practice-private", '{"password":"practice-private-password","native_helper":"local-only"}', "{}"))
    return session, card


def test_practice_scope_terminal_results_memory_conflicts_tombstones_and_privacy(home):
    none_a, none_b = home / "practice-none-a", home / "practice-none-b"
    _, none_peer = pair(none_a, none_b)
    seed_practice(none_a)
    with pytest.raises(ReplicationError, match="policy denies"): ReplicationService(none_a).export_batch(none_peer["peer_id"], "wellbeing.practice")

    a_home, b_home = home / "practice-a", home / "practice-b"
    aid, bid = pair_for(a_home, b_home, "wellbeing.practice")
    session, card = seed_practice(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "wellbeing.practice")
    assert [item["entry_id"] for item in baseline["entries"]] == ["wellbeing.cognitive_sessions", "wellbeing.memory_cards"]
    wire = json.dumps(baseline)
    assert '"expected"' not in wire and "practice-private-password" not in wire and "native_helper" not in wire
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    received_session = CognitiveStore(b_home).get(session["id"])
    assert received_session["status"] == "completed" and received_session["score"]["accuracy"] == 1
    memory_b = MemoryPracticeStore(b_home)
    assert memory_b.artifacts.get(card["artifact"]["slug"], version=1) is None
    with memory_b.connection() as db: received_card = json.loads(db.execute("SELECT data FROM memory_card_revisions WHERE id=?", (card["id"],)).fetchone()[0])
    assert received_card["artifact"] == card["artifact"] and "front" not in received_card
    source_artifact = MemoryPracticeStore(a_home).artifacts.get(card["artifact"]["slug"], version=1)
    memory_b.artifacts.create(name="Capital of France?", content=source_artifact.content, kind="document", source="local", slug=card["artifact"]["slug"], readonly=True)

    practiced = MemoryPracticeStore(a_home).practice(card["id"], {"request_id": "local-practice", "revision": 1, "grade": "good"})
    memory_b.update(card["id"], {"request_id": "peer-archive", "revision": 1, "archived": True})
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "wellbeing.practice")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][1]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "wellbeing.memory_cards")
    ReplicationService(a_home).restore_fields(conflict.id, ["archived"])
    merged = MemoryPracticeStore(a_home).get(card["id"])
    assert merged["archived"] is True and merged["schedule"] == practiced["schedule"] and merged["revision"] == 3

    c_home, d_home = home / "practice-c", home / "practice-d"
    cid, did = pair_for(c_home, d_home, "wellbeing.practice")
    doomed_session, doomed_card = seed_practice(c_home)
    first = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.practice")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    with sqlite3.connect(c_home / "capabilities/wellbeing.sqlite3") as db:
        db.execute("DELETE FROM cognitive_sessions WHERE id=?", (doomed_session["id"],))
        db.execute("DELETE FROM memory_card_revisions WHERE id=?", (doomed_card["id"],))
    tombstones = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.practice")
    assert [item["removed"] for item in ReplicationService(d_home).apply_batch(cid["peer_id"], tombstones)["entries"]] == [1, 1]

    bad_home, target_home = home / "practice-bad", home / "practice-target"
    bad_id, target_id = pair_for(bad_home, target_home, "wellbeing.practice")
    seed_practice(bad_home)
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "wellbeing.practice")
    malformed["entries"][0]["rows"][0]["data"]["status"] = "active"
    with pytest.raises(ReplicationError, match="terminal results") as rejected: ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422 and CognitiveStore(target_home).list_sessions() == []
    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "wellbeing.practice")


def seed_life_calendar(home):
    store = LifeCalendarStore(home)
    config = store.configure({"request_id": "calendar-config", "revision": 0, "birth_date": "1990-01-01", "horizon_years": 90, "sleep_hours": 8, "timezone": "Europe/Berlin", "budgets": [{"name": "creative work", "hours_per_week": 20}], "source": "user declaration", "reminder": {"enabled": False, "time": "20:00"}})
    event = store.create_event({"request_id": "calendar-event", "date": "2026-09-25", "title": "Health review", "notes": "baseline", "kind": "planned", "source": "personal plan"})
    with sqlite3.connect(home / "capabilities/wellbeing.sqlite3") as db:
        db.execute("INSERT INTO requests VALUES(?,?,?)", ("calendar-private", '{"secret":"calendar-private-secret","access_grant":"local-only"}', "{}"))
        db.execute("INSERT INTO life_reminder_claims VALUES(?,?,?)", ("2026-09-25", "Europe/Berlin", "private-inbox-item"))
    return config, event


def test_life_calendar_scope_config_events_conflict_tombstone_and_private_state(home):
    none_a, none_b = home / "calendar-none-a", home / "calendar-none-b"
    _, none_peer = pair(none_a, none_b)
    seed_life_calendar(none_a)
    with pytest.raises(ReplicationError, match="policy denies"): ReplicationService(none_a).export_batch(none_peer["peer_id"], "wellbeing.life_calendar")

    a_home, b_home = home / "calendar-a", home / "calendar-b"
    aid, bid = pair_for(a_home, b_home, "wellbeing.life_calendar")
    config, event = seed_life_calendar(a_home)
    a, b = ReplicationService(a_home), ReplicationService(b_home)
    baseline = a.export_batch(bid["peer_id"], "wellbeing.life_calendar")
    assert [item["entry_id"] for item in baseline["entries"]] == ["wellbeing.life_config", "wellbeing.life_events"]
    wire = json.dumps(baseline)
    assert "trigger_id" not in wire and "calendar-private-secret" not in wire and "private-inbox-item" not in wire and "life_reminder_claims" not in wire
    assert b.apply_batch(aid["peer_id"], baseline)["accepted"] is True
    a._record_sent(bid["peer_id"], baseline)
    calendar_b = LifeCalendarStore(b_home)
    assert calendar_b.get_config()["birth_date"] == config["birth_date"] and "trigger_id" not in calendar_b.get_config()
    assert calendar_b.list_events()[0]["id"] == event["id"]
    assert calendar_b.projection("2026-09-25T12:00:00Z")["events"][0]["title"] == "Health review"

    local = LifeCalendarStore(a_home).update_event(event["id"], {"request_id": "local-event", "revision": 1, "title": "Local health review", "notes": "keep local notes"})
    calendar_b.update_event(event["id"], {"request_id": "peer-event", "revision": 1, "title": "Peer health review", "notes": "peer notes"})
    incoming = ReplicationService(b_home).export_batch(aid["peer_id"], "wellbeing.life_calendar")
    outcome = ReplicationService(a_home).apply_batch(bid["peer_id"], incoming)
    assert outcome["entries"][1]["conflicts"] == 1
    conflict = next(item for item in ConflictQueue(a_home).items(status=STATUS_NEEDS_REVIEW) if item.entry_id == "wellbeing.life_events")
    ReplicationService(a_home).restore_fields(conflict.id, ["title"])
    merged = LifeCalendarStore(a_home).list_events()[0]
    assert merged["title"] == "Peer health review" and merged["notes"] == local["notes"] and merged["revision"] == 3

    c_home, d_home = home / "calendar-c", home / "calendar-d"
    cid, did = pair_for(c_home, d_home, "wellbeing.life_calendar")
    _, doomed = seed_life_calendar(c_home)
    first = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.life_calendar")
    ReplicationService(d_home).apply_batch(cid["peer_id"], first)
    ReplicationService(c_home)._record_sent(did["peer_id"], first)
    LifeCalendarStore(c_home).update_event(doomed["id"], {"request_id": "delete-event", "revision": 1, "deleted": True})
    tombstone = ReplicationService(c_home).export_batch(did["peer_id"], "wellbeing.life_calendar")
    assert ReplicationService(d_home).apply_batch(cid["peer_id"], tombstone)["entries"][1]["removed"] == 1

    bad_home, target_home = home / "calendar-bad", home / "calendar-target"
    bad_id, target_id = pair_for(bad_home, target_home, "wellbeing.life_calendar")
    seed_life_calendar(bad_home)
    malformed = ReplicationService(bad_home).export_batch(target_id["peer_id"], "wellbeing.life_calendar")
    malformed["entries"][0]["rows"][0]["data"]["trigger_id"] = "remote-trigger"
    with pytest.raises(ReplicationError, match="local automation state") as rejected: ReplicationService(target_home).apply_batch(bad_id["peer_id"], malformed)
    assert rejected.value.status == 422 and LifeCalendarStore(target_home).get_config() is None
    peer = PeerStore(a_home).get(bid["peer_id"])
    PeerStore(a_home).put(bid["peer_id"], peer_record(bid, peer["endpoint"], revision=peer["revision"], send=[]))
    with pytest.raises(ReplicationError, match="export"): ReplicationService(a_home).export_batch(bid["peer_id"], "wellbeing.life_calendar")
