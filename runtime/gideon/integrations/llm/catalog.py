"""Model discovery contracts, wire records, and shared classification rules."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
                required=("id", "name"),
                nonnull=("size", "downloaded"),
                nonempty=("capabilities", "description"),
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

_STT_MARKERS = ("whisper", "stt-", "transcribe")

_TTS_MARKERS = ("tts-", "-tts", "piper", "elevenlabs", "polly", "kokoro")

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
    for tag, markers in _EXCLUSIVE_TAGS:
        if any(marker in searchable for marker in markers):
            return [tag]
    tags = ["chat"]
    for tag, capability_markers in _UNDERSTANDING_TAGS:
        matched = any(marker in searchable for marker in capability_markers)
        if matched or (tag == "image_modality" and "clip" in family_text):
            tags.append(tag)
    return tags


def _decode_model_rows(document: object) -> list[ModelInfo]:
    if not isinstance(document, dict) or not isinstance(document.get("data", []), list):
        return []
    models = []
    for row in document.get("data", []):
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
                capabilities=infer_capabilities(identifier),
                extra={
                    key: value for key, value in row.items()
                    if key in {"owned_by", "context_length", "context_window", "max_model_len", "n_ctx", "max_input_tokens"}
                },
            )
        )
    return models


async def openai_compatible_list_models(
    endpoint: str | None,
    api_key: str | None,
    *,
    default_base: str = "https://api.openai.com/v1",
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
        return (
            _decode_model_rows(json.loads(response.text))
            if response.status == 200
            else []
        )
    except Exception:
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
    "openai_compatible_list_models",
]
