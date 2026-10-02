"""Conversation journals and separately scheduled memory consolidation."""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import secrets
import time as _time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gideon.cognition.journal_pages import (
    JournalDocument,
    JournalPages,
    JournalSearch,
    message_rows,
)
from gideon.core.atomic_write import atomic_write
from gideon.core.concurrency import single_flight
from gideon.core.config import loader as config_loader
from gideon.engine.session import BACKGROUND_KEY
from gideon.extensions.skills import AutoSkillProvenance
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel

if TYPE_CHECKING:
    from gideon.cognition.memory import MemoryJournal
    from gideon.cognition.memory_service import MemoryService
    from gideon.cognition.vector_memory import SemanticArchive
    from gideon.engine.session import ConversationDirectory
    from gideon.extensions.skills import ProcedureLibrary

logger = logging.getLogger(__name__)

SESSIONS_DIR_NAME = "sessions"

ARCHIVE_DIR_NAME = "archive"

ARCHIVE_RETENTION_DAYS = 7

_CONSOLIDATION_THRESHOLD = 30

_SESSION_MAX_BYTES = 2 * 1024 * 1024

_SESSION_KEEP_LINES = 200

SEARCH_MIN_CHARS = 2

_TITLE_BOOST = 10

_SEARCH_SCAN_WINDOW = 500

_SENSITIVE_TOOL_PATTERNS: tuple[str, ...] = (
    ".aws/",
    ".ssh/",
    ".gnupg/",
    ".gpg/",
    ".docker/config",
    ".kube/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".gideon/.env",
    "169.254.169.254",
)

_TOOL_ROLES: frozenset[str] = frozenset({"tool", "tool_call", "tool_result"})


@dataclass(frozen=True, slots=True)
class ConversationSourceEvent:
    source_event_id: str
    source_digest: str
    raw_bytes: bytes
    message: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConversationCheckpoint:
    session_key: str
    source_digest: str
    byte_count: int


def config_dir() -> Path:
    return config_loader.config_dir()


def _live_restricted(session_key: str) -> bool:
    if session_key:
        try:
            from gideon.engine import session_restrictions

            return bool(session_restrictions.is_restricted(session_key))
        except Exception:
            return False
    return False


def _sessions_dir() -> Path:
    return config_dir().joinpath(SESSIONS_DIR_NAME)


def _archive_dir(base: Path | None = None) -> Path:
    return (base or _sessions_dir()).joinpath(ARCHIVE_DIR_NAME)


class _ConversationArchives:
    def __init__(self, base):
        self.base = base

    def save(self, key, lines, reason):
        directory = _archive_dir(self.base)
        directory.mkdir(parents=True, exist_ok=True)
        clock = datetime.now()
        prefix = f"{_safe_key(key)}__{clock.strftime('%Y%m%d-%H%M%S')}"
        header = dict(
            _type="archive",
            session_key=key,
            reason=reason,
            archived_at=clock.isoformat(),
            count=len(lines),
        )
        payload = json.dumps(header) + "\n" + "".join(lines)
        for sequence in range(1001):
            suffix = f"-{sequence}" if sequence else ""
            path = directory / (prefix + suffix + ".jsonl")
            try:
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(payload)
            except FileExistsError:
                continue
            logger.info(
                "Archived %d lines from session %s to %s (reason=%s)",
                len(lines),
                key,
                path.name,
                reason,
            )
            _cleanup_old_archives(base=self.base)
            return path
        raise RuntimeError("Failed to create archive file after 1001 attempts")

    def remove_before(self, cutoff):
        directory = _archive_dir(self.base)
        removed = 0
        if directory.exists():
            for path in directory.glob("*.jsonl"):
                try:
                    expired = path.stat().st_mtime < cutoff
                    if expired:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed


def _archive_lines(
    key: str, lines: list[str], reason: str, base: Path | None = None
) -> Path | None:
    return _ConversationArchives(base).save(key, lines, reason) if lines else None


_last_cleanup: float = 0.0


def _cleanup_old_archives(
    retention_days: int = ARCHIVE_RETENTION_DAYS, base: Path | None = None
) -> int:
    global _last_cleanup
    clock = _time.time()
    if clock - _last_cleanup < 3600:
        return 0
    _last_cleanup = clock
    removed = _ConversationArchives(base).remove_before(clock - retention_days * 86400)
    if removed:
        logger.info("Cleaned %d expired archive files (>%dd)", removed, retention_days)
    return removed


def _safe_key(key: str) -> str:
    return re.sub(r"[^\w\-.]", "_", key)


