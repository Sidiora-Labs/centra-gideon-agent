"""Persist freshness and refresh bound surfaces only when their cache expires."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.automation.triggers.provider import armable

logger = logging.getLogger(__name__)
DEFAULT_TTL_SECS = 600.0
MIN_REFRESH_INTERVAL_SECS = 60.0
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Freshness:
    last_refresh_at: float = 0.0
    refreshes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(last_refresh_at=self.last_refresh_at, refreshes=self.refreshes)

    @classmethod
    def from_dict(cls, raw: Any) -> Freshness:
        if isinstance(raw, dict):
            try:
                timestamp, count = raw.get("last_refresh_at", 0.0), raw.get(
                    "refreshes", 0
                )
                return cls(float(timestamp or 0.0), int(count or 0))
            except (TypeError, ValueError):
                pass
        return cls()


@dataclass
class ViewDecision:
    refresh: bool
    reason: str = ""
    payload: dict[str, Any] | None = None
    age_secs: float = 0.0


@dataclass(frozen=True)
class FreshnessJournal:
    path: Path

    def read(self) -> Freshness:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Freshness()
        return Freshness.from_dict(document)

    def write(self, identity: str, state: Freshness) -> None:
        from gideon.core.atomic_write import atomic_write

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            document = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
            atomic_write(self.path, document)
        except OSError:
            logger.debug(
                "pull_on_view: could not persist freshness for %s",
                identity,
                exc_info=True,
            )


@dataclass(frozen=True)
class SurfaceRefresh:
    trigger: Any
    previous: Freshness
    observed_at: float
    ttl: float

    @property
    def age(self) -> float:
        return (
            self.observed_at - self.previous.last_refresh_at
            if self.previous.last_refresh_at
            else float("inf")
        )

    @property
    def cached(self) -> bool:
        return bool(self.previous.last_refresh_at) and self.age < self.ttl

    def decision(self) -> ViewDecision:
        age = self.age
        if self.cached:
            return ViewDecision(
                False,
                f"served cache ({int(age)}s old, TTL {int(self.ttl)}s)",
                age_secs=age,
            )
        reason = (
            f"stale ({int(age)}s > {int(self.ttl)}s)"
            if self.previous.last_refresh_at
            else "first render"
        )
        spec = self.trigger.spec if isinstance(self.trigger.spec, dict) else {}
        payload = dict(
            trigger_id=self.trigger.id,
            trigger_name=self.trigger.name,
            kind="view",
            surface_binding=str(spec.get("surface_binding", "") or ""),
            refresh_number=self.previous.refreshes + 1,
        )
        return ViewDecision(True, reason, payload, 0.0 if age == float("inf") else age)


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    from gideon.core.config.loader import config_dir

    directory = Path(base_dir) if base_dir else config_dir()
    filename = (_SAFE_RE.sub("-", trigger_id) or "view") + ".json"
    return directory / "trigger-view" / filename


def load_freshness(trigger_id: str, *, base_dir: Path | str | None = None) -> Freshness:
    try:
        return FreshnessJournal(_state_path(trigger_id, base_dir)).read()
    except (OSError, json.JSONDecodeError):
        return Freshness()


def save_freshness(
    trigger_id: str, state: Freshness, *, base_dir: Path | str | None = None
) -> None:
    FreshnessJournal(_state_path(trigger_id, base_dir)).write(trigger_id, state)


def ttl_for(trigger: Any) -> float:
    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    try:
        value = float(spec.get("ttl_secs", 0) or 0)
    except (TypeError, ValueError):
        value = 0.0
    return max(value or DEFAULT_TTL_SECS, MIN_REFRESH_INTERVAL_SECS)


def bound_triggers(store: Any, *, surface: str) -> list[Any]:
    if not surface:
        return []
    matches = []
    for trigger in armable(store):
        if trigger.kind == "view" and trigger.enabled:
            spec = trigger.spec if isinstance(trigger.spec, dict) else {}
            binding = str(spec.get("surface_binding", "") or "").strip()
            if binding == surface:
                matches.append(trigger)
    return matches


def on_render(
    trigger: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    persist: bool = True,
) -> ViewDecision:
    prior = load_freshness(trigger.id, base_dir=base_dir)
    refresh = SurfaceRefresh(trigger, prior, now, ttl_for(trigger))
    if persist and not refresh.cached:
        save_freshness(
            trigger.id, Freshness(now, prior.refreshes + 1), base_dir=base_dir
        )
    return refresh.decision()


def renders(
    store: Any, *, surface: str, now: float, base_dir: Path | str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    refreshes, cached = [], []
    for trigger in bound_triggers(store, surface=surface):
        try:
            result = on_render(trigger, now=now, base_dir=base_dir)
        except Exception:
            logger.warning(
                "view trigger %s failed on render", trigger.id, exc_info=True
            )
            result = ViewDecision(False, "the binding raised")
        if result.refresh and result.payload is not None:
            refreshes.append(result.payload)
        else:
            cached.append(dict(trigger_id=trigger.id, reason=result.reason))
    return refreshes, cached
