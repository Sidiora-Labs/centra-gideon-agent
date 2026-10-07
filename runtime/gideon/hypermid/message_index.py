from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from .models import Scope


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    session_id: str
    message_id: str
    sequence: int
    content: str
    observed_at_ms: int

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MessageIndexDocument:
    source_key: str
    content: str
    content_digest: str
    source_time_ms: int
    metadata: dict[str, str | int]


@dataclass(slots=True)
class MessageIndexState:
    cursor_sequence: int = 0
    dirty_floor_sequence: int | None = None

    def record_failure(self, sequence: int) -> None:
        if sequence < 0:
            raise ValueError("message sequence must be non-negative")
        if self.dirty_floor_sequence is None:
            self.dirty_floor_sequence = sequence
        else:
            self.dirty_floor_sequence = min(self.dirty_floor_sequence, sequence)

    def reconcile_from(self) -> int:
        return (
            self.dirty_floor_sequence
            if self.dirty_floor_sequence is not None
            else self.cursor_sequence + 1
        )

    def publish_reconciliation(self, *, started_at: int, through_sequence: int) -> None:
        expected = self.reconcile_from()
        if started_at > expected:
            raise ValueError("reconciliation did not cover the earliest dirty sequence")
        if through_sequence < self.cursor_sequence:
            raise ValueError("message cursor cannot move backwards")
        self.cursor_sequence = through_sequence
        if (
            self.dirty_floor_sequence is not None
            and through_sequence >= self.dirty_floor_sequence
        ):
            self.dirty_floor_sequence = None


def prepare_message_documents(
    scope: Scope,
    snapshots: Iterable[MessageSnapshot],
    *,
    from_sequence: int,
) -> tuple[MessageIndexDocument, ...]:
    del scope
    selected = sorted(
        (snapshot for snapshot in snapshots if snapshot.sequence >= from_sequence),
        key=lambda snapshot: (
            snapshot.sequence,
            snapshot.session_id,
            snapshot.message_id,
        ),
    )
    return tuple(
        MessageIndexDocument(
            source_key=f"{snapshot.session_id}:{snapshot.message_id}",
            content=snapshot.content,
            content_digest=snapshot.content_digest,
            source_time_ms=snapshot.observed_at_ms,
            metadata={
                "session_id": snapshot.session_id,
                "message_id": snapshot.message_id,
                "sequence": snapshot.sequence,
            },
        )
        for snapshot in selected
    )
