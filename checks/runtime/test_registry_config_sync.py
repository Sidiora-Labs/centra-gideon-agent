"""sync_entries_from_config replays config.json providers[] into the registry.

Persisted providers[] entries are re-registered on startup so a configured
model provider stays visible to chat resolution across process restarts.
"""

from __future__ import annotations

import json

import pytest

import gideon.integrations.llm  # noqa: F401
from gideon.integrations.llm import registry as R


@pytest.fixture
def cfg_with_provider(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "TestAli",
                        "type": "openai_compatible",
                        "model": "glm-5.1",
                        "options": {"endpoint": "https://x/v1", "api_key": "k"},
                    },
                    {"name": "LocalLlama", "type": "ollama", "model": "llama3"},
                ]
            }
        )
    )
    monkeypatch.setattr("gideon.core.config.loader.config_path", lambda: cfg)
    return cfg


def test_sync_registers_config_providers(cfg_with_provider):
    reg = R.get_default_registry()
    reg.unregister_entry("TestAli")
    reg.unregister_entry("LocalLlama")

    from gideon.integrations.llm.capabilities import Capability, ProviderCapability

    if "openai_compatible" not in reg._capabilities:  # noqa: SLF001
        reg.register_type(
            ProviderCapability(
                type="openai_compatible",
                capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
                supports_streaming=True,
                supports_tools=True,
                supports_embeddings=True,
                supports_vision=True,
                max_context_tokens=0,
            ),
            lambda **kw: None,
        )

    n = R.sync_entries_from_config()
    assert n >= 2

    by_name = {e.name: e for e in reg.list_entries()}
    assert "TestAli" in by_name and "LocalLlama" in by_name
    assert by_name["TestAli"].type == "openai_compatible"
    assert by_name["TestAli"].options.get("_original_type") is None
    assert by_name["LocalLlama"].type == "ollama"

    assert R.sync_entries_from_config() == 0

    reg.unregister_entry("TestAli")
    reg.unregister_entry("LocalLlama")


def test_sync_no_config_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gideon.core.config.loader.config_path", lambda: tmp_path / "missing.json"
    )
    assert R.sync_entries_from_config() == 0
