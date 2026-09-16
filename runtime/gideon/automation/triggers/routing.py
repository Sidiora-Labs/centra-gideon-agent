"""Merge provider records for arming and verify writes against the store that served them."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.triggers.registry import (
    provider_rows_by_store,
    registered_stores,
)

logger = logging.getLogger(__name__)
_QUARANTINE: dict[str, str] = {}


def quarantine_report() -> dict[str, str]:
    return _QUARANTINE.copy()


def clear_quarantine() -> None:
    _QUARANTINE.clear()


def _quarantine(name: str, reason: str) -> None:
    if name not in _QUARANTINE:
        _QUARANTINE[name] = reason
        logger.warning(
            "trigger provider %r cannot persist a reschedule (%s) — withholding its rows from arming until restart; they remain visible on the Automations page",
            name,
            reason,
        )


def _row_id(row: Any) -> str:
    record = getattr(row, "trigger", None)
    return str(getattr(record, "id", "") or "")


def _next_fire_of(row: Any) -> str:
    record = getattr(row, "trigger", row)
    return str(getattr(record, "next_fire_at", "") or "")


def serving_store(trigger_id: str) -> tuple[str, Any] | None:
    if trigger_id and registered_stores():
        for name, store, rows in provider_rows_by_store():
            if any(_row_id(row) == trigger_id for row in rows):
                return name, store
    return None


def _read_back(name: str, store: Any, trigger_id: str) -> Any:
    try:
        row = store.get(trigger_id)
    except Exception:
        logger.debug(
            "trigger provider %r raised on get(%r) during write-back", name, trigger_id
        )
        row = None
    return row


def _target(trigger_id: str, native: Any) -> tuple[str, Any] | None:
    provider = serving_store(trigger_id)
    if provider is not None:
        try:
            local = native.get(trigger_id) if native is not None else None
        except Exception:
            logger.debug(
                "could not check the native store for %r while routing", trigger_id
            )
        else:
            if local is not None:
                return None
    return provider


@dataclass(frozen=True)
class ProviderWrite:
    name: str
    store: Any
    identity: str

    def save(self, trigger: Any) -> Any:
        intended = str(getattr(trigger, "next_fire_at", "") or "")
        try:
            answer = self.store.upsert(trigger)
        except Exception:
            _quarantine(self.name, "upsert() raised")
            return trigger
        persisted = _read_back(self.name, self.store, self.identity)
        if persisted is None:
            _quarantine(self.name, "the upserted row could not be read back")
        else:
            actual = _next_fire_of(persisted)
            if actual != intended:
                _quarantine(
                    self.name, f"next_fire_at stayed {actual!r}, wanted {intended!r}"
                )
        return trigger if answer is None else answer

    def remove(self) -> bool:
        try:
            removed = bool(self.store.delete(self.identity))
        except Exception:
            _quarantine(self.name, "delete() raised")
            return False
        if _read_back(self.name, self.store, self.identity) is not None:
            _quarantine(self.name, "delete() left the row in place")
        return removed


def route_upsert(trigger: Any, *, native: Any = None) -> Any:
    identity = str(getattr(trigger, "id", "") or "")
    destination = _target(identity, native)
    return (
        ProviderWrite(*destination, identity).save(trigger)
        if destination is not None
        else None
    )


def route_delete(trigger_id: str, *, native: Any = None) -> bool | None:
    destination = _target(trigger_id, native)
    return (
        ProviderWrite(*destination, trigger_id).remove()
        if destination is not None
        else None
    )


@dataclass
class ArmingRows:
    rows: list[Any]
    origin: dict[str, tuple[str, Any]] = field(default_factory=dict)

    def merge(self) -> None:
        occupied = {_row_id(row) for row in self.rows}
        for name, store, rows in provider_rows_by_store(skip=_QUARANTINE):
            for row in rows:
                identity = _row_id(row)
                if not identity:
                    logger.warning(
                        "trigger provider %r served a row with no id; skipping", name
                    )
                elif identity in occupied:
                    logger.warning(
                        "trigger id %r conflicts with trigger provider %r; arming the earlier row only. Rename one of them.",
                        identity,
                        name,
                    )
                else:
                    occupied.add(identity)
                    self.origin[identity] = name, store
                    self.rows.append(row)


class RoutingTriggerStore:
    def __init__(self, native: Any) -> None:
        self._native = native
        self._origin: dict[str, tuple[str, Any]] = {}

    @property
    def native(self) -> Any:
        return self._native

    @property
    def base_dir(self) -> Any:
        local = self.native
        return getattr(local, "base_dir", None)

    def load(self) -> list[Any]:
        snapshot = ArmingRows(list(self.native.load()))
        snapshot.merge()
        self._origin = snapshot.origin
        return snapshot.rows

    def get(self, trigger_id: str) -> Any:
        local = self.native.get(trigger_id)
        if local is not None:
            return local
        source = self._origin.get(trigger_id) or serving_store(trigger_id)
        return _read_back(*source, trigger_id) if source is not None else None

    def list_triggers(
        self, *, kind: str = "", include_broken: bool = True
    ) -> list[Any]:
        result = list(
            self.native.list_triggers(kind=kind, include_broken=include_broken)
        )
        local_ids = {str(getattr(row, "id", "") or "") for row in result}
        for _, _, rows in provider_rows_by_store(skip=_QUARANTINE):
            for row in rows:
                record = getattr(row, "trigger", None)
                identity = _row_id(row)
                if record is None or not identity or identity in local_ids:
                    continue
                allowed_kind = not kind or str(getattr(record, "kind", "")) == kind
                allowed_state = include_broken or getattr(row, "ok", True)
                if allowed_kind and allowed_state:
                    result.append(record)
        return result

    def changed_on_disk(self) -> bool:
        changed = getattr(self.native, "changed_on_disk", lambda: False)
        if changed():
            return True
        candidates = (
            (name, store)
            for name, store in registered_stores().items()
            if name not in _QUARANTINE
        )
        for name, store in candidates:
            try:
                observed = store.changed_on_disk()
            except Exception:
                logger.debug("trigger provider %r raised on changed_on_disk()", name)
                continue
            if observed:
                return True
        return False

    def upsert(self, trigger: Any) -> Any:
        persist = self.native.upsert
        return persist(trigger)

    def delete(self, trigger_id: str) -> bool:
        removed = self.native.delete(trigger_id)
        return bool(removed)


def routed(store: Any) -> Any:
    needs_wrapper = not isinstance(store, RoutingTriggerStore) and bool(
        registered_stores()
    )
    return RoutingTriggerStore(store) if needs_wrapper else store
