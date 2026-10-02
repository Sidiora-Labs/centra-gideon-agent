from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from .foundation import Cursor, Digest, Id, Scope


class IdentityKind(str, Enum):
    SESSION = "session"
    JOURNAL_ITEM = "journal_item"
    TOOL_CALL = "tool_call"
    SUMMARY = "summary"
    PROJECTION = "projection"
    SUBAGENT_SNAPSHOT = "subagent_snapshot"


class RelationKind(str, Enum):
    CONTINUES = "continues"
    SUPERSEDES = "supersedes"
    REGENERATES = "regenerates"
    FORKS_FROM = "forks_from"
    IMPORTS = "imports"
    DERIVED_FROM = "derived_from"
    CONTRIBUTED_BY = "contributed_by"


class IdentityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_id(value: str, field: str) -> Id:
    try:
        return Id(value)
    except (TypeError, ValueError) as exc:
        raise IdentityError("INVALID_ID", f"{field} is not a canonical Id") from exc


def _canonical_digest(value: str, field: str) -> Digest:
    try:
        return Digest(value)
    except (TypeError, ValueError) as exc:
        raise IdentityError(
            "INVALID_DIGEST", f"{field} is not a canonical Digest"
        ) from exc


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_event_id: Id
    source_digest: Digest

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_event_id", _canonical_id(self.source_event_id, "source_event_id")
        )
        object.__setattr__(
            self, "source_digest", _canonical_digest(self.source_digest, "source_digest")
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "source_event_id": self.source_event_id,
            "source_digest": self.source_digest,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SourceIdentity:
        return cls(
            source_event_id=value.get("source_event_id"),
            source_digest=value.get("source_digest"),
        )


@dataclass(frozen=True, slots=True)
class IdentityRelation:
    kind: RelationKind
    item_id: Id
    source_digest: Digest | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _canonical_id(self.item_id, "item_id"))
        if self.source_digest is not None:
            object.__setattr__(
                self,
                "source_digest",
                _canonical_digest(self.source_digest, "source_digest"),
            )

    def to_mapping(self) -> dict[str, str]:
        result = {"kind": self.kind.value, "item_id": self.item_id}
        if self.source_digest is not None:
            result["source_digest"] = self.source_digest
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> IdentityRelation:
        return cls(
            kind=RelationKind(value.get("kind")),
            item_id=value.get("item_id"),
            source_digest=value.get("source_digest"),
        )


