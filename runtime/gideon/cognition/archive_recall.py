"""Constrained exact recall of displaced conversation journal rows."""

from __future__ import annotations

import json
import re
from pathlib import Path

from gideon.cognition.history import ConversationLog, _archive_dir, _live_restricted, _safe_key
from gideon.security.security import redact_credentials, redact_exfiltration_urls

_MAX_FILES = 1000
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _safe_session(key: str, log: ConversationLog) -> bool:
    if not key or _live_restricted(key) or not log.has_session(key):
        return False
    return log.get_metadata(key).get("memory_mode", "persistent") not in ("incognito", "temporary")


def _redact(text: str) -> str:
    return redact_exfiltration_urls(redact_credentials(text)[0])[0]


def _files(key: str, base: Path) -> list[Path]:
    directory = _archive_dir(base)
    if not directory.is_dir():
        return []
    prefix = _safe_key(key) + "__"
    matched = []
    for path in directory.iterdir():
        if not path.is_file() or not path.name.startswith(prefix) or path.suffix != ".jsonl" or path.is_symlink():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                header = json.loads(stream.readline())
        except (OSError, ValueError):
            continue
        declared = header.get("session_key")
        if declared == key or (declared is None and _safe_key(key) == key):
            matched.append(path)
    return sorted(matched, reverse=True)[:_MAX_FILES]


def _rows(path: Path) -> list[dict]:
    if path.stat().st_size > _MAX_FILE_BYTES:
        return []
    rows = []
    with path.open(encoding="utf-8", errors="replace") as source:
        for index, line in enumerate(source):
            if index == 0:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("role") and isinstance(value.get("content"), str):
                rows.append(value)
    return rows


def search(key: str, query: str, *, limit: int = 5, base: Path | None = None) -> list[dict]:
    log = ConversationLog(base)
    if not _safe_session(key, log) or not query.strip():
        return []
    terms = re.findall(r"\w+", query.casefold())[:12]
    if not terms:
        return []
    hits = []
    for path in _files(key, log._dir):
        for index, row in enumerate(_rows(path)):
            content = row["content"]
            folded = content.casefold()
            score = sum(term in folded for term in terms)
            if score:
                excerpt = _redact(content[:240].replace("\n", " "))
                hits.append({"session": key, "archive": path.name, "index": index,
                             "role": row["role"], "ts": row.get("ts", ""),
                             "snippet": excerpt, "score": score})
    return sorted(hits, key=lambda hit: (-hit["score"], hit["archive"], hit["index"]))[:max(1, min(limit, 20))]


def read(key: str, archive: str, index: int, *, max_chars: int = 12000,
         base: Path | None = None) -> dict | None:
    log = ConversationLog(base)
    if not _safe_session(key, log) or not isinstance(index, int) or index < 0:
        return None
    path = next((p for p in _files(key, log._dir) if p.name == archive), None)
    if path is None:
        return None
    rows = _rows(path)
    if index >= len(rows):
        return None
    budget = max(500, min(max_chars, 20000))
    selected = []
    for position in range(max(0, index - 1), min(len(rows), index + 2)):
        row = rows[position]
        text = _redact(row["content"])
        if position != index and not (row.get("role") == "tool" or row.get("tool_calls") or rows[index].get("role") == "tool"):
            continue
        if not budget:
            break
        calls = row.get("tool_calls", [])
        if calls:
            try:
                calls = json.loads(_redact(json.dumps(calls, ensure_ascii=False)))
            except ValueError:
                calls = []
        selected.append({"index": position, "role": row["role"], "ts": row.get("ts", ""),
                         "content": text[:budget], "truncated": len(text) > budget,
                         "tool_call_id": row.get("tool_call_id", ""),
                         "tool_calls": calls})
        budget -= len(selected[-1]["content"])
    return {"session": key, "archive": archive, "messages": selected}
