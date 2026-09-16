"""Durable provider-session identities and channel thread associations."""

import json
import logging
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_SESSION_MAP_FILE = "session_map.json"


def config_dir():
    return config_loader.config_dir()


def _path_home_gideon():
    try:
        return config_loader.config_dir()
    except Exception:
        return Path.home() / ".gideon"


def transcript_path(session_id: str):
    identifier = (session_id or "").strip()
    invalid = (
        not identifier
        or identifier.startswith(".")
        or any(separator in identifier for separator in ("/", "\\"))
    )
    if invalid:
        return None
    candidate = _path_home_gideon().joinpath("sessions", identifier + ".jsonl")
    return candidate if candidate.exists() else None


def _transcript_objects(text: str):
    for line in filter(None, map(str.strip, text.splitlines())):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def read_transcript(session_id: str) -> list[dict]:
    source = transcript_path(session_id)
    if source is None:
        return []
    try:
        document = source.read_text(encoding="utf-8")
    except OSError:
        logger.debug("transcript unreadable for %s", session_id, exc_info=True)
        return []
    return list(_transcript_objects(document))


class _SessionLinks:
    def __init__(self):
        self.entries: dict[str, dict] = {}
        self.threads: dict[str, str] = {}

    def index_threads(self):
        self.threads.clear()
        self.threads.update(
            (entry["thread_ts"], key)
            for key, entry in self.entries.items()
            if entry.get("thread_ts")
        )

    def merge(self, key, values, defaults):
        entry = self.entries.get(key)
        if not entry:
            entry = dict(defaults)
            self.entries[key] = entry
        entry.update(values)
        return entry

    def discard(self, key):
        removed = self.entries.pop(key, None)
        if removed:
            thread = removed.get("thread_ts")
            if thread and self.threads.get(thread) == key:
                self.threads.pop(thread)
        return bool(removed)

    def link(self, key, thread, channel):
        previous = self.entries.get(key)
        if previous and (previous.get("thread_ts"), previous.get("channel_id")) == (
            thread,
            channel,
        ):
            self.threads.setdefault(thread, key)
            return False
        old = previous.get("thread_ts") if previous else None
        if old and old != thread:
            self.threads.pop(old, None)
        self.merge(key, {"thread_ts": thread, "channel_id": channel}, {"sid": ""})
        self.threads[thread] = key
        return True


class SessionMap:
    def __init__(self) -> None:
        self._path = config_dir() / _SESSION_MAP_FILE
        self._links = _SessionLinks()
        self._load()

    @property
    def _data(self):
        return self._links.entries

    @_data.setter
    def _data(self, entries):
        self._links.entries = entries

    @property
    def _thread_to_session(self):
        return self._links.threads

    @_thread_to_session.setter
    def _thread_to_session(self, threads):
        self._links.threads = threads

    def _load(self) -> None:
        self._links.threads.clear()
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            self._links.entries = {}
            return
        if not isinstance(document, dict):
            self._links.entries = {}
            return
        accepted = {
            key: value
            for key, value in document.items()
            if isinstance(value, dict) and "sid" in value
        }
        self._links.entries.update(accepted)
        self._rebuild_thread_index()

    def _rebuild_thread_index(self) -> None:
        self._links.index_threads()

    def _save(self) -> None:
        atomic_write(self._path, json.dumps(self._links.entries))

    def get(self, key: str) -> str | None:
        keys = [key]
        prefix = "dashboard:dashboard_"
        if key.startswith(prefix):
            keys.append("dashboard:" + key[len(prefix) :])
        entry = next(
            (self._data[candidate] for candidate in keys if self._data.get(candidate)),
            None,
        )
        return (entry.get("sid") or None) if entry else None

    def _remove_entry(self, key: str) -> None:
        if self._links.discard(key):
            self._save()

    def set(self, key: str, sid: str, *, provider: str = "", cwd: str = "") -> None:
        values = {
            name: value
            for name, value in (("provider", provider), ("cwd", cwd))
            if value
        }
        values["sid"] = sid
        self._links.merge(
            key, values, {"sid": sid, "thread_ts": None, "channel_id": None}
        )
        self._save()

    def get_cwd(self, key: str) -> str:
        return (self._data.get(key) or {}).get("cwd", "")

    def get_provider(self, key: str) -> str:
        return (self._data.get(key) or {}).get("provider", "")

    def delete(self, key: str) -> None:
        self._remove_entry(key)

    def prune(self) -> int:
        expired = set(self._data).difference(
            key
            for key, entry in self._data.items()
            if entry.get("sid") or entry.get("thread_ts")
        )
        if expired:
            for key in expired:
                self._data.pop(key)
            self._rebuild_thread_index()
            self._save()
            logger.info("Pruned %d stale session map entries", len(expired))
        return len(expired)

    def set_channel_link(
        self, key: str, thread_ts: str, channel_id: str | None
    ) -> None:
        if self._links.link(key, thread_ts, channel_id):
            self._save()

    def get_channel_link(self, key: str) -> tuple[str | None, str | None]:
        entry = self._data.get(key) or {}
        return entry.get("thread_ts"), entry.get("channel_id")

    def get_session_for_thread(self, thread_ts: str) -> str | None:
        return self._links.threads.get(thread_ts)

    def find_key_by_sid(self, session_id: str) -> str | None:
        return next(
            (
                key
                for key, entry in self._data.items()
                if (entry.get("sid") if isinstance(entry, dict) else entry)
                == session_id
            ),
            None,
        )
