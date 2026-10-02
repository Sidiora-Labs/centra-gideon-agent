from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from gideon.hypermid.client import canonical_json_bytes
from gideon.hypermid import (
    HypermidAdapter,
    HypermidClient,
    HypermidLifecycle,
    LocalEnrollment,
    Scope,
)
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MemoryOperation,
    MutationRequest,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
)
from gideon.hypermid.foundation import Id, Trace
from gideon.hypermid.memory_client import MemoryClient

from checks.hypermid.waves.local_journey import daemon_binary
from checks.hypermid.evidence import ObservationWriter


TURN_COUNT = 20_000
TOOL_RESULT_COUNT = 2_000
MEMORY_COUNT = 2_000
SESSION_COUNT = 40
TURNS_PER_SESSION = TURN_COUNT // SESSION_COUNT
EXPANSION_COUNT = 1_000
CAPABILITY_ID = Id("sec08-acceptance-capability")


class _Sec08Lifecycle(HypermidLifecycle):
    def __init__(self, root: Path, resources: Sequence[Id]) -> None:
        record = root / "connection.json"
        scope = Scope(Id("acceptance-owner"), Id("acceptance-project"))
        enrolled_resources = tuple(sorted({Id(resource) for resource in resources}))
        config = DaemonConfig(
            transport=DaemonTransport.UNIX_SOCKET,
            endpoint=str(root / "daemon.sock"),
            auth=LocalAuthConfig(LocalAuthMethod.PEER_AND_HMAC, str(record), True),
            executable=daemon_binary(),
            connection_record=str(record),
            request_timeout_ms=120_000,
        )
        client = HypermidClient(record, scope=scope)
        super().__init__(
            HypermidAdapter(client, mode="shadow"),
            config,
            connection_record=record,
            enrollment=LocalEnrollment(
                scope=scope,
                credential_id=Id("sec08-acceptance-credential"),
                capability_id=CAPABILITY_ID,
                operations=("read", "append", "revise", "delete"),
                resources=enrolled_resources,
                expires_ms=int(time.time() * 1000) + 3_600_000,
            ),
        )


def lifecycle(root: Path, resources: Sequence[Id]) -> _Sec08Lifecycle:
    return _Sec08Lifecycle(root, resources)


async def _acquire_writer(client: HypermidClient, name: str) -> dict[str, Any]:
    lease = await client.request(
        "writer.acquire",
        {"request_id": f"{name}-acquire", "minimum_fence_epoch": 2},
        effect_kind="durable",
    )
    empty_digest = _digest(b"")
    receipt = await client.request(
        "writer.cutover",
        {
            "request_id": f"{name}-cutover",
            "lease": lease,
            "barrier": {
                "quiescent": True,
                "before_digest": empty_digest,
                "after_digest": empty_digest,
                "cursor": lease["cursor"],
            },
        },
        effect_kind="durable",
    )
    return receipt["lease"]


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _structured_digest(*fields: bytes) -> str:
    material = bytearray()
    for field in fields:
        material.extend(len(field).to_bytes(8, "big"))
        material.extend(field)
    return _digest(bytes(material))


def _profile(context_window_tokens: int = 50_000_000) -> dict[str, Any]:
    capabilities: dict[str, Any] = {
        "profile_id": "sec08-long-history",
        "context_window_tokens": context_window_tokens,
        "reserved_output_tokens": 8_192,
        "roles": ["system", "user", "assistant", "tool"],
        "part_kinds": [
            "text",
            "reasoning",
            "tool_call",
            "tool_result",
            "image",
            "file",
            "context_marker",
        ],
        "requires_tool_adjacency": True,
        "supports_reasoning": True,
        "supports_cache_boundaries": True,
        "max_cache_boundaries": 2,
        "max_images": 4_096,
        "image_accounting": "host_reported",
    }
    return {
        **capabilities,
        "profile_digest": _digest(canonical_json_bytes(capabilities)),
    }


