from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from gideon.hypermid.models import MAX_SAFE_INTEGER


class ContextMode(str, Enum):
    OFF = "off"
    PASS_THROUGH = "pass_through"
    SHADOW = "shadow"
    PRIMARY = "primary"

    @property
    def capabilities(self) -> ModeCapabilities:
        if self is ContextMode.OFF:
            return ModeCapabilities(
                False, False, False, False, False, False, False, False, True
            )
        if self is ContextMode.PASS_THROUGH:
            return ModeCapabilities(
                True, True, False, False, False, False, False, False, True
            )
        if self is ContextMode.SHADOW:
            return ModeCapabilities(
                True, True, True, False, False, False, False, False, True
            )
        return ModeCapabilities(True, True, True, True, True, True, True, True, False)


class OverflowPolicy(str, Enum):
    RECLAIM_THEN_REFUSE = "reclaim_then_refuse"
    REFUSE_IMMEDIATELY = "refuse_immediately"


class RefusalPolicy(str, Enum):
    REFUSE = "refuse"
    COMPATIBLE_LAST_KNOWN_GOOD = "compatible_last_known_good"
    HOST_PASSTHROUGH = "host_passthrough"


@dataclass(frozen=True, slots=True)
class ModeCapabilities:
    hypermid_active: bool
    observes_health: bool
    computes_projection: bool
    allows_model_calls: bool
    allows_durable_writes: bool
    allows_publication: bool
    allows_serving_cursor_advance: bool
    requires_writer_lease: bool
    preserves_host_request_bytes: bool


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    background_summaries: bool = False
    reduction_tools: bool = False
    automatic_reclaim: bool = False
    nudges: bool = False
    subagent_contributions: bool = False
    synthetic_hook_blocks: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in self.to_wire().values()):
            raise ValueError("Hypermid feature flags must be booleans")

    def to_wire(self) -> dict[str, bool]:
        return {
            "background_summaries": self.background_summaries,
            "reduction_tools": self.reduction_tools,
            "automatic_reclaim": self.automatic_reclaim,
            "nudges": self.nudges,
            "subagent_contributions": self.subagent_contributions,
            "synthetic_hook_blocks": self.synthetic_hook_blocks,
        }

    @classmethod
    def from_wire(cls, value: object) -> FeatureFlags:
        raw = _exact_mapping(value, "features", set(cls.__dataclass_fields__))
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class ContextConfig:
    mode: ContextMode = ContextMode.OFF
    overflow_policy: OverflowPolicy = OverflowPolicy.RECLAIM_THEN_REFUSE
    refusal_policy: RefusalPolicy = RefusalPolicy.REFUSE
    features: FeatureFlags = field(default_factory=FeatureFlags)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ContextMode):
            raise ValueError("mode must be a ContextMode")
        if not isinstance(self.overflow_policy, OverflowPolicy):
            raise ValueError("overflow_policy must be an OverflowPolicy")
        if not isinstance(self.refusal_policy, RefusalPolicy):
            raise ValueError("refusal_policy must be a RefusalPolicy")
        if not isinstance(self.features, FeatureFlags):
            raise ValueError("features must be FeatureFlags")

    def to_wire(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "overflow_policy": self.overflow_policy.value,
            "refusal_policy": self.refusal_policy.value,
            "features": self.features.to_wire(),
        }

    @classmethod
    def from_wire(cls, value: object) -> ContextConfig:
        raw = _exact_mapping(
            value,
            "context configuration",
            {"mode", "overflow_policy", "refusal_policy", "features"},
        )
        try:
            return cls(
                mode=ContextMode(raw["mode"]),
                overflow_policy=OverflowPolicy(raw["overflow_policy"]),
                refusal_policy=RefusalPolicy(raw["refusal_policy"]),
                features=FeatureFlags.from_wire(raw["features"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid Hypermid context configuration") from exc

    @property
    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_wire(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def effective_features(self) -> FeatureFlags:
        return self.features if self.mode is ContextMode.PRIMARY else FeatureFlags()


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    config: ContextConfig
    policy_revision: int
    config_digest: str


@dataclass(frozen=True, slots=True)
class PendingConfiguration:
    expected_policy_revision: int
    previous_config_digest: str
    next_config: ContextConfig
    next_config_digest: str


@dataclass(frozen=True, slots=True)
class ConfigTransition:
    previous: ConfigSnapshot
    next: ConfigSnapshot


class ConfigError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TurnBoundaryConfig:
    def __init__(self, initial: ContextConfig | None = None) -> None:
        config = initial or ContextConfig()
        self._active = ConfigSnapshot(config, 1, config.digest)
        self._pending: PendingConfiguration | None = None
        self._lock = threading.RLock()

    @property
    def active(self) -> ConfigSnapshot:
        with self._lock:
            return self._active

    @property
    def pending(self) -> PendingConfiguration | None:
        with self._lock:
            return self._pending

    def stage(
        self, next_config: ContextConfig, *, expected_digest: str
    ) -> PendingConfiguration | None:
        with self._lock:
            if expected_digest != self._active.config_digest:
                raise ConfigError("STALE_CONFIGURATION")
            next_digest = next_config.digest
            if self._pending is not None:
                if self._pending.next_config_digest == next_digest:
                    return self._pending
                raise ConfigError("CHANGE_ALREADY_PENDING")
            if next_digest == self._active.config_digest:
                return None
            self._pending = PendingConfiguration(
                expected_policy_revision=self._active.policy_revision,
                previous_config_digest=self._active.config_digest,
                next_config=next_config,
                next_config_digest=next_digest,
            )
            return self._pending

    def apply_at_turn_boundary(self) -> ConfigTransition | None:
        with self._lock:
            pending = self._pending
            if pending is None:
                return None
            if (
                pending.expected_policy_revision != self._active.policy_revision
                or pending.previous_config_digest != self._active.config_digest
            ):
                raise ConfigError("STALE_CONFIGURATION")
            next_revision = self._active.policy_revision + 1
            if next_revision > MAX_SAFE_INTEGER:
                raise ConfigError("POLICY_REVISION_EXHAUSTED")
            previous = self._active
            next_snapshot = ConfigSnapshot(
                config=pending.next_config,
                policy_revision=next_revision,
                config_digest=pending.next_config_digest,
            )
            self._active = next_snapshot
            self._pending = None
            return ConfigTransition(previous=previous, next=next_snapshot)


class DaemonTransport(str, Enum):
    UNIX_SOCKET = "unix_socket"
    LOOPBACK_TCP = "loopback_tcp"


class LocalAuthMethod(str, Enum):
    PEER_AND_HMAC = "peer_and_hmac"
    HMAC_TOKEN_FILE = "hmac_token_file"


@dataclass(frozen=True, slots=True)
class LocalAuthConfig:
    method: LocalAuthMethod
    token_file: str
    require_peer_identity: bool

    def __post_init__(self) -> None:
        if not isinstance(self.method, LocalAuthMethod):
            raise ValueError("auth method must be a LocalAuthMethod")
        _bounded_string(self.token_file, "token_file")
        if type(self.require_peer_identity) is not bool:
            raise ValueError("require_peer_identity must be a boolean")

    def to_wire(self) -> dict[str, object]:
        return {
            "method": self.method.value,
            "token_file": self.token_file,
            "require_peer_identity": self.require_peer_identity,
        }

    @classmethod
    def from_wire(cls, value: object) -> LocalAuthConfig:
        raw = _exact_mapping(
            value, "daemon auth", {"method", "token_file", "require_peer_identity"}
        )
        try:
            return cls(
                method=LocalAuthMethod(raw["method"]),
                token_file=raw["token_file"],
                require_peer_identity=raw["require_peer_identity"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid daemon authentication configuration") from exc


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    transport: DaemonTransport
    endpoint: str
    auth: LocalAuthConfig
    executable: str = "hypermid-daemon"
    start_on_demand: bool = True
    request_timeout_ms: int = 30_000
    shutdown_timeout_ms: int = 10_000
    connection_record: str | None = None
    mcp_config: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.transport, DaemonTransport):
            raise ValueError("transport must be a DaemonTransport")
        if not isinstance(self.auth, LocalAuthConfig):
            raise ValueError("auth must be a LocalAuthConfig")
        _bounded_string(self.endpoint, "endpoint")
        _bounded_string(self.executable, "executable")
        if self.connection_record is not None:
            _bounded_string(self.connection_record, "connection_record")
        if self.mcp_config is not None:
            _bounded_string(self.mcp_config, "mcp_config")
        if type(self.start_on_demand) is not bool:
            raise ValueError("start_on_demand must be a boolean")
        _bounded_integer(self.request_timeout_ms, "request_timeout_ms", 100, 600_000)
        _bounded_integer(self.shutdown_timeout_ms, "shutdown_timeout_ms", 100, 120_000)
        expected = (
            (LocalAuthMethod.PEER_AND_HMAC, True)
            if self.transport is DaemonTransport.UNIX_SOCKET
            else (LocalAuthMethod.HMAC_TOKEN_FILE, False)
        )
        if (self.auth.method, self.auth.require_peer_identity) != expected:
            raise ValueError("daemon transport and authentication mode do not match")

    @property
    def connection_record_path(self) -> str:
        if self.connection_record is None:
            raise ConfigError("CONNECTION_RECORD_UNAVAILABLE")
        return self.connection_record

    def with_connection_record(self, path: str) -> DaemonConfig:
        _bounded_string(path, "connection_record")
        return replace(self, connection_record=path)

    def to_wire(self) -> dict[str, object]:
        return {
            "transport": self.transport.value,
            "endpoint": self.endpoint,
            "executable": self.executable,
            "start_on_demand": self.start_on_demand,
            "auth": self.auth.to_wire(),
            "request_timeout_ms": self.request_timeout_ms,
            "shutdown_timeout_ms": self.shutdown_timeout_ms,
        }

    @classmethod
    def from_wire(cls, value: object) -> DaemonConfig:
        keys = {
            "transport",
            "endpoint",
            "executable",
            "start_on_demand",
            "auth",
            "request_timeout_ms",
            "shutdown_timeout_ms",
        }
        raw = _exact_mapping(value, "daemon configuration", keys)
        try:
            return cls(
                transport=DaemonTransport(raw["transport"]),
                endpoint=raw["endpoint"],
                executable=raw["executable"],
                start_on_demand=raw["start_on_demand"],
                auth=LocalAuthConfig.from_wire(raw["auth"]),
                request_timeout_ms=raw["request_timeout_ms"],
                shutdown_timeout_ms=raw["shutdown_timeout_ms"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid Hypermid daemon configuration") from exc


def _exact_mapping(
    value: object, name: str, expected_keys: set[str]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ValueError(f"{name} must contain exactly {sorted(expected_keys)}")
    return value


def _bounded_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{name} must be a non-empty string at most 4096 characters")
    return value


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
