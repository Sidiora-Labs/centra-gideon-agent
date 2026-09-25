"""Reviewed conversation exports retained as canonical knowledge sources."""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.atomic_write import atomic_write_bytes
from gideon.core.config.loader import CONFIG_DIR_NAME
from gideon.core.config.locations import configuration_home

from .capture import CaptureError, request_key

MAX_BYTES = 512 * 1024


def runtime_home():
    return configuration_home(
        os.environ.get("GIDEON_HOME"),
        Path.home() / CONFIG_DIR_NAME,
        logging.getLogger(__name__),
    ).resolve()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def instant(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptureError("Archive timestamps must be Unix seconds or null")
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        raise CaptureError("Archive timestamp is out of range") from None


def conversation(record):
    if not isinstance(record, dict):
        raise CaptureError("Each conversation must be an object")
    identity = record.get("id") or record.get("conversation_id")
    if not isinstance(identity, str) or not 1 <= len(identity) <= 200:
        raise CaptureError("Each conversation requires a stable id")
    result = {
        "id": identity,
        "title": record.get("title") or "Untitled conversation",
        "created_at": None,
        "updated_at": None,
        "message_count": 0,
        "branch_count": 0,
        "unsupported_parts": 0,
        "valid": False,
        "error": "",
    }
    try:
        if not isinstance(result["title"], str) or len(result["title"]) > 1000:
            raise CaptureError("Invalid conversation title")
        result["created_at"], result["updated_at"] = instant(
            record.get("create_time")
        ), instant(record.get("update_time"))
        mapping = record.get("mapping")
        if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 5000:
            raise CaptureError("Conversation mapping requires 1..5000 nodes")
        sections, child_counts = [], {}
        for identity, node in mapping.items():
            if not isinstance(identity, str) or not isinstance(node, dict):
                raise CaptureError("Invalid conversation node")
            parent = node.get("parent")
            if parent is not None and (
                not isinstance(parent, str) or parent not in mapping
            ):
                raise CaptureError("Conversation node references a missing parent")
            child_counts[parent] = child_counts.get(parent, 0) + 1
            seen, ancestor = {identity}, parent
            while ancestor is not None:
                if ancestor in seen:
                    raise CaptureError("Conversation branch contains a cycle")
                seen.add(ancestor)
                next_node = mapping.get(ancestor)
                if not isinstance(next_node, dict):
                    raise CaptureError("Invalid branch ancestor")
                ancestor = next_node.get("parent")
                if ancestor is not None and (
                    not isinstance(ancestor, str) or ancestor not in mapping
                ):
                    raise CaptureError(
                        "Conversation branch references a missing ancestor"
                    )
            message = node.get("message")
            if message is None:
                continue
            if (
                not isinstance(message, dict)
                or not isinstance(message.get("author"), dict)
                or not isinstance(message.get("content"), dict)
            ):
                raise CaptureError("Invalid message author or content")
            role = message["author"].get("role")
            if not isinstance(role, str) or not role:
                raise CaptureError("Message role is required")
            content = message["content"]
            parts = content.get("parts", [])
            if not isinstance(parts, list):
                raise CaptureError("Message content parts must be a list")
            texts = [part for part in parts if isinstance(part, str)]
            result["unsupported_parts"] += sum(
                not isinstance(part, str) for part in parts
            )
            if content.get("content_type") not in ("text", None):
                result["unsupported_parts"] += 1
            stamp = instant(message.get("create_time"))
            sections.append(
                (
                    stamp or "",
                    identity,
                    f"## {role} · {stamp or 'undated'}\nNode: {identity}; parent: {parent or 'root'}\n\n"
                    + "\n".join(texts),
                )
            )
        result.update(
            message_count=len(sections),
            branch_count=sum(
                count > 1
                for parent, count in child_counts.items()
                if parent is not None
            ),
            valid=True,
        )
        if not sections:
            raise CaptureError("Conversation has no messages")
        return (
            result,
            "\n\n".join(section[2] for section in sorted(sections)),
            sha(canonical(record)),
        )
    except (CaptureError, TypeError) as exc:
        result.update(valid=False, error=str(exc))
        return result, "", sha(canonical(record))


class ConversationArchive:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.runtime_home = Path(home).resolve() if home is not None else runtime_home()
        self.files_root = (
            Path(self.db.execute("PRAGMA database_list").fetchone()[2]).resolve().parent
            / "files"
        )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_archives (request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, receipt TEXT);
            CREATE TRIGGER IF NOT EXISTS archive_receipt_immutable BEFORE UPDATE ON capability_knowledge_archives
            WHEN OLD.receipt IS NOT NULL BEGIN SELECT RAISE(ABORT,'archive receipt is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS archive_receipt_no_delete BEFORE DELETE ON capability_knowledge_archives
            BEGIN SELECT RAISE(ABORT,'archive receipt is immutable'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS conversation_archive_guid ON items(guid) WHERE substr(guid,1,8) = 'archive:';
        """)

    def _parse(self, body):
        if (
            not isinstance(body, dict)
            or set(body) != {"format", "content"}
            or body["format"] != "chatgpt"
        ):
            raise CaptureError(
                "Archive requires format chatgpt and original JSON content only"
            )
        raw = body["content"]
        if not isinstance(raw, str) or not 1 <= len(raw.encode()) <= MAX_BYTES:
            raise CaptureError("Archive must contain at most 512 KiB of UTF-8 JSON")
        try:
            records = json.loads(
                raw,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError("Nonfinite archive number")
                ),
            )
        except (ValueError, RecursionError):
            raise CaptureError("Archive is not valid finite JSON") from None
        if not isinstance(records, list) or not 1 <= len(records) <= 500:
            raise CaptureError("Archive must contain 1..500 conversations")
        rows = [conversation(record) for record in records]
        if len({row[0]["id"] for row in rows}) != len(rows):
            raise CaptureError("Archive contains duplicate conversation ids")
        return sha(raw), rows

    def _existing(self, guid):
        row = self.db.execute("SELECT id FROM items WHERE guid=?", (guid,)).fetchone()
        return row[0] if row else None

    def preview(self, body):
        source_digest, rows = self._parse(body)
        return {
            "source_digest": source_digest,
            "format": body["format"],
            "total": len(rows),
            "conversations": [
                {
                    **row,
                    "existing_id": self._existing(
                        "archive:conversation:" + fingerprint
                    ),
                }
                for row, content, fingerprint in rows
            ],
        }

    def commit(self, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "source_digest",
            "format",
            "content",
            "conversation_ids",
        }:
            raise CaptureError(
                "Commit requires original archive, reviewed digest, conversation_ids and request_id only"
            )
        request_key(body["request_id"])
        source_digest, rows = self._parse(
            {key: body[key] for key in ("format", "content")}
        )
        if source_digest != body["source_digest"]:
            raise CaptureError("Archive changed since preview", 409)
        selected = body["conversation_ids"]
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(key, str) for key in selected)
            or len(set(selected)) != len(selected)
        ):
            raise CaptureError("Choose distinct conversation ids")
        chosen = [row for row in rows if row[0]["id"] in selected]
        if len(chosen) != len(selected) or any(not row[0]["valid"] for row in chosen):
            raise CaptureError("Selection includes an unknown or invalid conversation")
        payload_hash = sha(canonical({**body, "conversation_ids": sorted(selected)}))
        previous = self.db.execute(
            "SELECT * FROM capability_knowledge_archives WHERE request_id=?",
            (body["request_id"],),
        ).fetchone()
        if previous:
            if previous["payload_hash"] != payload_hash:
                raise CaptureError(
                    "Import request already belongs to different input", 409
                )
            if previous["receipt"]:
                return json.loads(previous["receipt"])
        if runtime_home() != self.runtime_home:
            raise CaptureError(
                "Runtime home changed; restore the bound allocation before importing archives",
                409,
            )
        if not previous:
            self.db.execute(
                "INSERT INTO capability_knowledge_archives VALUES (?,?,NULL)",
                (body["request_id"], payload_hash),
            )
            self.db.commit()
        self.files_root.mkdir(parents=True, exist_ok=True)
        path = self.files_root / ("archive-" + source_digest + ".json")
        if (
            path.exists()
            and hashlib.sha256(path.read_bytes()).hexdigest() != source_digest
        ):
            raise CaptureError("Original archive integrity mismatch", 409)
        if not path.exists():
            atomic_write_bytes(path, body["content"].encode(), fsync=True)
        source_id = self._existing("archive:source:" + source_digest)
        if not source_id:
            source_id = self.store.create_typed_item(
                item_type="document",
                title="Conversation archive " + source_digest[:12],
                guid="archive:source:" + source_digest,
                extra={
                    "file_path": str(path),
                    "file_size": path.stat().st_size,
                    "mime_type": "application/json",
                    "file_metadata": {
                        "content_hash": source_digest,
                        "original_filename": "conversations.json",
                    },
                },
            )
        items = []
        for row, content, fingerprint in chosen:
            identity = self._existing("archive:conversation:" + fingerprint)
            status = "existing" if identity else "imported"
            if not identity:
                identity = self.store.create_typed_item(
                    item_type="note",
                    title=row["title"],
                    content=content,
                    guid="archive:conversation:" + fingerprint,
                    extra={
                        "file_metadata": {
                            "archive_source_id": source_id,
                            "conversation_id": row["id"],
                            "original_at": row["created_at"],
                            "source_updated_at": row["updated_at"],
                            "source_record_hash": fingerprint,
                            "unsupported_parts": row["unsupported_parts"],
                        }
                    },
                )
            items.append(
                {
                    "conversation_id": row["id"],
                    "destination_id": identity,
                    "source_link": "#/knowledge/item/" + identity,
                    "status": status,
                }
            )
        receipt = {
            "request_id": body["request_id"],
            "source_digest": source_digest,
            "source_item_id": source_id,
            "source_link": "#/knowledge/item/" + source_id,
            "items": items,
            "committed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.execute(
            "UPDATE capability_knowledge_archives SET receipt=? WHERE request_id=?",
            (canonical(receipt), body["request_id"]),
        )
        self.db.commit()
        return receipt

    def list(self, limit=20, offset=0):
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 100
            or not 0 <= offset <= 1000000
        ):
            raise CaptureError("Invalid pagination")
        total = self.db.execute(
            "SELECT count(*) FROM capability_knowledge_archives WHERE receipt IS NOT NULL"
        ).fetchone()[0]
        items = [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT receipt FROM capability_knowledge_archives WHERE receipt IS NOT NULL ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def original(self, identity):
        item = self.store.get_item(identity)
        if not item or not (item.get("guid") or "").startswith("archive:source:"):
            raise CaptureError("Archive source not found", 404)
        path = Path(item.get("file_path") or "").resolve()
        if not path.is_relative_to(self.files_root.resolve()) or not path.is_file():
            raise CaptureError("Original archive is unavailable", 404)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["file_metadata"]["content_hash"]:
            raise CaptureError("Original archive integrity mismatch", 409)
        return raw