def _part(global_index: int) -> tuple[str, dict[str, Any], bytes]:
    if global_index < TOOL_RESULT_COUNT * 2:
        call_number = global_index // 2
        call_id = f"call-{call_number:04d}"
        if global_index % 2 == 0:
            arguments = json.dumps(
                {"turn": global_index, "query": f"fixture-{call_number}"},
                separators=(",", ":"),
                sort_keys=True,
            )
            raw = arguments.encode()
            return (
                "assistant",
                {
                    "part_id": f"part-{global_index:05d}",
                    "kind": "tool_call",
                    "content_digest": _structured_digest(
                        call_id.encode(), b"fixture_lookup", raw
                    ),
                    "call_id": call_id,
                    "tool_name": "fixture_lookup",
                    "arguments_json": arguments,
                },
                raw,
            )
        result = json.dumps(
            {"call": call_number, "value": f"result-{call_number}"},
            separators=(",", ":"),
            sort_keys=True,
        )
        raw = result.encode()
        return (
            "tool",
            {
                "part_id": f"part-{global_index:05d}",
                "kind": "tool_result",
                "content_digest": _structured_digest(call_id.encode(), raw),
                "call_id": call_id,
                "result_json": result,
            },
            raw,
        )
    if global_index < TOOL_RESULT_COUNT * 2 + 256:
        raw = b"\x00hypermid-artifact\xff" + global_index.to_bytes(4, "big")
        media_type = "application/octet-stream"
        uri = f"artifact://fixture/{global_index:05d}"
        return (
            "user",
            {
                "part_id": f"part-{global_index:05d}",
                "kind": "file",
                "content_digest": _structured_digest(
                    media_type.encode(), uri.encode(), bytes(8), bytes(8)
                ),
                "media_type": media_type,
                "source_uri": uri,
            },
            raw,
        )
    text = f"turn {global_index:05d} keeps exact source bytes"
    raw = text.encode()
    return (
        "user" if global_index % 2 == 0 else "assistant",
        {
            "part_id": f"part-{global_index:05d}",
            "kind": "text",
            "content_digest": _digest(raw),
            "text": text,
        },
        raw,
    )


def _source_item(
    scope: Any, session_id: str, global_index: int
) -> tuple[dict[str, Any], str, bytes]:
    role, part, raw = _part(global_index)
    item_id = f"item-{global_index:05d}"
    item = {
        "item_id": item_id,
        "source_event_id": f"event-{global_index:05d}",
        "source_digest": _digest(raw),
        "scope": scope.to_wire(),
        "session_id": session_id,
        "role": role,
        "parts": [part],
        "relations": [],
        "created_at": f"2026-10-02T12:{global_index % 60:02d}:00Z",
        "recoverable": True,
        "tombstone": False,
    }
    return (
        {
            "idempotency_key": f"ingest-{global_index:05d}",
            "item": item,
            "source_snapshot": list(raw),
        },
        item_id,
        raw,
    )


def _primary_payload(
    session_id: str,
    new_items: Sequence[Mapping[str, Any]],
    *,
    writer_lease: Mapping[str, Any],
    baseline_sequence: int = 0,
    delta_sequence: int = 0,
    generation: int = 1,
    cache_boundary: str | None = None,
    cached_change: str = "none",
) -> dict[str, Any]:
    profile = _profile()
    return {
        "session_id": session_id,
        "writer_lease": dict(writer_lease),
        "new_items": list(new_items),
        "expected_source_cursor": None,
        "baseline_through": {"epoch": 1, "sequence": baseline_sequence},
        "delta_through": {"epoch": 1, "sequence": delta_sequence},
        "generation": generation,
        "policy_revision": 1,
        "provider_profile": profile,
        "budget_inputs": {
            "context_window_tokens": profile["context_window_tokens"],
            "reserved_output_tokens": profile["reserved_output_tokens"],
            "max_input_tokens": profile["context_window_tokens"]
            - profile["reserved_output_tokens"],
            "max_items": 100_000,
            "max_images": 4_096,
        },
        "created_at": "2026-10-02T12:00:00Z",
        "cache_boundary": cache_boundary,
        "cached_change": cached_change,
        "overflow_requires_cached_change": False,
        "reduction_boundary": {
            "kind": "tail_safe",
            "reason_code": "active_tail",
            "cache_generation": generation,
        },
        "reclaim_policy": {
            "advisory_basis_points": 7_000,
            "action_basis_points": 8_200,
            "emergency_basis_points": 9_300,
            "max_rewrite_cost_nanodollars": None,
        },
        "reclaim_candidates": [],
    }


