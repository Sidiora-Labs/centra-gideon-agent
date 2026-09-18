"""SDK helpers for building a model-provider APP with minimal boilerplate.

A model provider that speaks one of the two supported inference PROTOCOLS
(OpenAI-compatible or Anthropic-compatible) over a fixed endpoint + bearer key —
i.e. every branded provider app (Together, Groq, DeepSeek, Mistral, Gemini's
OpenAI shim, …) and the two generic "bring-your-own-endpoint" apps — differs from
its siblings ONLY in: its default base URL, its API-key env var, and its fallback
model catalog. Everything else (the registry ``_factory``, the ``create_provider``
config-path factory, credential resolution, and the ``ModelCatalog``) is identical.

These helpers capture that identical wiring so each app's ``provider.py`` is a few
declarations + one ``register_*_app(...)`` call. The helpers build on the SDK
primitives (``OpenAIProvider`` / ``AnthropicProvider`` / ``ModelCatalog`` /
``openai_compatible_list_models``) — this is generic protocol infra, NOT one app
importing another.

An app that needs provider-specific behavior beyond endpoint+key+catalog (e.g.
Azure OpenAI's distinct ``AsyncAzureOpenAI`` client + ``api_version`` + api-key
header) does NOT use these — it subclasses the protocol client directly.
"""

from __future__ import annotations

from typing import Any, Callable

from gideon.integrations.llm import branded_specs  # noqa: E402
from gideon.integrations.llm.anthropic import AnthropicProvider  # noqa: F401
from gideon.integrations.llm.base import ModelProvider  # noqa: F401
from gideon.integrations.llm.branded_specs import (
    BrandedProviderSpec,
    build_protocol_provider,
    resolve_credential,
    resolve_spec_secret,
)
from gideon.integrations.llm.capabilities import ProviderCapability  # noqa: F401
from gideon.integrations.llm.capabilities import Capability
from gideon.integrations.llm.catalog import (
    ConnectionResult,
    ModelCatalog,
    ModelDiscoveryError,
    ModelInfo,
    infer_capabilities,
    openai_compatible_list_models,
)
from gideon.integrations.llm.credentials import Credential  # noqa: F401
from gideon.integrations.llm.openai import OpenAIProvider  # noqa: F401
from gideon.integrations.llm.prompt_cache import PromptCache  # noqa: F401
from gideon.integrations.llm.registry import (
    CredentialMissing,
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)
from gideon.integrations.llm.subscription_credentials import (
    SubscriptionSource,
    register_subscription_source,
    resolve_subscription_credential,
)


