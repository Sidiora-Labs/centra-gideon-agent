"""Messages protocol translation and response assembly with a lazy SDK client."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from gideon.core._sdk_deps import require_sdk
from gideon.integrations.llm.base import (
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    LLMEvent,
)
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.prompt_cache import CACHE_HINT_KEY, PromptCache
from gideon.integrations.llm.protocol_turn import (
    ConversationProtocol,
    ToolFragment,
    TurnUsage,
    wire_value,
)
from gideon.integrations.llm.registry import CredentialMissing
from gideon.integrations.model_windows import declared_context_window, is_local_endpoint
from gideon.integrations.model_windows import model_context_window as _model_window

logger = logging.getLogger(__name__)
_MAX_HISTORY = 50
_DEFAULT_CONTEXT_WINDOW = 200_000
_THINKING_BUDGETS: dict[str, int] = {
    "low": 4_096,
    "medium": 10_240,
    "high": 24_576,
    "max": 63_999,
}
_VOLATILE_MESSAGE_KEY = "_volatile"
_CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _read_cache_usage(usage: object) -> tuple[int, int]:
    values = (
        wire_value(usage, name)
        for name in ("cache_creation_input_tokens", "cache_read_input_tokens")
    )
    creation, read = (value if isinstance(value, int) else 0 for value in values)
    return creation, read


def _translate_tools(tools: list[dict], *, cache_enabled: bool = False) -> list[dict]:
    translated = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            if "name" in tool:
                translated.append(tool)
            continue
        translated.append(
            dict(
                name=function.get("name", ""),
                description=function.get("description") or "",
                input_schema=function.get("parameters")
                or {"type": "object", "properties": {}},
            )
        )
    if cache_enabled and translated:
        translated[-1] = _mark_block(dict(translated[-1]))
    return translated


def _mark_block(block: dict) -> dict:
    block.update(cache_control=dict(_CACHE_CONTROL_EPHEMERAL))
    return block


def _image_block(data_url: str) -> dict | None:
    scheme, separator, remainder = data_url.partition(":")
    metadata, comma, payload = remainder.partition(",")
    if (
        scheme != "data"
        or not separator
        or not comma
        or not payload
        or not metadata.endswith(";base64")
    ):
        return None
    media = metadata.removesuffix(";base64").strip().lower()
    if not media:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": payload},
    }


def _hinted_content(content: Any) -> Any:
    if not content:
        return content
    if not isinstance(content, list):
        return [_mark_block({"type": "text", "text": str(content)})]
    blocks = [*content]
    if isinstance(blocks[-1], dict):
        blocks[-1] = _mark_block(dict(blocks[-1]))
    return blocks


def _tool_use_block(call: dict) -> dict:
    function = call.get("function", {})
    arguments = function.get("arguments") or ""
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (ValueError, TypeError):
        parsed = {}
    return {
        "type": "tool_use",
        "id": str(call.get("id") or ""),
        "name": function.get("name", ""),
        "input": parsed if isinstance(parsed, dict) else {},
    }


class _MessageEnvelope:
    def __init__(self) -> None:
        self.system_parts: list[str] = []
        self.system_hinted = False
        self.messages: list[dict] = []
        self.tail: list[dict] = []

    def _system(self, message: dict) -> None:
        content = message.get("content")
        if not content:
            return
        if message.get(_VOLATILE_MESSAGE_KEY):
            self.tail.append({"role": "user", "content": str(content)})
        else:
            self.system_parts.append(str(content))
            self.system_hinted |= CACHE_HINT_KEY in message

    def _tool(self, message: dict) -> None:
        content = message.get("content")
        block = {
            "type": "tool_result",
            "tool_use_id": str(message.get("tool_call_id") or ""),
            "content": "" if content is None else str(content),
        }
        if CACHE_HINT_KEY in message:
            _mark_block(block)
        previous = self.messages[-1] if self.messages else {}
        blocks = previous.get("content")
        can_extend = (
            previous.get("role") == "user"
            and isinstance(blocks, list)
            and all(
                isinstance(item, dict) and item.get("type") == "tool_result"
                for item in blocks
            )
        )
        if can_extend and isinstance(blocks, list):
            self.messages[-1] = dict(previous, content=[*blocks, block])
        else:
            self.messages.append({"role": "user", "content": [block]})

    def _ordinary(self, message: dict) -> None:
        role, content = message.get("role"), message.get("content")
        if role == "assistant" and message.get("tool_calls"):
            blocks = [{"type": "text", "text": str(content)}] if content else []
            blocks.extend(_tool_use_block(call) for call in message["tool_calls"])
            content = blocks
        if CACHE_HINT_KEY in message:
            content = _hinted_content(content)
        self.messages.append({"role": role, "content": content})

    def add(self, message: dict) -> None:
        dispatch = {"system": self._system, "tool": self._tool}
        role = message.get("role")
        handler = dispatch.get(role) if isinstance(role, str) else None
        (handler or self._ordinary)(message)

    def result(self) -> tuple[str | list[dict], list[dict]]:
        system = "\n\n".join(self.system_parts)
        return (_hinted_content(system) if self.system_hinted else system), [
            *self.messages,
            *self.tail,
        ]


def _translate_messages(messages: list[dict]) -> tuple[str | list[dict], list[dict]]:
    envelope = _MessageEnvelope()
    for message in messages:
        envelope.add(message)
    return envelope.result()


class _MessagesDecoder:
    def __init__(self) -> None:
        self.usage = TurnUsage()
        self.answer: list[str] = []
        self.stop_reason = ""
        self._blocks: dict[int, ToolFragment] = {}

    def _message_start(self, event: Any) -> list[LLMEvent]:
        usage = wire_value(wire_value(event, "message"), "usage")
        for name in ("input_tokens", "output_tokens"):
            value = wire_value(usage, name)
            if value is not None:
                setattr(self.usage, name, value)
        if usage is not None:
            self.usage.cache_creation_tokens, self.usage.cache_read_tokens = (
                _read_cache_usage(usage)
            )
        return []

    def _block_start(self, event: Any) -> list[LLMEvent]:
        block = wire_value(event, "content_block")
        if wire_value(block, "type") == "tool_use":
            index = wire_value(event, "index", 0) or 0
            previous = self._blocks.get(index)
            self._blocks[index] = ToolFragment(
                str(wire_value(block, "id", "") or ""),
                name=str(wire_value(block, "name", "") or ""),
                delivered=bool(previous and previous.delivered),
            )
        return []

    def _block_delta(self, event: Any) -> list[LLMEvent]:
        delta = wire_value(event, "delta")
        category = wire_value(delta, "type")
        if category == "input_json_delta":
            assembly = self._blocks.get(wire_value(event, "index", 0) or 0)
            if assembly is not None:
                assembly.arguments += wire_value(delta, "partial_json", "") or ""
            return []
        field_and_kind = {
            "text_delta": ("text", EVENT_TEXT_CHUNK),
            "thinking_delta": ("thinking", EVENT_THINKING_CHUNK),
        }.get(category)
        if field_and_kind is None:
            return []
        field, kind = field_and_kind
        text = wire_value(delta, field, "") or ""
        if not text:
            return []
        if kind == EVENT_TEXT_CHUNK:
            self.answer.append(text)
        return [LLMEvent(kind=kind, text=text)]

    def _block_stop(self, event: Any) -> list[LLMEvent]:
        assembly = self._blocks.get(wire_value(event, "index", 0) or 0)
        return assembly.emit() if assembly else []

    def _message_delta(self, event: Any) -> list[LLMEvent]:
        reason = wire_value(wire_value(event, "delta"), "stop_reason") or wire_value(
            event, "stop_reason"
        )
        if reason:
            self.stop_reason = str(reason)
        output = wire_value(wire_value(event, "usage"), "output_tokens")
        if output is not None:
            self.usage.output_tokens = output
        return []

    def feed(self, event: Any) -> list[LLMEvent]:
        handlers = {
            "message_start": self._message_start,
            "content_block_start": self._block_start,
            "content_block_delta": self._block_delta,
            "content_block_stop": self._block_stop,
            "message_delta": self._message_delta,
        }
        handler = handlers.get(wire_value(event, "type"))
        return handler(event) if handler else []

    def finish(self) -> list[LLMEvent]:
        return [
            event
            for assembly in self._blocks.values()
            for event in assembly.emit(self.stop_reason)
        ]


class AnthropicProvider(ConversationProtocol):
    supports_tools: bool = True
    prompt_cache: PromptCache = PromptCache.EXPLICIT

    def __init__(
        self,
        *,
        model: str,
        credential: Credential | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        extra_options: dict[str, object] | None = None,
    ) -> None:
        sdk = require_sdk(
            "anthropic", "anthropic", feature="the Anthropic chat provider"
        )
        if credential is None or not credential.secret:
            raise CredentialMissing(
                "AnthropicProvider requires a credential with a populated secret"
            )
        self._anthropic_module = sdk
        self._model, self._base_url, self._max_tokens = model, base_url, max_tokens
        self._extra_options = dict(extra_options or {})
        self.context_window = declared_context_window(
            self._extra_options.pop("context_window", None)
        )
        self.is_local = is_local_endpoint(base_url)
        connection = {"api_key": credential.secret}
        if base_url:
            connection["base_url"] = base_url
        self._client = sdk.AsyncAnthropic(**connection)
        self._initialize_conversation()

    async def start(self) -> None:
        logger.info(
            "Messages provider ready: model=%s base_url=%s",
            self._model,
            self._base_url or "<default>",
        )

    def _image_content(self, data_url: str) -> dict | None:
        return _image_block(data_url)

    def _request(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
        reasoning_effort: str = "",
        translate: bool,
    ) -> dict[str, Any]:
        prepared = self._with_pending_image(messages)
        cache_enabled = translate and any(CACHE_HINT_KEY in message for message in prepared)
        system, prepared = (
            _translate_messages(prepared) if translate else ("", prepared)
        )
        request = {"model": model, "messages": prepared, "max_tokens": self._max_tokens}
        if system:
            request["system"] = system
        if tools:
            request["tools"] = _translate_tools(tools, cache_enabled=cache_enabled)
        budget = _THINKING_BUDGETS.get(reasoning_effort or "")
        if budget:
            request["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(budget, max(1024, self._max_tokens - 1024)),
            }
        for name, value in self._extra_options.items():
            if translate and name == "temperature" and "thinking" in request:
                continue
            if name not in request:
                request[name] = value
        return request

    async def _run_turn(
        self, request: dict[str, Any], model: str, *, remember: bool
    ) -> AsyncIterator[LLMEvent]:
        decoder = _MessagesDecoder()
        async with self._client.messages.stream(**request) as response:
            async for frame in response:
                for event in decoder.feed(frame):
                    yield event
        for event in decoder.finish():
            yield event
        context = None
        if decoder.usage.input_tokens > 0:
            context = (
                decoder.usage.input_tokens
                / _model_window(
                    model,
                    _DEFAULT_CONTEXT_WINDOW,
                    override=self.context_window,
                    local=self.is_local,
                )
            ) * 100
        context = self._record_completion(decoder.answer, context, remember=remember)
        yield decoder.usage.terminal(context)

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        messages = self._begin_message(message, _MAX_HISTORY)
        request = self._request(messages, model=self._model, translate=False)
        async for event in self._run_turn(request, self._model, remember=True):
            yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        selected = model or self._model
        request = self._request(
            messages,
            model=selected,
            tools=tools,
            reasoning_effort=reasoning_effort,
            translate=True,
        )
        async for event in self._run_turn(request, selected, remember=False):
            yield event
