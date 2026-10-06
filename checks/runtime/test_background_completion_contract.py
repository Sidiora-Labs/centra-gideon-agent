"""Consumer contracts through the real SDK, provider registry and local HTTP wire."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from gideon.cognition.consolidation_cycle import consolidation_problem, formation_problem
from gideon.cognition.memory_formation import Candidate, Decision, Overlap, parse_decisions
from gideon.core.config.loader import AppConfig, config_path
from gideon.extensions.providers.use_cases import save_active_models
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.registry import ProviderRegistry, get_default_registry, set_default_registry
from gideon.integrations.llm_helpers import one_shot_completion, expecting, stream_and_collect
from gideon.security.guardrails.failure import OutputContractError, ModelCallTimeout
from gideon.security.guardrails.model_call import ModelCallGuard


@asynccontextmanager
async def configured_completion(responder, *, chain=("good",), configured_cap=None):
    """A real OpenAI-wire provider configured against a controlled local endpoint."""
    requests, tasks, writers = [], set(), set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            if header.startswith(b"GET "):
                body = json.dumps({"object": "list", "data": []}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                await writer.drain()
                return
            headers = dict(line.split(":", 1) for line in header.decode().split("\r\n")[1:] if ":" in line)
            size = int(next(value for key, value in headers.items() if key.lower() == "content-length"))
            request = json.loads(await reader.readexactly(size))
            requests.append(request)
            response = responder(request, len(requests))
            if asyncio.iscoroutine(response):
                response = await response
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            frames = [{"content": response}, {}]
            for index, delta in enumerate(frames):
                chunk = {"id": "local-contract", "object": "chat.completion.chunk", "created": 0,
                         "model": request["model"], "choices": [{"index": 0, "delta": delta,
                         "finish_reason": "stop" if index else None}]}
                writer.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            writer.write(b"data: [DONE]\n\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)
            writers.discard(writer)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1"
    old_registry = get_default_registry()
    registry = ProviderRegistry()
    # The factory builds the production SDK adapter; it supplies no response behavior.
    def build(*, entry, **options):
        return OpenAIProvider(model=entry.model, base_url=entry.options["base_url"],
                              credential=Credential("local-contract", "api_key", "local-transport-only"),
                              max_tokens=options.get("max_tokens"))
    registry.register_type(ProviderCapability(type="contract-sdk", capabilities=frozenset({Capability.CHAT}),
                           supports_streaming=True, supports_tools=True, supports_embeddings=False,
                           supports_vision=False, max_context_tokens=8192, hosts_model=True), build)
    set_default_registry(registry)
    AppConfig.load().save()
    data = json.loads(config_path().read_text())
    options = {"base_url": endpoint}
    if configured_cap is not None:
        options["max_tokens"] = configured_cap
    data["providers"] = [{"name": "ContractSDK", "type": "contract-sdk", "model": "good", "options": options}]
    config_path().write_text(json.dumps(data))
    from gideon.integrations.llm.registry import sync_entries_from_config
    sync_entries_from_config()
    save_active_models({"background": ["ContractSDK:" + model for model in chain]})
    try:
        yield requests, endpoint
    finally:
        set_default_registry(old_registry)
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def isolated_completion(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    AppConfig.load().save()
    return home


@pytest.mark.asyncio
async def test_real_sdk_contract_miss_advances_finite_chain(isolated_completion):
    async with configured_completion(lambda request, n: '{"wrong":1}' if request["model"] == "bad" else '{"history_entry":"Accepted"}', chain=("bad", "good")) as (requests, _):
        text = await one_shot_completion("Summarize", output_type=dict, validate=consolidation_problem, max_output_tokens=80)
        assert json.loads(text)["history_entry"] == "Accepted"
        assert [request["model"] for request in requests] == ["bad", "good"]
        assert all(request.get("max_tokens") == 80 for request in requests)
        assert all(not request.get("tools") for request in requests)


@pytest.mark.asyncio
async def test_single_model_corrects_once_and_restores_expected_context(isolated_completion):
    async with configured_completion(lambda request, n: '{"semantic":"malformed"}' if n == 1 else '{"semantic":[]}') as (requests, _):
        with expecting(consolidation_problem):
            text = await one_shot_completion("Extract")
        assert json.loads(text) == {"semantic": []}
        assert len(requests) == 2
        assert "Expected:" in requests[1]["messages"][-1]["content"]
        # The consumer-specific expectation ended with the block.
        assert await one_shot_completion("Unstructured") == '{"semantic":[]}'
        assert len(requests) == 3


@pytest.mark.asyncio
async def test_exhausted_shape_and_empty_output_never_succeed(isolated_completion):
    async with configured_completion(lambda request, n: "", chain=("bad", "good")) as (requests, _):
        with pytest.raises(OutputContractError):
            await one_shot_completion("Extract", validate=consolidation_problem)
        assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,requested,expected", [(12, 80, 12), (1000, 80, 80)])
async def test_configured_finite_lower_output_limit_wins(isolated_completion, configured, requested, expected):
    async with configured_completion(lambda request, n: "answer", configured_cap=configured) as (requests, _):
        await one_shot_completion("Answer", max_output_tokens=requested)
        assert requests[0]["max_tokens"] == expected


@pytest.mark.asyncio
async def test_real_sdk_background_guard_hard_deadline(isolated_completion):
    async def slow(request, number):
        await asyncio.sleep(0.2)
        return "late"
    async with configured_completion(slow) as (_, endpoint):
        provider = OpenAIProvider(model="good", credential=Credential("local", "api_key", "wire"), base_url=endpoint)
        guard = ModelCallGuard(provider, use_case="background", provider_name="contract", model="good", timeout_secs=0.03)
        try:
            with pytest.raises(ModelCallTimeout):
                await stream_and_collect(guard, "wait")
        finally:
            await guard.shutdown()


def test_background_timeout_zero_and_large_cannot_disable_ceiling(isolated_completion, monkeypatch):
    script = isolated_completion / "script.json"
    script.write_text(json.dumps({"version": 1, "turns": [{"text": "answer"}]}))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    from gideon.integrations.llm.scripted import ScriptedProvider
    for configured in (0, 900):
        guard = ModelCallGuard(ScriptedProvider(), use_case="background", provider_name="bounded", model="m", timeout_secs=configured)
        assert guard._timeout_secs == 300
    assert ModelCallGuard(ScriptedProvider(), use_case="chat", provider_name="interactive", model="m", timeout_secs=0)._timeout_secs == 0


def test_actual_formation_dataclasses_reject_missing_and_silent_add_targets():
    candidate = Candidate(0, "project.plan", "blue", 0.9)
    candidate.overlaps.append(Overlap("project.old", "red", why="same_key"))
    valid = json.dumps({"verdicts": [{"index": 0, "verdict": "UPDATE", "target": "project.old"}]})
    assert formation_problem(valid, [candidate]) == ""
    assert isinstance(parse_decisions(json.loads(valid), [candidate])[0], Decision)
    assert formation_problem('{"verdicts":[]}', [candidate])
    assert formation_problem('{"verdicts":[{"index":0,"verdict":"UPDATE","target":"unknown"}]}', [candidate])
    assert formation_problem('{"verdicts":[{"index":true,"verdict":"ADD"}]}', [candidate])


@pytest.mark.parametrize("text", ['{"unknown":1}', '{"history_entry":3}', '{"semantic":[{"key":"x"}]}', '{"episodic":[{"text":"x","importance":"bad"}]}'])
def test_consumer_rejects_valid_json_unusable_by_native_stores(text):
    assert consolidation_problem(text)
