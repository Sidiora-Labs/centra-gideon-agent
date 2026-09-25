import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.sqlite_compat import sqlite3
from gideon.interfaces.dashboard.handlers.capabilities_replication import register
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.platform import (
    replication_adapters,
    replication_creative_documents,
)
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import (
    ReplicationError,
    ReplicationService,
)

PREFIX = "/api/capabilities/platform/replication"


def peer_record(identity, endpoint, categories):
    return {
        "label": identity["peer_id"][-8:],
        "endpoint": endpoint,
        "public_key": identity["public_key"],
        "enabled": True,
        "send_categories": categories,
        "receive_categories": categories,
        "revision": 0,
    }


def application():
    app = web.Application(middlewares=[token_auth_middleware(port=8014)])
    register(app)
    return app


@pytest.mark.asyncio
async def test_creative_work_documents_signed_two_home_round_trip(
    tmp_path, monkeypatch
):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_store = WorkStore(source)
    work = source_store.create(
        {
            "request_id": "work-central",
            "title": "Canonical manuscript",
            "kind": "work",
            "prompt": "Write the ending.",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    first = source_store.draft(
        work["id"],
        {
            "request_id": "draft-central-one",
            "revision": work["revision"],
            "text": "First immutable manuscript.\n",
            "note": "first",
        },
    )
    second = source_store.draft(
        work["id"],
        {
            "request_id": "draft-central-two",
            "revision": first["work"]["revision"],
            "text": "Second immutable manuscript.\n",
            "note": "approved",
        },
    )
    current = replication_adapters.read_rows(source, "creative.works")
    replicated = replication_adapters.apply_rows(
        target,
        "creative.works",
        current,
        {},
        conflicts.ConflictQueue(target),
        "2026-09-25T12:00:00+00:00",
    )
    assert replicated.added == 1

    source_peers, target_peers = PeerStore(source), PeerStore(target)
    source_id, target_id = (
        source_peers.snapshot()["self"],
        target_peers.snapshot()["self"],
    )
    source_peers.put(
        target_id["peer_id"],
        peer_record(
            target_id, "https://target.example", [replication_creative_documents.SCOPE]
        ),
    )
    target_peers.put(
        source_id["peer_id"],
        peer_record(
            source_id, "https://source.example", [replication_creative_documents.SCOPE]
        ),
    )
    sender = ReplicationService(source)
    batch = sender.export_batch(
        target_id["peer_id"], replication_creative_documents.SCOPE
    )
    assert [item["entry_id"] for item in batch["entries"]] == list(
        replication_creative_documents.ENTRIES
    )
    wire = json.dumps(batch, sort_keys=True)
    for forbidden in (
        "request_id",
        "work_requests",
        "work_draft_requests",
        "credential",
        "private_key",
    ):
        assert forbidden not in wire
    envelope = {
        "proof": source_peers.create_proof(
            target_id["peer_id"], replication_creative_documents.SCOPE
        ),
        "payload": batch,
    }

    monkeypatch.setenv("GIDEON_HOME", str(target))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    use_ephemeral_secret()
    body = json.dumps(envelope)
    async with TestClient(TestServer(application())) as client:
        denied = await client.post(
            PREFIX + "/receive", data=body, headers={"Content-Type": "application/json"}
        )
        assert denied.status == 403
        accepted = await client.post(
            PREFIX + "/receive",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Cookie": "gideon_token_8014="
                + generate_token("dashboard:creative-documents"),
            },
        )
        assert accepted.status == 200
        response = await accepted.json()
        assert response["entries"] == [
            {
                "entry_id": replication_creative_documents.VERSIONS_ENTRY,
                "added": 3,
                "updated": 0,
                "removed": 0,
                "conflicts": 0,
            },
            {
                "entry_id": replication_creative_documents.DRAFTS_ENTRY,
                "added": 2,
                "updated": 0,
                "removed": 0,
                "conflicts": 0,
            },
        ]

    target_store = WorkStore(target)
    assert target_store.revisions(work["id"]) == source_store.revisions(work["id"])
    assert (
        target_store.read_draft(work["id"], first["draft"]["id"])["text"]
        == "First immutable manuscript.\n"
    )
    assert (
        target_store.read_draft(work["id"], second["draft"]["id"])["text"]
        == "Second immutable manuscript.\n"
    )
    with sqlite3.connect(target / "capabilities/creative/catalog.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM work_requests").fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM work_draft_requests"
        ).fetchone() == (0,)


def test_creative_work_documents_policy_is_default_denied(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_store, target_store = PeerStore(source), PeerStore(target)
    target_id = target_store.snapshot()["self"]
    source_store.put(
        target_id["peer_id"], peer_record(target_id, "https://target.example", [])
    )
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(source).export_batch(
            target_id["peer_id"], replication_creative_documents.SCOPE
        )
