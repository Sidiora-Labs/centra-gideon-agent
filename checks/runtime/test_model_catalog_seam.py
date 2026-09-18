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
import json
from typing import Any

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
    import gideon.sdk.net  # noqa: F401
    from gideon.security.net import CONNECTOR

    # ``gideon.sdk.net`` is imported BEFORE the patches below so monkeypatch
    # records its REAL attributes: a module first imported DURING patching binds
    # the fakes from the module it re-exports, and the undo then restores those.
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


class _Endpoint:
    """A real loopback HTTP server standing in for an OpenAI-compatible provider.

    Real, because every failure class under test is a property of the WIRE: the path
    the address builder actually requests, the status the server actually returns,
    the bytes it actually sends. A patched ``fetch`` can only re-assert the mapping
    the test itself wrote down.
    """

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.authorization: list[str] = []
        self.reply: Any = None
        self.base = ""

    async def _handle(self, request):
        self.paths.append(request.path)
        self.authorization.append(request.headers.get("Authorization", ""))
        return self.reply(request)


@pytest.fixture
async def endpoint():
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    state = _Endpoint()
    state.reply = lambda _r: web.json_response({"data": []})
    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", state._handle)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    state.base = str(server.make_url("")).rstrip("/")
    try:
        yield state
    finally:
        await server.close()


@pytest.fixture
def operator_home(tmp_path, monkeypatch):
    """A real ``$GIDEON_HOME`` whose ``security.egress`` the test writes.

    The egress decision under test is the product's own: ``egress_policy_for`` reads
    this file. Writing it (rather than patching the policy) is what makes the
    policy-blocked case a real refusal and the other cases real allowances.
    """
    import json as _json

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))

    def _write(**egress):
        (home / "config.json").write_text(
            _json.dumps({"security": {"egress": egress}}), encoding="utf-8"
        )

    return _write


@pytest.fixture
def allow_loopback(operator_home):
    operator_home(allow_hosts=["127.0.0.1"], allow_private=True)


def _json_reply(payload, status=200):
    from aiohttp import web

    return lambda _r: web.json_response(payload, status=status)


@pytest.mark.parametrize(
    "suffix,requested",
    [
        ("", "/v1/models"),
        ("/v1", "/v1/models"),
        ("/v1beta", "/v1beta/models"),
        ("/v2", "/v2/models"),
        ("/openai/v1", "/openai/v1/models"),
        ("/v1/", "/v1/models"),
    ],
)
async def test_an_existing_api_version_segment_is_not_doubled(
    allow_loopback, endpoint, suffix, requested
):
    """Only ``/v1`` used to be recognized, so every other versioned endpoint was asked
    for ``…/v1beta/v1/models`` — a 404 that discovery then reported as "no models",
    making a misconfigured address look like an empty provider."""
    endpoint.reply = _json_reply({"data": [{"id": "qwen"}]})

    models = await openai_compatible_list_models(f"{endpoint.base}{suffix}", "sk-test")

    assert [m.id for m in models] == ["qwen"]
    assert endpoint.paths == [requested]
    assert endpoint.authorization == ["Bearer sk-test"]


async def test_an_egress_policy_block_is_reported_not_swallowed(
    endpoint, operator_home
):
    """The operator has NOT allow-listed the loopback endpoint, so the guard refuses.

    This is the failure a self-hoster hits first (vLLM/LM Studio/Ollama on a private
    address) and the one the empty list was most misleading about: the picker was
    empty, the endpoint was fine, and nothing named the control that stopped it.
    """
    from gideon.integrations.llm.catalog import (
        DISCOVERY_POLICY_BLOCKED,
        ModelDiscoveryError,
    )

    operator_home(allow_hosts=[], allow_private=False)
    endpoint.reply = _json_reply({"data": [{"id": "qwen"}]})

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test")

    failure = caught.value
    assert failure.kind == DISCOVERY_POLICY_BLOCKED
    assert "allow_hosts" in failure.remedy
    assert endpoint.paths == [], "the request left the machine despite the policy"


