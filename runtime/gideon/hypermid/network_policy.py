from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, TypeVar


EGRESS_DENIED = "HYPERMID_EGRESS_DENIED"
SECRET_DENIED = "HYPERMID_SECRET_DENIED"
REDACTED = "[redacted]"
_METADATA_HOSTS = {"metadata", "metadata.google.internal", "instance-data.ec2.internal"}
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("192.0.0.192"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_RESERVED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "240.0.0.0/4",
        "2001:db8::/32",
    )
)
_SENSITIVE_FIELD = re.compile(
    r"(?:authorization|cookie|setcookie|password|passwd|secret|token|apikey|"
    r"credential|clientsecret|privatekey|accesstoken|refreshtoken|certificatekey)$"
)
_COMMON_CREDENTIAL = re.compile(
    r"(?i)(bearer\s+|(?:api[_-]?key|access_token|refresh_token|client_secret|password)=)"
    r"[^\s&,;\"']+"
)
_URL_USERINFO = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
T = TypeVar("T")


class EgressDenied(RuntimeError):
    def __init__(self, rule: str) -> None:
        self.code = EGRESS_DENIED
        self.rule = rule
        super().__init__(f"egress request denied ({rule})")


class SecretDenied(RuntimeError):
    def __init__(self, rule: str) -> None:
        self.code = SECRET_DENIED
        self.rule = rule
        super().__init__(f"secret use denied ({rule})")


def normalize_hostname(value: str) -> str:
    hostname = value.strip().rstrip(".").lower()
    if not hostname or len(hostname) > 253 or any(mark in hostname for mark in "/\\@:"):
        raise EgressDenied("destination.invalid")
    try:
        return str(ipaddress.ip_address(hostname))
    except ValueError:
        pass
    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise EgressDenied("destination.invalid")
    return hostname


def normalize_scheme(value: str) -> str:
    scheme = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme):
        raise EgressDenied("destination.invalid")
    return scheme


@dataclass(frozen=True)
class Destination:
    scheme: str
    hostname: str
    port: int

    @classmethod
    def normalized(cls, scheme: str, hostname: str, port: int) -> Destination:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise EgressDenied("destination.invalid")
        return cls(normalize_scheme(scheme), normalize_hostname(hostname), port)


@dataclass(frozen=True)
class ProxyRoute:
    scheme: str
    hostname: str
    port: int
    resolved_addresses: tuple[str, ...]


@dataclass(frozen=True)
class ProxyPolicy:
    mode: str
    scheme: str | None = None
    hostname: str | None = None
    port: int | None = None
    address_classes: frozenset[str] = frozenset()

    @classmethod
    def direct_only(cls) -> ProxyPolicy:
        return cls("direct")

    @classmethod
    def required(
        cls,
        scheme: str,
        hostname: str,
        port: int,
        address_classes: Iterable[str] = ("public",),
    ) -> ProxyPolicy:
        destination = Destination.normalized(scheme, hostname, port)
        classes = frozenset(address_classes)
        if not classes:
            raise EgressDenied("grant.proxy_invalid")
        return cls(
            "required",
            destination.scheme,
            destination.hostname,
            destination.port,
            classes,
        )


@dataclass(frozen=True)
class NetworkGrant:
    grant_id: str
    principal_id: str
    operation: str
    scheme: str
    hostname: str
    ports: frozenset[int]
    address_classes: frozenset[str]
    proxy_policy: ProxyPolicy
    redirect_limit: int
    byte_limit: int
    expires_at_ms: int

    def validate(self) -> None:
        if (
            not self.grant_id
            or not self.principal_id
            or not self.operation
            or normalize_scheme(self.scheme) != self.scheme
            or normalize_hostname(self.hostname) != self.hostname
            or not self.ports
            or any(isinstance(port, bool) or not 1 <= port <= 65535 for port in self.ports)
            or not self.address_classes
            or isinstance(self.redirect_limit, bool)
            or not 0 <= self.redirect_limit <= 255
            or isinstance(self.byte_limit, bool)
            or self.byte_limit <= 0
            or isinstance(self.expires_at_ms, bool)
            or self.expires_at_ms <= 0
            or self.proxy_policy.mode not in {"direct", "required"}
        ):
            raise EgressDenied("grant.invalid")


@dataclass(frozen=True)
class EgressAuthorization:
    grant_id: str
    max_response_bytes: int
    redirects_remaining: int


