"""SDK: the model (LLM) provider ABC + the generic LLM infrastructure a model app
builds on.

Stable re-exports of the provider-agnostic LLM machinery — the ``ModelProvider`` ABC
+ event/stream types, the capability descriptors, the provider registry (so an app
registers its type + capability factory), the credential type, the streaming-tag
splitter, the context-window lookup, and ``OpenAIProvider`` (the canonical OpenAI-wire
client that any OpenAI-COMPATIBLE endpoint app — vllm, together, groq, … — subclasses).

An app imports these, not core internals, so core can evolve underneath it. This is
generic infra: openai/anthropic/bedrock/vllm are all implementations built on it (they
ship pre-installed for a working out-of-box system, but are architecturally identical
to any installed model app), including bundled local model providers.
"""

from gideon.extensions.providers.media_scanners import register_scanner  # noqa: F401
from gideon.integrations.llm.anthropic import AnthropicProvider  # noqa: F401
from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    CancelOutcome,
    LLMEvent,
    ModelProvider,
)
from gideon.integrations.llm.branded_specs import BrandedProviderSpec  # noqa: F401
from gideon.integrations.llm.capabilities import ProviderCapability  # noqa: F401
from gideon.integrations.llm.capabilities import Capability, StructuredOutput
from gideon.integrations.llm.catalog import (
    ConnectionResult,
    ModelCatalog,
    ModelInfo,
    ModelManager,
    PullProgress,
    infer_capabilities,
    openai_compatible_list_models,
)
from gideon.integrations.llm.credentials import Credential  # noqa: F401
from gideon.integrations.llm.openai import OpenAIProvider  # noqa: F401
from gideon.integrations.llm.prompt_cache import PromptCache  # noqa: F401
from gideon.integrations.llm.prompt_cache import CACHE_HINT_KEY
from gideon.integrations.llm.registry import (
    CredentialMissing,
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)
from gideon.integrations.llm.stream_tags import make_think_splitter  # noqa: F401
from gideon.integrations.llm.stream_tags import KIND_OUTSIDE
from gideon.integrations.media_catalogs import (
    MediaCatalog,
    MediaModel,
    register_media_catalog,
)
from gideon.integrations.model_windows import (
    LOCAL_SERVED_CONTEXT_WINDOW,
    declared_context_window,
    model_context_window,
)
from gideon.sdk.provider_helpers import register_branded_app  # noqa: F401

__all__ = [
    "ModelProvider",
    "LLMEvent",
    "CancelOutcome",
    "EVENT_COMPLETE",
    "EVENT_TEXT_CHUNK",
    "EVENT_THINKING_CHUNK",
    "EVENT_TOOL_CALL",
    "Capability",
    "StructuredOutput",
    "ProviderCapability",
    "PromptCache",
    "CACHE_HINT_KEY",
    "Credential",
    "get_default_registry",
    "ProviderEntry",
    "ProviderResolutionError",
    "CredentialMissing",
    "KIND_OUTSIDE",
    "make_think_splitter",
    "model_context_window",
    "declared_context_window",
    "LOCAL_SERVED_CONTEXT_WINDOW",
    "OpenAIProvider",
    "AnthropicProvider",
    "ModelCatalog",
    "ModelManager",
    "ModelInfo",
    "ConnectionResult",
    "PullProgress",
    "infer_capabilities",
    "openai_compatible_list_models",
    "BrandedProviderSpec",
    "register_branded_app",
    "MediaCatalog",
    "MediaModel",
    "register_media_catalog",
    "register_scanner",
]
