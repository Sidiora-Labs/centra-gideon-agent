"""One bounded, pinned model call requested by the owner in Settings → Models."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass, replace

from gideon.core.turn_streams import closing_stream
from gideon.extensions.providers.use_cases import VALID_USE_CASES, parent_capability

TEST_REPLY_TOKENS = 1024


class ModelUntestable(Exception):
    pass


class ModelTestRunning(Exception):
    pass


@dataclass(frozen=True)
class ModelTestResult:
    ok: bool
    detail: str
    reason: str = ""
    duration_ms: int = 0

    def to_dict(self):
        return asdict(self)


def untestable_reason(use_case: str, provider_name: str) -> str:
    """Pure registration check; never calls a model while listing its row."""
    if use_case not in VALID_USE_CASES:
        return "This is not a model use case Settings lists."
    capability = parent_capability(use_case)
    if capability == "embedding":
        from gideon.integrations.embedding_providers.registry import direct_provider

        try:
            adapter = direct_provider(provider_name)
            if adapter is not None:
                return adapter.untestable_reason()
        except Exception:
            return "This embedding provider is not available."
    if capability not in {"chat", "embedding"}:
        if capability == "video_gen":
            return "A Test would have to make a whole video clip, which is slow and costly."
        return f"A small {capability.replace('_', ' ')} Test is not available yet."
    from gideon.integrations.llm.capabilities import Capability
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(provider_name)
        declared = (
            entry.declared_capabilities
            or registry.capability_of(entry.type).capabilities
        )
        required = (
            Capability.EMBEDDING if capability == "embedding" else Capability.CHAT
        )
        if entry.type == "acp_agent" or required not in declared:
            return f"This provider does not serve {capability} models."
        if entry.type not in registry._factories:
            return "This provider's model app is not available."
    except Exception:
        return "This model provider is not set up."
    return ""


def mark_untestable(rows: list[dict]) -> None:
    from gideon.extensions.providers.use_cases import USE_CASES

    cache = {}
    for row in rows:
        for model in row.get("models") or []:
            provider = str(model.get("provider") or row.get("name") or "")
            refused = {}
            for use_case in USE_CASES:
                capability = parent_capability(use_case)
                if capability not in (model.get("capabilities") or []):
                    continue
                key = (use_case, provider)
                if key not in cache:
                    cache[key] = untestable_reason(use_case, provider)
                if cache[key]:
                    refused[use_case] = cache[key]
            if refused:
                model["untestable"] = refused


def _timeout_secs() -> float:
    try:
        from gideon.core.config.loader import AppConfig

        value = float(AppConfig.load().local_models.selftest_timeout_secs)
        return value if math.isfinite(value) and value > 0 else 90.0
    except Exception:
        return 90.0


def _safe(text: object) -> str:
    # A masking failure withholds all provider words, including successful output.
    try:
        from gideon.security.security import redact_field

        return " ".join(redact_field(str(text)).split())[:320]
    except Exception:
        return "The provider's response could not be safely displayed."


async def _reply(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    from gideon.extensions.providers.provider_bridge import (
        ProviderResolutionError,
        _resolve_from_config_registry,
    )
    from gideon.integrations.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK
    from gideon.integrations.local_models.budgets import output_budget

    ref = f"{provider_name}:{model}"
    try:
        budget = await output_budget(ref)
        cap = (
            min(TEST_REPLY_TOKENS, int(budget))
            if int(budget) > 0
            else TEST_REPLY_TOKENS
        )
    except Exception:
        cap = TEST_REPLY_TOKENS
    # Explicit provider_hint prevents this strict candidate build from falling through
    # active bindings. Calling the model axis avoids starting an agent/Hypermid turn.
    provider = _resolve_from_config_registry(
        "chat",
        provider_hint=provider_name,
        model_override=model,
        _model_axis_only=True,
        _guard_use_case=use_case,
        max_tokens=cap,
    )
    if provider is None:
        raise ProviderResolutionError(
            "The requested model cannot serve this use case; no fallback was used."
        )
    parts = []
    terminal = False
    try:
        await provider.start()
        async with closing_stream(
            provider.complete(
                [{"role": "user", "content": "Reply with the single word OK."}]
            )
        ) as events:
            async for event in events:
                if event.kind == EVENT_TEXT_CHUNK:
                    parts.append(event.text or "")
                if event.kind == EVENT_COMPLETE:
                    terminal = True
                    from gideon.operations.usage_ledger import record_from_event

                    record_from_event(
                        event,
                        source="eval",
                        session_key="",
                        agent="",
                        provider=provider_name,
                        model=model,
                        estimate_if_missing=False,
                    )
                    break
    finally:
        try:
            await provider.shutdown()
        except Exception:
            pass  # cleanup cannot turn an actual answer into a second inference attempt
    text = "".join(parts).strip()
    if not terminal:
        return ModelTestResult(
            False, "The answer stopped before the model finished.", "cut_off"
        )
    if not text:
        return ModelTestResult(False, "Answered with an empty reply.", "empty_reply")
    return ModelTestResult(True, f"Replied “{_safe(text)[:80]}”.")


async def _embedding(provider_name: str, model: str) -> ModelTestResult:
    from numbers import Real

    from gideon.integrations.embedding_providers.registry import embed_for
    from gideon.security.guardrails.local_inference import turn

    async with turn(provider_name, model):
        vector = await embed_for(provider_name, model, "hello")
    if not isinstance(vector, (list, tuple)) or not vector:
        return ModelTestResult(
            False, "The provider returned no embedding vector.", "empty_embedding"
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        for value in vector
    ):
        return ModelTestResult(
            False,
            "The provider returned an invalid embedding vector.",
            "invalid_embedding",
        )
    return ModelTestResult(True, f"Embedded a test word into {len(vector)} dimensions.")


async def run_model_test(
    use_case: str, provider_name: str, model: str
) -> ModelTestResult:
    refusal = untestable_reason(use_case, provider_name)
    if refusal:
        raise ModelUntestable(refusal)
    from gideon.core.concurrency import single_flight

    lease = single_flight(f"model-test:{provider_name}")
    if not lease.__enter__():
        lease.__exit__(None, None, None)
        raise ModelTestRunning(provider_name)
    task = None
    deferred = False
    started = time.monotonic()
    timeout = _timeout_secs()

    def settled(done):
        try:
            if not done.cancelled():
                done.exception()
        finally:
            lease.__exit__(None, None, None)

    try:
        from gideon.security.guardrails.local_inference import (
            Attended,
            attending,
            next_entry,
        )

        with attending(Attended("Testing the model")), next_entry(""):
            probe = (
                _embedding(provider_name, model)
                if parent_capability(use_case) == "embedding"
                else _reply(use_case, provider_name, model)
            )
            task = asyncio.create_task(probe)
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            deferred = True
            task.add_done_callback(settled)
            task.cancel()
            result = ModelTestResult(
                False, f"No answer within {timeout:g} seconds.", "timeout"
            )
        elif task.cancelled():
            result = ModelTestResult(
                False, "The Test was stopped before it finished.", "stopped"
            )
        elif (failure := task.exception()) is not None:
            result = ModelTestResult(
                False,
                _safe(str(failure) or type(failure).__name__),
                f"error:{type(failure).__name__}",
            )
        else:
            result = task.result()
        return replace(result, duration_ms=round((time.monotonic() - started) * 1000))
    except asyncio.CancelledError:
        if task is not None and not task.done():
            deferred = True
            task.add_done_callback(settled)
            task.cancel()
        raise
    finally:
        if not deferred:
            lease.__exit__(None, None, None)
