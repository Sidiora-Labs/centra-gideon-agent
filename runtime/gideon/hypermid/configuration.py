"""Revisioned native configuration service over Hypermid authority."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import ContextConfig, ContextMode
from .config_store import ContextConfigStore

_CREDENTIAL_SECRET_KEYS = frozenset(
    {"value", "secret", "token", "password", "private_key", "credential"}
)


class ConfigurationContractError(ValueError):
    pass


def _mapping(value: object, operation: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationContractError(f"{operation} returned a non-object result")
    return dict(value)


def _contains_secret_field(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _CREDENTIAL_SECRET_KEYS
            or _contains_secret_field(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_field(child) for child in value)
    return False


class HypermidConfigurationService:
    def __init__(self, adapter: Any, store: ContextConfigStore) -> None:
        self.adapter = adapter
        self.store = store

    @property
    def client(self) -> Any:
        client = getattr(self.adapter, "client", None)
        if client is None:
            raise ConfigurationContractError("Hypermid is not connected")
        return client

    def runtime_snapshot(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        pending = self.store.pending()
        return {
            "config": snapshot.config.to_wire(),
            "policy_revision": snapshot.policy_revision,
            "config_digest": snapshot.config_digest,
            "source": "runtime",
            "editable": True,
            "restart_required": False,
            "validation": [],
            "pending": (
                {
                    "next_config_digest": pending.next_config_digest,
                    "expected_policy_revision": pending.expected_policy_revision,
                }
                if pending is not None
                else None
            ),
        }

    def stage_runtime(
        self,
        config: object,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> dict[str, Any]:
        next_config = ContextConfig.from_wire(config)
        current = self.store.snapshot()
        pending = self.store.stage(
            next_config,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        snapshot = self.store.snapshot()
        result = self.runtime_snapshot()
        result.update(
            {
                "status": "staged" if pending is not None else "unchanged",
                "previous_config_digest": current.config_digest,
                "next_config_digest": (
                    pending.next_config_digest if pending is not None else snapshot.config_digest
                ),
                "applies_at": "turn_boundary",
            }
        )
        return result

    async def models(self) -> dict[str, Any]:
        return _mapping(await self.client.request("models.list", {}), "models.list")

    async def plan_model_binding(
        self, duty: str, model_id: str, expected_digest: str
    ) -> dict[str, Any]:
        return _mapping(
            await self.client.request(
                "models.binding.plan",
                {"duty": duty, "model_id": model_id, "expected_digest": expected_digest},
            ),
            "models.binding.plan",
        )

    async def save_model_binding(
        self, duty: str, model_id: str, expected_digest: str
    ) -> dict[str, Any]:
        return _mapping(
            await self.client.request(
                "models.binding.set",
                {"duty": duty, "model_id": model_id, "expected_digest": expected_digest},
                effect_kind="durable",
            ),
            "models.binding.set",
        )

    async def probe_model(self, model_id: str) -> dict[str, Any]:
        return _mapping(
            await self.client.request(
                "models.probe", {"model_id": model_id}, effect_kind="idempotent"
            ),
            "models.probe",
        )

    async def credentials(self) -> dict[str, Any]:
        result = _mapping(
            await self.client.request("credentials.list", {}), "credentials.list"
        )
        if _contains_secret_field(result):
            raise ConfigurationContractError(
                "credential read returned a forbidden secret-bearing field"
            )
        return result

    async def put_credential(self, name: str, value: str) -> dict[str, Any]:
        result = _mapping(
            await self.client.request(
                "credentials.put",
                {"name": name, "value": value},
                effect_kind="durable",
            ),
            "credentials.put",
        )
        if _contains_secret_field(result):
            raise ConfigurationContractError(
                "credential write response contained a forbidden secret-bearing field"
            )
        return result

    async def validate_credential(self, name: str) -> dict[str, Any]:
        result = _mapping(
            await self.client.request(
                "credentials.validate", {"name": name}, effect_kind="idempotent"
            ),
            "credentials.validate",
        )
        if _contains_secret_field(result):
            raise ConfigurationContractError(
                "credential validation returned a forbidden secret-bearing field"
            )
        return result

    async def plan_credential_delete(self, name: str) -> dict[str, Any]:
        result = _mapping(
            await self.client.request("credentials.delete.plan", {"name": name}),
            "credentials.delete.plan",
        )
        if _contains_secret_field(result):
            raise ConfigurationContractError(
                "credential delete plan returned a forbidden secret-bearing field"
            )
        return result

    async def delete_credential(self, name: str) -> dict[str, Any]:
        result = _mapping(
            await self.client.request(
                "credentials.delete", {"name": name}, effect_kind="durable"
            ),
            "credentials.delete",
        )
        if _contains_secret_field(result):
            raise ConfigurationContractError(
                "credential delete response returned a forbidden secret-bearing field"
            )
        return result


def service_for(state: Any, adapter: Any, root: Path) -> HypermidConfigurationService:
    existing = getattr(state, "_hypermid_configuration_service", None)
    if isinstance(existing, HypermidConfigurationService):
        return existing
    store = ContextConfigStore(root / "hypermid" / "configuration.sqlite3")
    store.seed_if_empty(ContextConfig(mode=ContextMode(getattr(adapter, "mode", "off"))))
    service = HypermidConfigurationService(adapter, store)
    state._hypermid_configuration_service = service
    return service


__all__ = [
    "ConfigurationContractError",
    "HypermidConfigurationService",
    "service_for",
]