def _trace(prefix: str, index: int) -> Trace:
    return Trace(Id(f"{prefix}-trace-{index}"), Id(f"{prefix}-request-{index}"))


def _tool_result_count(items: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for source in items
        for part in source["item"]["parts"]
        if part["kind"] == "tool_result"
    )


def _mutation(scope: Any, operation: MemoryOperation, index: int, **values: Any) -> MutationRequest:
    return MutationRequest(
        operation=operation,
        actor_scope=scope,
        target_scope=scope,
        revision=values.pop("revision", RevisionPrecondition.must_not_exist()),
        trace=_trace(operation.value, index),
        record_id=values.pop("record_id", None),
        **values,
    )


def _draft(scope: Any, index: int, *, updated: bool = False) -> RecordDraft:
    suffix = " superseded" if updated else ""
    return RecordDraft(
        id=Id(f"memory-{index:04d}"),
        scope=scope,
        kind=RecordKind.NOTE,
        category="sec08",
        content=f"durable memory {index:04d}{suffix}",
        importance=0.5,
        confidence=0.75,
        metadata={"fixture_index": index, "superseded": updated},
    )


def _assert_budget(response: Mapping[str, Any], expected_items: int) -> None:
    projection = response["projection"]
    serialized = response["serialized"]
    budget = serialized["model_budget"]
    assert projection["source_cursor"]["sequence"] == expected_items
    assert len(projection["selected_item_ids"]) == expected_items
    assert len(set(projection["selected_item_ids"])) == expected_items
    assert budget["baseline_tokens"] + budget["delta_tokens"] + budget["tail_tokens"] <= budget[
        "max_input_tokens"
    ]
    assert projection["output_digest"] == _digest(canonical_json_bytes(serialized["blocks"]))


