import copy
import json
import os
from contextlib import contextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_replication import register
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.operations.durability import conflicts
from gideon.operations.durability.shards import machine_id
from gideon.operations.usage_ledger import TurnUsage, UsageJournal
from gideon.workspace.capabilities.platform import replication_usage as adapter
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import (
    ReplicationError,
    ReplicationService,
)

WHEN = "2026-09-25T12:00:00+00:00"
EVENT = "1" * 64
FILE_SHA = "2" * 64
PREFIX = "/api/capabilities/platform/replication"


@contextmanager
def workspace(home):
    home.mkdir(parents=True, exist_ok=True)
    values = {"GIDEON_HOME": str(home)}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield home, machine_id(home)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def imported(
    instance_id,
    *,
    event_id=EVENT,
    tokens=7,
    source_name="history.jsonl",
    format_name="claude_code_jsonl",
):
    provider = "claude" if format_name == "claude_code_jsonl" else "codex"
    return TurnUsage(
        ts=WHEN,
        session_key=f"external:{provider}:session-hash",
        source="cli_import",
        agent="",
        provider=provider,
        model="unknown",
        input_tokens=tokens,
        output_tokens=3,
        cache_read_tokens=2,
        cache_creation_tokens=1,
        cost_usd=0.0,
        priced=False,
        instance_id=instance_id,
        provider_instance=f"{provider}-cli-import",
        credential_ref=None,
        subscription_source=None,
        attribution="historical_cli_import",
        import_format=format_name,
        import_source_name=source_name,
        import_file_sha256=FILE_SHA,
        import_record_id=event_id,
    )


def append(home, row):
    UsageJournal(home / "usage" / "turns.jsonl").append(row)


def pair(first_home, second_home):
    first_home.mkdir(parents=True, exist_ok=True)
    second_home.mkdir(parents=True, exist_ok=True)
    first, second = PeerStore(first_home), PeerStore(second_home)
    first_id, second_id = first.snapshot()["self"], second.snapshot()["self"]
    first.put(
        second_id["peer_id"],
        {
            "label": "Second",
            "endpoint": "https://second.example",
            "public_key": second_id["public_key"],
            "enabled": True,
            "send_categories": [],
            "receive_categories": [],
            "revision": 0,
        },
    )
    second.put(
        first_id["peer_id"],
        {
            "label": "First",
            "endpoint": "https://first.example",
            "public_key": first_id["public_key"],
            "enabled": True,
            "send_categories": [],
            "receive_categories": [],
            "revision": 0,
        },
    )
    return first_id, second_id


def application():
    app = web.Application(middlewares=[token_auth_middleware(port=8014)])
    register(app)
    return app


@pytest.mark.asyncio
async def test_default_denied_then_signed_central_receive_imports_unpriced_local_attribution(
    tmp_path, monkeypatch
):
    source, target = tmp_path / "source", tmp_path / "target"
    source_id, target_id = pair(source, target)
    with workspace(source) as (_, source_instance):
        append(source, imported(source_instance))
        sender = ReplicationService(source)
        with pytest.raises(ReplicationError, match="policy denies"):
            sender.export_batch(target_id["peer_id"], adapter.SCOPE)
        source_peer = PeerStore(source).get(target_id["peer_id"])
        PeerStore(source).put(
            target_id["peer_id"],
            {
                **{
                    key: source_peer[key]
                    for key in (
                        "label",
                        "endpoint",
                        "public_key",
                        "enabled",
                        "revision",
                    )
                },
                "send_categories": [adapter.SCOPE],
                "receive_categories": [adapter.SCOPE],
            },
        )
        target_peer = PeerStore(target).get(source_id["peer_id"])
        PeerStore(target).put(
            source_id["peer_id"],
            {
                **{
                    key: target_peer[key]
                    for key in (
                        "label",
                        "endpoint",
                        "public_key",
                        "enabled",
                        "revision",
                    )
                },
                "send_categories": [adapter.SCOPE],
                "receive_categories": [adapter.SCOPE],
            },
        )
        batch = sender.export_batch(target_id["peer_id"], adapter.SCOPE)
        envelope = {
            "proof": PeerStore(source).create_proof(
                target_id["peer_id"], adapter.SCOPE
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
                + generate_token("dashboard:usage-replication"),
            },
        )
        assert accepted.status == 200
        response = await accepted.json()
        assert response["entries"] == [
            {
                "entry_id": adapter.ENTRY_ID,
                "added": 1,
                "updated": 0,
                "removed": 0,
                "conflicts": 0,
            }
        ]
    with workspace(target) as (_, target_instance):
        replicated = UsageJournal(target / "usage" / "turns.jsonl").rows()
        assert len(replicated) == 1
        assert replicated[0]["instance_id"] == target_instance
        assert replicated[0]["provider_instance"] == "claude-cli-import"
        assert replicated[0]["credential_ref"] is None


