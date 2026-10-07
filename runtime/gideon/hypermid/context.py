"""ConversationLog-backed context ingestion and exact source recovery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from gideon.cognition.history import (
    ConversationCheckpoint,
    ConversationLog,
    ConversationSourceEvent,
    summary_holds,
)

from .foundation import Cursor, Digest, Id, Scope
from .history import (
    ContextPart,
    HistoryJournal,
    JournalRange,
    PartKind,
    PendingContextItem,
    RecoveredItem,
    Role,
)


def session_id_for_key(session_key: str) -> Id:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
    return Id(f"session:{digest}")


def _stable_id(prefix: str, *values: str) -> Id:
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()
    return Id(f"{prefix}:{digest}")


def _structured_digest(*fields: str) -> Digest:
    material = bytearray()
    for field in fields:
        encoded = field.encode("utf-8")
        material.extend(len(encoded).to_bytes(8, "big"))
        material.extend(encoded)
    return Digest.sha256(bytes(material))


def _json_text(value: object) -> str:
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _role(value: str) -> Role:
    if value == "user":
        return Role.USER
    if value == "assistant":
        return Role.ASSISTANT
    if value in {"tool", "tool_call", "tool_result"}:
        return Role.TOOL
    return Role.SYSTEM


@dataclass(frozen=True, slots=True)
class ContextSyncReceipt:
    session_key: str
    session_id: Id
    cursor: Cursor
    appended: int
    transcript_digest: str


class ConversationLogSourceAdapter:
    """Resolve HistoryJournal references from the sole transcript authority."""

    def __init__(self, log: ConversationLog, *, scope: Scope, session_key: str) -> None:
        self.log = log
        self.scope = scope
        self.session_key = session_key
        self.session_id = session_id_for_key(session_key)

    def resolve(self, scope: Scope, session_id: Id, source_event_id: Id) -> bytes:
        if scope != self.scope or Id(session_id) != self.session_id:
            raise ValueError("conversation source request does not match its binding")
        return self.log.resolve_source_event(self.session_key, str(source_event_id))


class ConversationContextBridge:
    """Maintain an append-only context index over ConversationLog references."""

    def __init__(self, log: ConversationLog, root: str | Path, *, scope: Scope) -> None:
        self.log = log
        self.root = Path(root)
        self.scope = scope
        self.root.mkdir(parents=True, exist_ok=True)
        self._journals: dict[str, HistoryJournal] = {}

    def journal(self, session_key: str) -> HistoryJournal:
        journal = self._journals.get(session_key)
        if journal is not None:
            return journal
        session_id = session_id_for_key(session_key)
        path = self.root / f"{session_id}.sqlite3"
        journal = HistoryJournal(path, scope=self.scope, session_id=session_id)
        self._journals[session_key] = journal
        return journal

    @staticmethod
    def _pending(
        event: ConversationSourceEvent, *, scope: Scope, session_id: Id
    ) -> PendingContextItem:
        content = str(event.message["content"])
        source_id = Id(event.source_event_id)
        meta = event.message.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        raw_call_id = meta.get("tool_call_id")
        parts: tuple[ContextPart, ...]
        if (
            event.message.get("role") == "tool"
            and isinstance(raw_call_id, str)
            and raw_call_id
        ):
            call_id = _stable_id("call", raw_call_id)
            arguments_json = _json_text(meta.get("input", ""))
            tool_call = ContextPart(
                part_id=_stable_id("part", str(source_id), event.source_digest, "call"),
                kind=PartKind.TOOL_CALL,
                content_digest=_structured_digest(
                    str(call_id), content, arguments_json
                ),
                call_id=call_id,
                tool_name=content,
                arguments_json=arguments_json,
                metadata={"source_call_id": raw_call_id},
            )
            if meta.get("done") is True and "output" in meta:
                result_json = _json_text(meta["output"])
                tool_result = ContextPart(
                    part_id=_stable_id(
                        "part", str(source_id), event.source_digest, "result"
                    ),
                    kind=PartKind.TOOL_RESULT,
                    content_digest=_structured_digest(str(call_id), result_json),
                    call_id=call_id,
                    result_json=result_json,
                    metadata={"source_call_id": raw_call_id},
                )
                parts = (tool_call, tool_result)
            else:
                parts = (tool_call,)
        else:
            parts = (
                ContextPart(
                    part_id=_stable_id("part", str(source_id), event.source_digest),
                    kind=PartKind.TEXT,
                    content_digest=Digest.sha256(content.encode("utf-8")),
                    text=content,
                ),
            )
        return PendingContextItem(
            item_id=_stable_id("item", str(source_id), event.source_digest),
            source_event_id=source_id,
            source_digest=Digest(event.source_digest),
            scope=scope,
            session_id=session_id,
            role=_role(str(event.message.get("role", "system"))),
            parts=parts,
            relations=(),
            created_at=str(event.message.get("ts") or "unknown"),
            recoverable=True,
        )

    def sync_session(self, session_key: str) -> ContextSyncReceipt:
        before = self.log.flush_for_hypermid((session_key,))
        checkpoint = (
            before[0]
            if before
            else ConversationCheckpoint(session_key, Digest.sha256(b""), 0)
        )
        journal = self.journal(session_key)
        committed = {
            str(item.source_event_id): str(item.source_digest)
            for item in journal.all_items()
        }
        appended = 0
        for event in self.log.source_events(session_key):
            prior_digest = committed.get(event.source_event_id)
            if prior_digest is not None:
                if prior_digest != event.source_digest:
                    raise RuntimeError(
                        "ConversationLog source identity was reused for different bytes"
                    )
                continue
            item = self._pending(event, scope=self.scope, session_id=journal.session_id)
            journal.append(
                expected_cursor=journal.cursor,
                idempotency_key=_stable_id(
                    "ingest", event.source_event_id, event.source_digest
                ),
                item=item,
                source_snapshot=None,
            )
            committed[event.source_event_id] = event.source_digest
            appended += 1
        after = self.log.flush_for_hypermid((session_key,))
        if after and after[0].source_digest != checkpoint.source_digest:
            raise RuntimeError("ConversationLog changed while Hypermid indexed it")
        return ContextSyncReceipt(
            session_key=session_key,
            session_id=journal.session_id,
            cursor=journal.cursor,
            appended=appended,
            transcript_digest=checkpoint.source_digest,
        )

    def validate_checkpoints(
        self, checkpoints: Sequence[ConversationCheckpoint]
    ) -> bool:
        current = {
            item.session_key: item.source_digest
            for item in self.log.flush_for_hypermid(
                tuple(checkpoint.session_key for checkpoint in checkpoints)
            )
        }
        return all(
            current.get(checkpoint.session_key) == checkpoint.source_digest
            for checkpoint in checkpoints
        )

    def recover(
        self,
        session_key: str,
        *,
        item_ids: Sequence[Id] | None = None,
        tags: Sequence[int] | None = None,
        cursor_range: JournalRange | None = None,
    ) -> tuple[RecoveredItem, ...]:
        journal = self.journal(session_key)
        adapter = ConversationLogSourceAdapter(
            self.log, scope=self.scope, session_key=session_key
        )
        return journal.recover(
            adapter,
            item_ids=item_ids,
            tags=tags,
            cursor_range=cursor_range,
        )

    def publish_summary(
        self,
        session_key: str,
        *,
        summary: str,
        summarized: int,
        reduced: int,
        reduced_cap: int = 600,
    ) -> dict:
        messages = self.log.read_messages(session_key)
        record = self.log.write_summary(
            session_key,
            summary=summary,
            summarized=summarized,
            reduced=reduced,
            reduced_cap=reduced_cap,
            messages=messages,
        )
        if not summary_holds(record, self.log.read_messages(session_key)):
            raise RuntimeError("summary source digest changed during publication")
        return record


__all__ = [
    "ContextSyncReceipt",
    "ConversationContextBridge",
    "ConversationLogSourceAdapter",
    "session_id_for_key",
]
