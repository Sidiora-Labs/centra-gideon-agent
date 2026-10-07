"""Security status facade over authoritative network and memory sources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from .foundation import Id, Scope
from .network_policy import NetworkGrant, ProxyPolicy

_VERIFICATION = frozenset({"unverified", "verified", "rejected"})
_TRUST_CLASS = frozenset({"data", "privileged_instruction"})
_FORBIDDEN_CODES = frozenset({"AUTHORIZATION_DENIED", "FORBIDDEN", "SCOPE_DENIED"})
_STALE_CODES = frozenset({"STALE_MEMORY_REVISION", "STALE_REVISION"})
_UNAVAILABLE_CODES = frozenset(
    {
        "DAEMON_UNAVAILABLE",
        "NOT_CONFIGURED",
        "SECURITY_STATUS_UNAVAILABLE",
        "UNKNOWN_OPERATION",
    }
)
_NOT_FOUND_CODES = frozenset({"MEMORY_PROVENANCE_NOT_FOUND", "NETWORK_GRANT_NOT_FOUND"})


class SecurityStatusError(RuntimeError):
    """Safe typed error for native security routes."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        self.code = _error_code(code)
        self.message = _safe_message(message)
        self.http_status = http_status
        super().__init__(self.message)

    def to_wire(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "httpStatus": self.http_status,
        }


@dataclass(frozen=True, slots=True)
class NetworkDecision:
    code: str
    rule: str
    allowed: bool
    checked_at_ms: int

    def __post_init__(self) -> None:
        _bounded(self.code, "decision code", 64)
        _bounded(self.rule, "decision rule", 160)
        _uint(self.checked_at_ms, "decision checked_at_ms")


@dataclass(frozen=True, slots=True)
class NetworkSecuritySnapshot:
    checked_at_ms: int
    grants: tuple[NetworkGrant, ...]
    decisions: tuple[NetworkDecision, ...]

    def __post_init__(self) -> None:
        _uint(self.checked_at_ms, "snapshot checked_at_ms")
        grant_ids: set[str] = set()
        for grant in self.grants:
            if not isinstance(grant, NetworkGrant):
                raise TypeError("network authority returned an invalid grant")
            grant.validate()
            if grant.expires_at_ms <= self.checked_at_ms:
                raise ValueError("network authority returned an inactive grant")
            if grant.grant_id in grant_ids:
                raise ValueError("network authority returned a duplicate grant")
            grant_ids.add(grant.grant_id)
        if not all(isinstance(item, NetworkDecision) for item in self.decisions):
            raise TypeError("network authority returned an invalid decision")


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    memory_id: Id
    source_type: str
    source_id: Id
    author_principal_id: Id
    content_digest: str
    revision: int
    verification: str
    instruction_shaped: bool
    trust_class: str
    reviewer_principal_id: Id | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", Id(self.memory_id))
        object.__setattr__(self, "source_id", Id(self.source_id))
        object.__setattr__(self, "author_principal_id", Id(self.author_principal_id))
        if self.reviewer_principal_id is not None:
            object.__setattr__(
                self, "reviewer_principal_id", Id(self.reviewer_principal_id)
            )
        _bounded(self.source_type, "memory source type", 64)
        if (
            not isinstance(self.content_digest, str)
            or len(self.content_digest) != 64
            or any(
                character not in "0123456789abcdef" for character in self.content_digest
            )
        ):
            raise ValueError("memory authority returned invalid content digest")
        if self.verification not in _VERIFICATION:
            raise ValueError("memory authority returned invalid verification")
        if self.trust_class not in _TRUST_CLASS:
            raise ValueError("memory authority returned invalid trust class")
        _uint(self.revision, "memory provenance revision", minimum=1)


@runtime_checkable
class NetworkSecurityAuthority(Protocol):
    """Owner-scoped source of live daemon network security state."""

    async def snapshot(self, scope: Scope) -> NetworkSecuritySnapshot: ...

    async def revoke(self, scope: Scope, grant_id: Id) -> NetworkSecuritySnapshot: ...


@runtime_checkable
class MemoryProvenanceAuthority(Protocol):
    """Owner-scoped source of persisted memory provenance and promotion."""

    async def lookup(self, scope: Scope, memory_id: Id) -> ProvenanceRecord: ...

    async def promote(
        self, scope: Scope, memory_id: Id, expected_revision: int
    ) -> ProvenanceRecord: ...


