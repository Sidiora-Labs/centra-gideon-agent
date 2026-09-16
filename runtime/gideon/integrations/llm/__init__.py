"""LLM provider abstraction — pluggable provider layer.

Core holds the generic infra (the ModelProvider ABC + the two supported inference
PROTOCOL clients, ``openai`` + ``anthropic``, exposed via ``gideon.sdk.model``).
The model *providers* (openai/anthropic/vllm/bedrock/ollama/…) are ALL standalone apps
under ``apps/<name>-models/`` that register via the app loader when installed — none are
eager-imported here. Ollama is no exception: its full implementation
(``apps/ollama-models/provider.py`` — a bespoke wire client for Ollama's ``/api/*``
dialect + local-model pull/delete catalog) lives in the app and imports core contracts
only through ``gideon.sdk.model``. It loads like every other model app via its
manifest ``implementation`` entry-point when the ``ollama-models`` app is enabled. Only
``acp_agent`` (the ACP agent-runtime type, not a model provider) registers on import here.
"""

from gideon.integrations.llm import acp_agent as _acp_agent  # noqa: F401
from gideon.integrations.llm.acp_agent import AcpAgentProvider
from gideon.integrations.llm.base import LLMEvent, ModelProvider
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.credentials import Credential, CredentialStore
from gideon.integrations.llm.registry import (
    CredentialMissing,
    ProviderEntry,
    ProviderFactory,
    ProviderRegistry,
    ProviderResolutionError,
    get_default_registry,
    reset_default_registry,
    set_default_registry,
)

__all__ = [
    "AcpAgentProvider",
    "Capability",
    "Credential",
    "CredentialMissing",
    "CredentialStore",
    "LLMEvent",
    "ModelProvider",
    "ProviderCapability",
    "ProviderEntry",
    "ProviderFactory",
    "ProviderRegistry",
    "ProviderResolutionError",
    "get_default_registry",
    "reset_default_registry",
    "set_default_registry",
]
