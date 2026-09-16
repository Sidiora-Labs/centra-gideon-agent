"""Provider app declarations, protocol construction, and credential selection."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from importlib import import_module
from typing import TYPE_CHECKING, Any

from gideon.integrations.llm.capabilities import Capability
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.prompt_cache import PromptCache
from gideon.integrations.llm.registry import CredentialMissing, ProviderEntry
from gideon.integrations.llm.subscription_credentials import (
    resolve_subscription_credential,
)

if TYPE_CHECKING:
    from gideon.integrations.llm.base import ModelProvider


def _prices(value: dict) -> dict[str, dict[str, float]]:
    return {
        str(pattern): {str(key): float(amount) for key, amount in dict(row).items()}
        for pattern, row in value.items()
    }


@dataclass(frozen=True)
class BrandedProviderSpec:
    type: str
    protocol: str = "openai"
    default_base_url: str = ""
    api_key_env: str = ""
    default_model: str = ""
    max_tokens: int | None = None
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    fallback_models: tuple[dict[str, Any], ...] = ()
    notes: str = ""
    prompt_cache: PromptCache = PromptCache.NONE
    pricing: dict[str, dict[str, float]] = field(default_factory=dict, hash=False)
    credential_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        document = {item.name: getattr(self, item.name) for item in fields(self)}
        document.update(
            capabilities=sorted(flag.value for flag in self.capabilities),
            fallback_models=list(map(dict, self.fallback_models)),
            prompt_cache=self.prompt_cache.value,
            pricing=_prices(self.pricing),
        )
        return document

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrandedProviderSpec:
        defaults = cls(type="")
        arguments = {}
        for item in fields(cls):
            baseline = getattr(defaults, item.name)
            value = data.get(item.name, baseline)
            arguments[item.name] = str(value) if type(baseline) is str else value
        arguments.update(
            max_tokens=(
                int(data["max_tokens"]) if data.get("max_tokens") is not None else None
            ),
            capabilities=frozenset(map(Capability, data.get("capabilities") or ())),
            fallback_models=tuple(map(dict, data.get("fallback_models") or ())),
            prompt_cache=PromptCache(data.get("prompt_cache", PromptCache.NONE)),
            pricing=_prices(data.get("pricing") or {}),
            credential_source=str(data.get("credential_source") or ""),
        )
        return cls(**arguments)


def build_protocol_provider(
    spec: BrandedProviderSpec,
    *,
    model: str,
    credential: Credential,
    base_url: str,
    extra_options: dict[str, object] | None = None,
) -> ModelProvider:
    anthropic = spec.protocol == "anthropic"
    module, symbol = (
        ("anthropic", "AnthropicProvider")
        if anthropic
        else ("openai", "OpenAIProvider")
    )
    constructor = getattr(import_module(f"gideon.integrations.llm.{module}"), symbol)
    limit = spec.max_tokens
    if anthropic and limit is None:
        limit = 4096
    return constructor(
        **{
            "model": model,
            "credential": credential,
            "base_url": base_url or None,
            "max_tokens": limit,
            "extra_options": extra_options,
        }
    )


def resolve_credential(
    entry: ProviderEntry, kwargs: dict, *, label: str
) -> Credential | None:
    reference = entry.credential
    if reference:
        store = kwargs.get("credential_store")
        if store is None:
            raise CredentialMissing(
                f"{label} provider entry {entry.name!r} declares credential "
                f"{reference!r} but no credential_store was passed to build()"
            )
        result = store.resolve(reference)
        if result is not None and result.secret is not None:
            return result
        raise CredentialMissing(f"{label} credential {reference!r} is not configured")
    return None


def resolve_spec_secret(
    spec: BrandedProviderSpec, *, explicit_key: str = ""
) -> tuple[Credential | None, str]:
    failure = ""

    def choices():
        nonlocal failure
        yield "api_key", "file", explicit_key
        if spec.credential_source:
            auth = resolve_subscription_credential(spec.credential_source)
            if auth.logged_in:
                yield "oauth2", "file", auth.secret
            failure = auth.reason
        yield "api_key", "env", (
            os.environ.get(spec.api_key_env, "") if spec.api_key_env else ""
        )

    for kind, origin, secret in choices():
        if secret:
            return Credential(spec.type, kind, secret, origin), ""
    return None, failure


_REGISTERED_SPECS: dict[str, BrandedProviderSpec] = {}


def registered_spec(provider: str) -> BrandedProviderSpec | None:
    requested = str(provider or "").strip()
    if not requested:
        return None
    if requested in _REGISTERED_SPECS:
        return _REGISTERED_SPECS[requested]
    folded = requested.lower()
    partial = []
    for identifier, candidate in _REGISTERED_SPECS.items():
        key = identifier.lower()
        if key == folded:
            return candidate
        if key and key in folded:
            partial.append(candidate)
    if len(partial) == 1:
        return partial.pop()
    return None


def spec_pricing(provider: str) -> dict[str, dict[str, float]]:
    selected = registered_spec(provider)
    return {} if selected is None else dict(selected.pricing or {})


def spec_credential_source(provider: str) -> str:
    selected = registered_spec(provider)
    return str(selected.credential_source or "") if selected else ""


def spec_types_declaring_models(markers: tuple[str, ...]) -> frozenset[str]:
    needles = tuple(marker.lower() for marker in markers if marker)
    matches = set()
    for identifier, spec in _REGISTERED_SPECS.items():
        models = (spec.default_model, *(row.get("id") for row in spec.fallback_models))
        for model in models:
            if any(needle in str(model or "").lower() for needle in needles):
                matches.add(identifier)
                break
    return frozenset(matches)