class SecurityStatusFacade:
    """Expose security state without becoming another authority or cache."""

    def __init__(
        self,
        scope: Scope,
        *,
        network_authority: NetworkSecurityAuthority | None = None,
        provenance_authority: MemoryProvenanceAuthority | None = None,
    ) -> None:
        self.scope = scope
        self.network_authority = network_authority
        self.provenance_authority = provenance_authority

    async def status(self) -> dict[str, Any]:
        authority = self.network_authority
        if authority is None:
            unavailable = _unavailable("network security authority is unavailable")
            return {
                "scope": self.scope.to_wire(),
                "grants": [],
                "decisions": [],
                "availability": "unavailable",
                "error": unavailable.to_wire(),
            }
        try:
            snapshot = await authority.snapshot(self.scope)
            return _network_status(self.scope, snapshot)
        except Exception as error:
            unavailable = _translate(error, "network security authority is unavailable")
            return {
                "scope": self.scope.to_wire(),
                "grants": [],
                "decisions": [],
                "availability": "unavailable",
                "error": unavailable.to_wire(),
            }

    async def revoke(self, grant_id: Id | str) -> dict[str, Any]:
        authority = self.network_authority
        if authority is None:
            raise _unavailable("network grant revocation is unavailable")
        try:
            snapshot = await authority.revoke(self.scope, Id(grant_id))
            return _network_status(self.scope, snapshot)
        except Exception as error:
            raise _translate(
                error, "network grant revocation is unavailable"
            ) from error

    async def lookup(self, memory_id: Id | str) -> dict[str, Any]:
        authority = self.provenance_authority
        if authority is None:
            raise _unavailable("memory provenance authority is unavailable")
        try:
            return _provenance(await authority.lookup(self.scope, Id(memory_id)))
        except Exception as error:
            raise _translate(
                error, "memory provenance lookup is unavailable"
            ) from error

    async def promote(
        self, memory_id: Id | str, expected_revision: int
    ) -> dict[str, Any]:
        authority = self.provenance_authority
        if authority is None:
            raise _unavailable("memory provenance promotion is unavailable")
        _uint(expected_revision, "expected revision", minimum=1)
        try:
            return _provenance(
                await authority.promote(self.scope, Id(memory_id), expected_revision)
            )
        except Exception as error:
            raise _translate(
                error, "memory provenance promotion is unavailable"
            ) from error


def create_security_status(
    scope: Scope,
    *,
    network_authority: NetworkSecurityAuthority | None = None,
    provenance_authority: MemoryProvenanceAuthority | None = None,
) -> SecurityStatusFacade:
    return SecurityStatusFacade(
        scope,
        network_authority=network_authority,
        provenance_authority=provenance_authority,
    )


class _DaemonClient(Protocol):
    scope: Any

    async def request(
        self,
        operation: str,
        payload: Any,
        *,
        effect_kind: str = "query",
    ) -> Any: ...


class DaemonSecurityAuthority(NetworkSecurityAuthority, MemoryProvenanceAuthority):
    """Strict adapter for the authenticated daemon security route catalog."""

    def __init__(self, client: _DaemonClient, scope: Scope) -> None:
        self._client = client
        self._scope = scope

    async def snapshot(self, scope: Scope) -> NetworkSecuritySnapshot:
        self._require_scope(scope)
        return _snapshot(await self._request("security.network.status", {}), scope)

    async def revoke(self, scope: Scope, grant_id: Id) -> NetworkSecuritySnapshot:
        self._require_scope(scope)
        return _snapshot(
            await self._request(
                "security.network.revoke",
                {"grantId": str(grant_id)},
                effect_kind="durable",
            ),
            scope,
        )

    async def lookup(self, scope: Scope, memory_id: Id) -> ProvenanceRecord:
        self._require_scope(scope)
        return _provenance_record(
            await self._request(
                "security.memory.provenance", {"memoryId": str(memory_id)}
            )
        )

    async def promote(
        self, scope: Scope, memory_id: Id, expected_revision: int
    ) -> ProvenanceRecord:
        self._require_scope(scope)
        current = await self.lookup(scope, memory_id)
        return _provenance_record(
            await self._request(
                "security.memory.promote",
                {
                    "memoryId": str(memory_id),
                    "expectedRevision": expected_revision,
                    "expectedDigest": current.content_digest,
                },
                effect_kind="durable",
            )
        )

    def _require_scope(self, scope: Scope) -> None:
        if scope != self._scope:
            raise SecurityStatusError(
                "SCOPE_DENIED", "security action is not authorized", 403
            )

    async def _request(
        self, operation: str, payload: Any, *, effect_kind: str = "query"
    ) -> Any:
        try:
            return await self._client.request(
                operation, payload, effect_kind=effect_kind
            )
        except Exception as error:
            remote = getattr(error, "error", None)
            code = getattr(remote, "code", None)
            message = getattr(remote, "message", None)
            if isinstance(code, str):
                if code in _FORBIDDEN_CODES:
                    raise SecurityStatusError(
                        code, "security action is not authorized", 403
                    ) from error
                if code in _STALE_CODES:
                    raise SecurityStatusError(
                        "STALE_MEMORY_REVISION", "memory revision is stale", 409
                    ) from error
                if code in _NOT_FOUND_CODES:
                    raise SecurityStatusError(code, str(message), 404) from error
            raise _unavailable("security authority is unavailable") from error


