import base64
import hashlib
import struct

import pytest

from gideon.hypermid.ownership import OwnershipError, OwnershipFence
from gideon.hypermid.protocol import (
    MAX_ENVELOPE_BYTES,
    ChunkAssembler,
    Cursor,
    FrameDecoder,
    Operation,
    ProtocolRequest,
    ProtocolViolation,
    Scope,
    Trace,
    TransferChunk,
    encode_frame,
)


def test_framing_round_trip_is_bounded_and_incremental() -> None:
    request = ProtocolRequest(
        operation=Operation.BIND,
        scope=Scope("owner-1", "project-1"),
        trace=Trace("trace-1", "request-1"),
        session_id="session-1",
        payload={"mode": "pass_through"},
    )
    frame = encode_frame(request)
    decoder = FrameDecoder()
    assert decoder.feed(frame[:3]) == []
    decoded = decoder.feed(frame[3:])
    assert decoded == [request.to_mapping()]
    decoder.finish()

    with pytest.raises(ProtocolViolation, match="eight MiB") as error:
        FrameDecoder().feed(struct.pack(">I", MAX_ENVELOPE_BYTES + 1))
    assert error.value.code == "ENVELOPE_TOO_LARGE"


def test_chunks_require_order_and_both_digests() -> None:
    body = b"a" * 65_000 + b"b" * 1_000
    parts = [body[:65_000], body[65_000:]]
    full_digest = hashlib.sha256(body).hexdigest()
    chunks = [
        TransferChunk(
            transfer_id="transfer-1",
            ordinal=index,
            total=2,
            chunk_digest=hashlib.sha256(part).hexdigest(),
            full_digest=full_digest,
            payload_base64=base64.b64encode(part).decode("ascii"),
            trace=Trace("trace-1", f"chunk-{index}"),
        )
        for index, part in enumerate(parts)
    ]
    assembler = ChunkAssembler()
    assert assembler.add(chunks[0]) is None
    assert assembler.add(chunks[1]) == body

    out_of_order = ChunkAssembler()
    with pytest.raises(ProtocolViolation) as error:
        out_of_order.add(chunks[1])
    assert error.value.code == "INVALID_CHUNK_ORDER"

    incomplete = ChunkAssembler()
    incomplete.add(chunks[0])
    with pytest.raises(ProtocolViolation) as error:
        incomplete.finish("transfer-1")
    assert error.value.code == "INCOMPLETE_TRANSFER"


def test_scope_cursor_fence_and_idempotency_fail_closed() -> None:
    fence = OwnershipFence()
    scope = Scope("owner-1", "project-1", "workspace-a")
    foreign = Scope("owner-2", "project-1", "workspace-a")
    state = fence.bind("session-1", scope)
    lease_one = fence.acquire_writer(
        "session-1", scope, "lease-1", now_ms=1_000, ttl_ms=1_000
    )

    with pytest.raises(OwnershipError) as error:
        fence.commit(
            "session-1",
            foreign,
            state.cursor,
            lease_one,
            "mutation-foreign",
            "ingest",
            {"accepted": 1},
            now_ms=1_100,
        )
    assert error.value.code == "SCOPE_MISMATCH"
    assert fence.state("session-1", scope).cursor == Cursor(1, 0)

    first = fence.commit(
        "session-1",
        scope,
        state.cursor,
        lease_one,
        "mutation-1",
        "ingest",
        {"accepted": 1},
        now_ms=1_100,
    )
    replay = fence.commit(
        "session-1",
        scope,
        state.cursor,
        lease_one,
        "mutation-1",
        "ingest",
        {"accepted": 99},
        now_ms=1_200,
    )
    assert first.cursor == Cursor(1, 1)
    assert replay.cursor == first.cursor
    assert replay.result == {"accepted": 1}
    assert replay.replayed is True

    lease_two = fence.acquire_writer(
        "session-1", scope, "lease-2", now_ms=1_300, ttl_ms=1_000
    )
    with pytest.raises(OwnershipError) as error:
        fence.commit(
            "session-1",
            scope,
            first.cursor,
            lease_one,
            "mutation-2",
            "ingest",
            {"accepted": 1},
            now_ms=1_400,
        )
    assert error.value.code == "STALE_FENCE"

    with pytest.raises(OwnershipError) as error:
        fence.commit(
            "session-1",
            scope,
            Cursor(1, 0),
            lease_two,
            "mutation-3",
            "ingest",
            {"accepted": 1},
            now_ms=1_400,
        )
    assert error.value.code == "STALE_CURSOR"
    assert fence.state("session-1", scope).cursor == Cursor(1, 1)


def test_workspace_rebind_is_explicit_and_owner_project_are_immutable() -> None:
    fence = OwnershipFence()
    previous = Scope("owner-1", "project-1", "workspace-a")
    next_scope = Scope("owner-1", "project-1", "workspace-b")
    state = fence.bind("session-1", previous)
    lease = fence.acquire_writer(
        "session-1", previous, "lease-1", now_ms=1_000, ttl_ms=1_000
    )
    with pytest.raises(OwnershipError) as error:
        fence.bind("session-1", next_scope)
    assert error.value.code == "SCOPE_MISMATCH"

    outcome = fence.rebind_workspace(
        "session-1",
        previous,
        next_scope,
        state.cursor,
        lease,
        "rebind-1",
        now_ms=1_100,
    )
    assert outcome.cursor == Cursor(1, 1)
    assert fence.state("session-1", next_scope).scope == next_scope

    next_lease = fence.acquire_writer(
        "session-1", next_scope, "lease-2", now_ms=1_200, ttl_ms=1_000
    )
    with pytest.raises(OwnershipError) as error:
        fence.rebind_workspace(
            "session-1",
            next_scope,
            Scope("owner-2", "project-1", "workspace-b"),
            outcome.cursor,
            next_lease,
            "rebind-2",
            now_ms=1_300,
        )
    assert error.value.code == "IDENTITY_CHANGE_REQUIRES_NEW_SESSION"
