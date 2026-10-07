"""Home-scoped session indexing and honest search coverage."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from pathlib import Path

from gideon.engine import session_search as _fts

logger = logging.getLogger(__name__)
_TOKEN = re.compile(r"[0-9A-Za-z_]+")
_INDEXERS: dict[str, "SessionIndexer"] = {}
_INDEXERS_LOCK = threading.Lock()
_MAX_QUERY_CHARS = 200
_BACKGROUND_SLICE_SECS = 0.12
_BACKGROUND_REST_SECS = 0.22
_FALLBACK_WINDOW = 500


def _root(log) -> str:
    return str(Path(log._dir).resolve())


class SessionIndexer:
    """One disposable FTS worker for a canonical ConversationLog directory."""

    def __init__(self, log):
        self.log = log
        self.root = _root(log)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty: deque[str] = deque()
        self._dirty_keys: set[str] = set()
        self._cursor = -1
        self._started_at = 0.0

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="gideon-session-index",
                daemon=True,
            )
            self._started_at = time.monotonic()
            self._thread.start()
            return True

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def note_changed(self, key: str) -> None:
        key = str(key or "").strip()
        if not key:
            return
        with self._lock:
            if key not in self._dirty_keys:
                self._dirty.append(key)
                self._dirty_keys.add(key)
        self._wake.set()

    def foreground(self):
        return self._lock

    def progress(self, eligible: set[str] | None = None) -> dict | None:
        state = _fts.indexed_state(self.log)
        if not state["available"]:
            return None
        keys = state["keys"] if eligible is None else state["keys"] & eligible
        long_keys = state.get("long_keys", set())
        if eligible is not None:
            long_count = len(long_keys & eligible)
        else:
            long_count = state["long"]
        of = (
            len(eligible)
            if eligible is not None
            else len(self.log.list_session_records())
        )
        building = bool(self._thread and self._thread.is_alive()) and len(keys) < of
        return {
            "indexed": len(keys),
            "of": of,
            "building": building,
            "long": long_count,
        }

    def _take_dirty(self) -> str | None:
        with self._lock:
            if not self._dirty:
                return None
            key = self._dirty.popleft()
            self._dirty_keys.discard(key)
            return key

    def _step(self, entries: list[dict], indexed: dict[str, str]) -> bool:
        dirty = self._take_dirty()
        if dirty:
            return _fts.reindex_session(dirty, log=self.log)
        if not entries:
            return False
        start = (self._cursor + 1) % len(entries)
        for offset in range(len(entries)):
            position = (start + offset) % len(entries)
            entry = entries[position]
            key = str(entry.get("key", "") or "")
            if not key:
                continue
            current = _fts._source_identity(self.log, key)
            self._cursor = position
            if indexed.get(key) == current and current:
                continue
            if _fts.is_restricted(
                key, memory_mode=str(entry.get("memory_mode", "") or "")
            ):
                _fts.forget_session(key, scope=self.root)
                continue
            return _fts.reindex_session(key, log=self.log)
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            changed = False
            try:
                entries = self.log.list_session_records()
                state = _fts.indexed_state(self.log)
                indexed = state.get("identities", {})
                while time.monotonic() - started < _BACKGROUND_SLICE_SECS:
                    if self._stop.is_set():
                        break
                    with self._lock:
                        stepped = self._step(entries, indexed)
                    if stepped:
                        changed = True
                        state = _fts.indexed_state(self.log)
                        indexed = state.get("identities", {})
                    elif not self._dirty:
                        break
            except Exception:
                logger.debug("session index background pass failed", exc_info=True)
            if not changed:
                self._wake.wait(_BACKGROUND_REST_SECS)
                self._wake.clear()


def get_indexer(log) -> SessionIndexer:
    root = _root(log)
    with _INDEXERS_LOCK:
        indexer = _INDEXERS.get(root)
        if indexer is None:
            indexer = SessionIndexer(log)
            _INDEXERS[root] = indexer
        else:
            indexer.log = log
        return indexer


def note_changed(key: str, *, log) -> None:
    with _INDEXERS_LOCK:
        indexer = _INDEXERS.get(_root(log))
    if indexer is not None:
        state = _fts.indexed_state(log)
        if key not in state.get("keys", set()) and _fts.reindex_session(key, log=log):
            return
        indexer.note_changed(key)


def _eligible(log, request_app: str = "") -> list[dict]:
    rows = []
    for row in log.list_session_records():
        if str(row.get("memory_mode", "") or "").strip().lower() in (
            "incognito",
            "temporary",
        ):
            continue
        if row.get("_closed"):
            continue
        if request_app and row.get("_created_by_app", "") != request_app:
            continue
        rows.append(row)
    return rows


def _direct_hit(log, row: dict, terms: list[str]) -> tuple[dict | None, bool]:
    key = str(row.get("key", "") or "")
    path = log._path(key)
    title = str(row.get("title", "") or key)
    title_folded = title.casefold()
    found = {term for term in terms if term in title_folded}
    snippet = ""
    read_error = False
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    read_error = True
                    continue
                if not isinstance(message, dict) or message.get("_type") == "metadata":
                    continue
                if message.get("role") == "system":
                    continue
                content = str(message.get("content", "") or "")
                if not content:
                    continue
                folded = content.casefold()
                found.update(term for term in terms if term in folded)
                if not snippet and set(terms) <= found:
                    match_at = min(max(folded.find(term), 0) for term in terms)
                    snippet = content[max(0, match_at - 80) : match_at + 180]
    except OSError:
        return None, True
    if not set(terms) <= found:
        return None, read_error
    if not snippet:
        snippet = title
    return {
        "key": key,
        "session_key": key,
        "title": title,
        "snippet": snippet,
    }, read_error


def answer(
    log,
    query: str,
    *,
    limit: int = 50,
    rest: bool = False,
    request_app: str = "",
) -> dict:
    text = str(query or "").strip()[:_MAX_QUERY_CHARS]
    entries = _eligible(log, request_app)
    eligible = {str(row.get("key", "") or "") for row in entries}
    indexer = get_indexer(log)
    if len(text) < _fts.MIN_QUERY_CHARS or not eligible:
        return {
            "sessions": [],
            "source": "none",
            "searched": {"chats": 0, "of": len(eligible)},
            "complete": bool(not text or not eligible),
            "index": indexer.progress(eligible),
            "matched": 0,
        }
    indexer.start()
    state = _fts.indexed_state(log)
    index_keys = state.get("keys", set()) & eligible
    long_keys = state.get("long_keys", set()) & eligible
    indexed = indexer.progress(eligible)
    query_terms = [word.casefold() for word in _TOKEN.findall(text)]
    if not query_terms:
        return {
            "sessions": [],
            "source": "none",
            "searched": {"chats": 0, "of": len(eligible)},
            "complete": True,
            "index": indexed,
            "matched": 0,
        }
    results: dict[str, dict] = {}
    scanned: set[str] = set()
    available = bool(state.get("available"))
    search_ok = available
    if available:
        try:
            with indexer.foreground():
                for hit in _fts.search_sessions(
                    text, limit=max(200, limit), log=log, strict=True
                ):
                    if hit.get("key") in eligible:
                        results[hit["key"]] = hit
        except Exception:
            logger.debug("session search FTS request failed", exc_info=True)
            search_ok = False
    elif not rest:
        entries = entries[:_FALLBACK_WINDOW]

    if rest:
        scan_rows = entries
    elif not search_ok:
        scan_rows = entries[:_FALLBACK_WINDOW]
    else:
        scan_rows = [
            row
            for row in entries[:_FALLBACK_WINDOW]
            if row.get("key") not in index_keys
        ]
    scan_error = False
    for row in scan_rows:
        key = str(row.get("key", "") or "")
        if not key:
            continue
        if search_ok and not rest and key in index_keys:
            continue
        scanned.add(key)
        direct_hit, read_error = _direct_hit(log, row, query_terms)
        hit = direct_hit if direct_hit is not None else {}
        scan_error = scan_error or read_error
        if direct_hit is None:
            try:
                if not log._path(key).exists():
                    scan_error = True
            except OSError:
                scan_error = True
            continue
        results[key] = hit

    searched_keys = set(index_keys) | scanned if search_ok else scanned
    all_checked = eligible <= searched_keys and not scan_error
    fully_indexed = search_ok and eligible <= index_keys and not long_keys
    if rest:
        complete = all_checked
    else:
        complete = bool(fully_indexed)
    if search_ok and scanned:
        source = "index+scan" if index_keys else "scan"
    elif search_ok:
        source = "index"
    else:
        source = "scan" if scanned else "none"
    catalog = {
        str(row.get("key", "") or ""): {
            key: value for key, value in row.items() if not key.startswith("_")
        }
        for row in entries
    }
    enriched = [{**catalog.get(key, {}), **hit} for key, hit in results.items()]
    ordered = sorted(
        enriched,
        key=lambda row: (row.get("rank", 0), row.get("title", "").casefold()),
    )[: max(1, min(int(limit or 50), 200))]
    return {
        "sessions": ordered,
        "source": source,
        "searched": {"chats": len(searched_keys), "of": len(eligible)},
        "complete": complete,
        "index": indexed,
        "matched": len(results),
    }