async def test_a_dead_endpoint_is_reported_as_a_connection_failure(
    allow_loopback, endpoint
):
    """Nothing listening on the port: a connection failure, not an empty catalog."""
    import socket

    from gideon.integrations.llm.catalog import (
        DISCOVERY_CONNECTION_FAILED,
        ModelDiscoveryError,
    )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"http://127.0.0.1:{port}/v1", "sk-test")

    failure = caught.value
    assert failure.kind == DISCOVERY_CONNECTION_FAILED
    assert f"127.0.0.1:{port}" in failure.detail
    assert failure.remedy


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_credential_is_reported_as_unauthorized(
    allow_loopback, endpoint, status
):
    """A key the endpoint refuses is the one discovery failure the user can fix in the
    same dialog they are already looking at."""
    from gideon.integrations.llm.catalog import (
        DISCOVERY_UNAUTHORIZED,
        ModelDiscoveryError,
    )

    endpoint.reply = _json_reply({"error": "bad key"}, status=status)

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-wrong")

    failure = caught.value
    assert failure.kind == DISCOVERY_UNAUTHORIZED
    assert str(status) in failure.detail
    assert "API key" in failure.remedy


async def test_a_non_json_body_is_reported_as_malformed(allow_loopback, endpoint):
    """A 200 that is a login page or a proxy's error page, not a model list."""
    from aiohttp import web

    from gideon.integrations.llm.catalog import (
        DISCOVERY_MALFORMED_RESPONSE,
        ModelDiscoveryError,
    )

    endpoint.reply = lambda _r: web.Response(
        text="<html><body>sign in</body></html>", content_type="text/html"
    )

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test")

    failure = caught.value
    assert failure.kind == DISCOVERY_MALFORMED_RESPONSE
    assert "text/html" in failure.detail
    assert failure.remedy


@pytest.mark.parametrize(
    "payload",
    [{"models": [{"id": "qwen"}]}, {"data": {"id": "qwen"}}, [{"id": "qwen"}]],
)
async def test_a_wrong_shape_body_is_reported_as_malformed(
    allow_loopback, endpoint, payload
):
    """JSON, but not the OpenAI model-list shape — previously indistinguishable from
    a provider that genuinely lists nothing."""
    from gideon.integrations.llm.catalog import (
        DISCOVERY_MALFORMED_RESPONSE,
        ModelDiscoveryError,
    )

    endpoint.reply = _json_reply(payload)

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test")

    assert caught.value.kind == DISCOVERY_MALFORMED_RESPONSE


async def test_rows_without_ids_are_reported_rather_than_read_as_emptiness(
    allow_loopback, endpoint
):
    """``data`` full of rows that carry no id is a shape problem, not zero models."""
    from gideon.integrations.llm.catalog import (
        DISCOVERY_MALFORMED_RESPONSE,
        ModelDiscoveryError,
    )

    endpoint.reply = _json_reply({"data": [{"model": "qwen"}, {"name": "llama"}]})

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test")

    assert caught.value.kind == DISCOVERY_MALFORMED_RESPONSE


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_failing_endpoint_is_reported_as_a_server_error(
    allow_loopback, endpoint, status
):
    from gideon.integrations.llm.catalog import (
        DISCOVERY_SERVER_ERROR,
        ModelDiscoveryError,
    )

    endpoint.reply = _json_reply({"error": "upstream down"}, status=status)

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test")

    failure = caught.value
    assert failure.kind == DISCOVERY_SERVER_ERROR
    assert str(status) in failure.detail
    assert failure.remedy


@pytest.mark.parametrize("status", [404, 405, 501])
async def test_an_endpoint_with_no_models_route_is_still_an_empty_list(
    allow_loopback, endpoint, status
):
    """The ONE legitimate empty answer: several hosted providers serve no models
    route at all, and their curated fallback catalog is the right thing to show. If
    this became an error the fallback would be unreachable (ac_3)."""
    endpoint.reply = _json_reply({"error": "nope"}, status=status)

    assert await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test") == []


async def test_a_well_formed_empty_catalog_is_still_an_empty_list(
    allow_loopback, endpoint
):
    endpoint.reply = _json_reply({"data": []})

    assert await openai_compatible_list_models(f"{endpoint.base}/v1", "sk-test") == []


async def test_no_discovery_error_can_carry_a_secret(allow_loopback, endpoint):
    """Every field is rendered in the UI and written to the log, so none of them may
    quote the key or the credentials an endpoint URL can itself carry."""
    from gideon.integrations.llm.catalog import ModelDiscoveryError

    endpoint.reply = _json_reply({"error": "bad key"}, status=401)
    host = endpoint.base.split("//", 1)[1]

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_list_models(
            f"http://admin:hunter2@{host}/v1?api-key=sk-in-the-url", "sk-live-secret"
        )

    rendered = " ".join([str(caught.value), *caught.value.to_dict().values()])
    for secret in ("sk-live-secret", "hunter2", "admin:", "sk-in-the-url"):
        assert secret not in rendered, f"{secret!r} leaked into {rendered!r}"


