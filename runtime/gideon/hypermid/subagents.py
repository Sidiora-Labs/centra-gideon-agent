from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Mapping

from .config import ContextMode
from .foundation import Cursor, Digest, Id, Scope

MAX_SNAPSHOT_ITEMS = 100_000
MAX_CONTRIBUTION_BYTES = 1_048_576


class SubagentContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelBudget:
    context_window_tokens: int
    reserved_output_tokens: int
    max_input_tokens: int
    max_items: int
    max_images: int
    baseline_tokens: int
    delta_tokens: int
    tail_tokens: int
    confidence: str

    def __post_init__(self) -> None:
        numbers = (
            self.context_window_tokens,
            self.reserved_output_tokens,
            self.max_input_tokens,
            self.max_items,
            self.max_images,
            self.baseline_tokens,
            self.delta_tokens,
            self.tail_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in numbers
        ):
            raise SubagentContextError(
                "INVALID_BUDGET", "budget values must be integers"
            )
        region_tokens = self.baseline_tokens + self.delta_tokens + self.tail_tokens
        if (
            self.context_window_tokens < 1
            or self.reserved_output_tokens < 0
            or self.reserved_output_tokens > self.context_window_tokens
            or self.max_input_tokens < 0
            or self.max_input_tokens
            > self.context_window_tokens - self.reserved_output_tokens
            or self.max_items < 1
            or self.max_images < 0
            or min(self.baseline_tokens, self.delta_tokens, self.tail_tokens) < 0
            or region_tokens > self.max_input_tokens
            or self.confidence not in {"measured", "calibrated", "conservative"}
        ):
            raise SubagentContextError("INVALID_BUDGET", "model budget is inconsistent")

    def to_wire(self) -> dict[str, int | str]:
        return {
            "context_window_tokens": self.context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "max_input_tokens": self.max_input_tokens,
            "max_items": self.max_items,
            "max_images": self.max_images,
            "baseline_tokens": self.baseline_tokens,
            "delta_tokens": self.delta_tokens,
            "tail_tokens": self.tail_tokens,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class SubagentSnapshot:
    child_session_id: Id
    parent_session_id: Id
    scope: Scope
    spawn_cursor: Cursor
    source_digest: Digest
    mode_ceiling: ContextMode
    provider_profile_digest: Digest
    model_budget: ModelBudget
    item_ids: tuple[Id, ...]

    @classmethod
    def create(
        cls,
        *,
        child_session_id: str,
        parent_session_id: str,
        scope: Scope,
        spawn_cursor: Cursor,
        parent_mode: ContextMode,
        mode_ceiling: ContextMode,
        provider_profile_digest: str,
        model_budget: ModelBudget,
        item_ids: tuple[str, ...],
    ) -> SubagentSnapshot:
        child = Id(child_session_id)
        parent = Id(parent_session_id)
        items = tuple(Id(value) for value in item_ids)
        if child == parent:
            raise SubagentContextError(
                "CHILD_IDENTITY_REQUIRED", "child identity must differ from parent"
            )
        if _mode_rank(mode_ceiling) > _mode_rank(parent_mode):
            raise SubagentContextError(
                "MODE_ESCALATION", "child mode ceiling exceeds parent mode"
            )
        if len(items) > MAX_SNAPSHOT_ITEMS or len(set(items)) != len(items):
            raise SubagentContextError(
                "INVALID_SNAPSHOT_ITEMS", "snapshot items are duplicated or unbounded"
            )
        profile_digest = Digest(provider_profile_digest)
        raw = {
            "child_session_id": child,
            "parent_session_id": parent,
            "scope": scope.to_wire(),
            "spawn_cursor": spawn_cursor.to_wire(),
            "mode_ceiling": mode_ceiling.value,
            "provider_profile_digest": profile_digest,
            "model_budget": model_budget.to_wire(),
            "item_ids": list(items),
        }
        return cls(
            child_session_id=child,
            parent_session_id=parent,
            scope=scope,
            spawn_cursor=spawn_cursor,
            source_digest=Digest.sha256(_canonical(raw)),
            mode_ceiling=mode_ceiling,
            provider_profile_digest=profile_digest,
            model_budget=model_budget,
            item_ids=items,
        )

    def allows_item(self, item_id: str) -> bool:
        return Id(item_id) in self.item_ids

    def to_wire(self) -> dict[str, object]:
        return {
            "child_session_id": self.child_session_id,
            "parent_session_id": self.parent_session_id,
            "scope": self.scope.to_wire(),
            "spawn_cursor": self.spawn_cursor.to_wire(),
            "source_digest": self.source_digest,
            "mode_ceiling": self.mode_ceiling.value,
            "provider_profile_digest": self.provider_profile_digest,
            "model_budget": self.model_budget.to_wire(),
            "item_ids": list(self.item_ids),
        }


@dataclass(frozen=True, slots=True)
class ChildUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
            )
        ):
            raise SubagentContextError("INVALID_USAGE", "usage must be non-negative")

    def plus(self, other: ChildUsage) -> ChildUsage:
        return ChildUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class SubagentContribution:
    child_session_id: Id
    child_cursor: Cursor
    content: str
    content_digest: Digest
    authorized_by: Id

    @classmethod
    def create(
        cls,
        child_session_id: str,
        child_cursor: Cursor,
        content: str,
        authorized_by: str,
    ) -> SubagentContribution:
        if (
            not isinstance(content, str)
            or not content
            or len(content.encode()) > MAX_CONTRIBUTION_BYTES
        ):
            raise SubagentContextError(
                "INVALID_CONTRIBUTION", "contribution is empty or oversized"
            )
        return cls(
            child_session_id=Id(child_session_id),
            child_cursor=child_cursor,
            content=content,
            content_digest=Digest.sha256(content.encode()),
            authorized_by=Id(authorized_by),
        )


