"""Cross-language cache, model catalog, and provider-usage contracts."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from gideon.hypermid.foundation import Id


class CacheConflict(RuntimeError):
    pass


class Durability(str, Enum):
    MEMORY = "memory"
    PROCESS = "process"
    DURABLE = "durable"


@dataclass(frozen=True, slots=True)
class FrozenPass:
    boundary_id: Id
    rendered: bytes


@dataclass(frozen=True, slots=True)
class CacheState:
    version: int
    durability: Durability
    frozen: FrozenPass | None = None
    deferred_passes: int = 0
    reconciliation_pending: bool = False

    @classmethod
    def empty(cls, durability: Durability = Durability.DURABLE) -> CacheState:
        return cls(version=0, durability=durability)

    def _check(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise CacheConflict(
                f"cache version is stale: expected {expected_version}, current {self.version}"
            )

    def rebuild_local(
        self, expected_version: int, boundary_id: str, rendered: bytes
    ) -> CacheState:
        self._check(expected_version)
        return replace(
            self,
            version=self.version + 1,
            frozen=FrozenPass(Id(boundary_id), bytes(rendered)),
            reconciliation_pending=False,
        )

    def defer(
        self, expected_version: int, *, boundary_present: bool
    ) -> tuple[CacheState, bytes | None]:
        self._check(expected_version)
        if self.frozen is None:
            return replace(self, version=self.version + 1), None
        if not boundary_present:
            return (
                replace(
                    self,
                    version=self.version + 1,
                    reconciliation_pending=True,
                ),
                None,
            )
        return (
            replace(
                self,
                version=self.version + 1,
                deferred_passes=self.deferred_passes + 1,
            ),
            bytes(self.frozen.rendered),
        )

    def rebuild_full(
        self, expected_version: int, boundary_id: str, rendered: bytes
    ) -> CacheState:
        self._check(expected_version)
        return replace(
            self,
            version=self.version + 1,
            frozen=FrozenPass(Id(boundary_id), bytes(rendered)),
            deferred_passes=0,
            reconciliation_pending=False,
        )

    def with_durability(
        self, expected_version: int, durability: Durability
    ) -> CacheState:
        self._check(expected_version)
        if self.durability == durability:
            return replace(self, version=self.version + 1)
        return CacheState(version=self.version + 1, durability=durability)

    def to_wire(self) -> dict[str, Any]:
        frozen = None
        if self.frozen is not None:
            frozen = {
                "boundary_id": str(self.frozen.boundary_id),
                "rendered": list(self.frozen.rendered),
            }
        return {
            "version": self.version,
            "durability": self.durability.value,
            "frozen": frozen,
            "deferred_passes": self.deferred_passes,
            "reconciliation_pending": self.reconciliation_pending,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> CacheState:
        frozen_value = value.get("frozen")
        frozen = None
        if frozen_value is not None:
            if not isinstance(frozen_value, Mapping):
                raise ValueError("frozen cache pass must be an object")
            rendered = frozen_value.get("rendered")
            if not isinstance(rendered, list) or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 0 <= item <= 255
                for item in rendered
            ):
                raise ValueError("frozen rendered bytes are invalid")
            frozen = FrozenPass(Id(frozen_value.get("boundary_id")), bytes(rendered))
        return cls(
            version=_nonnegative_int(value.get("version"), "version"),
            durability=Durability(value.get("durability")),
            frozen=frozen,
            deferred_passes=_nonnegative_int(
                value.get("deferred_passes"), "deferred_passes"
            ),
            reconciliation_pending=_boolean(
                value.get("reconciliation_pending"), "reconciliation_pending"
            ),
        )


@dataclass(frozen=True, slots=True)
class FileCacheStore:
    path: Path

    def load(self) -> CacheState | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("cache document must be an object")
        return CacheState.from_wire(value)

    def compare_and_swap(self, expected_version: int | None, state: CacheState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with os.fdopen(descriptor, "r+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                current = self.load()
                current_version = current.version if current is not None else None
                if current_version != expected_version:
                    raise CacheConflict(
                        f"cache version is stale: expected {expected_version}, current {current_version}"
                    )
                encoded = json.dumps(
                    state.to_wire(), sort_keys=True, separators=(",", ":")
                )
                temporary_descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.", dir=self.path.parent
                )
                try:
                    os.fchmod(temporary_descriptor, 0o600)
                    with os.fdopen(
                        temporary_descriptor, "w", encoding="utf-8"
                    ) as output:
                        output.write(encoded + "\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary_name, self.path)
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except BaseException:
                    try:
                        os.unlink(temporary_name)
                    except FileNotFoundError:
                        pass
                    raise
        except BaseException:
            if not isinstance(descriptor, int):
                os.close(descriptor)
            raise


@dataclass(frozen=True, slots=True)
class PriceRate:
    nanodollars_per_million_tokens: int


@dataclass(frozen=True, slots=True)
class ModelRecord:
    id: str
    modalities: tuple[str, ...]
    context_tokens: int | None
    input_price: PriceRate | None
    output_price: PriceRate | None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    id: str
    models: tuple[ModelRecord, ...]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    providers: tuple[ProviderRecord, ...]

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> ModelCatalog:
        providers = value.get("providers")
        if not isinstance(providers, list):
            raise ValueError("catalog providers must be an array")
        return cls(tuple(_provider(value) for value in providers))


def _provider(value: object) -> ProviderRecord:
    if not isinstance(value, Mapping):
        raise ValueError("provider must be an object")
    provider_id = _text(value.get("id"), "provider id")
    models = value.get("models", [])
    if not isinstance(models, list):
        raise ValueError("provider models must be an array")
    return ProviderRecord(
        id=provider_id,
        models=tuple(_model(model) for model in models),
        raw={key: item for key, item in value.items() if key not in {"id", "models"}},
    )


def _model(value: object) -> ModelRecord:
    if not isinstance(value, Mapping):
        raise ValueError("model must be an object")
    modalities = value.get("modalities", [])
    if not isinstance(modalities, list) or not all(
        isinstance(item, str) for item in modalities
    ):
        raise ValueError("model modalities must be strings")
    context = value.get("context_tokens")
    return ModelRecord(
        id=_text(value.get("id"), "model id"),
        modalities=tuple(modalities),
        context_tokens=(
            _nonnegative_int(context, "context_tokens") if context is not None else None
        ),
        input_price=_price(value, "input_price"),
        output_price=_price(value, "output_price"),
        raw={
            key: item
            for key, item in value.items()
            if key
            not in {
                "id",
                "modalities",
                "context_tokens",
                "input_price",
                "output_price",
            }
        },
    )


def _price(value: Mapping[str, Any], name: str) -> PriceRate | None:
    if name not in value:
        return None
    raw = value[name]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError(f"{name} must be a non-negative decimal")
    try:
        decimal = Decimal(str(raw))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a non-negative decimal") from error
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"{name} must be a non-negative decimal")
    scaled = (decimal * Decimal(1_000_000_000)).quantize(
        Decimal(1), rounding=ROUND_HALF_EVEN
    )
    if decimal != 0 and scaled == 0:
        raise ValueError(f"{name} is below nanodollar precision")
    amount = int(scaled)
    if amount > 2**64 - 1:
        raise ValueError(f"{name} exceeds supported range")
    return PriceRate(amount)


@dataclass(frozen=True, slots=True)
class Money:
    minor_units: int
    exponent: int
    currency: str

    def __post_init__(self) -> None:
        if not 0 <= self.exponent <= 18:
            raise ValueError("money exponent is outside the supported range")
        if (
            len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isupper()
        ):
            raise ValueError("currency must be three uppercase ASCII letters")


@dataclass(frozen=True, slots=True)
class UsageWindow:
    kind: str
    model_id: str | None
    raw_percent_basis_points: int | None
    effective_percent_basis_points: int | None
    reset_at_ms: int | None
    regeneration: Mapping[str, int] | None

    def __post_init__(self) -> None:
        if self.kind not in {"primary", "secondary", "tertiary", "per_model"}:
            raise ValueError("usage window kind is invalid")
        if (self.kind == "per_model") != (self.model_id is not None):
            raise ValueError("only per-model windows carry model_id")
        for value in (
            self.raw_percent_basis_points,
            self.effective_percent_basis_points,
        ):
            if value is not None and not 0 <= value <= 10_000:
                raise ValueError("usage percentage exceeds 100 percent")


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    label: str
    subject: str
    provenance: str

    def __post_init__(self) -> None:
        if self.provenance not in {
            "provider_verified",
            "oauth_subject",
            "local_configuration",
            "imported",
        }:
            raise ValueError("account provenance is not recognized")


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    provider_id: str
    windows: tuple[UsageWindow, ...]
    breakdowns: tuple[Mapping[str, Any], ...]
    balances: tuple[Mapping[str, Any], ...]
    saved_credits: Money | None
    account: AccountIdentity | None
    observed_at_ms: int
    stale_after_ms: int | None
    error: Mapping[str, Any] | None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> ProviderUsage:
        known = {
            "provider_id",
            "windows",
            "breakdowns",
            "balances",
            "saved_credits",
            "account",
            "observed_at_ms",
            "stale_after_ms",
            "error",
        }
        windows = value.get("windows", [])
        breakdowns = value.get("breakdowns", [])
        balances = value.get("balances", [])
        if not all(
            isinstance(items, list) for items in (windows, breakdowns, balances)
        ):
            raise ValueError("usage collections must be arrays")
        account = value.get("account")
        credits = value.get("saved_credits")
        return cls(
            provider_id=_text(value.get("provider_id"), "provider_id"),
            windows=tuple(_usage_window(item) for item in windows),
            breakdowns=tuple(_object(item, "breakdown") for item in breakdowns),
            balances=tuple(_object(item, "balance") for item in balances),
            saved_credits=_money(credits) if credits is not None else None,
            account=(
                AccountIdentity(
                    label=_text(
                        _object(account, "account").get("label"), "account label"
                    ),
                    subject=_text(
                        _object(account, "account").get("subject"), "account subject"
                    ),
                    provenance=_text(
                        _object(account, "account").get("provenance"),
                        "account provenance",
                    ),
                )
                if account is not None
                else None
            ),
            observed_at_ms=_nonnegative_int(
                value.get("observed_at_ms"), "observed_at_ms"
            ),
            stale_after_ms=(
                _nonnegative_int(value.get("stale_after_ms"), "stale_after_ms")
                if value.get("stale_after_ms") is not None
                else None
            ),
            error=(
                dict(_object(value.get("error"), "error"))
                if value.get("error") is not None
                else None
            ),
            raw={key: item for key, item in value.items() if key not in known},
        )


def _usage_window(value: object) -> UsageWindow:
    raw = _object(value, "usage window")
    return UsageWindow(
        kind=_text(raw.get("kind"), "usage window kind"),
        model_id=(
            _text(raw.get("model_id"), "model_id")
            if raw.get("model_id") is not None
            else None
        ),
        raw_percent_basis_points=(
            _nonnegative_int(raw.get("raw_percent_basis_points"), "raw percentage")
            if raw.get("raw_percent_basis_points") is not None
            else None
        ),
        effective_percent_basis_points=(
            _nonnegative_int(
                raw.get("effective_percent_basis_points"), "effective percentage"
            )
            if raw.get("effective_percent_basis_points") is not None
            else None
        ),
        reset_at_ms=(
            _nonnegative_int(raw.get("reset_at_ms"), "reset_at_ms")
            if raw.get("reset_at_ms") is not None
            else None
        ),
        regeneration=(
            dict(_object(raw.get("regeneration"), "regeneration"))
            if raw.get("regeneration") is not None
            else None
        ),
    )


def _money(value: object) -> Money:
    raw = _object(value, "money")
    minor = raw.get("minor_units")
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise ValueError("minor_units must be an integer")
    return Money(
        minor_units=minor,
        exponent=_nonnegative_int(raw.get("exponent"), "money exponent"),
        currency=_text(raw.get("currency"), "currency"),
    )


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


__all__ = [
    "AccountIdentity",
    "CacheConflict",
    "CacheState",
    "Durability",
    "FileCacheStore",
    "FrozenPass",
    "ModelCatalog",
    "ModelRecord",
    "Money",
    "PriceRate",
    "ProviderRecord",
    "ProviderUsage",
    "UsageWindow",
]
