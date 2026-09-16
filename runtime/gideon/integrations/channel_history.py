"""Bounded channel context with optional, restart-safe observation journals."""

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from threading import RLock

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_DEFAULT_MAX_ENTRIES = 50
_DEFAULT_TTL_SECS = 300
OBSERVE_MAX_ENTRIES = 200
OBSERVE_TTL_SECS = 604800


@dataclass
class HistoryEntry:
    user: str
    text: str
    thread_ts: str | None = None
    timestamp: float = field(default_factory=time.monotonic)
    wall_ts: float | None = None


@dataclass(frozen=True)
class _HistoryClock:
    monotonic: float = field(default_factory=time.monotonic)
    wall: float = field(default_factory=time.time)

    def restore(self, record: dict) -> HistoryEntry:
        return HistoryEntry(
            user=record.get("user", ""),
            text=record.get("text", ""),
            thread_ts=record.get("thread_ts"),
            timestamp=self.monotonic + record["ts"] - self.wall,
            wall_ts=record["ts"],
        )

    def line(self, entry: HistoryEntry, names: dict[str, str]) -> str:
        elapsed = (
            self.wall - entry.wall_ts
            if entry.wall_ts is not None
            else self.monotonic - entry.timestamp
        )
        seconds = int(elapsed)
        age = f"{seconds}s ago" if seconds < 60 else f"{seconds // 60}m ago"
        clipped = entry.text[:300] + ("…" if len(entry.text) > 300 else "")
        return f"  {names.get(entry.user) or entry.user} ({age}): {clipped}"


@dataclass(frozen=True)
class _Retention:
    capacity: int
    ttl: int
    observed: bool

    def trim(self, entries: deque[HistoryEntry]) -> None:
        clock = time.time if self.observed else time.monotonic
        cutoff = clock() - self.ttl
        attribute = "wall_ts" if self.observed else "timestamp"
        for entry in tuple(entries):
            stamp = getattr(entry, attribute)
            if stamp is None or stamp >= cutoff:
                break
            entries.popleft()


def _read_journal(path: Path, ttl: int) -> tuple[list[HistoryEntry], bool]:
    clock = _HistoryClock()
    cutoff = clock.wall - ttl
    records = []
    expired = False
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("history entry must be an object")
                stamp = record.get("ts")
                if stamp is None:
                    continue
                if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                    raise ValueError("history timestamp must be finite")
                if not isinstance(record.get("user", ""), str) or not isinstance(
                    record.get("text", ""), str
                ):
                    raise ValueError("history message fields must be strings")
                if stamp < cutoff:
                    expired = True
                else:
                    records.append(clock.restore(record))
            except (ValueError, TypeError):
                logger.warning("Corrupt JSONL line %d in %s — skipping", number, path)
    return records, expired


