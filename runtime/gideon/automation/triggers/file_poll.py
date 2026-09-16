"""Persist file-watch baselines and collect trigger payloads from each poll."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from gideon.automation.triggers.file_watch import (
    WatchState,
    changed_files,
    fire_payload,
    should_fire,
)
from gideon.automation.triggers.provider import armable

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECS = 60.0
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class WatchJournal:
    path: Path

    def read(self) -> WatchState:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return WatchState()
        return WatchState.from_dict(record)

    def publish(self, state: WatchState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staged = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with staged.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(state.to_dict()))
            staged.replace(self.path)
        finally:
            staged.unlink(missing_ok=True)


@dataclass(frozen=True)
class FilePoll:
    trigger: Any
    base_dir: Path | str | None

    def collect(self) -> dict[str, Any] | None:
        spec = self.trigger.spec if isinstance(self.trigger.spec, dict) else {}
        paths = spec.get("paths")
        if not paths:
            logger.debug("file trigger %s has no paths; skipping", self.trigger.id)
            return None
        previous = load_state(self.trigger.id, base_dir=self.base_dir)
        delta, current = changed_files(list(paths), previous)
        save_state(self.trigger.id, current, base_dir=self.base_dir)
        if should_fire(delta):
            payload = fire_payload(
                delta, trigger_id=self.trigger.id, trigger_name=self.trigger.name
            )
            payload["dedup"] = str(spec.get("dedup") or "content")
            return payload
        return None


def _watch_dir(base_dir: Path | str | None) -> Path:
    from gideon.core.config.loader import config_dir

    directory = Path(base_dir) if base_dir else config_dir()
    return directory / "trigger-watch"


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    filename = (_SAFE_RE.sub("-", trigger_id) or "watch") + ".json"
    return _watch_dir(base_dir) / filename


def load_state(trigger_id: str, *, base_dir: Path | str | None = None) -> WatchState:
    return WatchJournal(_state_path(trigger_id, base_dir)).read()


def save_state(
    trigger_id: str, state: WatchState, *, base_dir: Path | str | None = None
) -> None:
    WatchJournal(_state_path(trigger_id, base_dir)).publish(state)


def file_triggers(store: Any) -> list[Any]:
    return [
        trigger
        for trigger in armable(store)
        if trigger.kind == "file" and trigger.enabled
    ]


def poll_one(
    trigger: Any, *, base_dir: Path | str | None = None
) -> dict[str, Any] | None:
    return FilePoll(trigger, base_dir).collect()


def poll_all(store: Any, *, base_dir: Path | str | None = None) -> list[dict[str, Any]]:
    pending = []
    for trigger in file_triggers(store):
        try:
            event = poll_one(trigger, base_dir=base_dir)
        except Exception:
            logger.warning("file-watch poll failed for %s", trigger.id, exc_info=True)
        else:
            if event is not None:
                pending.append(event)
    return pending
