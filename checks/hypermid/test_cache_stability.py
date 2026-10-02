from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from gideon.hypermid.client import HypermidRemoteError, canonical_json_bytes
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MemoryOperation,
    RecordDraft,
    RecordKind,
)
from gideon.hypermid.foundation import Id
from gideon.hypermid.memory_client import MemoryClient

from checks.hypermid.test_long_history import (
    CAPABILITY_ID,
    _acquire_writer,
    _digest,
    _mutation,
    _primary_payload,
    _source_item,
    _trace,
)
from checks.hypermid.test_long_history import lifecycle
from checks.hypermid.evidence import ObservationWriter


def _serialized_request(response: dict[str, Any]) -> bytes:
    serialized = response["serialized"]
    request = {
        "blocks": serialized["blocks"],
        "model_budget": serialized["model_budget"],
        "provider_profile_digest": serialized["provider_profile_digest"],
        "render_mode": serialized["render_mode"],
    }
    encoded = canonical_json_bytes(request)
    assert _digest(encoded) == serialized["serialized_digest"]
    return encoded


def _bounded_edges(value: bytes, width: int = 96) -> tuple[bytes, bytes]:
    return value[:width], value[-width:]


def _common_prefix(left: bytes, right: bytes) -> int:
    for index, (a, b) in enumerate(zip(left, right, strict=False)):
        if a != b:
            return index
    return min(len(left), len(right))


def _assert_tool_pair(blocks: list[dict[str, Any]]) -> None:
    parts = [part for block in blocks for part in block["parts"]]
    call_index = next(index for index, part in enumerate(parts) if part["kind"] == "tool_call")
    result_index = next(index for index, part in enumerate(parts) if part["kind"] == "tool_result")
    assert result_index == call_index + 1
    assert parts[call_index]["call_id"] == parts[result_index]["call_id"]


