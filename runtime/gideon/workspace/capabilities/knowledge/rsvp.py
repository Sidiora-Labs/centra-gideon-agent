"""Durable RSVP state over canonical Knowledge items."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from gideon.workspace.capabilities.knowledge.capture import CaptureError

_PUNCTUATION = re.compile(r"^[^\w\s]+$", re.UNICODE)
_OPENING = re.compile(r"^[\(\[\{\u00ab\u2018\u201c'\"\u00bf\u00a1]+$", re.UNICODE)


def words(text: str) -> list[dict]:
    result: list[dict] = []
    prefix = ""
    prefix_start: int | None = None
    for match in re.finditer(r"\S+", text or "", re.UNICODE):
        value = match.group(0)
        if _PUNCTUATION.fullmatch(value):
            if _OPENING.fullmatch(value) or not result:
                prefix += value
                if prefix_start is None:
                    prefix_start = match.start()
            else:
                result[-1]["text"] += value
                result[-1]["end"] = match.end()
            continue
        result.append(
            {
                "text": prefix + value,
                "start": prefix_start if prefix_start is not None else match.start(),
                "end": match.end(),
            }
        )
        prefix = ""
        prefix_start = None
    if prefix and result:
        result[-1]["text"] += prefix
    return result


def delay_multiplier(value: str) -> float:
    if re.search(
        r"[.!?\u2026\u3002\uff01\uff1f](?:[\"'\u201d\u2019\u00bb\)\]\}]+)?$",
        value or "",
        re.UNICODE,
    ):
        return 1.8
    if re.search(r"[,;:](?:[\"'\u201d\u2019\u00bb\)\]\}]+)?$", value or "", re.UNICODE):
        return 1.3
    return 1.15 if len(value or "") > 8 else 1.0


class RsvpStates:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_rsvp (
                item_id TEXT PRIMARY KEY,
                word_index INTEGER NOT NULL,
                wpm INTEGER NOT NULL,
                chunk_size INTEGER NOT NULL,
                bookmark_index INTEGER,
                content_revision TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            );
        """)

    @staticmethod
    def _revision(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _item(self, item_id: str) -> tuple[dict, list[dict], str]:
        if not isinstance(item_id, str) or not item_id or len(item_id) > 128:
            raise CaptureError("A valid Knowledge item id is required")
        item = self.store.get_item(item_id)
        if item is None or item.get("status") != "active" or item.get("is_archived"):
            raise CaptureError("Knowledge item is unavailable", 404)
        tokens = words(item.get("content") or "")
        if not tokens:
            raise CaptureError("Knowledge item has no readable words", 409)
        return item, tokens, self._revision(item.get("content") or "")

    def get(self, item_id: str) -> dict:
        item, tokens, revision = self._item(item_id)
        row = self.db.execute(
            "SELECT * FROM capability_knowledge_rsvp WHERE item_id=?", (item_id,)
        ).fetchone()
        changed = bool(row and row["content_revision"] != revision)
        state = {
            "item_id": item_id,
            "title": item.get("title") or item.get("url_title") or "Untitled",
            "word_index": min(int(row["word_index"]), len(tokens) - 1) if row else 0,
            "wpm": int(row["wpm"]) if row else 350,
            "chunk_size": int(row["chunk_size"]) if row else 1,
            "bookmark_index": (
                min(int(row["bookmark_index"]), len(tokens) - 1)
                if row and row["bookmark_index"] is not None
                else None
            ),
            "word_count": len(tokens),
            "content_revision": revision,
            "content_changed": changed,
            "updated_at": row["updated_at"] if row else None,
        }
        if changed:
            self._write(state)
        return state

    def _validated(
        self, item_id: str, payload: dict, required: set[str]
    ) -> tuple[dict, list[dict], str]:
        if not isinstance(payload, dict) or set(payload) != required:
            raise CaptureError("RSVP state has an invalid request shape")
        item, tokens, revision = self._item(item_id)
        if payload["content_revision"] != revision:
            raise CaptureError(
                "Knowledge content changed; reopen RSVP before saving", 409
            )
        return item, tokens, revision

    def save(self, item_id: str, payload: dict) -> dict:
        _, tokens, revision = self._validated(
            item_id, payload, {"word_index", "wpm", "chunk_size", "content_revision"}
        )
        if type(payload["word_index"]) is not int or not 0 <= payload[
            "word_index"
        ] < len(tokens):
            raise CaptureError("word_index is outside the canonical word range")
        if type(payload["wpm"]) is not int or not 100 <= payload["wpm"] <= 1000:
            raise CaptureError("wpm must be between 100 and 1000")
        if (
            payload["chunk_size"] not in (1, 2)
            or type(payload["chunk_size"]) is not int
        ):
            raise CaptureError("chunk_size must be 1 or 2")
        current = self.get(item_id)
        current.update(
            word_index=payload["word_index"],
            wpm=payload["wpm"],
            chunk_size=payload["chunk_size"],
            content_revision=revision,
            content_changed=False,
        )
        self._write(current)
        return self.get(item_id)

    def bookmark(self, item_id: str, payload: dict) -> dict:
        _, tokens, revision = self._validated(
            item_id, payload, {"word_index", "content_revision"}
        )
        if type(payload["word_index"]) is not int or not 0 <= payload[
            "word_index"
        ] < len(tokens):
            raise CaptureError("word_index is outside the canonical word range")
        current = self.get(item_id)
        current.update(
            bookmark_index=payload["word_index"],
            content_revision=revision,
            content_changed=False,
        )
        self._write(current)
        return self.get(item_id)

    def restore(self, item_id: str) -> dict:
        current = self.get(item_id)
        if current["bookmark_index"] is None:
            raise CaptureError("No RSVP bookmark exists for this item", 409)
        current["word_index"] = current["bookmark_index"]
        self._write(current)
        return self.get(item_id)

    def _write(self, state: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO capability_knowledge_rsvp(item_id,word_index,wpm,chunk_size,bookmark_index,content_revision,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET word_index=excluded.word_index,wpm=excluded.wpm,chunk_size=excluded.chunk_size,bookmark_index=excluded.bookmark_index,content_revision=excluded.content_revision,updated_at=excluded.updated_at",
            (
                state["item_id"],
                state["word_index"],
                state["wpm"],
                state["chunk_size"],
                state.get("bookmark_index"),
                state["content_revision"],
                now,
            ),
        )
        self.db.commit()
