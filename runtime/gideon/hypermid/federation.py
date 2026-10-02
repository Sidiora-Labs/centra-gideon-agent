"""Fail-closed peer negotiation and exact-scope federation admission."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .foundation import Digest, Id, Scope, Trace
from .models import PROTOCOL, Principal

_WRITER_OPERATIONS = frozenset(
    {"writer.acquire", "writer.renew", "writer.release", "lease.acquire", "lease.renew"}
)


class FederationViolation(PermissionError):
    pass


class EffectClass(str, Enum):
    QUERY = "query"
    IDEMPOTENT = "idempotent"
    DURABLE = "durable"


@dataclass(frozen=True, slots=True)
class PeerHello:
    peer_id: Id
    owner_id: Id
    project_id: Id
    protocol: str
    capabilities: tuple[str, ...]
    catalog_digest: Digest

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL:
            raise FederationViolation("peer protocol is incompatible")
        if len(self.capabilities) > 256 or len(set(self.capabilities)) != len(self.capabilities):
            raise FederationViolation("peer capabilities are invalid")
        if tuple(sorted(self.capabilities)) != self.capabilities:
            raise FederationViolation("peer capabilities must be sorted")


@dataclass(frozen=True, slots=True)
class PeerGrant:
    peer_id: Id
    principal_id: Id
    scope: Scope
    operations: frozenset[str]
    scopes: frozenset[str]
    expires_ms: int

    def __post_init__(self) -> None:
        if not self.operations or any(not value or len(value) > 160 for value in self.operations):
            raise FederationViolation("peer grant operations are invalid")
        if isinstance(self.expires_ms, bool) or self.expires_ms < 1:
            raise FederationViolation("peer grant expiry is invalid")


@dataclass(frozen=True, slots=True)
class FederatedCall:
    message_id: Id
    operation: str
    principal: Principal
    scope: Scope
    trace: Trace
    deadline_ms: int
    effect: EffectClass
    payload: object
    effect_id: Id | None = None
    input_digest: Digest | None = None

    def __post_init__(self) -> None:
        if not self.operation or len(self.operation) > 160:
            raise FederationViolation("federated operation is invalid")
        if self.effect is EffectClass.DURABLE:
            if self.effect_id is None or self.input_digest is None:
                raise FederationViolation("durable call requires effect identity")
        elif self.effect_id is not None or self.input_digest is not None:
            raise FederationViolation("non-durable call cannot carry effect identity")

    def to_wire(self) -> dict[str, object]:
        value: dict[str, object] = {
            "kind": "federated_call",
            "message_id": str(self.message_id),
            "operation": self.operation,
            "principal": self.principal.to_wire(),
            "scope": self.scope.to_wire(),
            "trace": self.trace.to_wire(),
            "deadline_ms": self.deadline_ms,
            "effect": self.effect.value,
            "payload": self.payload,
        }
        if self.effect_id is not None:
            value["effect_id"] = str(self.effect_id)
        if self.input_digest is not None:
            value["input_digest"] = str(self.input_digest)
        return value


@dataclass(frozen=True, slots=True)
class FederatedOperation:
    name: str
    effect: EffectClass
    required_scopes: frozenset[str]
    remote: bool = True


@dataclass(frozen=True, slots=True)
class AdmittedCall:
    peer_id: Id
    call: FederatedCall
    negotiated_capabilities: frozenset[str]
    writer_authority: Literal[False] = False


class FederationSession:
    """Authenticated peer session with an immutable exact-scope grant."""

    def __init__(
        self,
        *,
        hello: PeerHello,
        grant: PeerGrant,
        expected_scope: Scope,
        local_capabilities: frozenset[str],
        local_scopes: frozenset[str],
        catalog: dict[str, FederatedOperation],
        now_ms: int | None = None,
    ) -> None:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if hello.peer_id != grant.peer_id:
            raise FederationViolation("authenticated peer does not match its grant")
        if hello.owner_id != expected_scope.owner_id or hello.project_id != expected_scope.project_id:
            raise FederationViolation("peer owner or project identity does not match the target")
        if grant.scope != expected_scope:
            raise FederationViolation("peer grant must exactly match the target scope")
        if timestamp >= grant.expires_ms:
            raise FederationViolation("peer grant has expired")
        negotiated = frozenset(hello.capabilities).intersection(local_capabilities)
        if "federation" not in negotiated:
            raise FederationViolation("peer lacks the required federation capability")
        self.hello = hello
        self.grant = grant
        self.scope = expected_scope
        self.capabilities = negotiated
        self.local_scopes = local_scopes
        self.catalog = dict(catalog)

    def admit(self, call: FederatedCall, *, now_ms: int | None = None) -> AdmittedCall:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms
        if timestamp >= self.grant.expires_ms or timestamp >= call.deadline_ms:
            raise FederationViolation("federated call or grant has expired")
        if call.principal.id != self.grant.principal_id:
            raise FederationViolation("originating principal does not match the peer grant")
        if call.scope != self.scope or call.scope != self.grant.scope:
            raise FederationViolation("federated call attempted to widen its scope")
        if call.operation not in self.grant.operations:
            raise FederationViolation("operation is absent from the peer grant")
        operation = self.catalog.get(call.operation)
        if operation is None or not operation.remote:
            raise FederationViolation("operation is not advertised for remote use")
        if operation.effect is not call.effect:
            raise FederationViolation("effect class differs from the local catalog")
        principal_scopes = frozenset(call.principal.scopes)
        for required in operation.required_scopes:
            if (
                required not in self.grant.scopes
                or required not in self.local_scopes
                or required not in principal_scopes
            ):
                raise FederationViolation("required scope is absent from an authority boundary")
        if (
            call.operation in _WRITER_OPERATIONS
            or call.operation.startswith("writer.")
            or "writer.acquire" in principal_scopes
        ):
            raise FederationViolation("federation cannot acquire a local writer lease")
        return AdmittedCall(self.hello.peer_id, call, self.capabilities)


__all__ = [
    "AdmittedCall",
    "EffectClass",
    "FederatedCall",
    "FederatedOperation",
    "FederationSession",
    "FederationViolation",
    "PeerGrant",
    "PeerHello",
]
