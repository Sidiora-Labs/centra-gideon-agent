"""Gideon local inference and model management for https://ollama.com models."""

from __future__ import annotations

from gideon.core.turn_streams import closing_stream

import asyncio
import json
import logging
import math
import sys
from types import ModuleType
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
    FirstTokenTimeout,
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
    until_terminal,
)


logger = logging.getLogger(__name__)


async def _chat_rows(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("Ollama returned a non-object chat event")
        if row.get("error"):
            raise RuntimeError(str(row["error"]))
        yield row


_DEFAULT_ENDPOINT = "http://localhost:11434"
_CLOUD_TAG = "cloud"
_ANSWERED_FROM: dict[str, dict[str, str]] = {}

def _said() -> dict[str, dict[str, str]]:
    """The record of what the servers said, as the copy of this module the process holds now
    keeps it.

    A process can run this file more than once: loaded again from its files, it is a second copy,
    while core keeps the provider type, and so the pass-on probe, of the copy that registered it
    first, and a catalog of the second may be the one that lists next. Every copy keeps its record
    in the copy ``sys.modules`` holds, so the probe core asks reads what any of them heard."""
    current = sys.modules.setdefault("gideon_ollama_host_records", ModuleType("gideon_ollama_host_records"))
    record = getattr(current, "_ANSWERED_FROM", None)
    if not isinstance(record, dict):
        record = {}
        current._ANSWERED_FROM = record
    return record



def _server_key(endpoint: object) -> str:
    """The server an endpoint names, spelled one way; an instance that names none sends to the
    default, as its factory does."""
    return (str(endpoint or "").strip() or _DEFAULT_ENDPOINT).rstrip("/").lower()


def _model_key(model: object) -> str:
    """A model's name spelled one way: one named without a tag is its ``latest``, as the server
    lists it."""
    name = str(model or "").strip().lower()
    if name and ":" not in name.rsplit("/", 1)[-1]:
        name += ":latest"
    return name


def _cloud_tagged(model: object) -> bool:
    """Whether *model*'s tag is the one Ollama gives a model it answers from its cloud."""
    tag = _model_key(model).rsplit("/", 1)[-1].partition(":")[2]
    return tag == _CLOUD_TAG or tag.endswith(f"-{_CLOUD_TAG}")


def _heard_list(endpoint: object, models: object) -> None:
    """Keep where the server at *endpoint* answers each model of its list, in place of what it
    said before: a model it no longer lists, or now lists as its own, is answered elsewhere no
    more."""
    said: dict[str, str] = {}
    for m in models if isinstance(models, list) else []:
        if isinstance(m, dict) and m.get("name"):
            said[_model_key(m["name"])] = str(m.get("remote_host") or "")
    _said()[_server_key(endpoint)] = said


def _heard_one(endpoint: object, model: object, record: object) -> None:
    """Keep what one record from the server at *endpoint*, a model's record or a line of its
    answer, says about where it answers *model*: the host it names. A record that names none
    places nothing; the model list is what says a model is the server's own."""
    host = record.get("remote_host") if isinstance(record, dict) else None
    if host and model:
        _said().setdefault(_server_key(endpoint), {})[_model_key(model)] = str(host)


def passes_on(entry: ProviderEntry, model: str) -> bool:
    """Whether the Ollama server *entry* sends to answers *model* from another machine, which
    makes it no model of this machine for core (``ProviderRegistry.passes_on``): the server named
    a host for it the last time it listed, described or answered it, or the model carries the tag
    Ollama gives its cloud models, which says so before the server has been asked. Either says it;
    a model with no such tag that the server named no host for runs where the server is."""
    if _said().get(_server_key(entry.options.get("endpoint") or entry.options.get("base_url")), {}).get(_model_key(model)):
        return True
    return _cloud_tagged(model)


def _with_served(inferred: list[str], served: list[str] | None) -> list[str]:
    """What a model can be bound for: what Ollama reports it serves, over what its id suggests.

    Ollama's ``capabilities`` record says what the server will do with the model: ``completion``
    (it answers a chat), ``embedding`` (it embeds), and what a chat reads besides text
    (``vision`` sets ``image_modality``, ``audio`` sets ``audio_modality``). ``tools`` sets
    ``tools`` on a chat model: it calls the tools a chat turn offers it. A model that serves
    neither completion nor embedding (an image model) is offered for nothing here.

    The id still vetoes: a family no job binds (``infer_capabilities`` answers ``[]`` for a
    reranker or a safety classifier) is offered for nothing, though Ollama reports ``completion``
    for a guard model and ``embedding`` for a reranker it loads as an embedder. With no report
    (``None``, or an empty list from an older Ollama) the id's inference stands.
    """
    if not served or not inferred:
        return inferred
    if "completion" in served:
        tags = ["chat"]
        if "vision" in served:
            tags.append("image_modality")
        if "audio" in served:
            tags.append("audio_modality")
        if "tools" in served:
            tags.append("tools")
        return tags
    if "embedding" in served:
        return ["embedding"]
    return []


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
        ) or declared_context_window(self.options.get("num_ctx"))
        self.context_window = (
            self._declared_context_window or LOCAL_SERVED_CONTEXT_WINDOW
        )
        self.timeout_secs = float(cfg.get("timeout_secs") or 120)
        if not math.isfinite(self.timeout_secs) or self.timeout_secs <= 0:
            raise ValueError("timeout_secs must be positive")
        self._last_context_pct: float | None = None
        self._reported_context_window: int | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint, timeout=httpx.Timeout(self.timeout_secs, connect=10.0), trust_env=False
            )

    @property
    def first_token_timeout_secs(self) -> float:
        return self.timeout_secs

    @staticmethod
    def _loaded_context_window(response: dict[str, Any], model: str) -> int | None:
        if not model:
            return None
        rows = response.get("models")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("model") or "")
            if name.removesuffix(":latest") == model.removesuffix(":latest"):
                return declared_context_window(row.get("context_length"))
        return None

    async def served_context_window(self) -> int | None:
        return await self._served_context_window(self.model)

    async def _served_context_window(self, model: str) -> int | None:
        if self._declared_context_window is not None:
            return self._declared_context_window
        try:
            response = await self._request(
                "GET", "/api/ps", timeout=httpx.Timeout(10.0)
            )
            return self._loaded_context_window(response, model)
        except (httpx.HTTPError, ValueError, RuntimeError):
            return None

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.endpoint, timeout=httpx.Timeout(self.timeout_secs, connect=10.0), trust_env=False
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
        rows = result.get("models", [])
        self._listed_modified_at = {str(row.get("name") or row.get("model") or ""): row.get("modified_at", "") for row in rows if isinstance(row, dict)}
        _heard_list(self.endpoint, rows)
        semaphore = asyncio.Semaphore(4)
        async def describe(row):
            name = str(row.get("name") or "")
            if not name:
                return None
            inferred = infer_capabilities(name)
            served = row.get("capabilities")
            if not isinstance(served, list):
                try:
                    async with semaphore:
                        record = await self._request("POST", "/api/show", json={"model": name}, timeout=httpx.Timeout(10.0))
                    _heard_one(self.endpoint, name, record)
                    served = record.get("capabilities")
                except (httpx.HTTPError, ValueError, RuntimeError):
                    served = None
            entry = ProviderEntry(name=self.name, type="ollama", model=name, options={"endpoint":self.endpoint})
            from gideon.integrations.llm.registry import served_on_this_machine
            return LocalModel(name=name, size_mb=float(row.get("size",0))/1_000_000,
                downloaded=True, capabilities=_with_served(inferred, served), source=self.endpoint,
                runtime="ollama", runs_here=served_on_this_machine(entry,name))
        return [row for row in await asyncio.gather(*(describe(row) for row in rows if isinstance(row,dict))) if row is not None]

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
        async with closing_stream(self.complete([{"role": "user", "content": message}])) as _owned_events:
            async for event in _owned_events:
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
        if self.options or self._declared_context_window is not None:
            payload["options"] = dict(self.options)
            if self._declared_context_window is not None:
                payload["options"]["num_ctx"] = self._declared_context_window
        if response_format:
            payload["format"] = response_format.get("json_schema", {}).get(
                "schema", "json"
            )
        await self.start()
        assert self._client is not None
        answering = False
        try:
            async with asyncio.timeout(self.timeout_secs) as startup_deadline:
                async with self._client.stream("POST", "/api/chat", json=payload) as response:
                    response.raise_for_status()
                    calls: dict[str, dict[str, Any]] = {}
                    async for row in until_terminal(
                        _chat_rows(response), ends=lambda row: row.get("done") is True,
                        adapter="Ollama", missing="the done line", model=selected,
                    ):
                        _heard_one(self.endpoint, selected, row)
                        message = row.get("message") or {}
                        if row.get("done") or any(
                            message.get(field) for field in ("content", "thinking", "tool_calls")
                        ):
                            answering = True
                            startup_deadline.reschedule(None)
                        for field, kind in (
                            ("thinking", EVENT_THINKING_CHUNK),
                            ("content", EVENT_TEXT_CHUNK),
                        ):
                            if message.get(field):
                                yield LLMEvent(kind=kind, text=message[field])
                        for index, call in enumerate(message.get("tool_calls") or []):
                            function = call["function"]
                            identifier = str(call.get("id") or f"ollama-{function.get('index', index)}")
                            pending = calls.setdefault(identifier, {"name": "", "arguments": ""})
                            if function.get("name"):
                                pending["name"] = function["name"]
                            arguments = function.get("arguments", {})
                            if isinstance(arguments, str):
                                pending["arguments"] += arguments
                            else:
                                pending["arguments"] = json.dumps(arguments)
                        if row.get("done") is True:
                            reason = str(row.get("done_reason") or "stop")
                            for identifier, pending in calls.items():
                                arguments = pending["arguments"]
                                try:
                                    parsed = json.loads(arguments)
                                except json.JSONDecodeError:
                                    parsed = None
                                yield LLMEvent(
                                    kind=EVENT_TOOL_CALL, tool_call_id=identifier,
                                    title=pending["name"], tool_input=arguments,
                                    tool_input_obj=parsed if isinstance(parsed, dict) else None,
                                    stop_reason=reason,
                                )
                            reported_window = await self._served_context_window(selected)
                            self._reported_context_window = reported_window
                            self.context_window = reported_window or LOCAL_SERVED_CONTEXT_WINDOW
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
                                        context_window_tokens=reported_window,
                                    )
                                    if measured_prompt is not None
                                    or reported_window is not None
                                    else None
                                ),
                                stop_reason=str(row.get("done_reason") or "stop"),
                            )
                            return
        except (TimeoutError, httpx.ReadTimeout):
            if answering:
                raise
            error = FirstTokenTimeout(
                model=selected, provider=self.display_name, waited_secs=self.timeout_secs
            )
            logger.warning("%s", error)
            raise error from None

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
                extra={"runs_here": row.runs_here, "modified_at": getattr(self.provider, "_listed_modified_at", {}).get(row.name, "")},
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
    config = {**entry.options, "model": entry.model}
    if kwargs.get("max_tokens") is not None:
        cap = int(kwargs["max_tokens"])
        if cap <= 0:
            raise ValueError("max_tokens must be positive")
        options = dict(config.get("options") or {})
        previous = options.get("num_predict")
        if previous is not None and int(previous) > 0:
            cap = min(cap, int(previous))
        options["num_predict"] = cap
        config["options"] = options
    return OllamaProvider(config)


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
                hosts_model=True,
                default_endpoint=_DEFAULT_ENDPOINT,
            ),
            _factory,
            passes_on=passes_on,
        )
    registry.register_catalog("ollama", OllamaCatalog)


_register()