def create_daemon_security_status(client: _DaemonClient) -> SecurityStatusFacade:
    raw_scope = getattr(client, "scope", None)
    if raw_scope is None or not callable(getattr(raw_scope, "to_wire", None)):
        raise TypeError("authenticated Hypermid client scope is unavailable")
    scope = Scope.from_wire(raw_scope.to_wire())
    authority = DaemonSecurityAuthority(client, scope)
    return create_security_status(
        scope,
        network_authority=authority,
        provenance_authority=authority,
    )


def _network_status(scope: Scope, snapshot: NetworkSecuritySnapshot) -> dict[str, Any]:
    if not isinstance(snapshot, NetworkSecuritySnapshot):
        raise TypeError("network authority returned an invalid snapshot")
    grants = sorted(snapshot.grants, key=lambda item: item.grant_id)
    decisions = sorted(
        snapshot.decisions,
        key=lambda item: (item.checked_at_ms, item.code, item.rule),
    )
    return {
        "scope": scope.to_wire(),
        "grants": [_grant(item) for item in grants],
        "decisions": [_decision(item) for item in decisions],
        "availability": "ready",
    }


def _grant(grant: NetworkGrant) -> dict[str, Any]:
    return {
        "grantId": grant.grant_id,
        "principalId": grant.principal_id,
        "operation": grant.operation,
        "scheme": grant.scheme,
        "hostname": grant.hostname,
        "ports": sorted(grant.ports),
        "addressClasses": sorted(grant.address_classes),
        "proxyPolicy": grant.proxy_policy.mode,
        "redirectLimit": grant.redirect_limit,
        "byteLimit": grant.byte_limit,
        "expiresAtMs": grant.expires_at_ms,
    }


def _decision(decision: NetworkDecision) -> dict[str, Any]:
    return {
        "code": decision.code,
        "rule": decision.rule,
        "allowed": decision.allowed,
        "checkedAtMs": decision.checked_at_ms,
    }


def _provenance(record: ProvenanceRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "memoryId": str(record.memory_id),
        "sourceType": record.source_type,
        "sourceId": str(record.source_id),
        "authorPrincipalId": str(record.author_principal_id),
        "contentDigest": record.content_digest,
        "revision": record.revision,
        "verification": record.verification,
        "instructionShaped": record.instruction_shaped,
        "trustClass": record.trust_class,
    }
    if record.reviewer_principal_id is not None:
        result["reviewerPrincipalId"] = str(record.reviewer_principal_id)
    return result


def _snapshot(value: object, expected_scope: Scope) -> NetworkSecuritySnapshot:
    raw = _mapping(value, "network security status")
    returned_scope = Scope.from_wire(_mapping(raw.get("scope"), "security scope"))
    if returned_scope != expected_scope:
        raise SecurityStatusError(
            "SCOPE_DENIED", "security action is not authorized", 403
        )
    checked_at_ms = _uint(raw.get("checkedAtMs"), "checked at")
    grants_raw = _sequence(raw.get("grants"), "network grants")
    decisions_raw = _sequence(raw.get("decisions"), "network decisions")
    return NetworkSecuritySnapshot(
        checked_at_ms=checked_at_ms,
        grants=tuple(_network_grant(item) for item in grants_raw),
        decisions=tuple(_network_decision(item) for item in decisions_raw),
    )


