"""Provider contract and read projections for local and shared trigger rows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gideon.automation.triggers.ownership import owner_authored

if TYPE_CHECKING:
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers.store import LoadedTrigger


class TriggerStoreProvider(ABC):
    """The persistence contract the trigger service talks to.

    Named ``…Provider`` to match every other app-facing contract in the tree
    (``TriggerSourceProvider``, ``InboxProvider``, ``SyncTransport``) and to leave the concrete
    name :class:`gideon.automation.triggers.store.TriggerStore` where its callers already point — a
    rename there would be churn across the gateway and CLI for no semantic gain.

    Five methods, which is exactly §3's list. ``load`` is the read primitive (it returns rows WITH
    their parse issues, because a broken row must stay visible), ``list_triggers`` is the flat
    listing view, and ``changed_on_disk`` is the change-notification: "another writer has touched
    this store since you last read it". A provider backed by a network store answers it from an
    etag or a version counter rather than an mtime — the contract is the QUESTION, not the file
    stat.
    """

    @property
    @abstractmethod
    def base_dir(self) -> Path:
        """Root for this store's sidecars (claims, watch state).

        Part of the contract rather than an implementation detail: ``service.tick`` derives the
        claim-store root from it precisely so a store rooted at a temp dir cannot write runtime
        state into the real home.
        """

    @abstractmethod
    def load(self) -> list["LoadedTrigger"]:
        """Every row, INCLUDING broken ones, each carrying its parse issues."""

    @abstractmethod
    def list_triggers(
        self, *, kind: str = "", include_broken: bool = True
    ) -> list["Trigger"]:
        """The rows as a flat list, optionally filtered by kind.

        A LISTING view — it is what a management surface renders, so it includes foreign rows.
        The arm path must use :func:`armable` instead.
        """

    @abstractmethod
    def get(self, trigger_id: str) -> "LoadedTrigger | None":
        """One row by id, or None."""

    @abstractmethod
    def upsert(self, trigger: "Trigger") -> "Trigger":
        """Insert or replace one row, read-modify-write under whatever lock the impl owns."""

    @abstractmethod
    def delete(self, trigger_id: str) -> bool:
        """Remove one row. Returns whether it was there."""

    @abstractmethod
    def changed_on_disk(self) -> bool:
        """Change-notify: has another writer touched this store since this instance read it?"""


def armable(store: Any) -> list[Trigger]:
    candidates = _ok_triggers(store.load())
    return owner_authored(candidates)


def all_rows(store: Any) -> list[Any]:
    from gideon.automation.triggers.registry import provider_rows

    return [*store.load(), *provider_rows()]


def _ok_triggers(rows: Any) -> list[Any]:
    admissible = filter(lambda row: getattr(row, "ok", True), rows)
    return [row.trigger for row in admissible]
