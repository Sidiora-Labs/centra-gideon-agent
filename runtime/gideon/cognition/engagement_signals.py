"""Persistent topic engagement, read-time decay and stable recency weighting."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from gideon.cognition.preference_facets import decay
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_STATE_FILE = "engagement.json"
DEFAULT_HALF_LIFE_DAYS = math.log(0.5) / math.log(0.9)
_SIGNAL_DELTA: dict[str, float] = {
    "favorite": 0.6,
    "open": 0.2,
    "reply": 0.3,
    "dismiss": -0.4,
}
_WEIGHT_FLOOR = 0.3
_WEIGHT_CEIL = 3.0
_NEUTRAL = 1.0
_WARMUP_SIGNALS = 2


def config_dir() -> Path:
    resolve = config_loader.config_dir
    return resolve()


class _EngagementDocument:
    def __init__(self, path):
        self.path = path

    def read(self, current):
        if not self.path.exists():
            return current
        try:
            document = json.loads(self.path.read_text())
            source = document.get("rows", {}) if isinstance(document, dict) else {}
            result = {}
            for topic, row in source.items():
                if isinstance(row, dict):
                    result[str(topic)] = row
            return result
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load engagement state, starting fresh")
            return {}

    def write(self, rows):
        try:
            document = json.dumps(dict(rows=rows), indent=2)
            atomic_write(self.path, document, mode=0o600)
        except OSError:
            logger.warning("Failed to save engagement state")


class _TopicWeight:
    def __init__(self, row, now, half_life):
        self.row, self.now, self.half_life = row, now, half_life

    def value(self):
        try:
            stored = float(self.row.get("weight", _NEUTRAL))
            age = (
                max(0.0, self.now - float(self.row.get("updated_at", self.now)))
                / 86400.0
            )
        except (TypeError, ValueError):
            return _NEUTRAL
        return _NEUTRAL + decay(stored - _NEUTRAL, age, self.half_life)

    def signal(self, change):
        weight = self.value() if self.row else _NEUTRAL
        updated = _clamp(weight + change, _WEIGHT_FLOOR, _WEIGHT_CEIL)
        count = int(self.row.get("count", 0)) if self.row else 0
        return dict(weight=updated, updated_at=self.now, count=count + 1)


class EngagementStore:
    def __init__(
        self, path: Path | None = None, *, half_life_days: float | None = None
    ) -> None:
        self._path = path or config_dir().joinpath(_STATE_FILE)
        self._half_life = DEFAULT_HALF_LIFE_DAYS
        if half_life_days and half_life_days > 0:
            self._half_life = half_life_days
        self._rows: dict[str, dict] = {}

    def load(self) -> None:
        self._rows = _EngagementDocument(self._path).read(self._rows)

    def save(self) -> None:
        _EngagementDocument(self._path).write(self._rows)

    def record(self, topic_key: str, signal: str, *, now: float) -> None:
        change = _SIGNAL_DELTA.get(signal)
        if change is not None and topic_key:
            record = _TopicWeight(self._rows.get(topic_key), now, self._half_life)
            self._rows[topic_key] = record.signal(change)

    def weight_for(self, topic_key: str, *, now: float) -> float:
        row = self._rows.get(topic_key)
        warmed = bool(row) and int(row.get("count", 0)) >= _WARMUP_SIGNALS
        return self._decayed_weight(row, now) if warmed else _NEUTRAL

    def _decayed_weight(self, row: dict, now: float) -> float:
        return _TopicWeight(row, now, self._half_life).value()

    def prune(self, *, now: float, min_deviation: float = 0.02) -> int:
        prior = len(self._rows)
        keep = {}
        for topic, row in self._rows.items():
            if not abs(self._decayed_weight(row, now) - _NEUTRAL) < min_deviation:
                keep[topic] = row
        self._rows.clear()
        self._rows.update(keep)
        return prior - len(keep)


def _clamp(v: float, lo: float, hi: float) -> float:
    upper = min(hi, v)
    return max(lo, upper)


def rank_by_engagement(items, *, recency_key, topic_key, store, now):
    scored = []
    for item in items:
        recency = recency_key(item)
        topic = topic_key(item)
        weight = store.weight_for(topic, now=now) if topic else _NEUTRAL
        scored.append((recency * weight, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item in scored]
