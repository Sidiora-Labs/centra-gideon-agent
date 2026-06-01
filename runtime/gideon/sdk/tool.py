"""SDK: the tool-provider ABC + data types + the shared output-projection discipline.

Stable re-export of ``gideon.tool_providers.base`` (the ABC + types) plus the
dispatch-time output projection (``project_and_retain`` + its default cap) every tool
surface shares — so a tool APP (e.g. the MCP adapter) projects + retains large results
identically to the native builtins, without reaching into core internals.
"""

from gideon.tool_providers.base import (  # noqa: F401
    ToolProvider,
    ToolDefinition,
    ToolResult,
    RiskLevel,
)
from gideon.tool_providers.projection import (  # noqa: F401
    DEFAULT_TOOL_OUTPUT_CAP,
    project_and_retain,
)

__all__ = [
    'ToolProvider', 'ToolDefinition', 'ToolResult', 'RiskLevel',
    'project_and_retain', 'DEFAULT_TOOL_OUTPUT_CAP',
]
