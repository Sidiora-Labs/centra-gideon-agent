from __future__ import annotations

import json

import pytest

from gideon.hypermid.foundation import Cursor, Digest, Id, Scope
from gideon.hypermid.history import (
    ContextPart,
    HistoryError,
    HistoryJournal,
    JournalRange,
    PartKind,
    PendingContextItem,
    RawSourceJournal,
    Role,
)
from gideon.hypermid.identity import IdentityRelation, RelationKind


def _text_part(part_id: str, text: str) -> ContextPart:
    return ContextPart(
        part_id=Id(part_id),
        kind=PartKind.TEXT,
        content_digest=Digest.sha256(text.encode()),
        text=text,
    )


def _pending(
    *,
    item_id: str,
    source_event_id: str,
    source: bytes,
    scope: Scope,
    session_id: Id,
    text: str,
    relations: tuple[IdentityRelation, ...] = (),
) -> PendingContextItem:
    return PendingContextItem(
        item_id=Id(item_id),
        source_event_id=Id(source_event_id),
        source_digest=Digest.sha256(source),
        scope=scope,
        session_id=session_id,
        role=Role.USER,
        parts=(_text_part(f"part-{item_id}", text),),
        relations=relations,
        created_at="2026-10-02T12:00:00Z",
        recoverable=True,
    )


def test_history_is_append_only_idempotent_and_exactly_recoverable(tmp_path) -> None:
    scope = Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))
    session_id = Id("session-1")
    raw = RawSourceJournal(tmp_path / "raw.sqlite3")
    journal_path = tmp_path / "history.sqlite3"
    journal = HistoryJournal(
        journal_path, scope=scope, session_id=session_id, epoch=7
    )

    original_bytes = b'{"role":"user","content":"keep exact bytes"}'
    original_digest = raw.append(
        scope, session_id, Id("event-original"), original_bytes
    )
    original_pending = _pending(
        item_id="item-original",
        source_event_id="event-original",
        source=original_bytes,
        scope=scope,
        session_id=session_id,
        text="keep exact bytes",
    )
    original = journal.append(
        expected_cursor=Cursor(7, 0),
        idempotency_key=Id("append-original"),
        item=original_pending,
        source_snapshot=original_bytes,
    )
    assert original.source_digest == original_digest
    assert original.cursor == Cursor(7, 1)

    replay = journal.append(
        expected_cursor=Cursor(7, 0),
        idempotency_key=Id("append-original"),
        item=original_pending,
        source_snapshot=original_bytes,
    )
    assert replay == original
    assert journal.cursor == Cursor(7, 1)

    edited_bytes = b'{"role":"user","content":"keep revised exact bytes"}'
    raw.append(scope, session_id, Id("event-edit"), edited_bytes)
    edited = journal.append(
        expected_cursor=journal.cursor,
        idempotency_key=Id("append-edit"),
        item=_pending(
            item_id="item-edit",
            source_event_id="event-edit",
            source=edited_bytes,
            scope=scope,
            session_id=session_id,
            text="keep revised exact bytes",
            relations=(
                IdentityRelation(
                    kind=RelationKind.SUPERSEDES,
                    item_id=original.item_id,
                    source_digest=original.source_digest,
                ),
            ),
        ),
        source_snapshot=edited_bytes,
    )
    assert edited.cursor == Cursor(7, 2)
    assert journal.all_items()[0] == original
    assert journal.all_items()[1].relations[0].kind is RelationKind.SUPERSEDES

    recovered_by_id = journal.recover(raw, item_ids=(original.item_id,))
    recovered_by_tag = journal.recover(raw, tags=(edited.reclaim_tag,))
    recovered_range = journal.recover(
        raw, cursor_range=JournalRange(original.cursor, edited.cursor)
    )
    assert recovered_by_id[0].source_bytes == original_bytes
    assert recovered_by_tag[0].source_bytes == edited_bytes
    assert [item.source_bytes for item in recovered_range] == [
        original_bytes,
        edited_bytes,
    ]
    assert journal.source_digest(JournalRange(original.cursor, edited.cursor)) == Digest.sha256(
        original.cursor.epoch.to_bytes(8, "big")
        + original.cursor.sequence.to_bytes(8, "big")
        + str(original.item_id).encode()
        + b"\0"
        + str(original.source_event_id).encode()
        + b"\0"
        + bytes.fromhex(original.source_digest)
        + edited.cursor.epoch.to_bytes(8, "big")
        + edited.cursor.sequence.to_bytes(8, "big")
        + str(edited.item_id).encode()
        + b"\0"
        + str(edited.source_event_id).encode()
        + b"\0"
        + bytes.fromhex(edited.source_digest)
    )

    with pytest.raises(HistoryError, match="stale") as stale:
        journal.append(
            expected_cursor=Cursor(7, 1),
            idempotency_key=Id("append-stale"),
            item=_pending(
                item_id="item-stale",
                source_event_id="event-stale",
                source=b"stale",
                scope=scope,
                session_id=session_id,
                text="stale",
            ),
            source_snapshot=b"stale",
        )
    assert stale.value.code == "STALE_CURSOR"
    assert journal.cursor == edited.cursor

    journal._connection.execute("PRAGMA wal_checkpoint(FULL)")
    reopened = HistoryJournal(
        journal_path, scope=scope, session_id=session_id, epoch=7
    )
    assert reopened.cursor == Cursor(7, 2)
    assert reopened.recover(raw, tags=(1,))[0].source_bytes == original_bytes

    with pytest.raises(Exception, match="immutable"):
        reopened._connection.execute(
            "UPDATE history_entries SET item_json=? WHERE sequence=1",
            (json.dumps({"mutated": True}),),
        )