async def test_every_kind_is_one_of_the_declared_discovery_kinds():
    from gideon.integrations.llm.catalog import (
        DISCOVERY_CONNECTION_FAILED,
        DISCOVERY_ERROR_KINDS,
        DISCOVERY_MALFORMED_RESPONSE,
        DISCOVERY_POLICY_BLOCKED,
        DISCOVERY_SERVER_ERROR,
        DISCOVERY_UNAUTHORIZED,
    )

    assert DISCOVERY_ERROR_KINDS == {
        DISCOVERY_POLICY_BLOCKED,
        DISCOVERY_CONNECTION_FAILED,
        DISCOVERY_UNAUTHORIZED,
        DISCOVERY_MALFORMED_RESPONSE,
        DISCOVERY_SERVER_ERROR,
    }


async def test_a_branded_catalog_falls_back_but_does_not_hide_a_failure(
    allow_loopback, endpoint
):
    """The curated fallback covers discovery being UNSUPPORTED, not discovery FAILING.

    Answering a failed probe with the curated list renders a picker full of models
    the user cannot reach; ``test_connection`` is where the failure becomes a
    sentence instead of an exception.
    """
    from gideon.integrations.llm.branded_specs import BrandedProviderSpec
    from gideon.integrations.llm.catalog import ModelDiscoveryError
    from gideon.sdk.provider_helpers import BrandedCatalog

    spec = BrandedProviderSpec(
        type="fixture-provider",
        default_base_url=f"{endpoint.base}/v1",
        api_key_env="FIXTURE_API_KEY",
        fallback_models=({"id": "curated-1"},),
    )
    catalog = BrandedCatalog(spec, endpoint=f"{endpoint.base}/v1", api_key="sk-test")

    endpoint.reply = _json_reply({"error": "nope"}, status=404)
    assert [m.id for m in await catalog.list_models()] == ["curated-1"]

    endpoint.reply = _json_reply({"error": "down"}, status=503)
    with pytest.raises(ModelDiscoveryError):
        await catalog.list_models()

    probe = await catalog.test_connection()
    assert probe.ok is False
    assert "503" in probe.detail


async def test_the_models_endpoint_surfaces_the_typed_failure(
    allow_loopback, endpoint, monkeypatch
):
    """The dashboard route reports the failure instead of an empty list.

    ``error`` is the sentence the console already renders above the model list;
    ``discovery_error`` is the same failure typed, for a caller that branches on the
    class rather than parsing the sentence.
    """
    from aiohttp.test_utils import make_mocked_request

    from gideon.integrations.llm.branded_specs import BrandedProviderSpec
    from gideon.integrations.llm.registry import ProviderEntry, ProviderRegistry
    from gideon.interfaces.dashboard.handlers import providers as handlers
    from gideon.sdk.provider_helpers import BrandedCatalog

    spec = BrandedProviderSpec(
        type="fixture-provider",
        default_base_url=f"{endpoint.base}/v1",
        fallback_models=({"id": "curated-1"},),
    )
    registry = ProviderRegistry()
    registry.register_catalog(
        "fixture-provider",
        lambda options, model="": BrandedCatalog(
            spec, endpoint=options.get("endpoint", ""), api_key="sk-test"
        ),
    )
    entry = ProviderEntry(
        name="fixture",
        type="fixture-provider",
        model="",
        options={"endpoint": f"{endpoint.base}/v1"},
    )
    registry.register_entry(entry)
    monkeypatch.setattr(
        "gideon.integrations.llm.registry.get_default_registry", lambda: registry
    )
    endpoint.reply = _json_reply({"error": "down"}, status=503)

    request = make_mocked_request("GET", "/api/model-providers/fixture/models")
    request.match_info["name"] = "fixture"
    response = await handlers.api_provider_models(request)

    body = json.loads(response.body.decode())
    assert body["models"] == []
    assert body["discovery_error"]["kind"] == "server_error"
    assert body["discovery_error"]["remedy"]
    assert "503" in body["error"]
