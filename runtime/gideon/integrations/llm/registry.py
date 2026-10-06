"""Provider definitions, deferred construction, and persisted entry replay."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

from gideon.integrations.llm.base import ModelProvider
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.catalog import ModelCatalog

logger = logging.getLogger(__name__)
ProviderFactory = Callable[..., ModelProvider]
CatalogFactory = Callable[..., ModelCatalog]
ReadinessProbe = Callable[..., "tuple[str, str] | None"]
_Value = TypeVar("_Value")


class ProviderResolutionError(Exception):
    """A provider definition or requested binding cannot be resolved."""


class CredentialMissing(ProviderResolutionError):
    """Construction requires a credential that is unavailable."""


DEFAULT_MODEL_OPTION = "default_model"


def own_model(model: object, options: object) -> str:
    """Return the model an entry names itself, or an empty string when it names none."""
    named = str(model or "").strip()
    if named:
        return named
    if isinstance(options, dict):
        return str(options.get(DEFAULT_MODEL_OPTION) or "").strip()
    return ""


def no_model_chosen(entry_name: str) -> tuple[str, str]:
    return (
        f"no model is chosen for “{entry_name}”",
        "choose one of its models in Settings → Models",
    )


NO_MODEL_NAMED = "No model is chosen for this call. Choose one in Settings → Models."


def require_model(model: object) -> str:
    """Return a non-empty wire model id or refuse before sending a request."""
    named = str(model or "")
    if not named.strip():
        raise ProviderResolutionError(NO_MODEL_NAMED)
    return named


@dataclass(frozen=True)
class ProviderEntry:
    name: str
    type: str
    model: str
    options: dict[str, object] = field(default_factory=dict)
    credential: str | None = None
    declared_capabilities: frozenset[Capability] = field(default_factory=frozenset)

    @property
    def own_model(self) -> str:
        """The entry model, else its configured Default Model, or empty when unbound."""
        return own_model(self.model, self.options)


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


def _app_callable_owner(factory):
    from gideon.extensions.apps.code_provenance import loaded_app, owner
    app = owner()
    module = sys.modules.get(getattr(factory, "__module__", ""))
    path = getattr(module, "__file__", None)
    if app is None or module is None or not isinstance(path, str) or loaded_app(path) != app:
        return None
    globals_ = getattr(factory, "__globals__", None)
    if globals_ is not None and globals_ is not vars(module):
        return None
    return app, module, factory


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._factory_owners: dict[str, tuple[str, object, object]] = {}
        self._catalog_owners: dict[str, tuple[str, object, object]] = {}
        self._capabilities: dict[str, ProviderCapability] = {}
        self._entries: dict[str, ProviderEntry] = {}
        self._catalog_factories: dict[str, CatalogFactory] = {}
        self._readiness: dict[str, ReadinessProbe] = {}
        self._in_process_types: set[str] = set()
        self._passes_on: dict[str, Callable[[ProviderEntry, str], bool]] = {}

    def register_type(
        self,
        cap: ProviderCapability,
        factory: ProviderFactory,
        *,
        readiness: ReadinessProbe | None = None,
        in_process: bool = False,
        passes_on: Callable[[ProviderEntry, str], bool] | None = None,
    ) -> None:
        if cap.type in self._factories:
            raise ProviderResolutionError(
                f"provider type {cap.type!r} is already registered"
            )
        ownership = _app_callable_owner(factory)
        if ownership is not None:
            self._factory_owners[cap.type] = ownership
        self._factories.update({cap.type: factory})
        self._capabilities.update({cap.type: cap})
        if readiness is not None:
            self._readiness[cap.type] = readiness
        if in_process:
            self._in_process_types.add(cap.type)
        if passes_on is not None:
            self._passes_on[cap.type] = passes_on

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

    def not_ready(self, entry: ProviderEntry, *, implicit: bool) -> tuple[str, str] | None:
        if entry.type not in self._factories:
            return (
                f"The provider type for “{entry.name}” is not available",
                "install or enable its provider app, or choose another model in Settings → Models",
            )
        probe = self._readiness.get(entry.type)
        if probe is None:
            return None
        try:
            verdict = probe(entry, implicit=implicit)
        except Exception:
            logger.warning("Provider readiness probe failed for %r", entry.type, exc_info=True)
            return None
        return verdict if isinstance(verdict, tuple) and len(verdict) == 2 else None

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
        ownership = _app_callable_owner(factory)
        if ownership is not None:
            self._catalog_owners[type_] = ownership
        else:
            self._catalog_owners.pop(type_, None)
        self._catalog_factories.update({type_: factory})

    def unregister_app_module(self, app: str, module_name: str, module: object) -> int:
        if sys.modules.get(module_name) is not module:
            return 0
        removed = 0
        for type_, (registered_app, registered_module, factory) in tuple(self._factory_owners.items()):
            if registered_app != app or registered_module is not module or self._factories.get(type_) is not factory:
                continue
            self._factory_owners.pop(type_, None)
            self._factories.pop(type_, None)
            self._capabilities.pop(type_, None)
            self._readiness.pop(type_, None)
            self._passes_on.pop(type_, None)
            self._in_process_types.discard(type_)
            removed += 1
        for type_, (registered_app, registered_module, factory) in tuple(self._catalog_owners.items()):
            if registered_app == app and registered_module is module and self._catalog_factories.get(type_) is factory:
                self._catalog_owners.pop(type_, None)
                self._catalog_factories.pop(type_, None)
        return removed

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


def unregister_app_module_types(app: str, module_name: str, module: object) -> int:
    if _default_registry is None:
        return 0
    return _default_registry.unregister_app_module(app, module_name, module)


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


def serving_entry(provider: str) -> ProviderEntry | None:
    """Resolve the configured serving entry by its exact user-facing name."""
    try:
        return get_default_registry().get_entry(str(provider or ""))
    except Exception:
        return None


def serving_endpoint(entry: ProviderEntry) -> str:
    """Mirror the selected provider factory's endpoint precedence."""
    options = entry.options if isinstance(entry.options, dict) else {}
    if entry.type == "ollama":
        return str(
            options.get("endpoint")
            or options.get("base_url")
            or "http://localhost:11434"
        ).strip()
    from gideon.integrations.llm.branded_specs import registered_spec

    spec = registered_spec(entry.type)
    return str(
        options.get("base_url")
        or options.get("endpoint")
        or (spec.default_base_url if spec is not None else "")
        or getattr(get_default_registry()._capabilities.get(entry.type), "default_endpoint", "")
    ).strip()


