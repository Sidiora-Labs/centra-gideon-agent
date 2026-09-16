"""Chat Completions and embeddings with deferred SDK loading."""

from __future__ import annotations

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
from gideon.integrations.llm.prompt_cache import PromptCache
from gideon.integrations.llm.protocol_turn import (
    ConversationProtocol,
    ToolFragment,
    TurnUsage,
    wire_value,
)
from gideon.integrations.llm.registry import CredentialMissing
from gideon.integrations.llm.stream_tags import KIND_OUTSIDE, make_think_splitter
from gideon.integrations.model_windows import model_context_window as _model_window

logger = logging.getLogger(__name__)
_MAX_HISTORY = 50
_DEFAULT_CONTEXT_WINDOW = 128_000


class _ChatDecoder:
    def __init__(self) -> None:
        self.usage = TurnUsage()
        self.answer: list[str] = []
        self._splitter = make_think_splitter()
        self._calls: dict[str, ToolFragment] = {}
        self._indices: dict[int, str] = {}
        self._last_identifier = ""

    def _segments(self, segments) -> list[LLMEvent]:
        events = []
        for segment in segments:
            kind = (
                EVENT_TEXT_CHUNK
                if segment.kind == KIND_OUTSIDE
                else EVENT_THINKING_CHUNK
            )
            if kind == EVENT_TEXT_CHUNK:
                self.answer.append(segment.text)
            events.append(LLMEvent(kind=kind, text=segment.text))
        return events

    def _append_call(self, delta: Any) -> None:
        index = wire_value(delta, "index")
        identifier = wire_value(delta, "id")
        if not identifier:
            identifier = (
                self._indices.get(index) if index is not None else self._last_identifier
            )
        if not identifier:
            identifier = f"call-{index if index is not None else 0}"
        if index is not None:
            self._indices[index] = identifier
        self._last_identifier = identifier
        assembly = self._calls.setdefault(identifier, ToolFragment(identifier))
        function = wire_value(delta, "function")
        assembly.name += wire_value(function, "name", "") or ""
        assembly.arguments += wire_value(function, "arguments", "") or ""
        extra = wire_value(delta, "extra_content")
        if isinstance(extra, dict) and extra:
            assembly.metadata["extra_content"] = extra

    def _flush_calls(self, reason: str = "") -> list[LLMEvent]:
        return [
            event
            for assembly in self._calls.values()
            for event in assembly.emit(reason)
        ]

    def feed(self, chunk: Any) -> list[LLMEvent]:
        events = []
        choices = wire_value(chunk, "choices") or []
        if choices:
            choice = choices[0]
            delta = wire_value(choice, "delta")
            text = wire_value(delta, "content") or ""
            if text:
                events.extend(self._segments(self._splitter.feed(text)))
            for fragment in wire_value(delta, "tool_calls") or []:
                self._append_call(fragment)
            reason = wire_value(choice, "finish_reason")
            if reason in {"tool_calls", "stop", "length"}:
                events.extend(self._flush_calls(str(reason)))
        usage = wire_value(chunk, "usage")
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            measured = wire_value(usage, source)
            if measured:
                setattr(self.usage, target, measured)
        return events

    def finish(self) -> list[LLMEvent]:
        return self._segments(self._splitter.flush()) + self._flush_calls()


class OpenAIProvider(ConversationProtocol):
    supports_tools: bool = True
    prompt_cache: PromptCache = PromptCache.AUTOMATIC

    def __init__(
        self,
        *,
        model: str,
        credential: Credential | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, object] | None = None,
    ) -> None:
        sdk = require_sdk(
            "openai", "openai", feature="the OpenAI chat/embedding provider"
        )
        if credential is None or not credential.secret:
            raise CredentialMissing(
                "OpenAIProvider requires a credential with a populated secret"
            )
        self._openai_module = sdk
        self._model, self._base_url, self._max_tokens = model, base_url, max_tokens
        self._extra_options = dict(extra_options or {})
        self._embedding_model = str(self._extra_options.pop("embedding_model", ""))
        self._client = sdk.AsyncOpenAI(api_key=credential.secret, base_url=base_url)
        self._initialize_conversation()

    async def start(self) -> None:
        if self._model:
            return
        try:
            from gideon.integrations.llm.catalog import (
                infer_capabilities,
                openai_compatible_list_models,
            )

            models = await openai_compatible_list_models(
                self._base_url or "", getattr(self._client, "api_key", "") or ""
            )
            suitable = [
                item.id
                for item in models
                if "chat" in (item.capabilities or infer_capabilities(item.id))
            ]
            if models:
                self._model = suitable[0] if suitable else models[0].id
        except Exception:
            logger.debug("Default chat model discovery failed", exc_info=True)

    def _image_content(self, data_url: str) -> dict:
        return {"type": "image_url", "image_url": {"url": data_url}}

    def _request(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
        reasoning_effort: str = "",
    ) -> dict[str, Any]:
        request = {
            "model": model,
            "messages": self._with_pending_image(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            request["tools"] = tools
        if self._max_tokens is not None:
            request["max_tokens"] = self._max_tokens
        if reasoning_effort and any(
            marker in model.lower() for marker in ("o1", "o3", "o4", "gpt-5")
        ):
            request["reasoning_effort"] = {"max": "high"}.get(
                reasoning_effort, reasoning_effort
            )
        request.update(self._extra_options)
        return request

    async def _open_response(self, request: dict[str, Any]):
        try:
            return await self._client.chat.completions.create(**request)
        except self._openai_module.BadRequestError as error:
            supported_retry = "stream_options" in request and any(
                term in str(error) for term in ("stream_options", "include_usage")
            )
            if not supported_retry:
                raise
            retry = {
                key: value for key, value in request.items() if key != "stream_options"
            }
            return await self._client.chat.completions.create(**retry)

    async def _run_turn(
        self, request: dict[str, Any], model: str, *, remember: bool
    ) -> AsyncIterator[LLMEvent]:
        decoder = _ChatDecoder()
        response = await self._open_response(request)
        try:
            async for frame in response:
                for event in decoder.feed(frame):
                    yield event
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.debug("Could not close model response stream", exc_info=True)
        for event in decoder.finish():
            yield event
        context = None
        if decoder.usage.input_tokens > 0:
            context = (
                decoder.usage.input_tokens
                / _model_window(model, _DEFAULT_CONTEXT_WINDOW)
            ) * 100
        context = self._record_completion(decoder.answer, context, remember=remember)
        yield decoder.usage.terminal(context)

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        messages = self._begin_message(message, _MAX_HISTORY)
        request = self._request(messages, model=self._model)
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
            messages, model=selected, tools=tools, reasoning_effort=reasoning_effort
        )
        async for event in self._run_turn(request, selected, remember=False):
            yield event

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        response = await self._client.embeddings.create(
            model=self._embedding_model, input=inputs
        )
        return [
            list(wire_value(item, "embedding", []))
            for item in wire_value(response, "data", []) or []
        ]
