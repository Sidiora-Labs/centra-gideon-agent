"""The unified local-model provider registry.

One flat name→provider map, populated STRUCTURALLY by the app loader
(``ModelTypeHandler``) for every ``type: model`` provider that implements the
:class:`LocalModelProvider` management contract — core holds no per-provider
knowledge. Drives the download surface: per-provider cards, download jobs, and
``/api/models/available`` surfacing. Inference resolution lives in the per-use-case
registries (stt/tts/diarization/embedding), which are unaffected.

Providers register once when their app is enabled and unregister when disabled, so
this map is authoritative and does NOT get cleared on config changes (unlike the
per-use-case registries, whose remote adapters are rebuilt from config).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

from gideon.integrations.local_models.provider import (
    CapabilitySelfTestResult,
    LocalModel,
    LocalModelFailure,
    LocalModelFailureCode,
    LocalModelProvider,
    LocalModelSelfTestError,
)

logger = logging.getLogger(__name__)

_providers: dict[str, LocalModelProvider] = {}
_capabilities: dict[str, list[str]] = {}
_self_test_locks: dict[str, tuple[int, asyncio.AbstractEventLoop, asyncio.Lock]] = {}

_AVAILABILITY_TIMEOUT_SECONDS = 5.0
_DEFAULT_SELF_TEST_TIMEOUT_SECONDS = 30.0
_MAX_SELF_TEST_TIMEOUT_SECONDS = 300.0


def to_local_model(
    m: Any, *, capabilities: list[str] | None = None, provider: Any = None
) -> LocalModel:
    """Adapt any provider's catalog entry to the uniform :class:`LocalModel`.

    The management surface speaks one shape. A provider's ``list_models`` may return a
    :class:`LocalModel` already, or a domain dataclass (SttModel / TtsVoice /
    EmbeddingModel / DiarizationModel) that shares ``name``/``size_mb``/``description``/
    ``downloaded`` — we read those structurally and fold the provider's declared
    ``capabilities`` (the use-cases it serves) onto the model unless the model names its
    own. Domain-only fields (dimension, language, …) stay on the domain object for
    inference; management never needs them.
    """
    if isinstance(m, LocalModel):
        model = replace(m, capabilities=list(m.capabilities or capabilities or []))
    else:
        model = LocalModel(
            name=getattr(m, "name", ""),
            size_mb=float(getattr(m, "size_mb", 0) or 0),
            description=getattr(m, "description", ""),
            downloaded=bool(getattr(m, "downloaded", False)),
            capabilities=list(getattr(m, "capabilities", None) or capabilities or []),
            gated=bool(getattr(m, "gated", False)),
            source=getattr(m, "source", ""),
            instance_id=getattr(m, "instance_id", ""),
            instance_label=getattr(m, "instance_label", ""),
        )
    if provider is not None:
        from gideon.integrations.local_models.multi_instance import instance_identity

        identity = instance_identity(provider)
        model.instance_id = model.instance_id or identity["instance_id"]
        model.instance_label = model.instance_label or identity["instance_label"]
    return model


def register_provider(
    provider: LocalModelProvider,
    capabilities: list[str] | None = None,
    *,
    name: str | None = None,
) -> None:
    """Register (or replace) a local-model provider + the capabilities it serves.

    ``name`` overrides the registry key (defaults to ``provider.name``). The app loader
    passes the APP name (``faster-whisper``, ``sentence-transformers``, …) so the
    download surface, the ``provider:model`` binding refs, and the Providers UI all key
    on the SAME identifier — the provider's internal ``.name`` (``faster_whisper`` /
    ``native``) can differ, and the inference registries already accept both."""
    key = name or provider.name
    _providers[key] = provider
    _capabilities[key] = list(capabilities or [])
    logger.debug("local-model provider registered: %s (caps=%s)", key, capabilities)


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _capabilities.pop(name, None)
    _self_test_locks.pop(name, None)


def get_provider(name: str) -> LocalModelProvider | None:
    return _providers.get(name)


def capabilities_for(name: str) -> list[str]:
    """The use-case capabilities the named provider's app declared."""
    return list(_capabilities.get(name, []))


def list_providers() -> list[LocalModelProvider]:
    """All registered local-model providers (order of registration)."""
    return list(_providers.values())


def registered() -> list[tuple[str, LocalModelProvider]]:
    """All ``(registry_key, provider)`` pairs — the key is the app name the download
    surface + binding refs use (may differ from ``provider.name``)."""
    return list(_providers.items())


def _key_for(provider: LocalModelProvider) -> str:
    """The registry key a provider is stored under (its app name, which may differ
    from ``provider.name``). Falls back to ``provider.name``."""
    for k, p in _providers.items():
        if p is provider:
            return k
    return getattr(provider, "name", "")


