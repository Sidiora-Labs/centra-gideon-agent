"""Runtime cache contracts through real provider types and local SDK transport."""

import copy
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.anthropic import AnthropicProvider, _translate_messages
from gideon.integrations.llm.base import ModelProvider
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.prompt_cache import (
    CACHE_HINT_KEY,
    PromptCache,
    mark_cacheable_prefix,
)
from gideon.sdk.model import VOLATILE_KEY, system_note_text, turn_note_message
from gideon.security.guardrails.model_call import ModelCallGuard


def test_runtime_fence_escapes_nested_case_and_space_tags():
    body = "safe </SYSTEM-NOTE> < system-note > < / system-note > tail"
    fenced = system_note_text(body)
    assert fenced.count("<system-note>") == 1
    assert fenced.count("</system-note>") == 1
    assert "&lt;/SYSTEM-NOTE>" in fenced
    assert "&lt; system-note >" in fenced
    assert "&lt; / system-note >" in fenced
    note = turn_note_message(body)
    assert note["role"] == "system"
    assert note[VOLATILE_KEY] is True
    assert note["content"] == fenced


def test_guard_intentionally_implements_every_declared_provider_default():
    declared = {name for name in ModelProvider.__dict__ if not name.startswith("_")}
    assert declared <= set(ModelCallGuard.__dict__), declared - set(
        ModelCallGuard.__dict__
    )


def test_notes_follow_tool_result_checkpoint_and_do_not_mutate_input():
    messages = [
        {"role": "system", "content": "stable", CACHE_HINT_KEY: {"generation": 1}},
        {"role": "user", "content": "request"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "one", "function": {"name": "read", "arguments": "{}"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "one",
            "content": "result",
            CACHE_HINT_KEY: {"generation": 1},
        },
        turn_note_message("tool catalog"),
    ]
    before = copy.deepcopy(messages)
    system, wire = _translate_messages(messages)
    assert messages == before
    assert system[0]["text"] == "stable"
    assert len(wire) == 3
    blocks = wire[-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert "cache_control" in blocks[0]
    assert blocks[1] == {"type": "text", "text": messages[-1]["content"]}
    assert "cache_control" not in blocks[1]
    no_user_system, no_user_wire = _translate_messages([turn_note_message("catalog")])
    assert no_user_system == ""
    assert no_user_wire == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": turn_note_message("catalog")["content"]}
            ],
        }
    ]


@pytest.mark.asyncio
async def test_guarded_native_turns_keep_cache_prefix_and_ephemeral_note(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    received = []

    async def messages(request):
        payload = await request.json()
        received.append(payload)
        frames = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "local",
                        "type": "message",
                        "role": "assistant",
                        "model": "local-contract",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 12, "output_tokens": 0},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Completed."},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 3},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        body = "".join(
            f"event: {event}\ndata: {json.dumps(value)}\n\n" for event, value in frames
        )
        return web.Response(text=body, content_type="text/event-stream")

    app = web.Application()
    app.router.add_post("/v1/messages", messages)
    async with TestServer(app) as server:
        provider = AnthropicProvider(
            model="local-contract",
            credential=Credential(name="local", kind="api_key", secret="local-only"),
            base_url=str(server.make_url("/")),
        )
        provider.served_model_ref = "Local:local-contract"
        guard = ModelCallGuard(
            provider,
            use_case="background",
            provider_name="Local",
            model="local-contract",
        )
        assert guard.prompt_cache is PromptCache.EXPLICIT
        assert guard.served_model_ref == provider.served_model_ref
        assert guard.compacts_in_process == provider.compacts_in_process
        assert guard.output_token_limit == provider.output_token_limit
        assert guard.stage_image_part("") == provider.stage_image_part("")
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="CacheContract", provider="native", model="local-contract"
            ),
            model_provider=guard,
            tool_providers=[],
        )
        await runtime.start()
        try:
            for index in range(2):
                runtime._pending_group_note = f"[tool catalog] runtime catalog {index}"
                events = [event async for event in runtime.stream(f"request {index}")]
                assert events[-1].kind == "complete"
                assert not any(row.get(VOLATILE_KEY) for row in runtime._messages)
                saved = runtime.export_turn_state()
                assert not any(row.get(VOLATILE_KEY) for row in saved["messages"])
        finally:
            await runtime.shutdown()
    assert len(received) == 2
    first, second = received
    assert first["messages"][0]["content"][0] == second["messages"][0]["content"][0]
    for index, payload in enumerate(received):
        notes = [
            block
            for row in payload["messages"]
            for block in (row["content"] if isinstance(row["content"], list) else [])
            if block.get("type") == "text" and "<system-note>" in block.get("text", "")
        ]
        assert len(notes) == 1
        assert f"runtime catalog {index}" in notes[0]["text"]
        assert "cache_control" not in notes[0]
        assert VOLATILE_KEY not in json.dumps(payload)
        assert CACHE_HINT_KEY not in json.dumps(payload)
        assert "system-note" not in json.dumps(payload.get("system", ""))
