"""Model discovery contracts, wire records, and shared classification rules."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class ModelDiscoveryFailure(RuntimeError):
    def __init__(self, detail: str, *, rejected_credential: bool = False):
        super().__init__(detail)
        self.rejected_credential = rejected_credential


_DISCOVERY_FAILURES: contextvars.ContextVar[list[ModelDiscoveryFailure] | None] = (
    contextvars.ContextVar("gideon_discovery_failures", default=None)
)


@contextlib.contextmanager
def capture_discovery_failures():
    """Observe a fail-soft catalog on this request without sharing errors across tasks."""
    failures: list[ModelDiscoveryFailure] = []
    token = _DISCOVERY_FAILURES.set(failures)
    try:
        yield failures
    finally:
        _DISCOVERY_FAILURES.reset(token)


def _discovery_failed(endpoint: str, *, status: int = 0) -> None:
    from urllib.parse import urlsplit

    host = urlsplit(endpoint).hostname or "The provider"
    rejected = status in {401, 403}
    detail = (
        f"{host} rejected its credential (HTTP {status}). Check the key in Settings → Providers."
        if rejected
        else f"{host} did not return its models"
        + (
            f" (HTTP {status})."
            if status
            else ". Check its connection in Settings → Providers."
        )
    )
    failures = _DISCOVERY_FAILURES.get()
    if failures is not None:
        failures.append(ModelDiscoveryFailure(detail, rejected_credential=rejected))


def _present_fields(
    record: object,
    *,
    required: tuple[str, ...] = (),
    nonnull: tuple[str, ...] = (),
    nonempty: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = {name: getattr(record, name) for name in required}
    for name in nonnull:
        value = getattr(record, name)
        if value is not None:
            result[name] = value
    for name in nonempty:
        value = getattr(record, name)
        if value:
            result[name] = list(value) if isinstance(value, list) else value
    return result


@dataclass
class ModelInfo:
    id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    size: int | None = None
    downloaded: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extra or {})
        payload.update(
            _present_fields(
                self,
                required=("id", "name", "capabilities"),
                nonnull=("size", "downloaded"),
                nonempty=("description",),
            )
        )
        return payload


@dataclass
class ConnectionResult:
    ok: bool
    detail: str = ""
    model_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _present_fields(
            self, required=("ok",), nonnull=("model_count",), nonempty=("detail",)
        )


@dataclass
class PullProgress:
    status: str
    completed: int | None = None
    total: int | None = None
    digest: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        if self.error:
            return {"error": self.error}
        return _present_fields(
            self,
            required=("status",),
            nonnull=("completed", "total"),
            nonempty=("digest",),
        )


_NOT_BOUND_MARKERS = (
    "rerank",
    "moderation",
    "llama-guard",
    "prompt-guard",
    "shieldgemma",
    "guardian",
    "babbage-",
    "davinci-",
    "gpt-3.5-turbo-instruct",
    "realtime",
    "codex",
    "deep-research",
    "computer-use",
)
# The Responses-only reasoning tiers (``o1-pro``, ``o3-pro``, ``gpt-5-pro``, ``gpt-5.2-pro``).
_RESPONSES_ONLY_TIER = re.compile(r"(?:^|/)(?:o\d+|gpt-5(?:\.\d+)?)-pro(?:$|[-:])")

_EMBEDDING_MARKERS = (
    "embed",
    "embedding",
    "bge-",
    "e5-",
    "gte-",
    "minilm",
    "nomic",
    "mxbai",
    "arctic-embed",
    "paraphrase-",
    "sentence-",
)

_IMAGE_MODALITY_MARKERS = (
    "vision",
    "vl-",
    "-vl",
    "vlm",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-5",
    "gpt-4.1",
    "claude-3",
    "claude-4",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "gemini",
    "qwen-vl",
    "llava",
    "pixtral",
    "internvl",
    "minicpm-v",
)

_IMAGE_GEN_MARKERS = (
    "dall-e",
    "dalle",
    "stable-diffusion",
    "sdxl",
    "sd3",
    "flux",
    "qwen-image",
    "wan-image",
    "imagen",
    "midjourney",
    "image-gen",
    "-image",
    "ideogram",
    "playground-v",
)

_AUDIO_GEN_MARKERS = (
    "musicgen",
    "audiogen",
    "audio-gen",
    "bark",
    "suno",
    "audiocraft",
    "stable-audio",
)

_AUDIO_MODALITY_MARKERS = ("audio", "voice")

_VIDEO_GEN_MARKERS = (
    "video-gen",
    "sora",
    "runway",
    "veo",
    "wan2.",
    "kling",
    "pika",
    "ltx-video",
    "mochi",
)

_VIDEO_MODALITY_MARKERS = (
    "video-understanding",
    "video-vl",
    "videollava",
    "video-llava",
)

_STT_MARKERS = ("whisper", "stt-", "transcribe", "-asr", "paraformer", "sensevoice")

_TTS_MARKERS = (
    "tts-",
    "-tts",
    "piper",
    "elevenlabs",
    "polly",
    "kokoro",
    "orpheus",
    "cosyvoice",
    "sambert",
    "cartesia",
)

_MODEL_FAMILY_PROVIDER_TYPES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (
        ("claude", "opus", "sonnet", "haiku"),
        frozenset({"anthropic", "anthropic_compatible", "bedrock"}),
    ),
    (
        ("gpt-", "o1", "o3", "o4", "dall-e", "text-embedding-", "whisper", "tts-"),
        frozenset({"openai", "openai_compatible", "azure_openai"}),
    ),
    (("gemini",), frozenset({"google", "gemini"})),
)

__all__ = [
    "ModelInfo",
    "ConnectionResult",
    "PullProgress",
    "ModelCatalog",
    "ModelManager",
    "infer_capabilities",
    "refused_sampling",
    "openai_compatible_list_models",
]

_EXCLUSIVE_TAGS = (
    ("embedding", _EMBEDDING_MARKERS),
    ("stt", _STT_MARKERS),
    ("tts", _TTS_MARKERS),
    ("video_gen", _VIDEO_GEN_MARKERS),
    ("image_gen", _IMAGE_GEN_MARKERS),
    ("audio_gen", _AUDIO_GEN_MARKERS),
)
_UNDERSTANDING_TAGS = (
    ("image_modality", _IMAGE_MODALITY_MARKERS),
    ("video_modality", _VIDEO_MODALITY_MARKERS),
    ("audio_modality", _AUDIO_MODALITY_MARKERS),
)

SAMPLING_PARAMETERS = ("temperature", "top_p", "top_k")
_FIXED_SAMPLING_MARKERS = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
)
_TEMPERATURE_OR_TOP_P_MARKERS = (
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
)


def refused_sampling(
    model_id: str, requested: tuple[str, ...] | list[str]
) -> dict[str, str]:
    """Explain sampling parameters a known model family does not accept."""
    wanted = [parameter for parameter in requested if parameter in SAMPLING_PARAMETERS]
    normalized = (model_id or "").strip().lower().replace(".", "-")
    if not wanted or not normalized:
        return {}
    shown = model_id.strip()
    if any(marker in normalized for marker in _FIXED_SAMPLING_MARKERS):
        return {
            parameter: f"{shown} does not accept a custom {parameter}"
            for parameter in wanted
        }
    if any(marker in normalized for marker in _TEMPERATURE_OR_TOP_P_MARKERS):
        pair = [
            parameter for parameter in wanted if parameter in ("temperature", "top_p")
        ]
        if len(pair) == 2:
            return {pair[1]: f"{shown} takes a temperature or a top_p, not both"}
    return {}


def model_family_provider_types(model_id: str) -> frozenset[str]:
    identifier = (model_id or "").lower()
    matching = next(
        (
            row
            for row in _MODEL_FAMILY_PROVIDER_TYPES
            if any(marker in identifier for marker in row[0])
        ),
        None,
    )
    if matching is None:
        return frozenset()
    markers, declared = matching
    return declared.union(_app_declared_types(markers))


def _app_declared_types(markers: tuple[str, ...]) -> frozenset[str]:
    try:
        import gideon.sdk.model  # noqa: F401
        from gideon.integrations.llm.branded_specs import spec_types_declaring_models

        return spec_types_declaring_models(markers)
    except Exception:
        logger.debug("Unable to consult provider model declarations", exc_info=True)
        return frozenset()


def infer_capabilities(model_id: str, families: list[str] | None = None) -> list[str]:
    family_text = " ".join(families or []).lower()
    searchable = f"{model_id or ''} {family_text}".lower()
    if any(
        marker in searchable for marker in _NOT_BOUND_MARKERS
    ) or _RESPONSES_ONLY_TIER.search(str(model_id or "").lower()):
        return []
    for tag, markers in _EXCLUSIVE_TAGS:
        if any(marker in searchable for marker in markers):
            return [tag]
    tags = ["chat"]
    for tag, capability_markers in _UNDERSTANDING_TAGS:
        matched = any(marker in searchable for marker in capability_markers)
        if matched or (tag == "image_modality" and "clip" in family_text):
            tags.append(tag)
    return tags


def _capabilities_by_id(record: dict[str, Any]) -> list[str]:
    """What a model record says with nothing but the OpenAI models object: its ``id``, and its
    ``root`` — the model an alias serves, which that object has always carried and a self-hosted
    server (vLLM) fills in, so an alias of a reranker is read as the reranker it serves.
    """
    model_id = str(record.get("id") or "")
    root = record.get("root")
    families = [root] if isinstance(root, str) and root and root != model_id else None
    return infer_capabilities(model_id, families)


def _record_capabilities(
    record: dict[str, Any],
    capabilities_of: Callable[[dict[str, Any]], list[str]] | None,
) -> list[str]:
    """``record``'s tags by the vendor's own reading of its records, else by its id and root.

    A reading that raises lists that one model for no job, and says so: one record of a shape
    the reading did not expect must not cost the rest of the list, and a model nothing has
    described is not offered for chat by default.
    """
    if capabilities_of is None:
        return _capabilities_by_id(record)
    try:
        return [str(c) for c in capabilities_of(record)]
    except Exception:  # noqa: BLE001 — one record the vendor reading cannot read
        logger.warning(
            "Model discovery could not read what %r does; it is offered for nothing",
            record.get("id"),
            exc_info=True,
        )
        return []


def _decode_model_rows(document: object, capabilities_of=None) -> list[ModelInfo]:
    rows = (
        document
        if isinstance(document, list)
        else document.get("data", []) if isinstance(document, dict) else None
    )
    if not isinstance(rows, list):
        return []
    models = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            continue
        owner = row.get("owned_by")
        models.append(
            ModelInfo(
                id=identifier,
                name=identifier,
                capabilities=_record_capabilities(row, capabilities_of),
                extra={
                    key: value
                    for key, value in row.items()
                    if key
                    in {
                        "owned_by",
                        "context_length",
                        "context_window",
                        "max_model_len",
                        "n_ctx",
                        "max_input_tokens",
                    }
                },
            )
        )
    return models


async def openai_compatible_list_models(
    endpoint: str | None,
    api_key: str | None,
    *,
    default_base: str = "https://api.openai.com/v1",
    capabilities_of: Callable[[dict[str, Any]], list[str]] | None = None,
) -> list[ModelInfo]:
    if not (endpoint or api_key):
        return []
    from gideon.sdk.net import CONNECTOR, egress_policy_for, fetch

    address = (endpoint or default_base).rstrip("/")
    address += "/models" if address.endswith("/v1") else "/v1/models"
    authorization = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = await fetch(
            address,
            policy=egress_policy_for(CONNECTOR),
            method="GET",
            headers=authorization,
        )
        if response.status != 200:
            _discovery_failed(address, status=int(response.status))
            return []
        return _decode_model_rows(json.loads(response.text), capabilities_of)
    except Exception:
        _discovery_failed(address)
        return []


class ModelCatalog(ABC):
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    async def test_connection(self) -> ConnectionResult:
        result = ConnectionResult(ok=False)
        try:
            result.model_count = len(await self.list_models())
        except Exception as error:
            result.detail = str(error)[:200]
        else:
            result.ok = True
        return result


class ModelManager(ModelCatalog):
    @abstractmethod
    async def search_catalog(self, query: str) -> list[ModelInfo]:
        raise NotImplementedError

    @abstractmethod
    def pull_model(self, model_id: str) -> AsyncIterator[PullProgress]:
        raise NotImplementedError

    @abstractmethod
    async def delete_model(self, model_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def show_model(self, model_id: str) -> ModelInfo:
        raise NotImplementedError


__all__ = [
    "ModelInfo",
    "ConnectionResult",
    "PullProgress",
    "ModelCatalog",
    "ModelManager",
    "infer_capabilities",
    "refused_sampling",
    "openai_compatible_list_models",
]