def span_digest(messages: list[dict]) -> str:
    """Fingerprint the transcript content covered by a derived model summary."""
    canonical = [
        [str(message.get("role", "")), str(message.get("content", ""))]
        for message in messages
    ]
    payload = json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def summary_holds(record: dict | None, messages: list[dict]) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        summarized = int(record["summarized"])
        reduced = int(record["reduced"])
        reduced_cap = int(record.get("reduced_cap", 600))
        digest = str(record["digest"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        0 < summarized <= reduced <= len(messages)
        and reduced_cap > 0
        and bool(record.get("summary"))
        and digest == span_digest(messages[:reduced])
    )


def model_view(messages: list[dict], record: dict | None) -> list[dict]:
    """Apply a current summary without changing the authoritative transcript."""
    if not summary_holds(record, messages):
        return [dict(message) for message in messages]
    summarized = int(record["summarized"])
    reduced = int(record["reduced"])
    cap = max(1, int(record.get("reduced_cap", 600)))
    view = [
        dict(
            role="summary",
            content=str(record["summary"]),
        )
    ]
    for message in messages[summarized:reduced]:
        if message.get("role") not in ("user", "assistant"):
            continue
        content = str(message.get("content", ""))
        if len(content) > cap:
            content = content[:cap] + " …"
        view.append(dict(message, content=content))
    view.extend(dict(message) for message in messages[reduced:])
    return view


class _SessionSummary:
    def __init__(self, log, path, stat):
        self.log, self.path, self.stat = log, path, stat
        self.key = path.stem

    def metadata(self):
        cached = self.log._meta_cache.get(self.key)
        if cached and cached[0] == self.stat.st_mtime:
            return cached[1]
        try:
            with self.path.open(encoding="utf-8") as stream:
                head = stream.readline().strip()
            if head:
                metadata = json.loads(head)
                if metadata.get("_type") == "metadata":
                    self.log._meta_cache[self.key] = self.stat.st_mtime, metadata
                    return metadata
        except Exception:
            pass
        return {}

    def first_user_title(self):
        cached = self.log._msg_cache.get(self.key)
        if cached and cached[0] == self.stat.st_mtime:
            for record in cached[1]:
                if record.get("role") == "user" and record.get("content"):
                    return record["content"][:80]
            return None
        try:
            from itertools import islice

            with self.path.open(encoding="utf-8") as stream:
                for line in islice(stream, 21):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if record.get("role") == "user" and record.get("content"):
                        return record["content"][:80]
        except Exception:
            pass
        return None

    def project(self):
        metadata = self.metadata()
        result = dict(
            key=self.key,
            messages=(metadata.get("message_count") if isinstance(metadata.get("message_count"), int)
                      and metadata.get("message_count") >= 0 else max(1, int(self.stat.st_size / 200))),
            modified=self.stat.st_mtime,
            created=datetime.fromtimestamp(self.stat.st_mtime).isoformat(),
        )
        for stored, shown in (
            ("created_at", "created"),
            ("title", "title"),
            ("agent", "agent"),
        ):
            if metadata.get(stored):
                result[shown] = metadata[stored]
        result["memory_mode"] = metadata.get("memory_mode", "persistent")
        result["_created_by_app"] = str(metadata.get("created_by_app", "") or "")
        result["_closed"] = bool(metadata.get("closed", False))
        result["_lifecycle"] = str(metadata.get("lifecycle", "active") or "active")
        if "title" not in result:
            result["title"] = self.first_user_title() or self.key
        return result


class _SourceWindow:
    def __init__(self, log, prefix, exclude):
        self.log = log
        self.prefix = _safe_key(prefix)
        self.exclude = _safe_key(exclude) if exclude else ""

    def paths(self):
        matching = [
            path
            for path in self.log._dir.glob(f"{self.prefix}*.jsonl")
            if not self.exclude or path.stem != self.exclude
        ]
        return sorted(matching, key=lambda path: path.stat().st_mtime, reverse=True)[
            :50
        ]

    @staticmethod
    def tail(path):
        from itertools import islice

        with path.open(encoding="utf-8") as stream:
            prefix = []
            for line in islice(stream, 5):
                prefix.append(line)
                try:
                    row = json.loads(line.strip())
                    if row.get("_type") == "metadata" and row.get("memory_mode") in (
                        "incognito",
                        "temporary",
                    ):
                        return None
                except (json.JSONDecodeError, ValueError):
                    pass
            raw = "".join(prefix) + stream.read()
        return raw.splitlines()[-50:]

    def collect(self):
        records, used = [], 0
        for path in self.paths():
            if used == 5:
                break
            try:
                tail = self.tail(path)
            except OSError:
                continue
            if tail is None:
                continue
            used += 1
            records.extend(message_rows(tail))
        return sorted(records, key=lambda row: row.get("ts", ""))


class _SessionLineage:
    def __init__(self, log):
        self.log = log

    def rebuild(self):
        groups: dict = {}
        for path in sorted(self.log._dir.glob("dashboard_chat-*.jsonl")):
            try:
                with path.open(encoding="utf-8") as stream:
                    header = json.loads(stream.readline())
                tab = header.get("tab_id")
                if tab:
                    key = path.stem.replace("_", ":", 1)
                    groups.setdefault(tab, []).append(key)
            except Exception:
                continue
        self.log._tab_id_index = groups

    def read(self, key):
        tab = self.log.get_metadata(key).get("tab_id")
        if not tab:
            return self.log._read_messages(key)
        if not hasattr(self.log, "_tab_id_index"):
            self.log._tab_id_index = {}
        if tab not in self.log._tab_id_index:
            self.log._rebuild_tab_id_index()
            self.log._tab_id_index.setdefault(tab, [])
        records = [
            row
            for sibling in self.log._tab_id_index.get(tab, [])
            for row in self.log._read_messages(sibling)
        ]
        return records or self.log._read_messages(key)


class ConversationLog:
    """Session JSONL journal with cached views and recoverable retention."""

    def __init__(self, base_dir: Path | None = None):
        self._dir = base_dir or _sessions_dir()
        self._pages = JournalPages()
        self._msg_cache = self._pages.messages
        self._meta_cache = self._pages.metadata
        self._listing_cache: dict[str, dict] | None = None
        self._listing_stamp: tuple[int, int] | None = None

    def init(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir.joinpath(_safe_key(key) + ".jsonl")

    def summary_path(self, key: str) -> Path:
        return self._dir.joinpath(_safe_key(key) + ".summary.json")

    def read_summary(
        self, key: str, messages: list[dict] | None = None
    ) -> dict | None:
        path = self.summary_path(key)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        transcript = self._read_messages(key) if messages is None else messages
        return record if summary_holds(record, transcript) else None

    def write_summary(
        self,
        key: str,
        *,
        summary: str,
        summarized: int,
        reduced: int,
        reduced_cap: int = 600,
        messages: list[dict] | None = None,
    ) -> dict:
        transcript = self._read_messages(key) if messages is None else messages
        record = {
            "stamp": None,
            "turns": len(transcript),
            "summary": summary,
            "summarized": int(summarized),
            "reduced": int(reduced),
            "reduced_cap": int(reduced_cap),
            "digest": span_digest(transcript[: int(reduced)]),
            "created_at": datetime.now().isoformat(),
        }
        path = self._path(key)
        try:
            stat = path.stat()
            record["stamp"] = [stat.st_mtime_ns, stat.st_size]
        except OSError:
            pass
        if not summary_holds(record, transcript):
            raise ValueError("summary range does not match the transcript")
        atomic_write(
            self.summary_path(key),
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            fsync=True,
        )
        return record

    def model_view(
        self, key: str, messages: list[dict] | None = None
    ) -> list[dict]:
        transcript = self._read_messages(key) if messages is None else messages
        return model_view(transcript, self.read_summary(key, transcript))

    def has_log(self, key: str) -> bool:
        return self._path(key).exists()

    def append(
        self,
        key: str,
        role: str,
        content: str,
        tools: list[str] | None = None,
        source_thread: str | None = None,
        source_user: str | None = None,
        agent: str | None = None,
        tab_id: str | None = None,
        speaker: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        path = self._path(key)
        if not path.exists():
            self.init()
            header: dict[str, Any] = dict(
                _type="metadata",
                created_at=datetime.now().isoformat(),
                last_consolidated=0,
            )
            header.update(
                (name, value)
                for name, value in (("agent", agent), ("tab_id", tab_id))
                if value
            )
            path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        entry: dict[str, Any] = dict(
            role=role,
            content=content,
            ts=datetime.now().isoformat(),
            source_event_id=f"event:{secrets.token_hex(16)}",
        )
        entry.update(
            (name, value)
            for name, value in (
                ("tools", tools),
                ("source_thread", source_thread),
                ("source_user", source_user),
                ("speaker", speaker),
                ("meta", meta),
            )
            if value
        )
        with path.open("rb+") as stream:
            stream.seek(0, 2)
            if stream.tell():
                stream.seek(-1, 2)
                if stream.read(1) != b"\n":
                    stream.write(b"\n")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        self._invalidate_cache(key)
        self._maybe_rotate(path)
        self._notify_search_index(key)

    @staticmethod
    def _source_event(line: bytes, key: str) -> ConversationSourceEvent | None:
        raw = line.rstrip(b"\r\n")
        if not raw.strip():
            return None
        try:
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(message, dict) or message.get("_type") is not None:
            return None
        if not isinstance(message.get("role"), str) or not isinstance(
            message.get("content"), str
        ):
            return None
        digest = hashlib.sha256(raw).hexdigest()
        stored_id = message.get("source_event_id")
        source_event_id = (
            stored_id
            if isinstance(stored_id, str) and stored_id
            else f"legacy:{hashlib.sha256(key.encode() + b'\0' + raw).hexdigest()}"
        )
        return ConversationSourceEvent(source_event_id, digest, raw, message)

    def source_events(self, key: str) -> tuple[ConversationSourceEvent, ...]:
        """Return exact persisted transcript event bytes in authoritative order."""

        path = self._path(key)
        archive = _archive_dir(self._dir)
        paths = (
            sorted(archive.glob(f"{_safe_key(key)}__*.jsonl"))
            if archive.is_dir()
            else []
        )
        paths.append(path)
        if not any(candidate.is_file() for candidate in paths):
            return ()
        events: list[ConversationSourceEvent] = []
        seen: dict[str, str] = {}
        for candidate in paths:
            if not candidate.is_file():
                continue
            with candidate.open("rb") as stream:
                for line in stream:
                    event = self._source_event(line, key)
                    if event is None:
                        continue
                    prior = seen.get(event.source_event_id)
                    if prior is not None:
                        if prior != event.source_digest:
                            raise ValueError(
                                "conversation source identity was reused for different bytes"
                            )
                        continue
                    seen[event.source_event_id] = event.source_digest
                    events.append(event)
        return tuple(events)

    def resolve_source_event(self, key: str, source_event_id: str) -> bytes:
        """Resolve a source reference from ConversationLog or its retained archives."""

        paths = [self._path(key)]
        archive = _archive_dir(self._dir)
        if archive.is_dir():
            paths.extend(sorted(archive.glob(f"{_safe_key(key)}__*.jsonl"), reverse=True))
        for path in paths:
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                for line in stream:
                    event = self._source_event(line, key)
                    if event is not None and event.source_event_id == source_event_id:
                        if hashlib.sha256(event.raw_bytes).hexdigest() != event.source_digest:
                            raise ValueError("conversation source digest mismatch")
                        return event.raw_bytes
        raise KeyError(f"conversation source event {source_event_id!r} is unavailable")

    def flush_for_hypermid(
        self, session_keys: list[str] | tuple[str, ...] | None = None
    ) -> tuple[ConversationCheckpoint, ...]:
        """Durably flush transcript files and return digest checkpoints for cutover."""

        keys = (
            sorted({str(key) for key in session_keys if str(key)})
            if session_keys is not None
            else sorted(
                path.stem
                for path in self._dir.glob("*.jsonl")
                if path.is_file()
            )
        )
        checkpoints: list[ConversationCheckpoint] = []
        for key in keys:
            path = self._path(key)
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            material = bytearray()
            for event in self.source_events(key):
                material.extend(len(event.raw_bytes).to_bytes(8, "big"))
                material.extend(event.raw_bytes)
            payload = bytes(material)
            checkpoints.append(
                ConversationCheckpoint(
                    session_key=key,
                    source_digest=hashlib.sha256(payload).hexdigest(),
                    byte_count=len(payload),
                )
            )
        return tuple(checkpoints)

    def _notify_search_index(self, key: str) -> None:
        try:
            from gideon.cognition.session_search import note_changed

            note_changed(key, log=self)
        except Exception:
            logger.debug("session search update skipped for %s", key, exc_info=True)

    def recent(
        self, key: str, max_messages: int = 20, roles: set[str] | None = None
    ) -> list[dict]:
        messages = self._read_messages(key)
        selected = (
            [row for row in messages if row["role"] in roles] if roles else messages
        )
        return [
            dict(
                role=row["role"],
                content=row["content"],
                **({"speaker": row["speaker"]} if row.get("speaker") else {}),
            )
            for row in selected[-max_messages:]
        ]

    def recent_with_provenance(self, key: str, max_messages: int = 3) -> list[dict]:
        sourced = [row for row in self._read_messages(key) if row.get("source_thread")]
        return [
            dict(
                source_thread=row["source_thread"],
                ts=row.get("ts", "?"),
                snippet=row["content"][:150]
                + ("…" if len(row["content"]) > 150 else ""),
            )
            for row in sourced[-max_messages:]
        ]

    def get_unconsolidated(self, key: str) -> tuple[list[dict], int]:
        messages, metadata = self._read_messages(key), self._read_metadata(key)
        return messages[metadata.get("last_consolidated", 0) :], len(messages)

    def mark_consolidated(self, key: str, offset: int) -> None:
        path = self._path(key)
        if path.exists():
            document = JournalDocument.read(path)
            if document.lines:
                header = json.loads(document.lines[0])
                header.update(
                    last_consolidated=offset, updated_at=datetime.now().isoformat()
                )
                atomic_write(path, document.replace_header(header))
                self._invalidate_cache(key)

    def unconsolidated_count(self, key: str) -> int:
        count = len(self._read_messages(key))
        return max(0, count - self._read_metadata(key).get("last_consolidated", 0))

    def load_transcript(self, key: str) -> str:
        return "\n\n".join(
            f"{row.get('speaker') or row['role'].title()}: {row['content']}"
            for row in self._read_messages(key)
        )

    @staticmethod
    def _canonical_key(key: str) -> str:
        residual = re.sub(r"^(?:dashboard_)+", "", key)
        return "dashboard_" + residual if residual and residual != key else key

    def _list_session_records(self) -> list[dict]:
        if not self._dir.exists():
            return []
        root = self._dir.resolve()
        listing_path = root / "session_listing.json"
        try:
            listing_stat = listing_path.stat()
            listing_stamp = (listing_stat.st_mtime_ns, listing_stat.st_size)
        except OSError:
            listing_stamp = None
        cached = self._listing_cache
        if cached is None or listing_stamp != self._listing_stamp:
            cached = {}
            try:
                payload = json.loads(listing_path.read_text(encoding="utf-8"))
                if payload.get("version") == 1 and payload.get("root") == str(root):
                    rows = payload.get("entries", {})
                    if isinstance(rows, dict):
                        cached = rows
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            self._listing_cache = cached
            self._listing_stamp = listing_stamp

        refreshed: dict[str, dict] = {}
        summaries: dict[str, dict] = {}
        changed = False
        try:
            directory = os.scandir(root)
        except OSError:
            return []
        with directory:
            for entry in directory:
                if not entry.name.endswith(".jsonl"):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not os.path.isfile(entry.path):
                    continue
                identity = [
                    int(info.st_ino), int(info.st_size), int(info.st_mtime_ns)
                ]
                prior = cached.get(entry.name)
                if isinstance(prior, dict) and prior.get("identity") == identity:
                    item = prior.get("item")
                    if not isinstance(item, dict):
                        item = None
                        needs_refresh = True
                    else:
                        needs_refresh = False
                else:
                    item = None
                    needs_refresh = True
                if item is None:
                    path = Path(entry.path)
                    self._invalidate_cache(path.stem)
                    item = _SessionSummary(self, path, info).project()
                key = str(item.get("key", "") or "")
                mode = str(item.get("memory_mode", "persistent") or "persistent")
                if mode.strip().lower() in ("incognito", "temporary") or _live_restricted(key):
                    self._forget_search_rows(key)
                    changed = changed or prior is not None
                    continue
                refreshed[entry.name] = {"identity": identity, "item": item}
                item = dict(item, _identity=identity)
                canonical = self._canonical_key(key)
                existing = summaries.get(canonical)
                if existing is None or info.st_mtime >= existing["modified"]:
                    summaries[canonical] = item
                if needs_refresh:
                    changed = True
        if set(cached) != set(refreshed):
            changed = True
        if changed or listing_stamp is None:
            try:
                payload = {"version": 1, "root": str(root), "entries": refreshed}
                atomic_write(
                    listing_path,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
                info = listing_path.stat()
                self._listing_stamp = (info.st_mtime_ns, info.st_size)
                self._listing_cache = refreshed
            except OSError:
                logger.debug("session listing cache write skipped", exc_info=True)
        return sorted(
            summaries.values(), key=lambda item: item.get("modified", 0), reverse=True
        )

    def list_session_records(self) -> list[dict]:
        """Return listed session metadata with its private file identity for indexing."""
        return self._list_session_records()

    def list_sessions(self) -> list[dict]:
        return [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in self._list_session_records()
        ]

    def search_sessions(self, query: str, limit: int = 50) -> list[dict]:
        if not query or limit <= 0 or not self._dir.exists():
            return []
        import heapq

        search = JournalSearch(query, _TITLE_BOOST)
        ranked = []
        for position, item in enumerate(self.list_sessions()[:_SEARCH_SCAN_WINDOW]):
            if item.get("memory_mode") in (
                "incognito",
                "temporary",
            ) or _live_restricted(item.get("key", "")):
                continue
            try:
                score = search.score(self._path(item["key"]), item.get("title"))
            except OSError:
                continue
            if score is not None:
                ranked.append((score, -position, item))
        return [item for _, _, item in heapq.nlargest(limit, ranked)]

    def recent_from_source(
        self, source_prefix: str, exclude_key: str = "", max_messages: int = 20
    ) -> list[dict]:
        if not self._dir.exists():
            return []
        records = _SourceWindow(self, source_prefix, exclude_key).collect()
        return [
            dict(role=row["role"], content=row["content"])
            for row in records[-max_messages:]
        ]

    def read_messages(self, key: str) -> list[dict]:
        return self._read_messages(key)

    def has_session(self, key: str) -> bool:
        return self._path(key).exists()

    def read_messages_chained(self, key: str) -> list[dict]:
        return _SessionLineage(self).read(key)

    def _rebuild_tab_id_index(self) -> None:
        _SessionLineage(self).rebuild()

    def invalidate_tab_id_cache(self) -> None:
        index = getattr(self, "_tab_id_index", None)
        if index is not None:
            index.clear()

    def delete_session(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            self.summary_path(key).unlink(missing_ok=True)
            return False
        path.unlink()
        self.summary_path(key).unlink(missing_ok=True)
        self._invalidate_cache(key)
        self.invalidate_tab_id_cache()
        self._forget_search_rows(key)
        return True

    def _forget_search_rows(self, key: str) -> None:
        try:
            from gideon.engine import session_search

            aliases = {key.replace(":", "_", 1), key.replace("_", ":", 1), key}
            for alias in aliases:
                session_search.forget_session(alias, scope=str(self._dir.resolve()))
        except Exception:
            logger.debug("delete_session: FTS forget failed for %s", key, exc_info=True)

    def set_title(self, key: str, title: str) -> None:
        self.update_metadata(key, dict(title=title))

    def update_metadata(self, key: str, fields: dict) -> None:
        path = self._path(key)
        if not path.exists():
            return
        document = JournalDocument.read(path)
        if not document.lines:
            return
        try:
            header = json.loads(document.lines[0])
        except json.JSONDecodeError:
            return
        if header.get("_type") != "metadata":
            return
        header.update(fields)
        atomic_write(path, document.replace_header(header), fsync=True)
        self._invalidate_cache(key)
        self._notify_search_index(key)

    def _read_messages(self, key: str) -> list[dict]:
        return self._pages.read_messages(key, self._path(key))

    def _invalidate_cache(self, key: str) -> None:
        self._pages.invalidate(key)

    def get_metadata(self, key: str) -> dict:
        return self._read_metadata(key)

    def _read_metadata(self, key: str) -> dict:
        return self._pages.read_metadata(key, self._path(key))

    def sliding_window(
        self, key: str, keep_recent: int = 5
    ) -> tuple[list[dict], list[dict]]:
        messages = self._read_messages(key)
        boundary = max(len(messages) - 2 * keep_recent, 0)
        return messages[:boundary], messages[boundary:]

    def rewrite_session(
        self, key: str, messages: list[dict], *, reason: str = "compact"
    ) -> None:
        path = self._path(key)
        self.init()
        if path.exists():
            discarded = JournalDocument.read(path).removed_lines(messages)
            try:
                _archive_lines(key, discarded, reason=reason, base=self._dir)
            except Exception:
                logger.warning(
                    "Failed to archive dropped lines for %s", key, exc_info=True
                )
        prior = self.get_metadata(key) or {}
        header = dict(
            _type="metadata",
            created_at=prior.get("created_at", datetime.now().isoformat()),
            last_consolidated=prior.get("last_consolidated", 0),
            compacted_at=datetime.now().isoformat(),
        )
        if prior.get("memory_mode"):
            header["memory_mode"] = prior["memory_mode"]
        for field in ("title", "import_source", "import_key"):
            if prior.get(field):
                header[field] = prior[field]
        atomic_write(path, JournalDocument.render(header, messages))
        self._invalidate_cache(key)
        self._notify_search_index(key)

    def _maybe_rotate(self, path: Path) -> None:
        try:
            oversized = path.stat().st_size > _SESSION_MAX_BYTES
        except OSError:
            return
        if not oversized:
            return
        document = JournalDocument.read(path)
        if len(document.lines) <= _SESSION_KEEP_LINES:
            return
        header, discarded, kept = document.rotation(_SESSION_KEEP_LINES)
        try:
            _archive_lines(path.stem, discarded, reason="rotate", base=self._dir)
        except Exception:
            logger.warning(
                "Failed to archive rotated lines for %s", path.stem, exc_info=True
            )
        if header:
            try:
                metadata = json.loads(header)
                metadata.update(
                    last_consolidated=0, rotated_at=datetime.now().isoformat()
                )
                header = json.dumps(metadata) + "\n"
            except json.JSONDecodeError:
                pass
        atomic_write(path, header + "".join(kept))
        self._invalidate_cache(path.stem)
        logger.info(
            "Rotated session file %s (%d → %d lines)",
            path.name,
            len(document.lines),
            len(kept),
        )


def _count_tool_call_messages(messages: list[dict]) -> int:
    return sum(
        1
        for message in messages
        if (isinstance(message.get("tools"), list) and message["tools"])
        or message.get("role") in _TOOL_ROLES
    )


def _session_touched_sensitive(messages: list[dict]) -> bool:
    def tool_texts():
        for message in messages:
            tools = message.get("tools")
            if isinstance(tools, list):
                yield from (tool for tool in tools if isinstance(tool, str))
            if message.get("role") in _TOOL_ROLES:
                content = message.get("content", "")
                if isinstance(content, str):
                    yield content

    return any(
        pattern in text.lower()
        for text in tool_texts()
        for pattern in _SENSITIVE_TOOL_PATTERNS
    )


class HistoryConsolidator:
    """Coordinate extraction rounds, durable memory updates, and library maintenance."""

    def __init__(
        self,
        log: ConversationLog,
        memory: "MemoryJournal",
        sessions: "ConversationDirectory | None" = None,
        history_idle_secs: float = 3 * 3600,
        vector_store: "SemanticArchive | None" = None,
        migrated: bool = False,
        skills_loader: "ProcedureLibrary | None" = None,
        auto_skills_enabled: bool = False,
        auto_refine_enabled: bool = False,
        auto_min_tool_calls: int = 5,
        auto_similarity_threshold: float = 0.85,
    ) -> None:
        from gideon.cognition.consolidation_cycle import ConsolidationTasks

        self._log, self._memory, self._sessions = log, memory, sessions
        self._history_idle_secs = history_idle_secs
        self._vector_store = vector_store
        self._memory_service: MemoryService | None = None
        self._migrated, self._skills_loader = migrated, skills_loader
        self._auto_skills_enabled, self._auto_refine_enabled = (
            auto_skills_enabled,
            auto_refine_enabled,
        )
        self._auto_min_tool_calls, self._auto_similarity_threshold = (
            auto_min_tool_calls,
            auto_similarity_threshold,
        )
        self._schedule = ConsolidationTasks(self)
        self._running, self._tasks = self._schedule.running, self._schedule.tasks
        self._last_activity = self._schedule.activity
        self._history_consolidated = self._schedule.history
        self._prefs_offset = self._schedule.preferences
        self._consolidation_count, self._last_promote_monotonic = 0, 0.0
        self._quiesced = False
        self._schedule.recover()

    async def drain(self, timeout: float = 3.0) -> dict:
        return await self._schedule.drain(timeout)

    async def quiesce(self, timeout: float = 3.0) -> dict:
        """Fence new consolidation before draining already admitted tasks."""

        self._quiesced = True
        try:
            return await self.drain(timeout=timeout)
        except BaseException:
            self._quiesced = False
            raise

    def resume(self) -> None:
        self._quiesced = False

    @property
    def _svc(self):
        cached = self._memory_service
        if cached is not None:
            return cached
        from gideon.cognition.memory_service import MemoryService

        self._memory_service = MemoryService.over_vector_store(self._vector_store)
        return self._memory_service

    @property
    def _proactive_commitments(self) -> bool:
        from gideon.cognition.consolidation_cycle import memory_setting

        return bool(memory_setting("proactive_commitments"))

    @property
    def _proactive_commitments_max(self) -> int:
        from gideon.cognition.consolidation_cycle import memory_setting

        return max(int(memory_setting("proactive_commitments_max_per_day")), 1)

    @property
    def _holder_attribution(self) -> bool:
        from gideon.cognition.consolidation_cycle import memory_setting

        return bool(memory_setting("holder_attribution"))

    def maybe_consolidate(self, key: str) -> None:
        self._last_activity[key] = _time.time()
        if self._quiesced:
            return
        if key not in self._running:
            count = len(self._log._read_messages(key))
            pending = count - self._prefs_offset.get(key, 0)
            if pending >= _CONSOLIDATION_THRESHOLD:
                self._schedule.start(key, False, self._prefs_offset, count)

    async def consolidate_now(self, key: str) -> bool:
        if self._quiesced:
            return False
        admitted = key not in self._running
        if admitted:
            self._running.add(key)
            await self._consolidate(key, include_history=True)
        return admitted

    async def consolidate_session(self, key: str) -> bool:
        outcome = await self.consolidate_now(key)
        try:
            removed = self._svc.seal_session(key)
            if removed:
                logger.info(
                    "Sealed session %s — swept %d unpromoted record(s)", key, removed
                )
        except Exception:
            logger.debug("session seal failed for %s", key, exc_info=True)
        try:
            from gideon.cognition.memory_vault import mirror_after_consolidation

            mirror_after_consolidation(self._svc)
        except Exception:
            logger.debug("memory vault mirror failed for %s", key, exc_info=True)
        return outcome

    def check_idle_sessions(self) -> None:
        if self._quiesced:
            return
        now = _time.time()
        for key in self._schedule.idle(now):
            self._schedule.start(key, True, self._history_consolidated, now)

    async def _consolidate(self, key: str, include_history: bool = True) -> None:
        with single_flight(f"consolidate:{key}") as acquired:
            if acquired:
                await self._consolidate_locked(key, include_history=include_history)
            else:
                self._running.discard(key)
                logger.info(
                    "Consolidation for %s already running in another process — skipping",
                    key,
                )

    async def _consolidate_locked(self, key: str, include_history: bool = True) -> None:
        try:
            from gideon.cognition.consolidation_cycle import ConsolidationRound

            await ConsolidationRound(self, key, include_history).run()
        except Exception:
            logger.exception("Consolidation failed for %s", key)
            raise
        finally:
            self._running.discard(key)

    def _run_learning_curator(self) -> str:
        from gideon.cognition.consolidation_cycle import CuratorNotes
        from gideon.cognition.learning import curator as curator_mod
        from gideon.cognition.learning.usage import UsageStore
        from gideon.core.config.loader import AppConfig

        settings = AppConfig.load().learning
        if not (
            getattr(settings, "enabled", True)
            and getattr(settings, "curator_enabled", True)
        ):
            return ""
        notes = CuratorNotes()
        notes.measure(self)
        try:
            from gideon.cognition.learning.surfacing_events import SurfacingEventStore

            events = SurfacingEventStore()
            try:
                removed = events.prune()
            finally:
                events.close()
            if removed:
                logger.debug("pruned %d surfacing events past retention", removed)
        except Exception:
            logger.debug("Surfacing-event prune failed", exc_info=True)
        usage = UsageStore()
        try:
            records = []
            for kind in ("skill", "template"):
                records.extend(usage.list_kind(kind))
            if not records:
                return notes.render()
            candidates = tuple(
                curator_mod.Candidate(
                    kind=record.kind,
                    entity=record.entity,
                    last_used_at=record.last_used_at,
                    created_at=record.first_seen_at,
                    stability=min(1.0, record.used / 10.0),
                    pinned=record.pinned,
                    source_type=record.source_type,
                )
                for record in records
            )
            active = usage.active_days()
            aging = curator_mod.run_aging(
                list(candidates), active_dates=active, mode=""
            )
            curator_mod.file_review_proposals(aging)
            suggestions = curator_mod.promotion_suggestions(
                records, active_dates=active
            )
            filed = curator_mod.file_promotion_suggestions(suggestions)
            promotion_note = f"promotion suggestions filed={filed}" if filed else ""
            summary = aging.summary() if aging.changed or aging.review_proposals else ""
            return notes.render(summary, promotion_note)
        finally:
            usage.close()

    def _maybe_promote_episodic(self, memory) -> None:
        from gideon.core.config.loader import AppConfig

        settings = AppConfig.load().memory
        if not getattr(settings, "auto_promote_enabled", True):
            return
        from gideon.cognition.memory_service import service_for

        service = service_for(memory)
        if not service.can_vector_search:
            return
        self._consolidation_count += 1
        _, phase = divmod(
            self._consolidation_count, max(1, settings.auto_promote_every_n)
        )
        if phase:
            return
        now = _time.monotonic()
        cooling = (
            self._last_promote_monotonic and now - self._last_promote_monotonic < 1800
        )
        if cooling:
            return
        from gideon.core.concurrency import single_flight

        with single_flight("mem-promote-episodic") as acquired:
            if not acquired:
                return
            self._last_promote_monotonic = now
            changed = service.promote_episodic_patterns(
                max_promotions=settings.auto_promote_max_per_run
            )
        if not changed:
            return
        logger.info("Autonomous promotion: %d episodic→semantic", changed)
        try:
            sel().log_api_access(
                caller="consolidator:auto_promote",
                operation="memory.promote_episodic",
                outcome="allowed",
                resources=f"promoted={changed}",
            )
        except Exception:
            logger.debug("SEL audit failed for auto-promotion", exc_info=True)

    def _save_lessons(self, raw: object) -> None:
        if isinstance(raw, list) and self._svc.has_vector:
            from gideon.cognition.consolidation_cycle import ExtractedRecords

            ExtractedRecords(self).lessons(raw)

    async def _form_semantic_memory(self, result: dict, key: str) -> None:
        if self._vector_store is not None:
            from gideon.cognition.consolidation_cycle import SemanticFormationBatch

            await SemanticFormationBatch(self, key).apply(result)

    def _write_episodic_memory(self, result: dict, key: str) -> None:
        if not self._svc.has_vector:
            return
        items = result.get("episodic")
        if isinstance(items, list):
            from gideon.cognition.consolidation_cycle import ExtractedRecords

            ExtractedRecords(self).episodes(items, key)

    def _write_self_persona(self, result: dict, agent: str) -> None:
        traits = result.get("self_persona")
        if isinstance(traits, list):
            from gideon.cognition.consolidation_cycle import ExtractedRecords

            ExtractedRecords(self).persona(traits, agent)

    def _write_commitments(self, result: dict, agent: str, key: str) -> None:
        items = result.get("commitments")
        if isinstance(items, list):
            from gideon.cognition.consolidation_cycle import ExtractedRecords

            ExtractedRecords(self).commitments(items, agent, key)

    def _process_auto_skills(self, result: dict, key: str) -> None:
        if self._skills_loader is not None:
            from gideon.cognition.consolidation_cycle import SkillExtraction

            extraction = SkillExtraction(self, key)
            proposed = result.get("new_skill")
            if isinstance(proposed, dict):
                extraction.propose(proposed)
            if self._auto_refine_enabled:
                refined = result.get("refined_skill")
                if isinstance(refined, dict):
                    extraction.refine(refined)

    async def _call_llm(self, prompt: str) -> dict | None:
        if self._sessions:
            from gideon.integrations.llm_helpers import stream_and_collect_json

            acquired = False
            try:
                client, _, _ = await self._sessions.get_or_create(
                    BACKGROUND_KEY, agent="gideon-lite"
                )
                acquired = True
                return await stream_and_collect_json(client, prompt)
            except Exception:
                logger.warning("LLM consolidation call failed", exc_info=True)
                return None
            finally:
                if acquired:
                    self._sessions.release(BACKGROUND_KEY)
                    await self._sessions.recycle_background()
        logger.warning("LLM consolidation skipped — no session manager")
        return None
