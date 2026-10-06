from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.providers import use_cases
from gideon.extensions.providers.provider_bridge import (
    can_resolve_use_case,
    model_chosen,
    use_case_problem,
)
from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider
from gideon.integrations.image_gen.provider import ImageGenError
from gideon.integrations.llm.branded_specs import BrandedProviderSpec
from gideon.integrations.llm.capabilities import Capability
from gideon.integrations.llm.registry import (
    NO_MODEL_NAMED,
    ProviderEntry,
    ProviderRegistry,
    get_default_registry,
    set_default_registry,
)
from gideon.integrations.stt.openai_provider import OpenAISttProvider
from gideon.integrations.tts.openai_provider import OpenAITtsProvider
from gideon.operations.resilience import degraded
from gideon.sdk.provider_helpers import register_branded_app


@pytest.fixture
def isolated_models(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    assert use_cases.active_models_path().parent == tmp_path
    previous = get_default_registry()
    registry = ProviderRegistry()
    set_default_registry(registry)
    contracts = dict(degraded._CONTRACTS)
    degraded._CONTRACTS.clear()
    degraded.register_contract(
        degraded.DegradedContract("chat", ("chat",), "You can still browse history.")
    )
    degraded.reset_transition_state()
    yield registry, tmp_path
    set_default_registry(previous)
    degraded._CONTRACTS.clear()
    degraded._CONTRACTS.update(contracts)
    degraded.reset_transition_state()


def test_media_refuses_unnamed_before_credentials_or_files(tmp_path, caplog):
    image = OpenAIImageProvider(provider_name="Images", provider_type="openai")
    speech = OpenAISttProvider(provider_name="Speech", provider_type="openai")
    voice = OpenAITtsProvider(provider_name="Voice", provider_type="openai")
    for model in ("", "   ", None):
        with pytest.raises(ImageGenError, match="No model is chosen"):
            asyncio.run(image.generate("A tree", model=model))
        with pytest.raises(ImageGenError, match="No model is chosen"):
            asyncio.run(image.edit("A tree", source_image="missing.png", model=model))
        assert asyncio.run(speech.transcribe("missing.wav", model=model)) is None
        assert (
            asyncio.run(
                voice.synthesize(
                    "Hello", voice=model, output_path=str(tmp_path / "speech.wav")
                )
            )
            is None
        )
    assert not (tmp_path / "speech.wav").exists()
    assert NO_MODEL_NAMED in caplog.text


def test_named_media_jobs_preserve_full_model_identity():
    named = "vendor/nova:0"
    image = OpenAIImageProvider(provider_name="Images")
    assert image._default_model(named) == named
    speech = OpenAISttProvider(provider_name="Speech")
    job = speech._job("recording.wav", named, "en-US", "")
    assert job.model == named
    voice = OpenAITtsProvider(provider_name="Voice")
    request = voice._request("Hello", named, "alloy", 1.0)
    assert request["model"] == named


def test_legacy_empty_bindings_are_filtered_without_losing_colons(isolated_models):
    _, home = isolated_models
    (home / "config.json").write_text(json.dumps({"providers": [{"name": "Bedrock"}]}))
    use_cases.save_active_models(
        {"chat": ["", None, "Bedrock:", "Bedrock:  ", "Bedrock:amazon.nova:0"]}
    )
    assert use_cases.active_model_refs("chat") == ["Bedrock:amazon.nova:0"]


def test_active_model_http_rejects_entire_invalid_chain(isolated_models):
    _, home = isolated_models
    from gideon.interfaces.dashboard.handlers.model_registry import (
        api_models_active,
        api_models_active_set,
        api_models_chat,
    )

    (home / "config.json").write_text(json.dumps({"providers": [{"name": "Bedrock"}]}))

    async def exercise():
        app = web.Application()
        app.router.add_get("/api/models/active", api_models_active)
        app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
        app.router.add_get("/api/models/chat", api_models_chat)
        async with TestClient(TestServer(app)) as client:
            for invalid in ("Bedrock:", "Bedrock:   ", "", None):
                response = await client.put(
                    "/api/models/active/chat",
                    json={"models": ["Bedrock:valid", invalid]},
                )
                assert response.status == 400
                assert (await response.json())["error"][
                    "code"
                ] == "model_ref_names_no_model"
                assert use_cases.load_active_models() == {}
            current = await (await client.get("/api/models/active")).json()
            response = await client.put(
                "/api/models/active/chat",
                json={"models": ["Bedrock:amazon.nova:0"]},
                headers={"If-Match": f'"{current["revisions"]["chat"]}"'},
            )
            assert response.status == 200
            assert (await response.json())["models"] == ["Bedrock:amazon.nova:0"]
            response = await client.get("/api/models/chat")
            assert (await response.json())[0]["model_id"] == "amazon.nova:0"

    asyncio.run(exercise())


def test_no_choice_and_missing_provider_are_different_real_readiness(isolated_models):
    registry, _ = isolated_models
    register_branded_app(
        BrandedProviderSpec(type="openai", capabilities=frozenset({Capability.CHAT}))
    )
    registry.register_entry(ProviderEntry("Endpoint", "openai", ""))
    assert not model_chosen("chat")
    assert not can_resolve_use_case("chat")
    row = degraded.evaluate()[0]
    assert row["model_chosen"] is False
    assert row["problem"] == "No model chosen for Chat."
    registry.unregister_entry("Endpoint")
    registry.register_entry(
        ProviderEntry(
            "Unavailable",
            "not-installed",
            "model:0",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )
    assert model_chosen("chat")
    assert not can_resolve_use_case("chat")
    why, fix = use_case_problem("chat")
    assert "not available" in why
    row = degraded.evaluate()[0]
    assert row["model_chosen"] is True
    assert why + "." in row["problem"]
    assert fix[0].upper() + fix[1:] + "." in row["problem"]


def test_real_notifications_distinguish_selection_from_recovery(isolated_models):
    registry, _ = isolated_models
    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.state import ConsoleState

    register_branded_app(
        BrandedProviderSpec(type="openai", capabilities=frozenset({Capability.CHAT}))
    )
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    registry.register_entry(ProviderEntry("Endpoint", "openai", "model:0"))
    degraded.evaluate(notify=True, state=state)
    assert state._notification_log == []
    registry.unregister_entry("Endpoint")
    degraded.evaluate(notify=True, state=state)
    assert state._notification_log[-1]["kind"] == "info"
    assert state._notification_log[-1]["title"] == "Choose a model for Chat"
    registry.register_entry(ProviderEntry("Endpoint", "openai", "model:0"))
    degraded.evaluate(notify=True, state=state)
    assert state._notification_log[-1]["title"] == "Chat is ready"
    registry.unregister_entry("Endpoint")
    registry.register_entry(
        ProviderEntry(
            "Unavailable",
            "not-installed",
            "model:0",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )
    degraded.evaluate(notify=True, state=state)
    assert state._notification_log[-1]["kind"] == "warning"
    assert state._notification_log[-1]["title"] == "Chat degraded"
    assert degraded.evaluate()[0]["problem"] in state._notification_log[-1]["body"]
    registry.unregister_entry("Unavailable")
    registry.register_entry(ProviderEntry("Endpoint", "openai", "model:0"))
    degraded.evaluate(notify=True, state=state)
    assert state._notification_log[-1]["title"] == "Chat recovered"
