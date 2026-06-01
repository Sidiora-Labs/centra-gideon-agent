"""Native in-process agent runtime (E2-P4).

The native ``AgentProvider`` runs the agent turn loop *inside* the Gideon
process: it inferences through a :class:`~gideon.llm.base.ModelProvider`
(governed by Settings → Models), executes tools through the
:class:`~gideon.tool_providers.base.ToolProvider` seam, gates them through an
in-process :class:`~gideon.agents.native.approval.ApprovalGate`, and emits the
same neutral :class:`~gideon.llm.events.AgentEvent` stream the chat runner
already consumes from ACP. No external CLI subprocess is involved.
"""

from gideon.agents.native.approval import ApprovalGate
from gideon.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.agents.native.runtime import NativeAgentRuntime
from gideon.agents.native.tools import InProcessMcpToolProvider

__all__ = [
    "ApprovalGate",
    "InProcessMcpToolProvider",
    "NativeAgentRuntime",
    "NativeBuiltinToolProvider",
]
