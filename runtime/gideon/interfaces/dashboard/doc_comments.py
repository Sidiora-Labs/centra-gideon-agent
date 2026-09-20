"""Persistent document comments."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_FILENAME = "doc_comments.json"


def store_path() -> Path:
    return config_loader.config_dir() / _FILENAME


def _read() -> list[dict[str, Any]]:
    path = store_path()
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("doc_comments.json unreadable; treating as empty", exc_info=True)
        return []
    return value if isinstance(value, list) else []


def _write(comments: list[dict[str, Any]]) -> None:
    atomic_write(store_path(), json.dumps(comments, indent=2) + "\n")


def list_comments() -> list[dict[str, Any]]:
    return _read()


def create_comment(fields: dict[str, Any]) -> dict[str, Any]:
    comment = dict(fields)
    comment.update(id=f"c-{uuid.uuid4().hex}", ts=int(time.time()))
    comments = _read()
    comments.append(comment)
    _write(comments)
    return comment


def update_comment(comment_id: str, text: str) -> dict[str, Any] | None:
    comments = _read()
    found = None
    for comment in comments:
        if comment.get("id") == comment_id:
            comment["comment"] = text
            found = comment
            break
    if found is not None:
        _write(comments)
    return found


def delete_comments(ids: set[str]) -> int:
    comments = _read()
    kept = [comment for comment in comments if comment.get("id") not in ids]
    removed = len(comments) - len(kept)
    if removed:
        _write(kept)
    return removed