@dataclass(frozen=True, slots=True)
class GideonContributionAuthorization:
    authorization_id: Id
    authorized_by: Id
    parent_session_id: Id
    child_session_id: Id
    scope: Scope
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class ParentContribution:
    parent_item_id: Id
    parent_session_id: Id
    child_session_id: Id
    child_cursor: Cursor
    child_snapshot_digest: Digest
    content: str
    content_digest: Digest
    authorized_by: Id

    def to_wire(self) -> dict[str, object]:
        return {
            "parent_item_id": self.parent_item_id,
            "parent_session_id": self.parent_session_id,
            "child_session_id": self.child_session_id,
            "child_cursor": self.child_cursor.to_wire(),
            "child_snapshot_digest": self.child_snapshot_digest,
            "content": self.content,
            "content_digest": self.content_digest,
            "authorized_by": self.authorized_by,
        }


@dataclass(slots=True)
class _ChildState:
    snapshot: SubagentSnapshot
    cursor: Cursor
    usage: ChildUsage


class ParentContributionJournal:
    def __init__(self, parent_session_id: str, scope: Scope) -> None:
        self.parent_session_id = Id(parent_session_id)
        self.scope = scope
        self._cursor = Cursor(1, 0)
        self._items: dict[Id, ParentContribution] = {}

    @property
    def cursor(self) -> Cursor:
        return self._cursor

    @property
    def items(self) -> tuple[ParentContribution, ...]:
        return tuple(self._items.values())

    def append(self, key: Id, contribution: ParentContribution) -> ParentContribution:
        previous = self._items.get(key)
        if previous is not None:
            return previous
        self._cursor = self._cursor.next()
        self._items[key] = contribution
        return contribution