class ChannelHistory:
    def __init__(
        self,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl_secs: int = _DEFAULT_TTL_SECS,
        observe_max_entries: int = OBSERVE_MAX_ENTRIES,
        observe_ttl_secs: int = OBSERVE_TTL_SECS,
        history_dir: Path | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_secs = ttl_secs
        self._observe_max_entries = observe_max_entries
        self._observe_ttl_secs = observe_ttl_secs
        self._history_dir = history_dir
        self._channels: dict[str, deque[HistoryEntry]] = {}
        self._observe_channels: set[str] = set()
        self._user_names: dict[str, str] = {}
        self._lock = RLock()

    def _retention(self, channel_id: str | None) -> _Retention:
        if channel_id and channel_id in self._observe_channels:
            return _Retention(self._observe_max_entries, self._observe_ttl_secs, True)
        return _Retention(self._max_entries, self._ttl_secs, False)

    def _resize(self, channel_id: str, capacity: int) -> None:
        buffer = self._channels.get(channel_id)
        if buffer is not None and buffer.maxlen != capacity:
            self._channels[channel_id] = deque(buffer, maxlen=capacity)

    def set_user_name(self, user_id: str, name: str) -> None:
        if user_id and name:
            with self._lock:
                self._user_names.update({user_id: name})

    def set_observe(self, channel_id: str) -> None:
        with self._lock:
            self._observe_channels.add(channel_id)
            self._resize(channel_id, self._observe_max_entries)
            self._load_observe(channel_id)

    def unset_observe(self, channel_id: str) -> None:
        with self._lock:
            self._observe_channels.discard(channel_id)
            self._resize(channel_id, self._max_entries)
            path = self._observe_path(channel_id)
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Failed to remove history file %s", path, exc_info=True
                    )

    def push(
        self, channel_id: str, user: str, text: str, thread_ts: str | None = None
    ) -> None:
        if not (channel_id and text):
            return
        with self._lock:
            policy = self._retention(channel_id)
            buffer = self._channels.setdefault(
                channel_id, deque(maxlen=policy.capacity)
            )
            policy.trim(buffer)
            entry = HistoryEntry(
                user, text, thread_ts, wall_ts=time.time() if policy.observed else None
            )
            buffer.append(entry)
            if policy.observed:
                self._append_to_disk(channel_id, entry)

    def context_for(self, channel_id: str, thread_ts: str | None = None) -> str:
        with self._lock:
            buffer = self._channels.get(channel_id)
            if not buffer:
                return ""
            self._evict(buffer, channel_id)
            if not buffer:
                return ""
            requested_thread = thread_ts if thread_ts else None
            matching = [
                entry for entry in buffer if entry.thread_ts == requested_thread
            ]
            if thread_ts and not matching:
                return ""
            clock = _HistoryClock()
            body = "\n".join(clock.line(entry, self._user_names) for entry in matching)
            section = "[Current thread:]\n" if thread_ts else ""
            return (
                f"[Recent channel messages for context:]\n{section}{body}"
                "\n[End of channel context]\n\n"
            )

    def clear(self, channel_id: str) -> None:
        with self._lock:
            self._channels.pop(channel_id, None)

    @property
    def channel_count(self) -> int:
        with self._lock:
            return len(self._channels)

    def entry_count(self, channel_id: str) -> int:
        with self._lock:
            return len(self._channels.get(channel_id, ()))

    def _observe_path(self, channel_id: str) -> Path | None:
        if self._history_dir is None:
            return None
        from gideon.engine.hooks import is_sensitive_path

        root = self._history_dir.resolve()
        candidate = (root / f"{channel_id}.jsonl").resolve()
        if not candidate.is_relative_to(root):
            logger.warning("Refusing unsafe history path for channel %s", channel_id)
        elif is_sensitive_path(str(candidate)):
            logger.warning("Refusing sensitive history path for channel %s", channel_id)
        else:
            return candidate
        return None

    @staticmethod
    def _entry_to_jsonl(entry: HistoryEntry) -> str:
        record = dict(
            user=entry.user,
            text=entry.text,
            thread_ts=entry.thread_ts,
            ts=entry.wall_ts,
        )
        return json.dumps(record, ensure_ascii=False)

    def _append_to_disk(self, channel_id: str, entry: HistoryEntry) -> None:
        path = self._observe_path(channel_id)
        if path is not None:
            encoded = self._entry_to_jsonl(entry) + "\n"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open(mode="a", encoding="utf-8") as sink:
                    sink.write(encoded)
            except OSError:
                logger.warning(
                    "Failed to append to history file %s", path, exc_info=True
                )

    def _load_observe(self, channel_id: str) -> None:
        with self._lock:
            path = self._observe_path(channel_id)
            if path is None or not path.exists():
                return
            try:
                restored, expired = _read_journal(path, self._observe_ttl_secs)
            except OSError:
                logger.warning("Failed to read history file %s", path, exc_info=True)
                return
            current = self._channels.get(channel_id)
            capacity = (
                current.maxlen if current is not None else self._observe_max_entries
            )
            self._channels[channel_id] = deque(
                chain(restored, current or ()), maxlen=capacity
            )
            logger.info(
                "Loaded %d entries for channel %s from disk", len(restored), channel_id
            )
            if expired:
                self._compact(channel_id)

    def _compact(self, channel_id: str) -> None:
        with self._lock:
            path = self._observe_path(channel_id)
            if path is None:
                return
            entries = self._channels.get(channel_id)
            try:
                if entries:
                    data = "".join(
                        self._entry_to_jsonl(entry) + "\n"
                        for entry in entries
                        if entry.wall_ts is not None
                    )
                    atomic_write(path, data)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                if entries:
                    logger.warning(
                        "Failed to compact history file %s", path, exc_info=True
                    )

    def _evict(self, buf: deque[HistoryEntry], channel_id: str | None = None) -> None:
        self._retention(channel_id).trim(buf)
