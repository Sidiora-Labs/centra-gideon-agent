"""Local-model availability, health, and bounded per-capability self-tests."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.integrations.local_models import registry, residency
from gideon.integrations.local_models.provider import (
    CapabilitySelfTestResult,
    LocalModel,
    LocalModelFailure,
    LocalModelFailureCode,
    LocalModelProvider,
    LocalModelSelfTestError,
)
from gideon.interfaces.dashboard.handlers import model_registry as handlers


class _ContractProvider(LocalModelProvider):
    def __init__(self) -> None:
        self.available = True
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.behavior = "success"
        self._model = None

    @property
    def name(self) -> str:
        return "contract-provider"

    @property
    def display_name(self) -> str:
        return "Contract Provider"

    async def is_available(self) -> bool:
        if self.behavior == "availability_error":
            raise RuntimeError("runtime import failed")
        return self.available

    async def list_models(self) -> list[LocalModel]:
        return [LocalModel(name="fixture", capabilities=["chat", "embedding"])]

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True

    async def self_test(self, capability: str) -> CapabilitySelfTestResult:
        self.calls.append(capability)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            if self.behavior == "hang":
                await asyncio.Event().wait()
            if self.behavior == "typed_error":
                raise LocalModelSelfTestError(
                    LocalModelFailure(
                        LocalModelFailureCode.UNAVAILABLE,
                        "weights are not downloaded",
                    )
                )
            if self.behavior == "invalid_result":
                return True  # type: ignore[return-value]
            await asyncio.sleep(0)
            return CapabilitySelfTestResult.success(f"{capability} inference returned")
        finally:
            self.active -= 1


@pytest.fixture
def local_provider():
    provider = _ContractProvider()
    registry.register_provider(
        provider,
        capabilities=["chat", "embedding"],
        name="contract-provider",
    )
    try:
        yield provider
    finally:
        registry.unregister_provider("contract-provider")


def _body(response: web.Response) -> dict:
    return json.loads(response.body.decode())


def test_typed_self_test_values_have_one_wire_shape():
    result = CapabilitySelfTestResult.failed(
        LocalModelFailureCode.TIMEOUT,
        "probe exceeded its bound",
        retryable=True,
    )
    assert result.to_dict() == {
        "ok": False,
        "detail": "",
        "failure": {
            "code": "timeout",
            "message": "probe exceeded its bound",
            "retryable": True,
        },
    }


@pytest.mark.asyncio
async def test_default_embedding_probe_runs_real_provider_inference():
    class _EmbeddingProvider(_ContractProvider):
        async def list_models(self) -> list[LocalModel]:
            return [LocalModel(name="fixture", downloaded=True)]

        async def embed(self, text: str, model: str = "") -> list[float]:
            assert text == "Gideon local model self-test"
            assert model == "fixture"
            return [0.25, 0.75]

        async def self_test(self, capability: str) -> CapabilitySelfTestResult:
            return await LocalModelProvider.self_test(self, capability)

    result = await _EmbeddingProvider().self_test("embedding")
    assert result == CapabilitySelfTestResult.success("embedding returned 2 dims")


@pytest.mark.asyncio
async def test_availability_is_live_and_failures_are_typed(local_provider):
    snapshot = await registry.availability_snapshot()
    row = next(
        item
        for item in snapshot["providers"]
        if item["provider"] == "contract-provider"
    )
    assert row["available"] is True
    assert row["capabilities"] == ["chat", "embedding"]
    assert row["failure"] is None

    local_provider.behavior = "availability_error"
    snapshot = await registry.availability_snapshot()
    row = next(
        item
        for item in snapshot["providers"]
        if item["provider"] == "contract-provider"
    )
    assert row["available"] is False
    assert row["failure"] == {
        "code": "provider_error",
        "message": "runtime import failed",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_health_reports_readiness_capabilities_and_residency(local_provider):
    class _Loaded:
        name = "fixture"

    local_provider._model = _Loaded()
    snapshot = await residency.local_model_health_snapshot()
    row = next(
        item
        for item in snapshot["providers"]
        if item["provider"] == "contract-provider"
    )
    assert row["ok"] is True
    assert row["state"] == "ready"
    assert row["kind"] == "in-process"
    assert row["capabilities"] == ["chat", "embedding"]
    assert [item["model"] for item in row["loaded"]] == ["fixture"]
    assert row["failure"] is None


@pytest.mark.asyncio
async def test_capability_probes_run_in_order_and_never_overlap(local_provider):
    result = await registry.run_provider_self_tests("contract-provider")
    assert result["ok"] is True
    assert [item["capability"] for item in result["tests"]] == [
        "chat",
        "embedding",
    ]
    assert local_provider.calls == ["chat", "embedding"]
    assert local_provider.max_active == 1
    assert all(item["failure"] is None for item in result["tests"])


@pytest.mark.asyncio
async def test_an_overlapping_self_test_is_a_typed_busy_refusal(local_provider):
    local_provider.release = asyncio.Event()
    running = asyncio.create_task(
        registry.run_provider_self_tests("contract-provider", ["chat"])
    )
    await local_provider.started.wait()

    refused = await registry.run_provider_self_tests("contract-provider", ["embedding"])
    assert refused["ok"] is False
    assert refused["tests"] == []
    assert refused["failure"]["code"] == "busy"
    assert refused["failure"]["retryable"] is True

    local_provider.release.set()
    assert (await running)["ok"] is True


@pytest.mark.asyncio
async def test_each_capability_has_its_own_hard_timeout(local_provider):
    local_provider.behavior = "hang"
    result = await registry.run_provider_self_tests(
        "contract-provider", ["chat", "embedding"], timeout_seconds=0.001
    )
    assert result["ok"] is False
    assert [item["failure"]["code"] for item in result["tests"]] == [
        "timeout",
        "timeout",
    ]
    assert local_provider.calls == ["chat", "embedding"]


@pytest.mark.asyncio
async def test_provider_typed_failures_survive_the_registry_boundary(local_provider):
    local_provider.behavior = "typed_error"
    result = await registry.run_provider_self_tests("contract-provider", ["chat"])
    assert result["tests"][0]["failure"] == {
        "code": "unavailable",
        "message": "weights are not downloaded",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_untyped_results_are_rejected_not_treated_as_success(local_provider):
    local_provider.behavior = "invalid_result"
    result = await registry.run_provider_self_tests("contract-provider", ["chat"])
    assert result["ok"] is False
    assert result["tests"][0]["failure"]["code"] == "invalid_result"


@pytest.mark.asyncio
async def test_local_model_handlers_expose_snapshots_and_typed_unknown_provider(
    local_provider,
):
    response = await handlers.api_local_models_availability(
        make_mocked_request("GET", "/api/models/local/availability")
    )
    assert any(
        row["provider"] == "contract-provider" for row in _body(response)["providers"]
    )

    response = await handlers.api_local_models_health(
        make_mocked_request("GET", "/api/models/local/health")
    )
    assert any(
        row["provider"] == "contract-provider" for row in _body(response)["providers"]
    )

    response = await handlers.api_local_model_selftest(
        make_mocked_request(
            "POST",
            "/api/models/local/missing/selftest",
            match_info={"provider": "missing"},
        )
    )
    assert response.status == 404
    assert _body(response)["failure"]["code"] == "unknown_provider"


def test_model_registry_registers_the_local_observability_routes():
    app = web.Application()
    handlers.register_model_registry_routes(app)
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/models/local/availability") in routes
    assert ("GET", "/api/models/local/health") in routes
    assert ("POST", "/api/models/local/{provider}/selftest") in routes
    assert ("POST", "/api/model-providers/{name}/selftest") in routes
