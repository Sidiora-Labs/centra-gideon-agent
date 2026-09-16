"""Durable template candidates and per-shape nudge state."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gideon.automation.workflows.store import workflows_dir
from gideon.automation.workflows.template_pipeline import Candidate, NudgeState
from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_NUDGES_FILE = "template_nudges.json"
_CANDIDATES_FILE = "template_candidates.json"
_TURN_KEY = "__turn__"


def _path(filename: str) -> Path:
    return workflows_dir() / filename


def _read(filename: str) -> dict[str, Any]:
    try:
        value = json.loads(_path(filename).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(filename: str, data: dict[str, Any]) -> None:
    destination = _path(filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(destination, json.dumps(data, indent=2, sort_keys=True))


class TemplateIndex:
    def __init__(self, filename: str):
        self.filename = filename

    def replace(self, key: str, value: Any) -> None:
        document = _read(self.filename)
        document.update({key: value})
        _write(self.filename, document)

    def entries(self):
        document = _read(self.filename)
        return ((key, document.get(key)) for key in sorted(document))

    @staticmethod
    def clock(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) else 0


def load_nudge(shape: str) -> NudgeState:
    record = _read(_NUDGES_FILE).get(shape)
    if not isinstance(record, dict):
        return NudgeState(shape=shape)
    values = {key: bool(record.get(key)) for key in ("declined", "accepted")}
    values.update(
        occurrences=int(record.get("occurrences") or 0),
        last_offered_turn=int(record.get("last_offered_turn", -1)),
    )
    return NudgeState(shape=shape, **values)


def save_nudge(state: NudgeState) -> None:
    if state.shape:
        TemplateIndex(_NUDGES_FILE).replace(state.shape, state.to_dict())


def all_nudges() -> list[NudgeState]:
    shapes = (
        name for name, _ in TemplateIndex(_NUDGES_FILE).entries() if name != _TURN_KEY
    )
    return list(map(load_nudge, shapes))


def bump_turn() -> int:
    document = _read(_NUDGES_FILE)
    document[_TURN_KEY] = TemplateIndex.clock(document.get(_TURN_KEY)) + 1
    _write(_NUDGES_FILE, document)
    return document[_TURN_KEY]


def current_turn() -> int:
    return TemplateIndex.clock(_read(_NUDGES_FILE).get(_TURN_KEY))


def _as_candidate(entry: dict[str, Any]) -> Candidate | None:
    if not isinstance(entry, dict) or not entry.get("name"):
        return None
    text = {
        key: str(entry.get(key) or default)
        for key, default in (
            ("origin_goal", ""),
            ("scope", "session"),
            ("session_id", ""),
        )
    }
    document = entry.get("spec")
    return Candidate(
        name=str(entry["name"]),
        spec=document if isinstance(document, dict) else {},
        reuses=int(entry.get("reuses") or 0),
        **text,
    )


def save_candidate(candidate: Candidate) -> None:
    if candidate.name:
        TemplateIndex(_CANDIDATES_FILE).replace(candidate.name, candidate.to_dict())


def load_candidates(*, session_id: str = "") -> list[Candidate]:
    candidates = (
        _as_candidate(value or {})
        for _, value in TemplateIndex(_CANDIDATES_FILE).entries()
    )
    return [
        candidate
        for candidate in candidates
        if candidate is not None
        and not (
            candidate.scope == "session"
            and session_id
            and candidate.session_id != session_id
        )
    ]


def get_candidate(name: str) -> Candidate | None:
    return _as_candidate(_read(_CANDIDATES_FILE).get(name) or {}) if name else None
