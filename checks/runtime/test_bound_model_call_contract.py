import json

import pytest

from gideon.extensions.providers.provider_bridge import (
    ProviderResolutionError as BridgeResolutionError,
    can_resolve_use_case,
    resolve_provider_for_use_case,
)
from gideon.integrations.llm.anthropic import AnthropicProvider
from gideon.integrations.llm.branded_specs import (
    BrandedProviderSpec,
    build_protocol_provider,
)
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderRegistry,
    ProviderResolutionError as ModelResolutionError,
    get_default_registry,
    set_default_registry,
)


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [{"name": "Bound", "type": "bound-openai"}]}),
        encoding="utf-8",
    )
    original = get_default_registry()
    registry = ProviderRegistry()
    credential = Credential(
        name="local-contract", kind="api_key", secret="local-only", source="test"
    )

    def create_openai(*, entry, model="", **_kwargs):
        return OpenAIProvider(
            model=model or entry.own_model,
            credential=credential,
            base_url="http://127.0.0.1:1/v1",
        )

    registry.register_type(
        ProviderCapability(
            type="bound-openai",
            capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        create_openai,
    )
    registry.register_entry(
        ProviderEntry(
            name="Bound",
            type="bound-openai",
            model="",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )
    set_default_registry(registry)
    try:
        yield registry
    finally:
        set_default_registry(original)


def test_bound_slash_model_reaches_real_provider_unchanged(
    tmp_path, isolated_registry
):
    (tmp_path / "active_models.json").write_text(
        json.dumps({"background": ["Bound:org/model/with/slashes"]}),
        encoding="utf-8",
    )

    provider = resolve_provider_for_use_case("background")

    assert isinstance(provider._inner, OpenAIProvider)
    assert provider._inner._model == "org/model/with/slashes"


def test_implicit_resolution_refuses_entry_without_own_model(isolated_registry):
    with pytest.raises(BridgeResolutionError, match="no model is chosen"):
        resolve_provider_for_use_case("background")

    assert not can_resolve_use_case("background")


def test_actual_protocol_requests_refuse_empty_model_before_dispatch():
    credential = Credential(
        name="local-contract", kind="api_key", secret="local-only", source="test"
    )
    openai = OpenAIProvider(
        model="", credential=credential, base_url="http://127.0.0.1:1/v1"
    )
    anthropic = AnthropicProvider(model="", credential=credential)

    with pytest.raises(ModelResolutionError, match="No model is chosen"):
        openai._request(
            [{"role": "user", "content": "hello"}], model="", tools=None
        )
    with pytest.raises(ModelResolutionError, match="No model is chosen"):
        anthropic._request(
            [{"role": "user", "content": "hello"}],
            model="",
            translate=True,
        )


def test_protocol_factories_keep_call_options_under_configured_ceilings():
    credential = Credential(
        name="local-contract", kind="api_key", secret="local-only", source="test"
    )
    spec = BrandedProviderSpec(
        type="contract-openai", protocol="openai", max_tokens=900
    )

    provider = build_protocol_provider(
        spec,
        model="org/model/with/slashes",
        credential=credential,
        base_url="http://127.0.0.1:1/v1",
        extra_options={"temperature": 0.7},
        max_tokens=1200,
    )

    assert isinstance(provider, OpenAIProvider)
    assert provider._model == "org/model/with/slashes"
    assert provider.sampling_temperature == 0.7
    assert provider.output_token_limit == 900


def test_anthropic_sampling_uses_extra_body_and_records_model_refusals():
    credential = Credential(
        name="local-contract", kind="api_key", secret="local-only", source="test"
    )
    accepted = AnthropicProvider(
        model="claude-3-7-sonnet",
        credential=credential,
        extra_options={"temperature": 0.4, "top_k": 6},
    )
    request = accepted._request(
        [{"role": "user", "content": "hello"}],
        model="claude-3-7-sonnet",
        translate=True,
    )
    assert request["extra_body"] == {"temperature": 0.4, "top_k": 6}
    assert accepted.unsent_options == {}

    refused = AnthropicProvider(
        model="claude-opus-4-8",
        credential=credential,
        extra_options={"temperature": 0.4, "top_p": 0.8},
    )
    request = refused._request(
        [{"role": "user", "content": "hello"}],
        model="claude-opus-4-8",
        translate=True,
    )
    assert "extra_body" not in request
    assert set(refused.unsent_options) == {"temperature", "top_p"}
    assert all("does not accept" in reason for reason in refused.unsent_options.values())
