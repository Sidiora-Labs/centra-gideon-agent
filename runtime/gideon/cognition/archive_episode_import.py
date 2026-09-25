"""Narrow canonical import seam for authored episodic memories."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime

from gideon.cognition.onboarding_import.floors import safe_text, strip_secrets

_FIELDS = {
    "id",
    "conversation_id",
    "text",
    "tags",
    "importance",
    "source",
    "contributor",
    "created_at",
    "updated_at",
    "is_deleted",
}
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AUTH_HEADER = re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+")
_AUTHORED = {"user_explicit", "user", "vault_edit"}


@dataclass(frozen=True)
class EpisodeImportResult:
    accepted: bool
    code: str


def _timestamp(value, label):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"Invalid episodic memory {label}")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid episodic memory {label}") from error
    if stamp.tzinfo is None:
        raise ValueError(f"Invalid episodic memory {label}")
    return stamp


def _safe(value):
    cleaned, dropped = strip_secrets(value)
    if dropped or cleaned != value:
        return False
    if isinstance(value, str):
        screened, redactions = safe_text(value)
        return (
            not redactions and screened == value and _AUTH_HEADER.search(value) is None
        )
    if isinstance(value, list):
        return all(_safe(item) for item in value)
    return True


def validate_authored_episode(value):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid authored episodic memory record")
    if not isinstance(value.get("id"), str) or _UUID.fullmatch(value["id"]) is None:
        raise ValueError("Invalid episodic memory identity")
    conversation = value.get("conversation_id")
    if not isinstance(conversation, str) or _REF.fullmatch(conversation) is None:
        raise ValueError("Invalid episodic conversation provenance")
    contributor = value.get("contributor")
    if not isinstance(contributor, str) or _REF.fullmatch(contributor) is None:
        raise ValueError("Invalid episodic contributor provenance")
    if value.get("source") not in _AUTHORED:
        raise ValueError("Episodic memory is not explicitly authored")
    text, tags = value.get("text"), value.get("tags")
    if not isinstance(text, str) or not 20 <= len(text) <= 12000 or not _safe(text):
        raise ValueError("Invalid or private episodic memory text")
    if (
        not isinstance(tags, list)
        or len(tags) > 10
        or any(
            not isinstance(tag, str) or not tag or len(tag) > 50 or not _safe(tag)
            for tag in tags
        )
        or len(set(tags)) != len(tags)
    ):
        raise ValueError("Invalid or private episodic memory tags")
    importance = value.get("importance")
    if (
        type(importance) not in (int, float)
        or not math.isfinite(importance)
        or not 0 <= importance <= 1
    ):
        raise ValueError("Invalid episodic memory importance")
    created = _timestamp(value.get("created_at"), "created_at")
    updated = _timestamp(value.get("updated_at"), "updated_at")
    if (
        updated < created
        or type(value.get("is_deleted")) is not bool
        or (not value["is_deleted"] and updated != created)
    ):
        raise ValueError("Invalid episodic memory lifecycle")
    return value


class AuthoredEpisodeImport:
    def __init__(self, archive):
        self.archive = archive

    def apply(self, record, *, commit=True):
        value = validate_authored_episode(record)
        database = self.archive.db
        row = database.execute(
            "SELECT id,conversation_id,text,tags,importance,created_at,is_deleted,contributor "
            "FROM episodic_memories WHERE id=?",
            (value["id"],),
        ).fetchone()
        if row is not None:
            origin = database.execute(
                "SELECT source FROM memory_events WHERE memory_type='episodic' AND memory_key=? "
                "AND event_type IN ('create','import') ORDER BY id LIMIT 1",
                (value["id"],),
            ).fetchone()
            latest = database.execute(
                "SELECT created_at FROM memory_events WHERE memory_type='episodic' AND memory_key=? "
                "AND event_type IN ('create','import','delete') ORDER BY id DESC LIMIT 1",
                (value["id"],),
            ).fetchone()
            if origin is None or latest is None:
                raise ValueError("Episodic memory identity lacks canonical provenance")
            current = {
                "id": row["id"],
                "conversation_id": row["conversation_id"] or "",
                "text": row["text"],
                "tags": json.loads(row["tags"] or "[]"),
                "importance": row["importance"],
                "source": origin["source"],
                "contributor": row["contributor"] or "",
                "created_at": row["created_at"],
                "updated_at": latest["created_at"],
                "is_deleted": bool(row["is_deleted"]),
            }
            immutable = {
                key
                for key in _FIELDS - {"updated_at", "is_deleted"}
                if current[key] != value[key]
            }
            if immutable or (current["is_deleted"] and not value["is_deleted"]):
                raise ValueError(
                    "Episodic memory identity already has different immutable content"
                )
            if current["is_deleted"] == value["is_deleted"]:
                if current["updated_at"] != value["updated_at"]:
                    raise ValueError(
                        "Episodic memory identity already has different lifecycle state"
                    )
                return EpisodeImportResult(True, "already_current")
            database.execute(
                "UPDATE episodic_memories SET is_deleted=1,embedding=NULL,last_accessed_at=NULL WHERE id=?",
                (value["id"],),
            )
            event_type = "delete"
        else:
            database.execute(
                "INSERT INTO episodic_memories(id,conversation_id,text,embedding,tags,importance,created_at,last_accessed_at,is_deleted,contributor) "
                "VALUES(?,?,?,NULL,?,?,?,NULL,?,?)",
                (
                    value["id"],
                    value["conversation_id"],
                    value["text"],
                    json.dumps(value["tags"]),
                    value["importance"],
                    value["created_at"],
                    int(value["is_deleted"]),
                    value["contributor"],
                ),
            )
            database.execute(
                "INSERT INTO memory_events(event_type,memory_type,memory_key,old_value,new_value,source,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    "import",
                    "episodic",
                    value["id"],
                    None,
                    value["text"][:200],
                    value["source"],
                    value["created_at"],
                ),
            )
            event_type = "delete" if value["is_deleted"] else None
        if event_type is not None:
            database.execute(
                "INSERT INTO memory_events(event_type,memory_type,memory_key,old_value,new_value,source,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    event_type,
                    "episodic",
                    value["id"],
                    None,
                    value["text"][:200],
                    value["source"],
                    value["updated_at"],
                ),
            )
        if commit:
            database.commit()
        return EpisodeImportResult(
            True, "tombstoned" if value["is_deleted"] else "imported"
        )