@pytest.mark.asyncio
async def test_fifty_primary_projections_preserve_exact_cache_prefix_and_pairs(
    tmp_path: Path,
) -> None:
    observation = ObservationWriter.from_env("cache_bytes")
    host = lifecycle(tmp_path, [Id("cache-memory")])
    try:
        status = await host.start()
        assert status.available and status.scope_bound and status.writer == "gideon", status.to_dict()
        client = host.adapter.client
        scope = client.scope
        memory = MemoryClient(client, capability_id=CAPABILITY_ID)
        writer_lease = await _acquire_writer(client, "cache-stability")

        memory_draft = RecordDraft(
            id=Id("cache-memory"),
            scope=scope,
            kind=RecordKind.NOTE,
            category="cache-acceptance",
            content="retrieved memory remains in the frozen prefix",
            importance=0.8,
            confidence=0.9,
            metadata={"purpose": "cache-prefix"},
        )
        created = await memory.create(
            _mutation(
                scope,
                    MemoryOperation.CREATE,
                    0,
                    record_id=memory_draft.id,
                    category=memory_draft.category,
            ),
            memory_draft,
            now_ms=int(time.time() * 1000),
        )
        assert created.record is not None
        retrieved, _ = await memory.get(
            AccessRequest(
                operation=GrantOperation.READ,
                actor_scope=scope,
                target_scope=scope,
                resource_id=memory_draft.id,
                trace=_trace("cache-get", 0),
            )
        )
        assert retrieved is not None

        session_id = "cache-stability"
        memory_raw = retrieved.current.content.encode()
        memory_item = {
            "idempotency_key": "cache-memory-ingest",
            "item": {
                "item_id": "cache-memory-item",
                "source_event_id": "cache-memory-event",
                "source_digest": hashlib.sha256(memory_raw).hexdigest(),
                "scope": scope.to_wire(),
                "session_id": session_id,
                "role": "system",
                "parts": [
                    {
                        "part_id": "cache-memory-part",
                        "kind": "context_marker",
                        "content_digest": hashlib.sha256(memory_raw).hexdigest(),
                        "text": retrieved.current.content,
                    }
                ],
                "relations": [],
                "created_at": "2026-10-02T12:00:00Z",
                "recoverable": True,
                "tombstone": False,
            },
            "source_snapshot": list(memory_raw),
        }
        tool_call, _, _ = _source_item(scope, session_id, 0)
        tool_result, _, _ = _source_item(scope, session_id, 1)
        initialized = await client.request(
            "context.primary_project",
            _primary_payload(
                session_id,
                [memory_item, tool_call, tool_result],
                writer_lease=writer_lease,
                baseline_sequence=3,
                delta_sequence=3,
            ),
            deadline_ms=int(time.time() * 1000) + 120_000,
        )
        assert initialized["cache"]["reason_code"] == "cache_initialized"
        baseline_blocks = initialized["serialized"]["blocks"]
        assert len(baseline_blocks) == 3
        assert baseline_blocks[0]["parts"][0]["text"] == retrieved.current.content
        _assert_tool_pair(baseline_blocks)

        stable_prefix = b'{"blocks":[' + b",".join(
            canonical_json_bytes(block) for block in baseline_blocks
        ) + b","
        stable_prefix_digest = _digest(stable_prefix)
        captures: list[dict[str, Any]] = []
        prior_cursor = 3

        for turn in range(50):
            source, _, _ = _source_item(scope, session_id, 10_000 + turn)
            response = await client.request(
                "context.primary_project",
                _primary_payload(
                    session_id,
                    [source],
                    writer_lease=writer_lease,
                    baseline_sequence=3,
                    delta_sequence=prior_cursor,
                ),
                deadline_ms=int(time.time() * 1000) + 120_000,
            )
            encoded = _serialized_request(response)
            assert encoded.startswith(stable_prefix)
            assert _digest(encoded[: len(stable_prefix)]) == stable_prefix_digest
            assert response["cache"]["reason_code"] == "delta_refreshed"
            assert response["projection"]["baseline"]["item_ids"] == [
                "cache-memory-item",
                "item-00000",
                "item-00001",
            ]
            budget = response["serialized"]["model_budget"]
            assert budget["baseline_tokens"] + budget["delta_tokens"] + budget["tail_tokens"] <= budget[
                "max_input_tokens"
            ]
            _assert_tool_pair(response["serialized"]["blocks"])
            captures.append(
                {
                    "turn": turn,
                    "prefix_sha256": _digest(encoded[: len(stable_prefix)]),
                    "stable_prefix_bytes": len(stable_prefix),
                    "changed_after": _common_prefix(stable_prefix, encoded),
                    "changed_prefix_bytes": sum(
                        left != right
                        for left, right in zip(
                            stable_prefix,
                            encoded[: len(stable_prefix)],
                            strict=True,
                        )
                    ),
                    "diagnostic_edges": _bounded_edges(encoded),
                    "serialized_bytes": len(encoded),
                    "cache_eligible": True,
                    "delta_tokens": budget["delta_tokens"],
                    "tail_tokens": budget["tail_tokens"],
                }
            )
            prior_cursor += 1

            if turn == 24:
                with pytest.raises(HypermidRemoteError) as deferred:
                    await client.request(
                        "context.primary_project",
                        _primary_payload(
                            session_id,
                            [],
                            writer_lease=writer_lease,
                            baseline_sequence=3,
                            delta_sequence=prior_cursor,
                            cached_change="tier_decay",
                        ),
                    )
                assert deferred.value.error.code == "CONTEXT_PRESSURE_REFUSED"

        assert len(captures) == 50
        assert {capture["prefix_sha256"] for capture in captures} == {stable_prefix_digest}
        assert all(capture["stable_prefix_bytes"] == len(stable_prefix) for capture in captures)
        assert all(capture["changed_after"] >= len(stable_prefix) for capture in captures)
        assert all(capture["diagnostic_edges"][0] and capture["diagnostic_edges"][1] for capture in captures)

        folded = await client.request(
            "context.primary_project",
            _primary_payload(
                session_id,
                [],
                writer_lease=writer_lease,
                baseline_sequence=prior_cursor,
                delta_sequence=prior_cursor,
                generation=2,
                cache_boundary="explicit_flush",
            ),
        )
        assert folded["cache"] == {
            "kind": "applied",
            "generation": 2,
            "reason_code": "baseline_folded",
        }
        assert folded["projection"]["delta"]["item_ids"] == []
        assert _serialized_request(folded).startswith(stable_prefix)

        post_boundary, _, _ = _source_item(scope, session_id, 11_000)
        resumed = await client.request(
            "context.primary_project",
            _primary_payload(
                session_id,
                [post_boundary],
                writer_lease=writer_lease,
                baseline_sequence=prior_cursor,
                delta_sequence=prior_cursor,
                generation=2,
            ),
        )
        assert resumed["cache"]["generation"] == 2
        assert resumed["projection"]["baseline"]["item_ids"] == folded["projection"]["baseline"][
            "item_ids"
        ]
        assert resumed["serialized"]["model_budget"]["tail_tokens"] > 0
        _assert_tool_pair(resumed["serialized"]["blocks"])

        changed_prefix_bytes = sum(
            capture["changed_prefix_bytes"] for capture in captures
        )
        retained_ranges = sum(
            bool(capture["diagnostic_edges"][0])
            and bool(capture["diagnostic_edges"][1])
            for capture in captures
        )
        assert changed_prefix_bytes == 0
        assert retained_ranges == len(captures)
        if observation is not None:
            ranges_path = tmp_path / "stable-prefix-byte-ranges.json"
            ranges_path.write_text(
                json.dumps(
                    {
                        "captures": [
                            {
                                **{
                                    key: value
                                    for key, value in capture.items()
                                    if key != "diagnostic_edges"
                                },
                                "leading_bytes_hex": capture["diagnostic_edges"][0].hex(),
                                "leading_range": [
                                    0,
                                    len(capture["diagnostic_edges"][0]),
                                ],
                                "trailing_bytes_hex": capture["diagnostic_edges"][1].hex(),
                                "trailing_range": [
                                    capture["serialized_bytes"]
                                    - len(capture["diagnostic_edges"][1]),
                                    capture["serialized_bytes"],
                                ],
                            }
                            for capture in captures
                        ],
                        "stable_prefix_bytes": len(stable_prefix),
                        "stable_prefix_sha256": stable_prefix_digest,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            observation.measure(
                "cache-stable-turns", len(captures), "eq", 50, unit="turns"
            )
            observation.measure(
                "stable-prefix-changed-bytes",
                changed_prefix_bytes,
                "eq",
                0,
                unit="bytes",
            )
            observation.measure(
                "diagnostic-byte-ranges-retained",
                retained_ranges,
                "eq",
                len(captures),
                unit="ranges",
            )
            observation.artifact(
                "stable-prefix-byte-ranges", ranges_path, "application/json"
            )
            observation.finish(source_digest=os.environ["HYPERMID_SOURCE_DIGEST"])
    finally:
        await host.stop()
