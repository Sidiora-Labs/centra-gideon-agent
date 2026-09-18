"""Model discovery contracts, wire records, and shared classification rules."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    "ModelDiscoveryError",
    "DISCOVERY_ERROR_KINDS",
    "DISCOVERY_POLICY_BLOCKED",
    "DISCOVERY_CONNECTION_FAILED",
    "DISCOVERY_UNAUTHORIZED",
    "DISCOVERY_MALFORMED_RESPONSE",
    "DISCOVERY_SERVER_ERROR",
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
    for tag, markers in _UNDERSTANDING_TAGS:
        matched = any(marker in searchable for marker in markers)
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
                extra={"owned_by": owner} if owner else {},
            )
        )
    return models


DISCOVERY_POLICY_BLOCKED = "policy_blocked"
DISCOVERY_CONNECTION_FAILED = "connection_failed"
DISCOVERY_UNAUTHORIZED = "unauthorized"
DISCOVERY_MALFORMED_RESPONSE = "malformed_response"
DISCOVERY_SERVER_ERROR = "server_error"

DISCOVERY_ERROR_KINDS: frozenset[str] = frozenset(
    {
        DISCOVERY_POLICY_BLOCKED,
        DISCOVERY_CONNECTION_FAILED,
        DISCOVERY_UNAUTHORIZED,
        DISCOVERY_MALFORMED_RESPONSE,
        DISCOVERY_SERVER_ERROR,
    }
)


class ModelDiscoveryError(Exception):
    """A discovery attempt that failed in a way the operator can act on.

    Raised instead of the old bare ``return []``, which made five different problems
    — an egress policy that refuses the host, an endpoint that is not running, a key
    the endpoint rejected, a body that is not a model list, and a provider having a
    bad day — indistinguishable from "this provider has no models", the one state the
    empty list legitimately means. A provider setup screen cannot be honest about a
    failure it was never told about.

    ``kind`` is one of :data:`DISCOVERY_ERROR_KINDS` (what class of thing went wrong),
    ``detail`` says what was observed, and ``remedy`` says what to do about it. All
    three are SAFE to render: the API key is never part of any of them, and the
    address is carried in its credential-free form (:func:`_safe_address`).
    """

    def __init__(
        self, kind: str, detail: str, remedy: str, *, address: str = ""
    ) -> None:
        super().__init__(f"{detail} {remedy}".strip())
        self.kind = kind
        self.detail = detail
        self.remedy = remedy
        self.address = address

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "remedy": self.remedy,
            "address": self.address,
        }


_API_VERSION_SEGMENT = re.compile(r"^v\d+[a-z0-9]*$", re.IGNORECASE)

_DISCOVERY_UNSUPPORTED_STATUSES = frozenset({404, 405, 501})


def _models_address(base: str) -> str:
    """``base`` + the OpenAI-compatible models route, without doubling the version.

    An address whose LAST path segment is an API version — ``/v1``, but equally
    ``/v1beta`` (Gemini's OpenAI shim), ``/v2``, or a versioned sub-path like
    ``/openai/v1`` — already names the API root, so only ``/models`` is appended.
    Matching the literal string ``"/v1"`` (the old rule) sent every other versioned
    endpoint to ``…/v1beta/v1/models``, a 404 that discovery then reported as "no
    models" — the provider looked empty rather than misconfigured.
    """
    address = (base or "").rstrip("/")
    last = urlsplit(address).path.rsplit("/", 1)[-1]
    return (
        f"{address}/models"
        if _API_VERSION_SEGMENT.match(last)
        else f"{address}/v1/models"
    )


def _safe_address(url: str) -> str:
    """*url* with anything credential-shaped removed — userinfo and query string.

    A discovery address is operator-supplied and can legitimately carry a secret
    (``https://user:token@host/v1``, ``…?api-key=…``). Every field of a
    :class:`ModelDiscoveryError` is rendered in the UI and written to the log, so the
    address is reduced to scheme/host/path before it goes anywhere.
    """
    try:
        split = urlsplit(url)
    except ValueError:
        return ""
    host = split.hostname or ""
    if split.port:
        host = f"{host}:{split.port}"
    return urlunsplit((split.scheme, host, split.path, "", ""))


def _scrub(text: str, *, api_key: str = "", address: str = "", safe: str = "") -> str:
    """Strip the api key and the raw (possibly credentialed) address out of *text*.

    Belt and braces for the one string a discovery error does not author itself: the
    egress guard's refusal reason, and an exception's own words, can quote the URL
    they were given.
    """
    out = text or ""
    if address and safe and address != safe:
        out = out.replace(address, safe)
    if api_key:
        out = out.replace(api_key, "***")
    return out


async def openai_compatible_list_models(
    endpoint: str | None,
    api_key: str | None,
    *,
    default_base: str = "https://api.openai.com/v1",
) -> list[ModelInfo]:
    """Live models from an OpenAI-compatible ``/models`` route.

    Returns the discovered models, or an EMPTY list for the two states that honestly
    mean "nothing to discover here" and that the caller's static catalog is the right
    answer for: the endpoint serves no models route at all (404/405/501 — several
    hosted providers don't), and a well-formed response listing no models.

    Every other failure raises :class:`ModelDiscoveryError` rather than collapsing
    into that same empty list.
    """
    if not (endpoint or api_key):
        return []
    from gideon.sdk.net import CONNECTOR, EgressBlocked, egress_policy_for, fetch

    address = _models_address(endpoint or default_base)
    safe = _safe_address(address)
    key = api_key or ""

    def clean(text: str) -> str:
        return _scrub(text, api_key=key, address=address, safe=safe)

    authorization = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = await fetch(
            address,
            policy=egress_policy_for(CONNECTOR),
            method="GET",
            headers=authorization,
        )
    except EgressBlocked as blocked:
        hints = list(getattr(blocked, "recovery_hints", []) or [])
        raise ModelDiscoveryError(
            DISCOVERY_POLICY_BLOCKED,
            f"The egress policy refused {safe}: {clean(str(blocked))}.",
            clean(
                " ".join(
                    [
                        "Allow the endpoint's host under security.egress.allow_hosts "
                        "(or allow_private for a LAN/loopback endpoint), then retry.",
                        *hints,
                    ]
                )
            ),
            address=safe,
        ) from blocked
    except Exception as exc:
        raise ModelDiscoveryError(
            DISCOVERY_CONNECTION_FAILED,
            f"{safe} could not be reached ({type(exc).__name__}: {clean(str(exc))}).",
            "Check that the endpoint is running and that its address is right, then "
            "retry.",
            address=safe,
        ) from exc

    status = response.status
    if status in (401, 403):
        raise ModelDiscoveryError(
            DISCOVERY_UNAUTHORIZED,
            f"{safe} rejected the credential (HTTP {status}).",
            "Check the API key configured for this provider — it is missing, expired "
            "or not authorized for model discovery.",
            address=safe,
        )
    if status in _DISCOVERY_UNSUPPORTED_STATUSES:
        logger.debug("%s serves no models route (HTTP %s)", safe, status)
        return []
    if status >= 500:
        raise ModelDiscoveryError(
            DISCOVERY_SERVER_ERROR,
            f"{safe} reported a server error (HTTP {status}).",
            "The endpoint is failing on its own side; retry once it recovers, or "
            "check its logs.",
            address=safe,
        )
    if status != 200:
        raise ModelDiscoveryError(
            DISCOVERY_SERVER_ERROR,
            f"{safe} answered HTTP {status}, which is not a model list.",
            "Check the endpoint's base path and that model discovery is enabled for "
            "this credential.",
            address=safe,
        )

    try:
        document = json.loads(response.text)
    except ValueError:
        content_type = (
            getattr(response, "headers", {}).get("Content-Type", "") or "unknown"
        )
        raise ModelDiscoveryError(
            DISCOVERY_MALFORMED_RESPONSE,
            f"{safe} answered HTTP 200 with a body that is not JSON "
            f"(content-type {content_type.split(';')[0]}).",
            "Point the provider at the API root of an OpenAI-compatible server (the "
            "path that serves /models), not at a page or a proxy.",
            address=safe,
        ) from None
    rows = document.get("data") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ModelDiscoveryError(
            DISCOVERY_MALFORMED_RESPONSE,
            f"{safe} answered HTTP 200 with JSON that carries no 'data' list of "
            "models.",
            "Point the provider at the API root of an OpenAI-compatible server (the "
            "path that serves /models), not at a page or a proxy.",
            address=safe,
        )
    models = _decode_model_rows(document)
    if rows and not models:
        raise ModelDiscoveryError(
            DISCOVERY_MALFORMED_RESPONSE,
            f"{safe} answered with {len(rows)} model row(s), none of them carrying a "
            "usable id.",
            "The endpoint is not speaking the OpenAI model-list shape; check that it "
            "is an OpenAI-compatible API root.",
            address=safe,
        )
    return models


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
    "ModelDiscoveryError",
    "DISCOVERY_ERROR_KINDS",
    "DISCOVERY_POLICY_BLOCKED",
    "DISCOVERY_CONNECTION_FAILED",
    "DISCOVERY_UNAUTHORIZED",
    "DISCOVERY_MALFORMED_RESPONSE",
    "DISCOVERY_SERVER_ERROR",
]