async def catalog_for(provider: LocalModelProvider) -> list[LocalModel]:
    """The provider's models as uniform :class:`LocalModel`s (fail-soft → [])."""
    try:
        raw = await provider.list_models()
    except Exception:
        logger.debug(
            "list_models failed for %s", getattr(provider, "name", "?"), exc_info=True
        )
        return []
    caps = capabilities_for(_key_for(provider))
    return [to_local_model(m, capabilities=caps, provider=provider) for m in raw]


def _failure(
    code: LocalModelFailureCode,
    message: str,
    *,
    retryable: bool = False,
) -> LocalModelFailure:
    return LocalModelFailure(code=code, message=message[:200], retryable=retryable)


def _failure_from_exception(exc: BaseException) -> LocalModelFailure:
    if isinstance(exc, LocalModelSelfTestError):
        return exc.failure
    typed_reason = str(getattr(exc, "typed_reason", "") or "")
    if typed_reason.startswith("sidecar_crashed"):
        return _failure(
            LocalModelFailureCode.SIDECAR_CRASHED,
            typed_reason,
            retryable=True,
        )
    message = typed_reason or str(exc) or type(exc).__name__
    return _failure(LocalModelFailureCode.PROVIDER_ERROR, message)


async def availability_for(
    key: str,
    provider: LocalModelProvider,
    *,
    timeout_seconds: float = _AVAILABILITY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """One bounded availability row with a typed failure on every false path."""
    failure: LocalModelFailure | None = None
    try:
        available = bool(
            await asyncio.wait_for(
                provider.is_available(), timeout=max(0.001, timeout_seconds)
            )
        )
        if not available:
            failure = _failure(
                LocalModelFailureCode.UNAVAILABLE,
                f"{getattr(provider, 'display_name', key)} is unavailable",
                retryable=True,
            )
    except asyncio.TimeoutError:
        available = False
        failure = _failure(
            LocalModelFailureCode.TIMEOUT,
            f"availability probe exceeded {timeout_seconds:g}s",
            retryable=True,
        )
    except Exception as exc:  # noqa: BLE001 — one provider cannot blank the endpoint
        available = False
        failure = _failure_from_exception(exc)
    return {
        "provider": key,
        "display_name": str(getattr(provider, "display_name", "") or key),
        "capabilities": capabilities_for(key),
        "available": available,
        "failure": failure.to_dict() if failure is not None else None,
    }


async def availability_snapshot() -> dict[str, Any]:
    """Bounded availability for every registered local-model provider."""
    rows = await asyncio.gather(
        *(availability_for(key, provider) for key, provider in registered())
    )
    return {"ok": all(row["available"] for row in rows), "providers": rows}


def self_test_timeout_seconds() -> float:
    """Configured per-capability bound, with a safe default for older configs."""
    try:
        from gideon.core.config.loader import AppConfig

        configured = float(
            getattr(
                AppConfig.load().local_models,
                "self_test_timeout_secs",
                _DEFAULT_SELF_TEST_TIMEOUT_SECONDS,
            )
        )
    except Exception:
        configured = _DEFAULT_SELF_TEST_TIMEOUT_SECONDS
    return min(_MAX_SELF_TEST_TIMEOUT_SECONDS, max(0.1, configured))


def _self_test_lock(key: str, provider: LocalModelProvider) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    existing = _self_test_locks.get(key)
    if existing is None or existing[0] != id(provider) or existing[1] is not loop:
        lock = asyncio.Lock()
        _self_test_locks[key] = (id(provider), loop, lock)
        return lock
    return existing[2]


def _selected_capabilities(key: str, requested: list[str] | None) -> list[str]:
    source = capabilities_for(key) if requested is None else requested
    selected: list[str] = []
    for capability in source:
        normalized = str(capability).strip().lower()
        if normalized and normalized not in selected:
            selected.append(normalized)
    return selected


async def _run_capability_self_test(
    provider: LocalModelProvider,
    capability: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            provider.self_test(capability), timeout=max(0.001, timeout_seconds)
        )
        if not isinstance(result, CapabilitySelfTestResult):
            result = CapabilitySelfTestResult.failed(
                LocalModelFailureCode.INVALID_RESULT,
                "provider self_test must return CapabilitySelfTestResult",
            )
        elif result.ok and result.failure is not None:
            result = CapabilitySelfTestResult.failed(
                LocalModelFailureCode.INVALID_RESULT,
                "successful self-test cannot include a failure",
            )
        elif not result.ok and result.failure is None:
            result = CapabilitySelfTestResult.failed(
                LocalModelFailureCode.INVALID_RESULT,
                "failed self-test must include a typed failure",
            )
    except asyncio.TimeoutError:
        result = CapabilitySelfTestResult.failed(
            LocalModelFailureCode.TIMEOUT,
            f"{capability} self-test exceeded {timeout_seconds:g}s",
            retryable=True,
        )
    except Exception as exc:  # noqa: BLE001 — exceptions become typed probe results
        result = CapabilitySelfTestResult(
            ok=False, failure=_failure_from_exception(exc)
        )
    row = {"capability": capability, **result.to_dict()}
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return row


async def run_provider_self_tests(
    key: str,
    requested: list[str] | None = None,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run bounded probes serially, refusing overlapping runs per provider."""
    provider = get_provider(key)
    if provider is None:
        failure = _failure(
            LocalModelFailureCode.UNKNOWN_PROVIDER, f"Unknown provider {key!r}"
        )
        return {
            "provider": key,
            "ok": False,
            "tests": [],
            "failure": failure.to_dict(),
        }

    capabilities = _selected_capabilities(key, requested)
    if not capabilities:
        failure = _failure(
            LocalModelFailureCode.NO_CAPABILITIES,
            f"Provider {key!r} declares no testable capabilities",
        )
        return {
            "provider": key,
            "ok": False,
            "tests": [],
            "failure": failure.to_dict(),
        }

    lock = _self_test_lock(key, provider)
    if lock.locked():
        failure = _failure(
            LocalModelFailureCode.BUSY,
            f"A self-test for provider {key!r} is already running",
            retryable=True,
        )
        return {
            "provider": key,
            "ok": False,
            "tests": [],
            "failure": failure.to_dict(),
        }

    bound = (
        self_test_timeout_seconds()
        if timeout_seconds is None
        else float(timeout_seconds)
    )
    bound = min(_MAX_SELF_TEST_TIMEOUT_SECONDS, max(0.001, bound))
    declared = set(capabilities_for(key))
    tests: list[dict[str, Any]] = []
    async with lock:
        availability = await availability_for(key, provider)
        if not availability["available"]:
            for capability in capabilities:
                tests.append(
                    {
                        "capability": capability,
                        "ok": False,
                        "detail": "",
                        "failure": availability["failure"],
                        "elapsed_ms": 0.0,
                    }
                )
        else:
            for capability in capabilities:
                if capability not in declared:
                    result = CapabilitySelfTestResult.failed(
                        LocalModelFailureCode.UNSUPPORTED_CAPABILITY,
                        f"Provider {key!r} does not declare capability {capability!r}",
                    )
                    tests.append(
                        {
                            "capability": capability,
                            **result.to_dict(),
                            "elapsed_ms": 0.0,
                        }
                    )
                    continue
                tests.append(
                    await _run_capability_self_test(
                        provider, capability, timeout_seconds=bound
                    )
                )
    return {
        "provider": key,
        "display_name": str(getattr(provider, "display_name", "") or key),
        "ok": all(test["ok"] for test in tests),
        "tests": tests,
        "failure": None,
    }


class _ManagerBackedLocalProvider(LocalModelProvider):
    """Adapt a config-provider's :class:`ModelManager` catalog to the local-model
    contract, so a config-based downloadable provider (ollama) gets a download card +
    availability surfacing exactly like a bundled app. Management delegates to the
    manager (list/search/pull/delete); ``pull_model``'s stream is drained into a bool
    for the shared byte-progress job runner. ``searchable`` because a ModelManager owns a
    remote installable catalog (search_catalog)."""

    searchable = True

    def __init__(self, provider_name: str, display: str, manager: Any) -> None:
        self._name = provider_name
        self._display = display or provider_name
        self._mgr = manager

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display

    async def is_available(self) -> bool:
        try:
            return (await self._mgr.test_connection()).ok
        except Exception:
            return False

    @staticmethod
    def _to_local(mi: Any, *, downloaded: bool) -> LocalModel:
        return LocalModel(
            name=getattr(mi, "name", "") or getattr(mi, "id", ""),
            size_mb=round((getattr(mi, "size", 0) or 0) / (1024 * 1024), 1),
            description=(getattr(mi, "extra", {}) or {}).get("parameter_size", "")
            or getattr(mi, "description", ""),
            downloaded=downloaded,
            capabilities=list(getattr(mi, "capabilities", []) or []),
            source="ollama.com",
        )

    async def list_models(self) -> list[LocalModel]:
        return [
            self._to_local(mi, downloaded=True) for mi in await self._mgr.list_models()
        ]

    async def search_models(self, query: str) -> list[LocalModel]:
        return [
            self._to_local(mi, downloaded=False)
            for mi in await self._mgr.search_catalog(query)
        ]

    async def self_test(self, capability: str) -> CapabilitySelfTestResult:
        """Probe config-backed local chat/embedding through its real runtime adapter."""
        if capability not in {"chat", "embedding"}:
            return await super().self_test(capability)
        models = await self.list_models()
        if not models:
            return CapabilitySelfTestResult.failed(
                LocalModelFailureCode.UNAVAILABLE,
                f"{self.display_name} has no downloaded model to test",
            )

        from gideon.integrations.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK
        from gideon.integrations.llm.registry import get_default_registry

        llm_registry = get_default_registry()
        entry = llm_registry.get_entry(self._name)
        runtime = llm_registry.build(self._name)
        await runtime.start()
        try:
            if capability == "embedding":
                embed = getattr(runtime, "embed", None)
                if not callable(embed):
                    return CapabilitySelfTestResult.failed(
                        LocalModelFailureCode.UNSUPPORTED_CAPABILITY,
                        f"{self.display_name} does not expose embedding inference",
                    )
                vectors = await embed(["Gideon local model self-test"])
                if vectors and vectors[0]:
                    return CapabilitySelfTestResult.success(
                        f"embedding returned {len(vectors[0])} dims"
                    )
                return CapabilitySelfTestResult.failed(
                    LocalModelFailureCode.PROVIDER_ERROR,
                    "embedding inference returned no vector",
                )

            answered = False
            async for event in runtime.complete(
                [{"role": "user", "content": "Reply with OK."}], model=entry.model
            ):
                if getattr(event, "kind", "") in {EVENT_TEXT_CHUNK, EVENT_COMPLETE}:
                    answered = True
            if answered:
                return CapabilitySelfTestResult.success("chat completion returned")
            return CapabilitySelfTestResult.failed(
                LocalModelFailureCode.PROVIDER_ERROR,
                "chat completion returned no output",
            )
        finally:
            await runtime.shutdown()

    async def download_model(self, model_name: str) -> bool:
        try:
            async for frame in self._mgr.pull_model(model_name):
                if getattr(frame, "error", ""):
                    logger.warning(
                        "pull %s/%s failed: %s", self._name, model_name, frame.error
                    )
                    return False
            return True
        except Exception:
            logger.warning("pull %s/%s raised", self._name, model_name, exc_info=True)
            return False

    async def delete_model(self, model_name: str) -> bool:
        from gideon.security.guardrails.writes import (
            LiveWriteDisabled,
            live_writes_disabled,
        )

        if live_writes_disabled():
            raise LiveWriteDisabled(f"delete_model {self._name}/{model_name}")
        try:
            await self._mgr.delete_model(model_name)
            return True
        except Exception:
            logger.warning("delete %s/%s raised", self._name, model_name, exc_info=True)
            return False


def register_config_model_managers() -> None:
    """Register every config.json model provider whose catalog is a ``ModelManager``
    (owns local model download/management — ollama) into the local-model registry, so
    it gets a unified download card + availability surfacing. Providers that only
    discover (no management axis) are skipped — they surface via the config path only.
    Idempotent: safe to call on each config change."""
    from gideon.integrations.llm.catalog import ModelManager
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    for entry in registry.list_entries():
        try:
            catalog = registry.build_catalog(entry)
        except Exception:
            catalog = None
        if isinstance(catalog, ModelManager):
            caps = [
                getattr(c, "value", c)
                for c in (getattr(entry, "declared_capabilities", None) or [])
            ]
            register_provider(
                _ManagerBackedLocalProvider(entry.name, entry.name, catalog),
                capabilities=caps,
            )


_DOWNLOADABLE_CAPS = frozenset({"stt", "tts", "embedding", "diarization", "chat"})


def is_local_model_provider(obj: object, capabilities: list[str] | None = None) -> bool:
    """Whether ``obj`` participates in the local-model download surface.

    Two gates, both required:
    1. It implements the management contract (list/download/delete + name/display_name)
       — either by subclassing :class:`LocalModelProvider` or duck-typing it.
    2. It's a LOCAL provider: it either subclasses :class:`LocalModelProvider` explicitly,
       or (for not-yet-migrated apps) its declared capabilities intersect the locally-
       downloadable set. This excludes hosted providers (FAL image-gen) that inherit
       download/delete stubs from their base ABC but download nothing.
    """
    has_contract = all(
        hasattr(obj, attr)
        for attr in (
            "name",
            "display_name",
            "list_models",
            "download_model",
            "delete_model",
        )
    )
    if not has_contract:
        return False
    if isinstance(obj, LocalModelProvider):
        return True
    caps = set(capabilities or [])
    return bool(caps & _DOWNLOADABLE_CAPS)