@pytest.mark.asyncio
async def test_long_history_survives_restart_and_expands_exact_raw_sources(
    tmp_path: Path,
) -> None:
    observation = ObservationWriter.from_env("long_history")
    resources = [Id(f"memory-{index:04d}") for index in range(MEMORY_COUNT + 20)]
    resources.append(Id("memory-diagnostics"))
    host = lifecycle(tmp_path, resources)
    sampled: dict[str, list[tuple[str, bytes]]] = defaultdict(list)
    context_cursors: list[int] = []
    session_final_cursors: dict[str, int] = {}
    tool_results_observed = 0
    expanded_observed = 0
    source_digest_mismatches = 0
    memory_cursor_sequences: list[int] = []
    creates_observed = 0
    updates_observed = 0
    tombstones_observed = 0
    context_restart_observed = False
    memory_restart_observed = False
    memory_receipts: dict[int, Any] = {}
    try:
        started = await host.start()
        assert started.available and started.scope_bound and started.writer == "gideon", started.to_dict()
        client = host.adapter.client
        scope = client.scope
        writer_lease = await _acquire_writer(client, "long-history")

        for session_number in range(SESSION_COUNT):
            session_id = f"long-history-{session_number:02d}"
            start = session_number * TURNS_PER_SESSION
            source_items = []
            for global_index in range(start, start + TURNS_PER_SESSION):
                source, item_id, raw = _source_item(scope, session_id, global_index)
                source_items.append(source)
                if global_index % (TURN_COUNT // EXPANSION_COUNT) == 0:
                    sampled[session_id].append((item_id, raw))

            if session_number == SESSION_COUNT // 2:
                first = await client.request(
                    "context.primary_project",
                    _primary_payload(
                        session_id,
                        source_items[: TURNS_PER_SESSION // 2],
                        writer_lease=writer_lease,
                    ),
                    deadline_ms=int(time.time() * 1000) + 120_000,
                )
                _assert_budget(first, TURNS_PER_SESSION // 2)
                context_cursors.append(first["cursor"]["sequence"])
                tool_results_observed += _tool_result_count(
                    source_items[: TURNS_PER_SESSION // 2]
                )
                await host.stop()
                restarted = await host.start()
                assert restarted.available and restarted.daemon_instance_id != started.daemon_instance_id
                context_restart_observed = True
                client = host.adapter.client
                second = await client.request(
                    "context.primary_project",
                    _primary_payload(
                        session_id,
                        source_items[TURNS_PER_SESSION // 2 :],
                        writer_lease=writer_lease,
                    ),
                    deadline_ms=int(time.time() * 1000) + 120_000,
                )
                _assert_budget(second, TURNS_PER_SESSION)
                context_cursors.append(second["cursor"]["sequence"])
                session_final_cursors[session_id] = second["cursor"]["sequence"]
                tool_results_observed += _tool_result_count(
                    source_items[TURNS_PER_SESSION // 2 :]
                )
                continue

            response = await client.request(
                "context.primary_project",
                _primary_payload(
                    session_id, source_items, writer_lease=writer_lease
                ),
                deadline_ms=int(time.time() * 1000) + 120_000,
            )
            _assert_budget(response, TURNS_PER_SESSION)
            context_cursors.append(response["cursor"]["sequence"])
            session_final_cursors[session_id] = response["cursor"]["sequence"]
            tool_results_observed += _tool_result_count(source_items)

        assert sum(len(items) for items in sampled.values()) == EXPANSION_COUNT
        for session_id, expected in sampled.items():
            expanded = await client.request(
                "context.expand",
                {"session_id": session_id, "item_ids": [item_id for item_id, _ in expected]},
                deadline_ms=int(time.time() * 1000) + 120_000,
            )
            recovered = expanded["items"]
            assert len(recovered) == len(expected)
            expanded_observed += len(recovered)
            for actual, (item_id, raw) in zip(recovered, expected, strict=True):
                assert actual["item"]["item_id"] == item_id
                source_bytes = bytes(actual["source_bytes"])
                assert source_bytes == raw
                digest_matches = actual["item"]["source_digest"] == _digest(source_bytes)
                source_digest_mismatches += int(not digest_matches)
                assert digest_matches

        memory = MemoryClient(client, capability_id=CAPABILITY_ID)
        durable_total = MEMORY_COUNT + 20
        memory_now_ms = int(time.time() * 1000)
        for index in range(durable_total):
            draft = _draft(scope, index)
            receipt = await memory.create(
                _mutation(
                    scope,
                    MemoryOperation.CREATE,
                    index,
                    record_id=draft.id,
                    category=draft.category,
                ),
                draft,
                now_ms=memory_now_ms + index,
            )
            assert receipt.record is not None and receipt.record.id == draft.id
            memory_receipts[index] = receipt
            memory_cursor_sequences.append(receipt.cursor.sequence)
            creates_observed += 1
            if index + 1 == durable_total // 2:
                before_restart = receipt.cursor
                await host.stop()
                restarted = await host.start()
                assert restarted.available
                memory_restart_observed = True
                client = host.adapter.client
                memory = MemoryClient(client, capability_id=CAPABILITY_ID)

        for index in range(10):
            prior = memory_receipts[index].record
            assert prior is not None
            draft = _draft(scope, index, updated=True)
            updated = await memory.update(
                _mutation(
                    scope,
                    MemoryOperation.UPDATE,
                    index,
                    record_id=draft.id,
                    category=draft.category,
                    revision=RevisionPrecondition.match(str(prior.current.digest)),
                ),
                draft,
                now_ms=memory_now_ms + 100_000 + index,
            )
            assert updated.record is not None and updated.record.current.number == 2
            memory_receipts[index] = updated
            memory_cursor_sequences.append(updated.cursor.sequence)
            updates_observed += 1

        for index in range(10, 30):
            prior = memory_receipts[index].record
            assert prior is not None
            deleted = await memory.delete(
                _mutation(
                    scope,
                    MemoryOperation.DELETE,
                    index,
                    record_id=prior.id,
                    revision=RevisionPrecondition.match(str(prior.current.digest)),
                ),
                now_ms=memory_now_ms + 200_000 + index,
            )
            assert deleted.record is not None and deleted.record.status.value == "tombstoned"
            memory_receipts[index] = deleted
            memory_cursor_sequences.append(deleted.cursor.sequence)
            tombstones_observed += 1

        diagnostics_request = AccessRequest(
            operation=GrantOperation.READ,
            actor_scope=scope,
            target_scope=scope,
            resource_id=Id("memory-diagnostics"),
            trace=_trace("diagnostics", 0),
        )
        diagnostics = await memory.diagnostics(diagnostics_request)
        assert diagnostics.record_count == durable_total

        final_sequence = durable_total + 10 + 20
        for index in range(0, durable_total, 101):
            record, cursor = await memory.get(
                AccessRequest(
                    operation=GrantOperation.READ,
                    actor_scope=scope,
                    target_scope=scope,
                    resource_id=Id(f"memory-{index:04d}"),
                    trace=_trace("get", index),
                )
            )
            assert record is not None and record.id == Id(f"memory-{index:04d}")
            assert cursor.sequence == final_sequence

        assert before_restart.sequence == durable_total // 2
        assert context_cursors.count(TURNS_PER_SESSION) == SESSION_COUNT
        assert context_cursors[SESSION_COUNT // 2] == TURNS_PER_SESSION // 2
        history_turns_observed = sum(session_final_cursors.values())
        durable_memories_observed = sum(
            receipt.record is not None
            and receipt.record.status.value != "tombstoned"
            for receipt in memory_receipts.values()
        )
        cursor_monotonic = all(
            left < right
            for left, right in zip(
                memory_cursor_sequences, memory_cursor_sequences[1:], strict=False
            )
        )
        assert history_turns_observed == TURN_COUNT
        assert tool_results_observed == TOOL_RESULT_COUNT
        assert durable_memories_observed == MEMORY_COUNT
        assert creates_observed == durable_total
        assert updates_observed == 10
        assert tombstones_observed == 20
        assert expanded_observed == EXPANSION_COUNT
        assert source_digest_mismatches == 0
        assert cursor_monotonic

        if observation is not None:
            manifest_path = tmp_path / "long-history-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "context_restart_observed": context_restart_observed,
                        "cursor_monotonic": cursor_monotonic,
                        "durable_records_active": durable_memories_observed,
                        "durable_records_created": creates_observed,
                        "durable_tombstones": tombstones_observed,
                        "durable_updates": updates_observed,
                        "expanded_raw_sources": expanded_observed,
                        "history_tool_results": tool_results_observed,
                        "history_turns": history_turns_observed,
                        "memory_cursor_first": memory_cursor_sequences[0],
                        "memory_cursor_last": memory_cursor_sequences[-1],
                        "memory_restart_observed": memory_restart_observed,
                        "session_final_cursors": session_final_cursors,
                        "source_digest_mismatches": source_digest_mismatches,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            observation.measure("history-turns", history_turns_observed, "eq", TURN_COUNT)
            observation.measure(
                "history-tool-results",
                tool_results_observed,
                "eq",
                TOOL_RESULT_COUNT,
            )
            observation.measure(
                "history-durable-memories",
                durable_memories_observed,
                "eq",
                MEMORY_COUNT,
            )
            observation.measure(
                "expanded-compressed-spans",
                expanded_observed,
                "eq",
                EXPANSION_COUNT,
            )
            observation.measure("cursor-monotonic", cursor_monotonic, "eq", True)
            observation.artifact(
                "long-history-manifest", manifest_path, "application/json"
            )
            observation.finish(source_digest=os.environ["HYPERMID_SOURCE_DIGEST"])
    finally:
        await host.stop()