def endpoint_on_this_machine(provider: str | ProviderEntry, *, actual_provider=None) -> bool:
    """Endpoint location, independent of where the model is executed."""
    entry = provider if isinstance(provider, ProviderEntry) else serving_entry(provider)
    endpoint = serving_endpoint(entry) if entry is not None else ""
    if actual_provider is not None:
        endpoint = str(getattr(actual_provider, "endpoint", "") or getattr(actual_provider, "_base_url", "") or endpoint).strip()
    import ipaddress
    from urllib.parse import urlsplit
    host = urlsplit(endpoint).hostname if endpoint else None
    if host == "localhost" or (host and host.endswith(".localhost")):
        return True
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def served_on_this_machine(provider: str | ProviderEntry, model: str) -> bool:
    """Execution authority from a registered type declaration and its model probe."""
    registry = get_default_registry()
    entry = provider if isinstance(provider, ProviderEntry) else serving_entry(provider)
    if entry is None:
        return False
    if entry.type in registry._in_process_types:
        return True
    descriptor = registry._capabilities.get(entry.type)
    if descriptor is None or not descriptor.hosts_model or not endpoint_on_this_machine(entry):
        return False
    probe = registry._passes_on.get(entry.type)
    if probe is not None:
        try:
            if probe(entry, str(model or "")):
                return False
        except Exception:
            logger.warning("provider pass-on probe failed for %r", entry.type, exc_info=True)
            return False
    return True


def serving_is_local(provider: str | ProviderEntry, *, model: str = "", actual_provider=None) -> bool:
    entry = provider if isinstance(provider, ProviderEntry) else serving_entry(provider)
    return served_on_this_machine(provider, model or (entry.own_model if entry is not None else ""))


def serving_type(provider: str) -> str:
    """Return the exact provider type bound to an entry; accept exact type IDs too."""
    entry = serving_entry(provider)
    if entry is not None:
        return entry.type
    requested = str(provider or "").strip()
    if requested in get_default_registry()._factories:
        return requested
    return canonical_provider_type(requested)


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
            registry.register_type(
                SCRIPTED_PROVIDER_CAPABILITY, _scripted_factory, in_process=True
            )
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