class SubagentContextRegistry:
    def __init__(self) -> None:
        self._children: dict[Id, _ChildState] = {}
        self._published: dict[Id, ParentContribution] = {}
        self._lock = threading.RLock()

    def spawn(self, snapshot: SubagentSnapshot) -> SubagentSnapshot:
        with self._lock:
            previous = self._children.get(snapshot.child_session_id)
            if previous is not None:
                if previous.snapshot != snapshot:
                    raise SubagentContextError(
                        "CHILD_IDENTITY_CONFLICT",
                        "child identity names another snapshot",
                    )
                return previous.snapshot
            self._children[snapshot.child_session_id] = _ChildState(
                snapshot=snapshot,
                cursor=Cursor(1, 0),
                usage=ChildUsage(),
            )
            return snapshot

    def snapshot(self, child_session_id: str, scope: Scope) -> SubagentSnapshot:
        with self._lock:
            state = self._child(child_session_id)
            self._scope(state, scope)
            return state.snapshot

    def record_outcome(
        self,
        child_session_id: str,
        scope: Scope,
        cursor: Cursor,
        usage: ChildUsage,
    ) -> None:
        with self._lock:
            state = self._child(child_session_id)
            self._scope(state, scope)
            if (
                cursor.epoch != state.cursor.epoch
                or cursor.sequence < state.cursor.sequence
            ):
                raise SubagentContextError(
                    "CHILD_CURSOR_MISMATCH",
                    "child cursor moved backward or changed epoch",
                )
            state.cursor = cursor
            state.usage = state.usage.plus(usage)

    def usage(self, child_session_id: str, scope: Scope) -> ChildUsage:
        with self._lock:
            state = self._child(child_session_id)
            self._scope(state, scope)
            return state.usage

    def publish(
        self,
        *,
        parent_journal: ParentContributionJournal,
        parent_item_id: str,
        idempotency_key: str,
        contribution: SubagentContribution,
        authorization: GideonContributionAuthorization,
        now_ms: int,
    ) -> ParentContribution:
        key = Id(idempotency_key)
        with self._lock:
            child = self._child(contribution.child_session_id)
            if child.snapshot.scope != parent_journal.scope:
                raise SubagentContextError(
                    "SCOPE_MISMATCH", "parent and child scopes differ"
                )
            if child.snapshot.parent_session_id != parent_journal.parent_session_id:
                raise SubagentContextError(
                    "PARENT_MISMATCH", "snapshot belongs to another parent"
                )
            if (
                contribution.child_cursor.epoch != child.cursor.epoch
                or contribution.child_cursor.sequence > child.cursor.sequence
            ):
                raise SubagentContextError(
                    "CHILD_CURSOR_MISMATCH", "contribution exceeds the child cursor"
                )
            if (
                authorization.expires_at_ms <= now_ms
                or authorization.authorized_by != contribution.authorized_by
                or authorization.parent_session_id != parent_journal.parent_session_id
                or authorization.child_session_id != child.snapshot.child_session_id
                or authorization.scope != parent_journal.scope
            ):
                raise SubagentContextError(
                    "AUTHORIZATION_DENIED", "Gideon did not authorize this contribution"
                )
            previous = self._published.get(key)
            if previous is not None:
                return previous
            published = ParentContribution(
                parent_item_id=Id(parent_item_id),
                parent_session_id=parent_journal.parent_session_id,
                child_session_id=contribution.child_session_id,
                child_cursor=contribution.child_cursor,
                child_snapshot_digest=child.snapshot.source_digest,
                content=contribution.content,
                content_digest=contribution.content_digest,
                authorized_by=contribution.authorized_by,
            )
            published = parent_journal.append(key, published)
            self._published[key] = published
            return published

    def _child(self, child_session_id: str) -> _ChildState:
        try:
            return self._children[Id(child_session_id)]
        except KeyError as exc:
            raise SubagentContextError(
                "CHILD_NOT_FOUND", "child was not spawned"
            ) from exc

    @staticmethod
    def _scope(state: _ChildState, scope: Scope) -> None:
        if state.snapshot.scope != scope:
            raise SubagentContextError(
                "SCOPE_MISMATCH", "scope does not own child context"
            )


def _canonical(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _mode_rank(mode: ContextMode) -> int:
    return {
        ContextMode.OFF: 0,
        ContextMode.PASS_THROUGH: 1,
        ContextMode.SHADOW: 2,
        ContextMode.PRIMARY: 3,
    }[mode]
