"""The model-catalog seam: register_catalog / catalog_of / build_catalog.

This is the provider-agnostic discovery/management axis that replaced the
per-type switch in the HTTP handlers. Core resolves an entry → its catalog via
``registry.build_catalog(entry)`` (fail-soft). The reference ModelManager
(OllamaCatalog) is exercised in its app's own suite
(apps/ollama-models/tests/test_catalog.py) — core tests the seam against
in-test fakes only.
"""

from __future__ import annotations

import asyncio

import pytest

from gideon.integrations.llm.catalog import (
    ModelCatalog,
    ModelInfo,
    infer_capabilities,
    openai_compatible_list_models,
)
from gideon.integrations.llm.registry import ProviderEntry, ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def test_build_catalog_none_for_unregistered_type():
    reg = ProviderRegistry()
    entry = ProviderEntry(name="X", type="not-registered", model="m", options={})
    assert reg.build_catalog(entry) is None


def test_register_and_build_catalog_round_trip():
    reg = ProviderRegistry()

    class Cat(ModelCatalog):
        def __init__(self, opts, model=""):
            self.opts, self.model = opts, model

        async def list_models(self):
            return [ModelInfo(id="m", name="m", capabilities=["chat"])]

    reg.register_catalog("mytype", lambda options, model="": Cat(options, model))
    entry = ProviderEntry(name="E", type="mytype", model="pinned", options={"k": "v"})
    cat = reg.build_catalog(entry)
    assert isinstance(cat, Cat)
    assert cat.opts == {"k": "v"} and cat.model == "pinned"
    assert [m.id for m in _run(cat.list_models())] == ["m"]


def test_build_catalog_failsoft_when_factory_raises():
    reg = ProviderRegistry()

    def _boom(options, model=""):
        raise RuntimeError("bad factory")

    reg.register_catalog("boomtype", _boom)
    entry = ProviderEntry(name="E", type="boomtype", model="m", options={})
    assert reg.build_catalog(entry) is None


def test_register_catalog_last_wins():
    reg = ProviderRegistry()
    reg.register_catalog("t", lambda o, model="": "first")
    reg.register_catalog("t", lambda o, model="": "second")
    assert reg.catalog_of("t")({}) == "second"


@pytest.mark.parametrize(
    "mid,expected_contains",
    [
        ("text-embedding-3-small", ["embedding"]),
        ("whisper-1", ["stt"]),
        ("gpt-4o", ["chat", "image_modality"]),
        ("llama3", ["chat"]),
        ("dall-e-3", ["image_gen"]),
    ],
)
def test_infer_capabilities(mid, expected_contains):
    caps = infer_capabilities(mid)
    for c in expected_contains:
        assert c in caps


def test_openai_compatible_helper_returns_empty_without_config():
    assert _run(openai_compatible_list_models(None, None)) == []


def test_openai_compatible_discovery_layers_operator_egress(monkeypatch):
    """Model discovery for a self-hosted OpenAI-compatible endpoint must route
    through egress_policy_for(CONNECTOR) — NOT raw CONNECTOR — so an operator who
    allow-lists a private/loopback host (vLLM/LM Studio/Ollama) can actually
    discover its models. Regression: raw CONNECTOR blocked an allow-listed
    localhost endpoint, so the picker was always empty."""
    from gideon.security.net import CONNECTOR

    sentinel = CONNECTOR.with_overrides(allow_hosts=("127.0.0.1",))
    seen: dict = {}

    def fake_layer(policy):
        seen["layered_base"] = policy.name
        return sentinel

    class _Resp:
        status = 200
        text = '{"data": [{"id": "qwen2.5:0.5b"}]}'

    async def fake_fetch(url, *, policy=None, method="GET", headers=None):
        seen["policy"] = policy
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(
        "gideon.security.net.egress_policy_for", fake_layer, raising=False
    )
    monkeypatch.setattr("gideon.sdk.net.egress_policy_for", fake_layer, raising=False)
    monkeypatch.setattr("gideon.security.net.client.fetch", fake_fetch, raising=False)
    monkeypatch.setattr("gideon.sdk.net.fetch", fake_fetch, raising=False)

    out = _run(openai_compatible_list_models("http://127.0.0.1:11434/v1", ""))
    assert (
        seen.get("policy") is sentinel
    ), "discovery must use egress_policy_for(CONNECTOR), not raw CONNECTOR"
    assert seen["url"].endswith("/v1/models")
    assert [m.id for m in out] == ["qwen2.5:0.5b"]


def test_catalog_wire_projection_retains_zero_and_protects_emitted_fields():
    from gideon.integrations.llm.catalog import ConnectionResult, PullProgress

    model = ModelInfo(
        id="authoritative",
        name="Named",
        capabilities=["chat"],
        size=0,
        downloaded=False,
        extra={"id": "shadow", "size": 999, "owner": "local"},
    )
    encoded = model.to_dict()
    assert encoded == {
        "id": "authoritative",
        "name": "Named",
        "capabilities": ["chat"],
        "size": 0,
        "downloaded": False,
        "owner": "local",
    }
    encoded["capabilities"].append("changed")
    assert model.capabilities == ["chat"]
    assert ConnectionResult(False, model_count=0).to_dict() == {
        "ok": False,
        "model_count": 0,
    }
    assert PullProgress("loading", completed=0, total=0).to_dict() == {
        "status": "loading",
        "completed": 0,
        "total": 0,
    }
    assert PullProgress("loading", completed=10, error="failed").to_dict() == {
        "error": "failed"
    }


@pytest.mark.parametrize(
    "identifier,families,wanted",
    [
        ("whisper-embedding-vision", [], ["embedding"]),
        ("tts-whisper", [], ["stt"]),
        ("flux-sora", [], ["video_gen"]),
        (
            "voice-vision-video-vl",
            [],
            ["chat", "image_modality", "video_modality", "audio_modality"],
        ),
        ("local", ["clip"], ["chat", "image_modality"]),
    ],
)
def test_capability_rule_order_and_composable_modalities(identifier, families, wanted):
    assert infer_capabilities(identifier, families) == wanted


def test_model_row_decoder_ignores_malformed_rows_and_retains_owner():
    from gideon.integrations.llm.catalog import _decode_model_rows

    assert _decode_model_rows([{"id": "not-a-response"}]) == []
    assert _decode_model_rows({"data": {"id": "not-a-list"}}) == []
    rows = _decode_model_rows(
        {"data": [None, {}, {"id": 4}, {"id": "qwen", "owned_by": "local"}]}
    )
    assert [row.to_dict() for row in rows] == [
        {"id": "qwen", "name": "qwen", "capabilities": ["chat"], "owned_by": "local"}
    ]
