"""Provider definitions, deferred construction, and persisted entry replay."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

from gideon.integrations.llm.base import ModelProvider
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.catalog import ModelCatalog

logger = logging.getLogger(__name__)
ProviderFactory = Callable[..., ModelProvider]
CatalogFactory = Callable[..., ModelCatalog]
_Value = TypeVar("_Value")


class ProviderResolutionError(Exception):
    """A provider definition or requested binding cannot be resolved."""


class CredentialMissing(ProviderResolutionError):
    """Construction requires a credential that is unavailable."""


@dataclass(frozen=True)
class ProviderEntry:
    name: str
    type: str
    model: str
    options: dict[str, object] = field(default_factory=dict)
    credential: str | None = None
    declared_capabilities: frozenset[Capability] = field(default_factory=frozenset)


def _required(table: Mapping[str, _Value], key: str, subject: str) -> _Value:
    try:
        return table[key]
    except KeyError as error:
        raise ProviderResolutionError(
            f"unknown provider {subject} {key!r}; "
            f"known {'entries' if subject == 'entry' else 'types'}: {sorted(table)}"
        ) from error


def _validate_declaration(
    entry: ProviderEntry, available: ProviderCapability | None
) -> None:
    if available is None:
        return
    unsupported = entry.declared_capabilities.difference(available.capabilities)
    if unsupported:
        raise ProviderResolutionError(
            f"provider entry {entry.name!r} declares capabilities "
            f"{sorted(item.value for item in unsupported)} not supported by type {entry.type!r}"
        )


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._capabilities: dict[str, ProviderCapability] = {}
        self._entries: dict[str, ProviderEntry] = {}
        self._catalog_factories: dict[str, CatalogFactory] = {}

    def register_type(self, cap: ProviderCapability, factory: ProviderFactory) -> None:
        if cap.type in self._factories:
            raise ProviderResolutionError(
                f"provider type {cap.type!r} is already registered"
            )
        self._factories.update({cap.type: factory})
        self._capabilities.update({cap.type: cap})

    def register_entry(self, entry: ProviderEntry) -> None:
        if entry.name not in self._entries:
            _validate_declaration(entry, self._capabilities.get(entry.type))
            self._entries[entry.name] = entry

    def unregister_entry(self, name: str) -> None:
        self._entries.pop(name, None)

    def list_entries(self) -> list[ProviderEntry]:
        return [self.get_entry(name) for name in self._entries]

    def get_entry(self, name: str) -> ProviderEntry:
        from gideon.workspace.capabilities.platform.connections import effective_entry

        return effective_entry(_required(self._entries, name, "entry"))

    def capability_of(self, type_: str) -> ProviderCapability:
        return _required(self._capabilities, type_, "type")

    def build(
        self, name: str, *, session_key: str | None = None, **kwargs: object
    ) -> ModelProvider:
        selected = self.get_entry(name)
        from dataclasses import replace

        from gideon.workspace.capabilities.platform.connections import allowed

        model = str(kwargs.get("model") or selected.model)
        if not allowed(model, selected.options.get("model_access", {})):
            raise ProviderResolutionError(
                "Model is excluded by the connection access policy"
            )
        selected = replace(
            selected,
            model=model,
            options={
                key: value
                for key, value in selected.options.items()
                if key not in {"connection_id", "model_access"}
            },
        )
        invocation = dict(kwargs, entry=selected, session_key=session_key)
        return self._factories[selected.type](**invocation)

    def register_catalog(self, type_: str, factory: CatalogFactory) -> None:
        self._catalog_factories.update({type_: factory})

    def catalog_of(self, type_: str) -> CatalogFactory | None:
        return self._catalog_factories.get(type_)

    def build_catalog(self, entry: ProviderEntry) -> ModelCatalog | None:
        from gideon.workspace.capabilities.platform.connections import (
            ScopedCatalog,
            effective_entry,
        )

        entry = effective_entry(entry)
        constructor = self.catalog_of(entry.type)
        if constructor is not None:
            try:
                options = dict(entry.options or {})
                if entry.credential:
                    from gideon.core.config.loader import config_dir
                    from gideon.integrations.llm.credentials import CredentialStore

                    options["api_key"] = (
                        CredentialStore(config_dir()).resolve(entry.credential).secret
                        or ""
                    )
                catalog = constructor(options, model=entry.model)
                return (
                    ScopedCatalog(catalog, options["model_access"])
                    if "model_access" in options
                    else catalog
                )
            except Exception:
                logger.debug(
                    "Catalog construction failed for %r", entry.type, exc_info=True
                )
        return None


_default_registry: ProviderRegistry | None = None
_CONFIG_TYPE_MAP: dict[str, str] = {}


def get_default_registry() -> ProviderRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ProviderRegistry()
    return _default_registry


def set_default_registry(registry: ProviderRegistry) -> None:
    global _default_registry
    _default_registry = registry


def reset_default_registry() -> None:
    global _default_registry
    _default_registry = None


def canonical_provider_type(ptype: str) -> str:
    return _CONFIG_TYPE_MAP.get(ptype, ptype)


SCRIPTED_PROVIDER_TYPE = "scripted"
SCRIPTED_PROVIDER_ENV = "GIDEON_SCRIPTED_MODEL_SCRIPT"
SCRIPTED_PROVIDER_ENTRY_NAME = "Scripted"
SCRIPTED_PROVIDER_MODEL = "scripted-1"
SCRIPTED_PROVIDER_CAPABILITY = ProviderCapability(
    type=SCRIPTED_PROVIDER_TYPE,
    capabilities=frozenset({Capability.CHAT, Capability.CODE_TOOLS}),
    supports_streaming=False,
    supports_tools=True,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=0,
    notes=(
        "Deterministic offline fixture: replays the script JSON named by "
        f"{SCRIPTED_PROVIDER_ENV}. Zero network, no credential. Registered only "
        "while that variable is set; never present in a normal home."
    ),
)


def scripted_provider_enabled() -> bool:
    return bool(os.environ.get(SCRIPTED_PROVIDER_ENV, "").strip())


def _scripted_factory(
    *, entry: ProviderEntry, session_key: str | None = None, **kwargs: object
) -> ModelProvider:
    from gideon.integrations.llm.scripted import ScriptedProvider

    return ScriptedProvider()


def register_scripted_provider_type() -> bool:
    enabled = scripted_provider_enabled()
    if enabled:
        registry = get_default_registry()
        if SCRIPTED_PROVIDER_TYPE not in registry._factories:
            registry.register_type(SCRIPTED_PROVIDER_CAPABILITY, _scripted_factory)
        registry.register_entry(
            ProviderEntry(
                name=SCRIPTED_PROVIDER_ENTRY_NAME,
                type=SCRIPTED_PROVIDER_TYPE,
                model=SCRIPTED_PROVIDER_MODEL,
                credential=None,
                declared_capabilities=SCRIPTED_PROVIDER_CAPABILITY.capabilities,
            )
        )
    return enabled


def _configured_rows() -> list[object]:
    try:
        from gideon.core.config.loader import config_path

        path = config_path()
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        rows = document.get("providers") if isinstance(document, dict) else None
        return rows if isinstance(rows, list) else []
    except Exception:
        logger.debug("Provider configuration could not be read", exc_info=True)
        return []


def _configured_entry(row: object, registry: ProviderRegistry) -> ProviderEntry | None:
    if not isinstance(row, dict):
        return None
    name, configured_type = (
        str(row.get(key) or "").strip() for key in ("name", "type")
    )
    if not name or not configured_type or name in registry._entries:
        return None
    provider_type = canonical_provider_type(configured_type)
    descriptor = registry._capabilities.get(provider_type)
    options = dict(row.get("options") or {})
    if provider_type != configured_type:
        options["_original_type"] = configured_type
    return ProviderEntry(
        name=name,
        type=provider_type,
        model=str(row.get("model") or ""),
        options=options,
        credential=row.get("credential"),
        declared_capabilities=descriptor.capabilities if descriptor else frozenset(),
    )


def sync_entries_from_config() -> int:
    count = int(register_scripted_provider_type())
    registry = get_default_registry()
    for row in _configured_rows():
        entry = _configured_entry(row, registry)
        if entry is None:
            continue
        try:
            registry.register_entry(entry)
        except ProviderResolutionError:
            logger.debug("Skipping invalid configured provider %r", entry.name)
        else:
            count += 1
    return count
