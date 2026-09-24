from __future__ import annotations

import pytest

from gideon.extensions.providers.failure_copy import provider_load_error
from gideon.extensions.providers.provider_bridge import (
    ProviderResolutionError,
    _resolve_from_config_registry,
    resolve_provider_for_use_case,
)
from gideon.extensions.providers.use_cases import save_active_models
from gideon.integrations.llm.branded_specs import BrandedProviderSpec
from gideon.integrations.llm.capabilities import Capability
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderRegistry,
    set_default_registry,
)
from gideon.sdk.provider_helpers import register_branded_app


@pytest.mark.parametrize(
    ("cause", "why", "fix"),
    [
        ("registry_unavailable", "registry could not be loaded", "restart"),
        ("unsupported_capability", "not a supported model capability", "supported"),
        ("no_entries", "no configured entries", "add a model provider"),
        ("entry_missing", "entry 'Cloud' is absent", "configure"),
        ("capability_missing", "no eligible provider declares", "supports it"),
        ("factory_missing", "TYPE 'cloud_protocol'", "TYPE 'cloud_protocol'"),
        ("credential_missing", "missing credential", "credential"),
        ("dependency_missing", "module could not be imported", "missing module"),
        ("construction_failed", "constructor failed", "construction error"),
        ("future_cause", "not sure why", "check the gateway logs"),
    ],
)
def test_load_cause_copy(cause, why, fix):
    error = provider_load_error(
        cause, use_case="embedding", provider="Cloud", provider_type="cloud_protocol"
    )
    assert why in error.why
    assert fix in error.fix
    assert error.code == "ERR_MODEL_UNRESOLVED"


@pytest.fixture
def registry():
    registry = ProviderRegistry()
    set_default_registry(registry)
    return registry


def entry(name="Cloud", type_="cloud_protocol", **kwargs):
    return ProviderEntry(
        name=name,
        type=type_,
        model="embed-1",
        declared_capabilities=frozenset({Capability.EMBEDDING}),
        **kwargs,
    )


def test_missing_type_reaches_rendered_pinned_error(registry):
    registry.register_entry(entry())
    save_active_models({"embedding": ["Cloud:embed-1"]})
    with pytest.raises(ProviderResolutionError) as caught:
        resolve_provider_for_use_case("embedding")
    assert "TYPE 'cloud_protocol'" in str(caught.value)
    assert "absent from config" not in str(caught.value)
    assert caught.value.agent_error.fix in str(caught.value)


@pytest.mark.parametrize(
    ("capability", "hint", "expected"),
    [
        ("unknown-capability", None, "not a supported"),
        ("embedding", None, "no configured entries"),
    ],
)
def test_empty_registry_reports_actual_branch(registry, capability, hint, expected):
    failures = []
    assert (
        _resolve_from_config_registry(
            capability, provider_hint=hint, _failures=failures
        )
        is None
    )
    assert expected in failures[-1].why


def test_missing_entry_and_capability_have_different_causes(registry):
    register_branded_app(
        BrandedProviderSpec(type="chat-only", capabilities=frozenset({Capability.CHAT}))
    )
    registry.register_entry(
        ProviderEntry(name="Chat", type="chat-only", model="chat-1")
    )
    for hint, expected in (
        ("Deleted", "entry 'Deleted' is absent"),
        ("Chat", "no eligible provider"),
    ):
        failures = []
        assert (
            _resolve_from_config_registry(
                "embedding", provider_hint=hint, _failures=failures
            )
            is None
        )
        assert expected in failures[-1].why


def test_real_credential_failure_survives_resolution(registry, tmp_path):
    register_branded_app(
        BrandedProviderSpec(
            type="cloud_protocol", capabilities=frozenset({Capability.EMBEDDING})
        )
    )
    registry.register_entry(entry(credential="cloud-key"))
    store = CredentialStore(tmp_path)
    store.save({"cloud-key": {"type": "api_key"}})
    save_active_models({"embedding": ["Cloud:embed-1"]})
    with pytest.raises(ProviderResolutionError) as caught:
        resolve_provider_for_use_case("embedding", credential_store=store)
    assert "missing credential" in caught.value.agent_error.why
    assert "absent" not in str(caught.value)


def test_real_constructor_error_is_uncertain_and_hides_diagnostics(registry):
    register_branded_app(
        BrandedProviderSpec(
            type="cloud_protocol", capabilities=frozenset({Capability.EMBEDDING})
        )
    )
    registry.register_entry(
        entry(
            options={
                "api_key": "sensitive-key",
                "base_url": "http://localhost:invalid-port",
            }
        )
    )
    save_active_models({"embedding": ["Cloud:embed-1"]})
    with pytest.raises(ProviderResolutionError) as caught:
        resolve_provider_for_use_case("embedding")
    assert "constructor failed" in caught.value.agent_error.why
    assert "not sure" in caught.value.agent_error.why
    assert "sensitive-key" not in str(caught.value)