class BrandedCatalog(ModelCatalog):
    """Discovery for a branded OpenAI-compatible provider: try the live
    ``/v1/models`` endpoint, fall back to the spec's curated list so the picker is
    never empty when the key is set but the endpoint has no models route (some
    providers don't expose one). Anthropic-compatible providers have no models
    endpoint, so they always use the fallback list.

    The fallback covers exactly that — discovery being UNSUPPORTED, which
    :func:`openai_compatible_list_models` reports as an empty list. A discovery that
    FAILED (:class:`ModelDiscoveryError`: the policy refused it, the endpoint is
    down, the key was rejected, the body was not a model list) is propagated instead,
    because answering a failed probe with the curated list renders a picker full of
    models the user cannot actually reach and hides the one fact they need.
    :meth:`test_connection` is the exception: its whole job is to REPORT the failure,
    so it turns the error into a failed :class:`ConnectionResult`."""

    def __init__(
        self,
        spec: BrandedProviderSpec,
        *,
        endpoint: str = "",
        api_key: str = "",
        default_model: str = "",
    ) -> None:
        self._spec = spec
        self._endpoint = endpoint or spec.default_base_url
        self._explicit_api_key = api_key
        self._default_model = default_model

    def _resolved_key(self) -> tuple[str, str]:
        """This catalog's effective key, plus the honest reason when there isn't one.

        Same shared order as both factories (:func:`resolve_spec_secret`), so a subscription
        app the user is signed into gets probed with its CLI's token instead of being told to
        set an API-key env var it deliberately doesn't have. Returns ``("", reason)`` when no
        secret resolves; ``reason`` is the resolver's secret-free sentence, or ``""`` for an
        ordinary key-based app that simply has no key set.
        """
        cred, reason = resolve_spec_secret(
            self._spec, explicit_key=self._explicit_api_key
        )
        return (str(cred.secret or "") if cred is not None else "", reason)

    def _no_key_detail(self, reason: str) -> str:
        """The 'no credential' line, never a dangling parenthetical.

        A subscription app declares NO ``api_key_env`` by design, so the old unconditional
        ``f"... (set it or {api_key_env})"`` rendered literally as ``(set it or )`` and told a
        signed-out user to set a variable that does not exist. Prefer the resolver's typed
        reason, mention an env var only when the app actually declares one.
        """
        env = self._spec.api_key_env
        if reason:
            return f"{reason} (or set {env})" if env else reason
        return (
            f"No API key configured (set it or {env})"
            if env
            else "No API key configured"
        )

    def _fallback(self) -> list[ModelInfo]:
        rows = [
            ModelInfo(
                id=m["id"],
                name=m.get("name", m["id"]),
                capabilities=list(m.get("capabilities", infer_capabilities(m["id"]))),
            )
            for m in self._spec.fallback_models
        ]
        if self._default_model and not any(r.id == self._default_model for r in rows):
            rows.insert(
                0,
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    capabilities=list(self._spec.capabilities)
                    or infer_capabilities(self._default_model),
                ),
            )
        return rows

    async def list_models(self) -> list[ModelInfo]:
        if self._spec.protocol == "anthropic":
            return self._fallback()
        api_key, _ = self._resolved_key()
        live = await openai_compatible_list_models(
            self._endpoint,
            api_key,
            default_base=self._spec.default_base_url,
        )
        return live if live else self._fallback()

    async def test_connection(self) -> ConnectionResult:
        api_key, missing_reason = self._resolved_key()
        if not api_key:
            return ConnectionResult(
                ok=False, detail=self._no_key_detail(missing_reason)
            )
        if self._spec.protocol == "anthropic":
            return await self._probe_completion(api_key)
        try:
            models = await self.list_models()
        except ModelDiscoveryError as failure:
            return ConnectionResult(ok=False, detail=str(failure))
        if not models:
            return ConnectionResult(
                ok=False, detail="No models available (check key/endpoint)"
            )
        return ConnectionResult(ok=True, model_count=len(models))

    async def _probe_completion(self, api_key: str) -> ConnectionResult:
        """Verify an Anthropic-wire key/endpoint with a minimal completion. Auth
        errors → not connected; a model/validation error still means the credentials
        authenticated → connected.

        Takes the already-resolved secret (a subscription token as readily as a pasted key)
        so the probe validates exactly what :meth:`test_connection` found."""
        model = self._spec.default_model or "claude-3-5-haiku-latest"
        try:
            from gideon.integrations.llm.credentials import Credential

            prov = AnthropicProvider(
                model=model,
                credential=Credential(
                    name=self._spec.type, kind="api_key", secret=api_key, source="file"
                ),
                base_url=self._endpoint or None,
                max_tokens=1,
            )
            async for _ in prov.complete([{"role": "user", "content": "hi"}]):
                break
            return ConnectionResult(ok=True, detail="Connected (completion probe)")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if any(
                s in msg
                for s in (
                    "401",
                    "403",
                    "authentication",
                    "invalid api key",
                    "unauthorized",
                    "permission",
                    "x-api-key",
                )
            ):
                return ConnectionResult(
                    ok=False,
                    detail=f"Auth failed — check the API key ({str(exc)[:80]})",
                )
            if any(
                s in msg
                for s in ("not_found", "model", "400", "invalid_request", "bad request")
            ):
                return ConnectionResult(
                    ok=True, detail="Connected (key valid; verify the model id)"
                )
            return ConnectionResult(
                ok=False, detail=f"Connection failed: {str(exc)[:100]}"
            )