def _network_grant(value: object) -> NetworkGrant:
    raw = _mapping(value, "network grant")
    policy = raw.get("proxyPolicy")
    if policy not in {"direct", "required"}:
        raise ValueError("network grant proxy policy is invalid")
    return NetworkGrant(
        grant_id=_bounded(raw.get("grantId"), "grant id", 160),
        principal_id=_bounded(raw.get("principalId"), "principal id", 160),
        operation=_bounded(raw.get("operation"), "operation", 160),
        scheme=_bounded(raw.get("scheme"), "scheme", 32),
        hostname=_bounded(raw.get("hostname"), "hostname", 253),
        ports=frozenset(
            _uint(item, "port", minimum=1)
            for item in _sequence(raw.get("ports"), "ports")
        ),
        address_classes=frozenset(
            _bounded(item, "address class", 32)
            for item in _sequence(raw.get("addressClasses"), "address classes")
        ),
        proxy_policy=ProxyPolicy(cast(str, policy)),
        redirect_limit=_uint(raw.get("redirectLimit"), "redirect limit"),
        byte_limit=_uint(raw.get("byteLimit"), "byte limit", minimum=1),
        expires_at_ms=_uint(raw.get("expiresAtMs"), "expiry", minimum=1),
    )


def _network_decision(value: object) -> NetworkDecision:
    raw = _mapping(value, "network decision")
    allowed = raw.get("allowed")
    if not isinstance(allowed, bool):
        raise ValueError("network decision allowed is invalid")
    return NetworkDecision(
        code=_bounded(raw.get("code"), "decision code", 64),
        rule=_bounded(raw.get("rule"), "decision rule", 160),
        allowed=allowed,
        checked_at_ms=_uint(raw.get("checkedAtMs"), "decision checked at"),
    )


def _provenance_record(value: object) -> ProvenanceRecord:
    raw = _mapping(value, "memory provenance")
    instruction_shaped = raw.get("instructionShaped")
    if not isinstance(instruction_shaped, bool):
        raise ValueError("memory provenance instruction shape is invalid")
    reviewer = raw.get("reviewerPrincipalId")
    return ProvenanceRecord(
        memory_id=Id(_bounded(raw.get("memoryId"), "memory id", 160)),
        source_type=_bounded(raw.get("sourceType"), "source type", 64),
        source_id=Id(_bounded(raw.get("sourceId"), "source id", 160)),
        author_principal_id=Id(
            _bounded(raw.get("authorPrincipalId"), "author principal id", 160)
        ),
        content_digest=_bounded(raw.get("contentDigest"), "content digest", 64),
        revision=_uint(raw.get("revision"), "revision", minimum=1),
        verification=_bounded(raw.get("verification"), "verification", 32),
        instruction_shaped=instruction_shaped,
        trust_class=_bounded(raw.get("trustClass"), "trust class", 32),
        reviewer_principal_id=(
            Id(_bounded(reviewer, "reviewer principal id", 160))
            if reviewer is not None
            else None
        ),
    )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is invalid")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} is invalid")
    return cast(Sequence[Any], value)


def _translate(error: BaseException, fallback: str) -> SecurityStatusError:
    if isinstance(error, SecurityStatusError):
        return error
    code = getattr(error, "code", None)
    if not isinstance(code, str):
        code = str(error) if str(error) in _STALE_CODES else ""
    if code in _FORBIDDEN_CODES:
        return SecurityStatusError(code, "security action is not authorized", 403)
    if code in _STALE_CODES:
        return SecurityStatusError(
            "STALE_MEMORY_REVISION", "memory revision is stale", 409
        )
    if code in _UNAVAILABLE_CODES:
        return SecurityStatusError(code, fallback, 503)
    return _unavailable(fallback)


def _unavailable(message: str) -> SecurityStatusError:
    return SecurityStatusError("SECURITY_STATUS_UNAVAILABLE", message, 503)


def _error_code(value: object) -> str:
    code = str(value)
    if (
        not 2 <= len(code) <= 64
        or not code[0].isupper()
        or any(
            not (character.isupper() or character.isdigit() or character == "_")
            for character in code
        )
    ):
        return "SECURITY_STATUS_UNAVAILABLE"
    return code


def _safe_message(value: object) -> str:
    message = " ".join(str(value).split())[:240]
    return message or "security status is unavailable"


def _bounded(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{name} is invalid")
    return value


def _uint(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} is invalid")
    return value


__all__ = [
    "MemoryProvenanceAuthority",
    "NetworkDecision",
    "NetworkSecurityAuthority",
    "NetworkSecuritySnapshot",
    "ProvenanceRecord",
    "SecurityStatusError",
    "SecurityStatusFacade",
    "DaemonSecurityAuthority",
    "create_daemon_security_status",
    "create_security_status",
]
