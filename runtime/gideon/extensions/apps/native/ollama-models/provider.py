"""Gideon local inference and model management for https://ollama.com models."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from gideon.integrations.llm.events import ContextUsage

from gideon.sdk.embedding import EmbeddingProvider
from gideon.sdk.local_model import LocalModel, LocalModelProvider
from gideon.sdk.model import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LOCAL_SERVED_CONTEXT_WINDOW,
    Capability,
    ConnectionResult,
    LLMEvent,
    ModelCatalog,
    ModelInfo,
    ModelProvider,
    ProviderCapability,
    ProviderEntry,
    ProviderResolutionError,
    StructuredOutput,
    declared_context_window,
    get_default_registry,
    infer_capabilities,
    model_context_window,
)


class OllamaProvider(ModelProvider, EmbeddingProvider, LocalModelProvider):
    supports_tools = True
    is_local = True
    name = "ollama-models"
    display_name = "Ollama"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.endpoint = str(
            cfg.get("endpoint") or cfg.get("base_url") or "http://localhost:11434"
        ).rstrip("/")
        self.model = str(cfg.get("model") or "")
        self.embedding_model = str(cfg.get("embedding_model") or "")
        self.options = dict(cfg.get("options") or {})
        nested_window = self.options.pop("context_window", None)
        self._declared_context_window = declared_context_window(
            cfg.get("context_window", nested_window)
        )
        self.context_window = (
            self._declared_context_window or LOCAL_SERVED_CONTEXT_WINDOW
        )
        self.timeout_secs = float(cfg.get("timeout_secs") or 120)
        if self.timeout_secs <= 0:
            raise ValueError("timeout_secs must be positive")
        self._last_context_pct: float | None = None
        self._reported_context_window: int | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint, timeout=self.timeout_secs, trust_env=False
            )

    async def _served_context_window(self, model: str) -> int:
        if self._declared_context_window is not None:
            self._reported_context_window = self._declared_context_window
            return self._declared_context_window
        self._reported_context_window = None
        try:
            response = await self._request("GET", "/api/ps")
            for row in response.get("models", []):
                name = str(row.get("name") or row.get("model") or "")
                if name == model or name.removesuffix(":latest") == model.removesuffix(
                    ":latest"
                ):
                    served = declared_context_window(row.get("context_length"))
                    if served is not None:
                        self._reported_context_window = served
                        return served
        except (httpx.HTTPError, ValueError, RuntimeError):
            pass
        return LOCAL_SERVED_CONTEXT_WINDOW

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.endpoint, timeout=self.timeout_secs, trust_env=False
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Ollama returned a non-object response")
            if result.get("error"):
                raise RuntimeError(str(result["error"]))
            return result

    async def is_available(self) -> bool:
        try:
            await self._request("GET", "/api/tags")
            return True
        except (httpx.HTTPError, ValueError, RuntimeError):
            return False

    async def list_models(self) -> list[LocalModel]:
        result = await self._request("GET", "/api/tags")
        return [
            LocalModel(
                name=row["name"],
                size_mb=float(row.get("size", 0)) / 1_000_000,
                downloaded=True,
                capabilities=infer_capabilities(row["name"]),
                source=self.endpoint,
                runtime="ollama",
            )
            for row in result.get("models", [])
        ]

    async def download_model(self, model_name: str) -> bool:
        async with httpx.AsyncClient(
            base_url=self.endpoint, timeout=600, trust_env=False
        ) as client:
            async with client.stream(
                "POST", "/api/pull", json={"name": model_name, "stream": True}
            ) as response:
                response.raise_for_status()
                succeeded = False
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("error"):
                        raise RuntimeError(str(row["error"]))
                    succeeded = row.get("status") == "success"
                return succeeded

    async def delete_model(self, model_name: str) -> bool:
        async with httpx.AsyncClient(
            base_url=self.endpoint, timeout=30, trust_env=False
        ) as client:
            response = await client.request(
                "DELETE", "/api/delete", json={"name": model_name}
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True

    async def embed(self, text: str, model: str = "") -> list[float] | None:
        rows = await self.embed_batch([text], model)
        return rows[0] if rows else None

    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float]]:
        if not texts:
            return []
        selected = model or self.embedding_model
        if not selected:
            raise ValueError("Choose an Ollama embedding model in Apps settings")
        response = await self._request(
            "POST", "/api/embed", json={"model": selected, "input": texts}
        )
        rows = response.get("embeddings")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise ValueError("Ollama returned the wrong number of embeddings")
        if any(not isinstance(row, list) or not row for row in rows):
            raise ValueError("Ollama returned an empty or invalid embedding")
        return [[float(value) for value in row] for row in rows]

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
        response_format: dict | None = None,
    ) -> AsyncIterator[LLMEvent]:
        selected = model or self.model
        if not selected:
            raise ValueError("Choose an Ollama chat model in Apps settings")
        payload: dict[str, Any] = {
            "model": selected,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if self.options:
            payload["options"] = self.options
        if response_format:
            payload["format"] = response_format.get("json_schema", {}).get(
                "schema", "json"
            )
        await self.start()
        assert self._client is not None
        async with self._client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            call_index = 0
            async for line in response.aiter_lines():
                if not line:
                    continue
                row = json.loads(line)
                if row.get("error"):
                    raise RuntimeError(str(row["error"]))
                message = row.get("message") or {}
                for field, kind in (
                    ("thinking", EVENT_THINKING_CHUNK),
                    ("content", EVENT_TEXT_CHUNK),
                ):
                    if message.get(field):
                        yield LLMEvent(kind=kind, text=message[field])
                for call in message.get("tool_calls") or []:
                    function = call["function"]
                    arguments = function.get("arguments", {})
                    yield LLMEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id=str(call.get("id") or f"ollama-{call_index}"),
                        title=function["name"],
                        tool_input=json.dumps(arguments),
                        tool_input_obj=arguments,
                    )
                    call_index += 1
                if row.get("done"):
                    self.context_window = await self._served_context_window(selected)
                    prompt_tokens = int(row.get("prompt_eval_count", 0))
                    self._last_context_pct = (
                        100.0
                        * prompt_tokens
                        / model_context_window(
                            selected,
                            override=self.context_window,
                            local=True,
                        )
                        if prompt_tokens > 0
                        else None
                    )
                    reported_prompt = row.get("prompt_eval_count")
                    measured_prompt = (
                        reported_prompt
                        if type(reported_prompt) is int and reported_prompt >= 0
                        else None
                    )
                    yield LLMEvent(
                        kind=EVENT_COMPLETE,
                        context_usage_pct=self._last_context_pct,
                        input_tokens=int(row.get("prompt_eval_count", 0)),
                        output_tokens=int(row.get("eval_count", 0)),
                        context_usage=(
                            ContextUsage(
                                input_tokens=measured_prompt,
                                total_input_tokens=measured_prompt,
                                context_window_tokens=self._reported_context_window,
                            )
                            if measured_prompt is not None
                            or self._reported_context_window is not None
                            else None
                        ),
                        stop_reason=str(row.get("done_reason") or "stop"),
                    )
                    return
            raise RuntimeError("Ollama chat stream ended before completion")

    async def approve_tool(self, request_id: str | int) -> None:
        raise RuntimeError("Tool approval is owned by the Gideon agent runtime")

    async def reject_tool(self, request_id: str | int) -> None:
        raise RuntimeError("Tool approval is owned by the Gideon agent runtime")

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct


class OllamaCatalog(ModelCatalog):
    def __init__(
        self, options: dict[str, Any] | None = None, *, model: str = ""
    ) -> None:
        self.provider = OllamaProvider(options)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=row.name,
                name=row.name,
                capabilities=row.capabilities,
                size=int(row.size_mb * 1_000_000),
                downloaded=True,
            )
            for row in await self.provider.list_models()
        ]

    async def test_connection(self) -> ConnectionResult:
        try:
            rows = await self.list_models()
            return ConnectionResult(ok=True, model_count=len(rows))
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            return ConnectionResult(ok=False, detail=str(error))


def create_provider(config: dict[str, Any] | None = None) -> OllamaProvider:
    _register()
    return OllamaProvider(config)


def _factory(*, entry: ProviderEntry, **kwargs: Any) -> ModelProvider:
    return OllamaProvider({**entry.options, "model": entry.model})


def _register() -> None:
    registry = get_default_registry()
    try:
        registry.capability_of("ollama")
    except ProviderResolutionError:
        registry.register_type(
            ProviderCapability(
                type="ollama",
                capabilities=frozenset(
                    {
                        Capability.CHAT,
                        Capability.EMBEDDING,
                        Capability.STREAMING,
                        Capability.CODE_TOOLS,
                    }
                ),
                supports_streaming=True,
                supports_tools=True,
                supports_embeddings=True,
                supports_vision=False,
                max_context_tokens=0,
                structured_output=StructuredOutput.JSON_SCHEMA,
            ),
            _factory,
        )
    registry.register_catalog("ollama", OllamaCatalog)


_register()
