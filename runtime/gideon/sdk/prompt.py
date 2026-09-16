"""SDK: the prompt-provider ABC + data types.

Stable re-export of ``gideon.integrations.prompt_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from gideon.integrations.prompt_providers.base import (
    PromptProvider,
    PromptRenderError,
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
)

__all__ = [
    "PromptProvider",
    "PromptTemplate",
    "PromptSnippet",
    "PromptVariable",
    "PromptRenderError",
]