def register_branded_app(
    spec: BrandedProviderSpec,
) -> tuple[Callable, Callable, Callable]:
    """Wire a branded/generic protocol provider app into the default registry and
    return its ``(_factory, create_provider, create_catalog)`` trio.

    Registers both the provider TYPE (inference) and the catalog (discovery) as the
    same import-time side effect the app loader triggers. Idempotent against reload
    (type registration is guarded; catalog registration is last-wins). The returned
    callables are what the app module exposes so the manifest's
    ``implementation: "provider:create_provider"`` resolves.
    """

    def _factory(
        *, entry: ProviderEntry, session_key: str | None = None, **kwargs: object
    ) -> ModelProvider:
        del session_key
        cred = resolve_credential(entry, kwargs, label=spec.type)
        options = dict(entry.options or {})
        _base = options.pop("base_url", None)
        _endpoint = options.pop("endpoint", None)
        base_url = str(_base or _endpoint or spec.default_base_url)
        _snake_key = str(options.pop("api_key", "") or "")
        _camel_key = str(options.pop("apiKey", "") or "")
        if cred is None:
            cred, _ = resolve_spec_secret(spec, explicit_key=_snake_key or _camel_key)
        for _k in ("model", "default_model", "type", "name"):
            options.pop(_k, None)
        _emb_model = kwargs.get("embedding_model")
        if _emb_model:
            options["embedding_model"] = str(_emb_model)
        _temperature = kwargs.get("temperature")
        if isinstance(_temperature, (int, float)) and not isinstance(
            _temperature, bool
        ):
            options["temperature"] = float(_temperature)
        max_tokens_value = options.pop("max_tokens", None)
        if isinstance(max_tokens_value, int):
            eff_spec = BrandedProviderSpec(
                **{**spec.__dict__, "max_tokens": max_tokens_value}
            )
        else:
            eff_spec = spec
        return build_protocol_provider(
            eff_spec,
            model=entry.model or spec.default_model,
            credential=cred or _anon_credential(spec),
            base_url=base_url,
            extra_options=options,
        )

    def create_provider(config: dict[str, Any] | None = None) -> ModelProvider:
        cfg = dict(config or {})
        cred, _ = resolve_spec_secret(
            spec,
            explicit_key=str(cfg.get("api_key", "") or cfg.get("apiKey", "") or ""),
        )
        cred = cred or _anon_credential(spec)
        base_url = str(
            cfg.get("endpoint") or cfg.get("base_url") or spec.default_base_url
        )
        model = str(cfg.get("model") or cfg.get("default_model") or spec.default_model)
        return build_protocol_provider(
            spec, model=model, credential=cred, base_url=base_url
        )

    def create_catalog(
        options: dict[str, Any] | None = None, *, model: str = ""
    ) -> ModelCatalog:
        opts = options or {}
        return BrandedCatalog(
            spec,
            endpoint=str(opts.get("endpoint") or opts.get("base_url") or ""),
            api_key=str(opts.get("api_key") or ""),
            default_model=str(
                model or opts.get("default_model") or opts.get("model") or ""
            ),
        )

    cap = ProviderCapability(
        type=spec.type,
        capabilities=spec.capabilities
        or frozenset({Capability.CHAT, Capability.STREAMING}),
        supports_streaming=True,
        supports_tools=Capability.CODE_TOOLS in spec.capabilities,
        supports_embeddings=Capability.EMBEDDING in spec.capabilities,
        supports_vision=Capability.VISION in spec.capabilities,
        max_context_tokens=0,
        notes=spec.notes or f"{spec.type}: {spec.protocol}-compatible endpoint.",
        prompt_cache=spec.prompt_cache,
    )
    try:
        get_default_registry().register_type(cap, _factory)
    except ProviderResolutionError:
        pass
    get_default_registry().register_catalog(spec.type, create_catalog)
    branded_specs._REGISTERED_SPECS[spec.type] = spec

    return _factory, create_provider, create_catalog


def _anon_credential(spec: BrandedProviderSpec) -> Credential:
    """A placeholder credential for an unauth'd/optional-key provider. The OpenAI
    SDK client constructor requires a populated secret even when the endpoint
    ignores it (mirrors the vLLM app's placeholder)."""
    return Credential(
        name=f"{spec.type}-anon", kind="none", secret="unused", source="none"
    )


__all__ = [
    "BrandedProviderSpec",
    "BrandedCatalog",
    "SubscriptionSource",
    "register_subscription_source",
    "register_branded_app",
]
