from __future__ import annotations

import hashlib
import json

import pytest

from gideon.hypermid.foundation import Cursor, Digest, Id, Scope
from gideon.hypermid.history import (
    ContextPart,
    HistoryJournal,
    JournalRange,
    PartKind,
    PendingContextItem,
    RawSourceJournal,
    Role,
)
from gideon.hypermid.recovery import (
    LastKnownGood,
    RecoveryBinding,
    RecoveryRefusal,
    RecoveryStore,
    ReplayRequest,
    rebuild_from_journal,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def test_recovery_replays_only_exact_bindings_and_quarantines_corruption(tmp_path) -> None:
    scope = Scope(Id("owner-1"), Id("project-1"))
    session_id = Id("session-1")
    source_bytes = b'{"role":"user","content":"recover this"}'
    source_digest = Digest.sha256(source_bytes)
    raw = RawSourceJournal(tmp_path / "raw.sqlite3")
    raw.append(scope, session_id, Id("event-1"), source_bytes)
    journal = HistoryJournal(
        tmp_path / "history.sqlite3", scope=scope, session_id=session_id
    )
    item = journal.append(
        expected_cursor=Cursor(1, 0),
        idempotency_key=Id("append-1"),
        item=PendingContextItem(
            item_id=Id("item-1"),
            source_event_id=Id("event-1"),
            source_digest=source_digest,
            scope=scope,
            session_id=session_id,
            role=Role.USER,
            parts=(
                ContextPart(
                    part_id=Id("part-1"),
                    kind=PartKind.TEXT,
                    content_digest=Digest.sha256(b"recover this"),
                    text="recover this",
                ),
            ),
            relations=(),
            created_at="2026-10-02T12:00:00Z",
            recoverable=True,
        ),
        source_snapshot=source_bytes,
    )

    block = {
        "block_id": "block:item-1",
        "role": "user",
        "parts": [item.parts[0].to_mapping()],
        "source_item_ids": ["item-1"],
        "content_digest": str(Digest.sha256(b"normalized-block")),
        "cache_boundary": "none",
        "synthetic": False,
    }
    output_digest = Digest.sha256(_canonical([block]))
    empty_region_digest = Digest.sha256(b"empty-region")
    tail_region_digest = Digest.sha256(b"tail-region")
    profile_digest = Digest.sha256(b"provider-profile")
    journal_digest = journal.source_digest(JournalRange(item.cursor, item.cursor))
    projection = {
        "projection_id": f"projection:{output_digest}",
        "scope": scope.to_wire(),
        "session_id": str(session_id),
        "source_cursor": item.cursor.to_wire(),
        "source_digest": str(journal_digest),
        "generation": 2,
        "policy_revision": 4,
        "mode": "primary",
        "render_mode": "host_serialized",
        "provider_profile_digest": str(profile_digest),
        "baseline": {"digest": str(empty_region_digest)},
        "delta": {"digest": str(empty_region_digest)},
        "tail": {"digest": str(tail_region_digest)},
        "blocks": [block],
        "output_digest": str(output_digest),
    }
    model_budget = {
        "context_window_tokens": 1_000,
        "reserved_output_tokens": 100,
        "max_input_tokens": 900,
        "max_items": 10,
        "max_images": 0,
        "baseline_tokens": 0,
        "delta_tokens": 0,
        "tail_tokens": 4,
        "confidence": "measured",
    }
    record = LastKnownGood.capture(
        projection, model_budget, stored_at="2026-10-02T12:00:01Z"
    )
    store = RecoveryStore(tmp_path / "recovery")
    store.publish(record)
    request = ReplayRequest(record.binding, input_tokens=4, item_count=1, image_count=0)
    assert store.replay(request) == projection

    incompatible_mapping = record.binding.to_mapping()
    incompatible_mapping["provider_profile_digest"] = str(
        Digest.sha256(b"other-provider")
    )
    incompatible = RecoveryBinding.from_mapping(incompatible_mapping)
    with pytest.raises(RecoveryRefusal) as refused:
        store.replay(
            ReplayRequest(incompatible, input_tokens=4, item_count=1, image_count=0)
        )
    assert refused.value.error.code == "RECOVERY_BINDING_MISMATCH"
    assert refused.value.error.retryable is True

    store.active.write_bytes(b"{partial")
    assert store.load() is None
    quarantined = store.quarantine_entries()
    assert len(quarantined) == 1
    assert quarantined[0].content_digest == hashlib.sha256(b"{partial").hexdigest()
    assert quarantined[0].byte_length == 8

    rebuilt = rebuild_from_journal(journal, raw)
    assert rebuilt.cursor == item.cursor
    assert rebuilt.source_digest == record.binding.source_digest
    assert rebuilt.items[0].source_bytes == source_bytes
