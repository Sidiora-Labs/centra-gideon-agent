"""JSONL document operations and cached projections for conversation journals."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


def message_rows(lines):
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if record.get("_type") != "metadata":
            yield record


@dataclass
class JournalDocument:
    lines: list[str]

    @classmethod
    def read(cls, path: Path):
        return cls(path.read_text(encoding="utf-8").splitlines(keepends=True))

    @property
    def has_header(self):
        return bool(self.lines and '"_type"' in self.lines[0])

    def replace_header(self, metadata):
        self.lines[0] = json.dumps(metadata) + "\n"
        return "".join(self.lines)

    def removed_lines(self, messages):
        retained = {json.dumps(message, sort_keys=True) for message in messages}
        start = int(self.has_header)
        removed = []
        for line in self.lines[start:]:
            if not line.strip():
                continue
            try:
                identity = json.dumps(json.loads(line), sort_keys=True)
            except (json.JSONDecodeError, ValueError):
                removed.append(line)
            else:
                if identity not in retained:
                    removed.append(line)
        return removed

    def rotation(self, count):
        header = self.lines[0] if self.has_header else ""
        return header, self.lines[int(bool(header)) : -count], self.lines[-count:]

    @staticmethod
    def render(metadata, messages):
        return "".join(json.dumps(record) + "\n" for record in (metadata, *messages))


class JournalPages:
    def __init__(self):
        self.messages: dict[str, tuple[float, list[dict]]] = {}
        self.metadata: dict[str, tuple[float, dict]] = {}

    def invalidate(self, key):
        for cache in (self.messages, self.metadata):
            cache.pop(key, None)

    @staticmethod
    def stamp(path, cache, key):
        if not path.exists():
            cache.pop(key, None)
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def read_messages(self, key, path):
        timestamp = self.stamp(path, self.messages, key)
        if timestamp is None:
            return []
        cached = self.messages.get(key)
        if cached and cached[0] == timestamp:
            return cached[1]
        text = path.read_text(encoding="utf-8")
        records = list(message_rows(text.splitlines()))
        self.messages[key] = timestamp, records
        return records

    def read_metadata(self, key, path):
        timestamp = self.stamp(path, self.metadata, key)
        if timestamp is None:
            return {}
        cached = self.metadata.get(key)
        if cached and cached[0] == timestamp:
            return cached[1]
        first = path.read_text(encoding="utf-8").partition("\n")[0].strip()
        if not first:
            return {}
        try:
            parsed = json.loads(first)
            result = parsed if parsed.get("_type") == "metadata" else {}
        except json.JSONDecodeError:
            result = {}
        self.metadata[key] = timestamp, result
        return result


class JournalSearch:
    def __init__(self, query, title_boost):
        self.needle = query.casefold()
        self.title_boost = title_boost

    def score(self, path, title):
        fragments = []
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = row.get("content") if isinstance(row, dict) else None
                if isinstance(text, str) and text:
                    fragments.append(text)
        characters = sum(map(len, fragments))
        hits = "\x00".join(fragments).casefold().count(self.needle) if fragments else 0
        title_hits = (title or "").casefold().count(self.needle)
        if not hits and not title_hits:
            return None
        return title_hits * self.title_boost + hits / math.sqrt(1 + characters / 1024)