@dataclass(frozen=True, slots=True)
class ContextIdentity:
    identity_id: Id
    kind: IdentityKind
    scope: Scope
    session_id: Id
    source: SourceIdentity | None = None
    relations: tuple[IdentityRelation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "identity_id", _canonical_id(self.identity_id, "identity_id")
        )
        object.__setattr__(
            self, "session_id", _canonical_id(self.session_id, "session_id")
        )
        if self.kind is IdentityKind.JOURNAL_ITEM and self.source is None:
            raise IdentityError(
                "SOURCE_IDENTITY_REQUIRED",
                "journal items require a canonical source identity",
            )

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "identity_id": self.identity_id,
            "kind": self.kind.value,
            "scope": self.scope.to_wire(),
            "session_id": self.session_id,
            "relations": [relation.to_mapping() for relation in self.relations],
        }
        if self.source is not None:
            result.update(self.source.to_mapping())
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ContextIdentity:
        source_event_id = value.get("source_event_id")
        source_digest = value.get("source_digest")
        if (source_event_id is None) != (source_digest is None):
            raise IdentityError(
                "SOURCE_IDENTITY_REQUIRED",
                "source_event_id and source_digest must be supplied together",
            )
        return cls(
            identity_id=value.get("identity_id"),
            kind=IdentityKind(value.get("kind")),
            scope=Scope.from_wire(value.get("scope", {})),
            session_id=value.get("session_id"),
            source=(
                SourceIdentity(source_event_id, source_digest)
                if source_event_id is not None
                else None
            ),
            relations=tuple(
                IdentityRelation.from_mapping(relation)
                for relation in value.get("relations", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class RebindRecord:
    rebind_id: Id
    session_id: Id
    previous_scope: Scope
    next_scope: Scope
    cursor: Cursor
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rebind_id", _canonical_id(self.rebind_id, "rebind_id"))
        object.__setattr__(
            self, "session_id", _canonical_id(self.session_id, "session_id")
        )
        if not isinstance(self.reason, str) or not (1 <= len(self.reason) <= 1024):
            raise IdentityError("INVALID_REBIND", "reason must contain 1 to 1024 characters")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "rebind_id": self.rebind_id,
            "session_id": self.session_id,
            "previous_scope": self.previous_scope.to_wire(),
            "next_scope": self.next_scope.to_wire(),
            "cursor": self.cursor.to_wire(),
            "reason": self.reason,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RebindRecord:
        return cls(
            rebind_id=value.get("rebind_id"),
            session_id=value.get("session_id"),
            previous_scope=Scope.from_wire(value.get("previous_scope", {})),
            next_scope=Scope.from_wire(value.get("next_scope", {})),
            cursor=Cursor.from_wire(value.get("cursor", {})),
            reason=value.get("reason"),
        )


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    session_id: Id
    scope: Scope
    rebinds: tuple[RebindRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _canonical_id(self.session_id, "session_id")
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scope": self.scope.to_wire(),
            "rebinds": [rebind.to_mapping() for rebind in self.rebinds],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> IdentityBinding:
        return cls(
            session_id=value.get("session_id"),
            scope=Scope.from_wire(value.get("scope", {})),
            rebinds=tuple(
                RebindRecord.from_mapping(rebind)
                for rebind in value.get("rebinds", ())
            ),
        )


class IdentityStore:
    def __init__(self) -> None:
        self._bindings: dict[Id, IdentityBinding] = {}
        self._identities: dict[Id, ContextIdentity] = {}
        self._source_items: dict[tuple[Id, Id], Id] = {}
        self._lock = threading.RLock()

    def bind_session(self, session_id: str, scope: Scope) -> IdentityBinding:
        session_id = _canonical_id(session_id, "session_id")
        with self._lock:
            existing = self._bindings.get(session_id)
            if existing is not None:
                if existing.scope != scope:
                    raise IdentityError(
                        "SCOPE_MISMATCH", "session is already bound to another scope"
                    )
                return existing
            binding = IdentityBinding(session_id=session_id, scope=scope)
            self._bindings[session_id] = binding
            return binding

    def binding(self, session_id: str, scope: Scope) -> IdentityBinding:
        session_id = _canonical_id(session_id, "session_id")
        with self._lock:
            binding = self._bindings.get(session_id)
            if binding is None:
                raise IdentityError("SESSION_NOT_BOUND", "session has no identity binding")
            if binding.scope != scope:
                raise IdentityError("SCOPE_MISMATCH", "scope does not match session binding")
            return binding

    def identity(self, identity_id: str, scope: Scope) -> ContextIdentity:
        identity_id = _canonical_id(identity_id, "identity_id")
        with self._lock:
            identity = self._identities.get(identity_id)
            if identity is None:
                raise IdentityError("IDENTITY_NOT_FOUND", "identity was not found")
            self.binding(identity.session_id, scope)
            return identity

    def register(self, identity: ContextIdentity) -> ContextIdentity:
        with self._lock:
            self.binding(identity.session_id, identity.scope)
            existing = self._identities.get(identity.identity_id)
            if existing is not None:
                if existing != identity:
                    raise IdentityError(
                        "IDENTITY_CONFLICT", "identity id is already bound differently"
                    )
                return existing
            self._identities[identity.identity_id] = identity
            return identity

    def register_source_item(self, identity: ContextIdentity) -> ContextIdentity:
        if identity.kind is not IdentityKind.JOURNAL_ITEM or identity.source is None:
            raise IdentityError(
                "SOURCE_IDENTITY_REQUIRED",
                "source registration requires a journal item and source identity",
            )
        key = (identity.session_id, identity.source.source_event_id)
        with self._lock:
            self.binding(identity.session_id, identity.scope)
            existing_id = self._source_items.get(key)
            if existing_id is not None:
                existing = self._identities[existing_id]
                if existing.source != identity.source:
                    raise IdentityError(
                        "SOURCE_IDENTITY_CONFLICT",
                        "source event id was replayed with another digest",
                    )
                return existing
            registered = self.register(identity)
            self._source_items[key] = registered.identity_id
            return registered

    def rebind(
        self,
        *,
        rebind_id: str,
        session_id: str,
        previous_scope: Scope,
        next_scope: Scope,
        cursor: Cursor,
        reason: str,
    ) -> RebindRecord:
        rebind_id = _canonical_id(rebind_id, "rebind_id")
        session_id = _canonical_id(session_id, "session_id")
        with self._lock:
            current = self._bindings.get(session_id)
            if current is None:
                raise IdentityError("SESSION_NOT_BOUND", "session has no identity binding")
            for existing in current.rebinds:
                if existing.rebind_id == rebind_id:
                    if (
                        existing.previous_scope != previous_scope
                        or existing.next_scope != next_scope
                        or existing.cursor != cursor
                        or existing.reason != reason
                    ):
                        raise IdentityError(
                            "IDENTITY_CONFLICT", "rebind id is already bound differently"
                        )
                    return existing
            binding = self.binding(session_id, previous_scope)
            if (
                previous_scope.owner_id != next_scope.owner_id
                or previous_scope.project_id != next_scope.project_id
            ):
                raise IdentityError(
                    "SCOPE_MISMATCH",
                    "owner or project changes require a distinct context identity",
                )
            if previous_scope.workspace_id == next_scope.workspace_id:
                raise IdentityError("INVALID_REBIND", "workspace binding did not change")
            record = RebindRecord(
                rebind_id=rebind_id,
                session_id=session_id,
                previous_scope=previous_scope,
                next_scope=next_scope,
                cursor=cursor,
                reason=reason,
            )
            self._bindings[session_id] = IdentityBinding(
                session_id=session_id,
                scope=next_scope,
                rebinds=(*binding.rebinds, record),
            )
            for identity_id, identity in tuple(self._identities.items()):
                if identity.session_id == session_id:
                    self._identities[identity_id] = replace(identity, scope=next_scope)
            return record

    def to_mapping(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "bindings": [
                    self._bindings[key].to_mapping() for key in sorted(self._bindings)
                ],
                "identities": [
                    self._identities[key].to_mapping()
                    for key in sorted(self._identities)
                ],
            }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> IdentityStore:
        if value.get("schema_version") != 1:
            raise IdentityError("UNSUPPORTED_VERSION", "identity state version is unsupported")
        store = cls()
        for raw_binding in value.get("bindings", ()):
            binding = IdentityBinding.from_mapping(raw_binding)
            if binding.session_id in store._bindings:
                raise IdentityError("IDENTITY_CONFLICT", "duplicate session binding")
            if any(rebind.session_id != binding.session_id for rebind in binding.rebinds):
                raise IdentityError("IDENTITY_CONFLICT", "foreign rebind in session binding")
            store._bindings[binding.session_id] = binding
        for raw_identity in value.get("identities", ()):
            identity = ContextIdentity.from_mapping(raw_identity)
            store.register(identity)
            if identity.source is not None:
                key = (identity.session_id, identity.source.source_event_id)
                existing = store._source_items.get(key)
                if existing is not None and existing != identity.identity_id:
                    raise IdentityError("IDENTITY_CONFLICT", "duplicate source event identity")
                store._source_items[key] = identity.identity_id
        return store