def test_two_workspace_append_only_replication_preserves_event_identity(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    with workspace(source) as (_, source_instance):
        append(
            source, imported(source_instance, source_name="operator-private-path.jsonl")
        )
        append(
            source,
            TurnUsage(
                ts=WHEN,
                session_key="dashboard:private",
                source="chat",
                agent="private-agent",
                provider="private-provider",
                model="private-model",
                input_tokens=999,
                instance_id=source_instance,
            ),
        )
        append(
            source,
            imported(
                source_instance, event_id="3" * 64, format_name="codex_rollout_jsonl"
            ),
        )
        foreign = copy.copy(imported(source_instance, event_id="4" * 64))
        foreign.instance_id = "other-instance"
        append(source, foreign)
        wire = adapter.read_rows(source, adapter.ENTRY_ID)
        assert [row["id"] for row in wire] == [EVENT, "3" * 64]
        encoded = json.dumps(wire)
        for private in (
            "operator-private-path",
            "dashboard:private",
            "private-agent",
            "private-provider",
            "private-model",
            source_instance,
        ):
            assert private not in encoded

    with workspace(target) as (_, target_instance):
        queue = conflicts.ConflictQueue(target)
        result = adapter.apply_rows(
            target, adapter.ENTRY_ID, wire, {}, queue, "2026-09-25T12:01:00+00:00"
        )
        assert (result.added, result.updated, result.removed, result.conflicts) == (
            2,
            0,
            0,
            0,
        )
        stored = UsageJournal(target / "usage" / "turns.jsonl").rows()
        assert {row["import_record_id"] for row in stored} == {EVENT, "3" * 64}
        assert {row["instance_id"] for row in stored} == {target_instance}
        assert {row["provider_instance"] for row in stored} == {
            "claude-cli-import",
            "codex-cli-import",
        }
        assert all(row["credential_ref"] is None for row in stored)
        assert all(row["subscription_source"] is None for row in stored)
        assert {row["session_key"] for row in stored} == {
            "replicated:" + EVENT[:24],
            "replicated:" + ("3" * 24),
        }
        assert {row["provider"] for row in stored} == {"claude", "codex"}
        assert all(row["model"] == "unknown" for row in stored)
        assert adapter.read_rows(target, adapter.ENTRY_ID) == wire

        replay = adapter.apply_rows(
            target,
            adapter.ENTRY_ID,
            wire,
            result.new_ancestors,
            queue,
            "2026-09-25T12:02:00+00:00",
        )
        assert (replay.added, replay.updated, replay.removed, replay.conflicts) == (
            0,
            0,
            0,
            0,
        )
        assert len(UsageJournal(target / "usage" / "turns.jsonl").rows()) == 2

        omitted = adapter.apply_rows(
            target,
            adapter.ENTRY_ID,
            [],
            result.new_ancestors,
            queue,
            "2026-09-25T12:03:00+00:00",
        )
        assert omitted.removed == 0
        assert adapter.read_rows(target, adapter.ENTRY_ID) == wire


def test_identity_collision_is_held_and_never_mutates_immutable_event(tmp_path):
    home = tmp_path / "home"
    with workspace(home) as (_, instance_id):
        append(home, imported(instance_id, tokens=7))
        original = adapter.read_rows(home, adapter.ENTRY_ID)[0]
        changed = copy.deepcopy(original)
        changed["data"]["input_tokens"] = 8
        queue = conflicts.ConflictQueue(home)
        result = adapter.apply_rows(
            home,
            adapter.ENTRY_ID,
            [changed],
            {EVENT: conflicts.row_sha(original)},
            queue,
            "2026-09-25T12:04:00+00:00",
        )
        assert (result.added, result.updated, result.removed, result.conflicts) == (
            0,
            0,
            0,
            1,
        )
        assert adapter.read_rows(home, adapter.ENTRY_ID) == [original]
        pending = queue.items(status=conflicts.STATUS_NEEDS_REVIEW)
        assert len(pending) == 1
        assert pending[0].entity_id == EVENT
        assert pending[0].local_row == original
        assert pending[0].remote_row == changed
        assert len(UsageJournal(home / "usage" / "turns.jsonl").rows()) == 1


def test_projection_selects_only_owned_stable_unpriced_import_events(tmp_path):
    home = tmp_path / "home"
    with workspace(home) as (_, instance_id):
        accepted = imported(instance_id)
        append(home, accepted)
        variants = []
        for field, value in (
            ("source", "chat"),
            ("attribution", "live_turn"),
            ("priced", True),
            ("cost_usd", 0.01),
            ("import_format", "provider_export"),
            ("import_record_id", None),
            ("import_file_sha256", None),
            ("instance_id", "foreign"),
        ):
            row = copy.copy(accepted)
            setattr(row, field, value)
            if field not in {"import_record_id", "instance_id"}:
                row.import_record_id = str(len(variants) + 5) * 64
            variants.append(row)
        for row in variants:
            append(home, row)

        rows = adapter.read_rows(home, adapter.ENTRY_ID)
        assert rows == [
            {
                "id": EVENT,
                "data": {
                    "event_id": EVENT,
                    "occurred_at": WHEN,
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "cache_read_tokens": 2,
                    "cache_creation_tokens": 1,
                    "import_format": "claude_code_jsonl",
                    "source_file_sha256": FILE_SHA,
                },
            }
        ]
        assert len(UsageJournal(home / "usage" / "turns.jsonl").rows()) == 9


def test_preflight_rejects_private_or_mutable_usage_shapes_and_wrong_home(tmp_path):
    canonical = {
        "id": EVENT,
        "data": {
            "event_id": EVENT,
            "occurred_at": WHEN,
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_tokens": 2,
            "cache_creation_tokens": 1,
            "import_format": "claude_code_jsonl",
            "source_file_sha256": FILE_SHA,
        },
    }
    adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [canonical]}])
    invalid = []
    for key, value in (
        ("credential", "secret"),
        ("allocation_account", "other"),
        ("provider", "private"),
        ("session_key", "private"),
        ("prompt", "private prompt"),
        ("priced", True),
        ("cost_usd", 1.0),
    ):
        row = copy.deepcopy(canonical)
        row["data"][key] = value
        invalid.append(row)
    mismatch = copy.deepcopy(canonical)
    mismatch["id"] = "3" * 64
    invalid.append(mismatch)
    negative = copy.deepcopy(canonical)
    negative["data"]["input_tokens"] = -1
    invalid.append(negative)
    unstable = copy.deepcopy(canonical)
    unstable["data"]["event_id"] = "not-stable"
    unstable["id"] = "not-stable"
    invalid.append(unstable)
    for row in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries([{"entry_id": adapter.ENTRY_ID, "rows": [row]}])
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries([])

    active = tmp_path / "active"
    inactive = tmp_path / "inactive"
    inactive.mkdir()
    with workspace(active):
        with pytest.raises(ValueError, match="active workspace"):
            adapter.read_rows(inactive, adapter.ENTRY_ID)
        with pytest.raises(ValueError, match="active workspace"):
            adapter.apply_rows(
                inactive,
                adapter.ENTRY_ID,
                [canonical],
                {},
                conflicts.ConflictQueue(inactive),
                WHEN,
            )
