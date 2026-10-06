"""Registered serving authority and diagnostic behavior with an isolated local server."""

import asyncio
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gideon.engine.routing.policy import is_local_ref
from gideon.extensions.providers.connection import (
    CONNECTED,
    Connection,
    ConnectionBoard,
)
from gideon.integrations.llm.catalog import ConnectionResult
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderRegistry,
    get_default_registry,
    served_on_this_machine,
    set_default_registry,
)
from gideon.integrations.model_windows import (
    model_context_window,
    register_served_context_window,
)
from gideon.security.guardrails.breaker import CircuitBreaker
from gideon.security.guardrails.model_call import wrap_model_call_guard


@pytest.fixture
def serving(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    original = get_default_registry()
    registry = ProviderRegistry()
    set_default_registry(registry)
    path = (
        Path(__file__).parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    spec = importlib.util.spec_from_file_location("health_contract_ollama", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._register()
    state = {
        "models": [
            {"name": "regular:8b", "size": 10, "capabilities": ["completion"]},
            {
                "name": "forwarded:8b",
                "remote_host": "https://hosted.invalid",
                "capabilities": ["completion"],
            },
        ],
        "fail": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503 if state["fail"] else 200)
            self.end_headers()
            self.wfile.write(json.dumps({"models": state["models"]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    registry.register_entry(
        ProviderEntry("Local", "ollama", "regular:8b", {"endpoint": endpoint})
    )
    registry.register_entry(
        ProviderEntry(
            "LAN", "ollama", "regular:8b", {"endpoint": "http://192.168.77.77:11434"}
        )
    )
    try:
        yield registry, module, state, endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        set_default_registry(original)


@pytest.mark.asyncio
async def test_live_model_authority_scan_route_and_endpoint_window(serving):
    registry, module, state, endpoint = serving
    catalog = registry.build_catalog(registry.get_entry("Local"))
    await catalog.list_models()
    assert served_on_this_machine("Local", "regular:8b")
    assert not served_on_this_machine("Local", "forwarded:8b")
    assert not served_on_this_machine("Local", "future:cloud")
    assert not served_on_this_machine("LAN", "regular:8b")
    from types import SimpleNamespace

    from gideon.automation.loop.kinds.sdlc import _pool_cap

    assert _pool_cap(SimpleNamespace(provider="", model="Local:regular:8b")) == 1
    assert _pool_cap(SimpleNamespace(provider="", model="LAN:regular:8b")) == 4
    assert (
        _pool_cap(SimpleNamespace(provider="external-agent", model="Local:regular:8b"))
        == 4
    )
    assert is_local_ref("Local:regular:8b")
    assert not is_local_ref("Local:forwarded:8b")
    provider = registry.build("Local")
    guard = wrap_model_call_guard(
        provider,
        use_case="chat",
        provider_name="Local",
        model="regular:8b",
        scan_mode="block",
    )
    assert guard._scan_mode == "warn"
    guard._refresh_scan_mode("forwarded:8b")
    assert guard._scan_mode == "block"
    guard._refresh_scan_mode("regular:8b")
    assert guard._scan_mode == "warn"
    state["models"][0]["remote_host"] = "https://hosted.invalid"
    await catalog.list_models()
    guard._refresh_scan_mode("regular:8b")
    assert guard._scan_mode == "block"
    register_served_context_window("regular:8b", 8192, endpoint=endpoint)
    assert model_context_window("Local:regular:8b") == 8192
    assert (
        model_context_window("regular:8b", endpoint="http://127.0.0.1:1", local=True)
        == 4096
    )


@pytest.mark.asyncio
async def test_remeasure_singleflight_and_breaker_transition(serving, monkeypatch):
    calls = []
    release = asyncio.Event()

    class Catalog:
        async def test_connection(self):
            calls.append(1)
            await release.wait()
            return ConnectionResult(ok=True, model_count=1)

    board = ConnectionBoard()
    board.record("Local", "fp", Connection(CONNECTED))
    board.remeasure("Local", "fp", Catalog)
    board.remeasure("Local", "fp", Catalog)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    await asyncio.gather(*board._checks.values())
    refresh = []
    monkeypatch.setattr(
        "gideon.extensions.providers.connection.recheck", refresh.append
    )
    registry = serving[0]
    guard = wrap_model_call_guard(
        registry.build("Local"),
        use_case="chat",
        provider_name="Local",
        model="regular:8b",
        breaker=CircuitBreaker("Local", threshold=2),
    )
    guard._record_failure()
    guard._record_failure()
    guard._record_failure()
    assert refresh == ["Local"]
    guard._record_success()
    guard._record_success()
    assert refresh == ["Local", "Local"]


@pytest.mark.asyncio
async def test_doctor_outage_is_not_missing_models(serving, monkeypatch):
    from gideon.extensions.providers import use_cases
    from gideon.integrations.local_models import registry as local
    from gideon.operations.resilience.doctor import (
        _probe_local_models,
        local_binding_state,
    )

    registry, module, state, endpoint = serving
    provider = module.OllamaProvider({"endpoint": endpoint})
    monkeypatch.setattr(local, "registered", lambda: [("Local", provider)])
    monkeypatch.setattr(
        use_cases,
        "load_active_models",
        lambda: {"chat": ["Local:missing", "Local:regular:8b"]},
    )
    state["fail"] = True
    result = await local_binding_state()
    assert result["phantom_bindings"] == []
    assert result["bound_unavailable"] == ["Local"]
    assert not (await _probe_local_models(None)).ok
    state["fail"] = False
    result = await local_binding_state()
    assert result["phantom_bindings"] == ["Local:missing"]

    async def failed_list():
        raise RuntimeError("temporary list outage")

    monkeypatch.setattr(provider, "list_models", failed_list)
    result = await local_binding_state()
    assert result["phantom_bindings"] == []
    assert "Local" in result["catalog_failures"]


def test_explicit_prune_preserves_outage_models_and_chain_order(serving, monkeypatch):
    from gideon.extensions.providers import use_cases
    from gideon.integrations.local_models import registry as local
    from gideon.operations.resilience.fixes import _active_models_prune_apply

    _, module, state, endpoint = serving
    provider = module.OllamaProvider({"endpoint": endpoint})
    monkeypatch.setattr(local, "registered", lambda: [("Local", provider)])
    monkeypatch.setattr(use_cases, "_known_provider_names", lambda: {"Local"})
    use_cases.save_active_models(
        {
            "chat": [
                "Local:regular:8b",
                "Removed:model",
                "Local:missing",
                "Local:forwarded:8b",
            ]
        }
    )
    state["fail"] = True
    _active_models_prune_apply()
    assert use_cases.load_active_models()["chat"] == [
        "Local:regular:8b",
        "Local:missing",
        "Local:forwarded:8b",
    ]
    state["fail"] = False
    _active_models_prune_apply()
    assert use_cases.load_active_models()["chat"] == [
        "Local:regular:8b",
        "Local:forwarded:8b",
    ]
