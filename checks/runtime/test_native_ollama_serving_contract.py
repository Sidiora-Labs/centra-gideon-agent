import importlib.util
from pathlib import Path

import pytest

from gideon.engine.agents.native.runtime import (
    NativeAgentRuntime,
    _inference_failure_mode,
    _ModelExchange,
    _TurnTotals,
)
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.prompt_cache import PromptCache
from gideon.sdk.model import FirstTokenTimeout, ModelProvider
from gideon.security.guardrails.breaker import CircuitBreaker
from gideon.security.guardrails.budgets import SpendMeter
from gideon.security.guardrails.failure import FailureMode, is_retryable
from gideon.security.guardrails.model_call import ModelCallGuard


@pytest.mark.asyncio
async def test_configured_first_token_deadline_and_loaded_window_contract(tmp_path):
    path = Path(__file__).resolve().parents[2] / (
        "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    spec = importlib.util.spec_from_file_location(
        "gideon_ollama_serving_contract", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = module.create_provider(
        {"model": "gemma4:12b", "timeout_secs": "900", "context_window": "8192"}
    )
    guard = ModelCallGuard(
        provider,
        use_case="consolidation",
        provider_name="ollama",
        model=provider.model,
        timeout_secs=300,
        breaker=CircuitBreaker("ollama"),
        meter=SpendMeter(config_dir=tmp_path),
    )
    await provider.start()
    try:
        assert provider._client.timeout.read == 900
        assert provider._client.timeout.connect == 10
        assert guard.first_token_timeout_secs == 900
        assert guard._stream_deadline(100, startup=True) == 1300
        assert guard._stream_deadline(100) == 400
        assert await guard.served_context_window() == 8192
        assert await ModelProvider.served_context_window(provider) is None
        assert provider.context_usage_pct() is None
        assert provider._reported_context_window is None

        loaded = {
            "models": [
                {"name": "other:latest", "context_length": 999999},
                {"name": "gemma4:12b", "context_length": 32768},
                {"model": "llama3.1:latest", "context_length": "16384"},
            ]
        }
        assert provider._loaded_context_window(loaded, provider.model) == 32768
        assert provider._loaded_context_window(loaded, "llama3.1") == 16384
        assert provider._loaded_context_window(loaded, "gemma4:27b") is None
        assert provider._loaded_context_window(loaded, "") is None
        assert provider._loaded_context_window({"models": None}, provider.model) is None
        for unknown in (None, 0, -1, True, "unknown"):
            assert (
                provider._loaded_context_window(
                    {"models": [{"name": provider.model, "context_length": unknown}]},
                    provider.model,
                )
                is None
            )

        error = FirstTokenTimeout(
            model=provider.model, provider="Ollama", waited_secs=900
        )
        assert _inference_failure_mode(error) is FailureMode.FIRST_TOKEN_TIMEOUT
        assert not is_retryable(error.mode)
        assert is_retryable(FailureMode.PROVIDER_ERROR)
        assert "gemma4:12b on Ollama" in str(error)
        assert "Request Timeout" in str(error)
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", model=provider.model),
            model_provider=guard,
            cwd=tmp_path,
        )
        exchange = _ModelExchange(runtime, _TurnTotals())
        assert not exchange.retry_allowed(error.mode)
        assert exchange.retry_allowed(FailureMode.PROVIDER_ERROR)
        with pytest.raises(FirstTokenTimeout) as caught:
            await exchange._retry_messages(error, [], PromptCache.NONE)
        assert caught.value is error
        assert exchange.retried is False
        assert exchange.totals.recoveries == 0
    finally:
        await provider.shutdown()
