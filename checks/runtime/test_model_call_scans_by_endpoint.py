"""Serving-entry endpoint locality is shared by price, route order and egress scan."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from gideon.engine.routing import policy
from gideon.engine.routing.rates import ModelRate, rate_for
from gideon.integrations.llm.branded_specs import (
    BrandedProviderSpec,
    registered_spec,
    spec_credential_source,
)
from gideon.integrations.llm.capabilities import Capability
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderRegistry,
    get_default_registry,
    serving_is_local,
    set_default_registry,
)
from gideon.sdk.provider_helpers import register_branded_app
from gideon.security.guardrails.model_call import wrap_model_call_guard


def test_rate_route_and_egress_follow_resolved_endpoint(tmp_path, monkeypatch):
    original = get_default_registry()
    registry = ProviderRegistry()
    set_default_registry(registry)
    try:
        provider_path = (
            Path(__file__).parents[2]
            / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
        )
        spec = importlib.util.spec_from_file_location(
            "ollama_provider_for_contract", provider_path
        )
        assert spec is not None and spec.loader is not None
        ollama_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ollama_module)
        ollama_module._register()

        registry.register_entry(
            ProviderEntry(
                name="OllamaRemote",
                type="ollama",
                model="qwen3:8b",
                options={
                    "endpoint": "https://inference.example/v1",
                    "base_url": "http://127.0.0.1:11434",
                },
            )
        )
        registry.register_entry(
            ProviderEntry(name="OllamaLocal", type="ollama", model="qwen3:8b")
        )
        remote = registry.build("OllamaRemote")
        local = registry.build("OllamaLocal")
        remote.served_model_ref = "OllamaRemote:qwen3:8b"
        local.served_model_ref = "OllamaLocal:qwen3:8b"

        assert remote.endpoint == "https://inference.example/v1"
        assert serving_is_local("OllamaRemote", actual_provider=remote) is False
        assert serving_is_local("OllamaLocal", actual_provider=local) is True
        assert rate_for("OllamaRemote", "qwen3:8b", home=tmp_path) is None
        assert rate_for("OllamaLocal", "qwen3:8b", home=tmp_path) == ModelRate(
            0.0, 0.0, source="local"
        )

        monkeypatch.setattr(policy, "master_enabled", lambda: True)
        monkeypatch.setattr(
            policy, "_settings_for", lambda _use_case: {policy.MODE_KEY: "heuristic"}
        )
        assert policy.route_refs(
            "chat",
            "short_chat",
            ["OllamaRemote:qwen3:8b", "OllamaLocal:qwen3:8b"],
            home=tmp_path,
        ) == ["OllamaLocal:qwen3:8b", "OllamaRemote:qwen3:8b"]

        remote_guard = wrap_model_call_guard(
            remote,
            use_case="chat",
            provider_name="OllamaRemote",
            model="qwen3:8b",
            scan_mode="block",
        )
        local_guard = wrap_model_call_guard(
            local,
            use_case="chat",
            provider_name="OllamaLocal",
            model="qwen3:8b",
            scan_mode="block",
        )
        assert remote_guard._scan_mode == "block"
        assert local_guard._scan_mode == "warn"

        branded_spec = BrandedProviderSpec(
            type="brand-acme",
            default_base_url="https://default.example/v1",
            api_key_env="BRAND_ACME_KEY",
            credential_source="acme-subscription",
            pricing={
                "brand-only-test-model": {"in_per_mtok": 2.0, "out_per_mtok": 4.0}
            },
        )
        register_branded_app(branded_spec)
        registry.register_entry(
            ProviderEntry(
                name="acme-instance",
                type="brand-acme",
                model="brand-only-test-model",
                options={
                    "base_url": "https://configured.example/v1",
                    "endpoint": "http://localhost:9000/v1",
                },
            )
        )
        branded = registry.build("acme-instance")
        assert branded._base_url == "https://configured.example/v1"
        assert serving_is_local("acme-instance", actual_provider=branded) is False
        assert rate_for(
            "acme-instance", "brand-only-test-model", home=tmp_path
        ) == ModelRate(2.0, 4.0, source="app_default")
        assert registered_spec("acme-instance") is None
        assert spec_credential_source("acme-instance") == ""
    finally:
        set_default_registry(original)