class NetworkPolicy:
    def authorize_attempt(
        self,
        grant: NetworkGrant | None,
        *,
        principal_id: str,
        operation: str,
        destination: Destination,
        resolved_addresses: Iterable[str],
        proxy: ProxyRoute | None = None,
        redirect_hops: int = 0,
        now_ms: int,
    ) -> EgressAuthorization:
        if grant is None:
            raise EgressDenied("grant.missing")
        grant.validate()
        if grant.principal_id != principal_id or grant.operation != operation:
            raise EgressDenied("grant.scope")
        if now_ms >= grant.expires_at_ms:
            raise EgressDenied("grant.expired")
        if (
            destination.scheme != grant.scheme
            or destination.hostname != grant.hostname
            or destination.port not in grant.ports
        ):
            raise EgressDenied("destination.not_granted")
        if redirect_hops > grant.redirect_limit:
            raise EgressDenied("redirect.limit")
        self._validate_addresses(
            destination.hostname, tuple(resolved_addresses), grant.address_classes
        )
        self._validate_proxy(grant.proxy_policy, proxy)
        return EgressAuthorization(
            grant.grant_id,
            grant.byte_limit,
            grant.redirect_limit - redirect_hops,
        )

    def resolve_and_authorize(
        self,
        grant: NetworkGrant | None,
        *,
        principal_id: str,
        operation: str,
        destination: Destination,
        resolver: Callable[[str, int], Iterable[str]],
        proxy: ProxyRoute | None = None,
        redirect_hops: int = 0,
        now_ms: int,
    ) -> EgressAuthorization:
        addresses = tuple(resolver(destination.hostname, destination.port))
        return self.authorize_attempt(
            grant,
            principal_id=principal_id,
            operation=operation,
            destination=destination,
            resolved_addresses=addresses,
            proxy=proxy,
            redirect_hops=redirect_hops,
            now_ms=now_ms,
        )

    @staticmethod
    def enforce_response_limit(
        authorization: EgressAuthorization, received_bytes: int
    ) -> None:
        if received_bytes > authorization.max_response_bytes:
            raise EgressDenied("response.byte_limit")

    @staticmethod
    def _validate_addresses(
        hostname: str, addresses: tuple[str, ...], allowed: frozenset[str]
    ) -> None:
        if not addresses:
            raise EgressDenied("dns.empty")
        if hostname in _METADATA_HOSTS and "metadata" not in allowed:
            raise EgressDenied("address.metadata")
        for raw in addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise EgressDenied("dns.invalid") from exc
            category = address_class(address)
            if category in {"unspecified", "reserved"}:
                raise EgressDenied("address.class")
            if category not in allowed:
                raise EgressDenied(
                    "address.metadata" if category == "metadata" else "address.class"
                )

    def _validate_proxy(self, policy: ProxyPolicy, proxy: ProxyRoute | None) -> None:
        if policy.mode == "direct":
            if proxy is not None:
                raise EgressDenied("proxy.not_granted")
            return
        if proxy is None:
            raise EgressDenied("proxy.required")
        if (
            normalize_scheme(proxy.scheme) != policy.scheme
            or normalize_hostname(proxy.hostname) != policy.hostname
            or proxy.port != policy.port
        ):
            raise EgressDenied("proxy.bypass")
        self._validate_addresses(
            policy.hostname or "", proxy.resolved_addresses, policy.address_classes
        )


def address_class(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address_class(address.ipv4_mapped)
    if address in _METADATA_ADDRESSES:
        return "metadata"
    if address.is_unspecified:
        return "unspecified"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_multicast:
        return "multicast"
    if any(address.version == network.version and address in network for network in _RESERVED_NETWORKS):
        return "reserved"
    if address.is_private:
        return "private"
    if not address.is_global:
        return "reserved"
    return "public"


@dataclass(frozen=True)
class SecretHandle:
    identifier: str

    def __repr__(self) -> str:
        return "SecretHandle([opaque])"


class SecretVault:
    def __init__(self) -> None:
        self._values: dict[str, tuple[str, str, bytes]] = {}

    def __repr__(self) -> str:
        return f"SecretVault(registered={len(self._values)})"

    def register(self, principal_id: str, operation: str, value: bytes) -> SecretHandle:
        if not principal_id or not operation or not 4 <= len(value) <= 64 * 1024:
            raise SecretDenied("secret.invalid")
        handle = SecretHandle(f"secret_{secrets.token_urlsafe(24)}")
        self._values[handle.identifier] = (principal_id, operation, bytes(value))
        return handle

    def dispatch_with_secret(
        self,
        handle: SecretHandle,
        *,
        principal_id: str,
        operation: str,
        dispatch: Callable[[bytes], T],
    ) -> T:
        stored = self._values.get(handle.identifier)
        if stored is None:
            raise SecretDenied("secret.unknown")
        expected_principal, expected_operation, value = stored
        if expected_principal != principal_id or expected_operation != operation:
            raise SecretDenied("secret.scope")
        return dispatch(value)

    def revoke(self, handle: SecretHandle) -> bool:
        return self._values.pop(handle.identifier, None) is not None

    def redactor(self) -> Redactor:
        return Redactor(value for _, _, value in self._values.values())


class Redactor:
    def __init__(self, registered_values: Iterable[bytes] = ()) -> None:
        values = {bytes(value) for value in registered_values if len(value) >= 4}
        self._registered = tuple(sorted(values, key=len, reverse=True))

    def redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: REDACTED
                if _sensitive_field(str(key))
                else self.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, bytes):
            return self.redact_bytes(value)
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def redact_bytes(self, value: bytes) -> bytes:
        output = bytes(value)
        for secret in self._registered:
            output = output.replace(secret, REDACTED.encode())
        return self._scrub_common(output.decode("utf-8", errors="replace")).encode()

    def redact_text(self, value: str) -> str:
        return self.redact_bytes(value.encode()).decode()

    @staticmethod
    def _scrub_common(value: str) -> str:
        value = _COMMON_CREDENTIAL.sub(lambda match: match.group(1) + REDACTED, value)
        return _URL_USERINFO.sub(lambda match: match.group(1) + REDACTED + "@", value)


def _sensitive_field(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return bool(_SENSITIVE_FIELD.fullmatch(normalized)) or normalized.endswith(
        ("secret", "token")
    )
