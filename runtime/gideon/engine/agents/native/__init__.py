"""Native in-process agent runtime (E2-P4).

The native ``AgentProvider`` runs the agent turn loop *inside* the Gideon
process: it inferences through a :class:`~gideon.integrations.llm.base.ModelProvider`
(governed by Settings → Models), executes tools through the
:class:`~gideon.integrations.tool_providers.base.ToolProvider` seam, gates them through an
in-process :class:`~gideon.engine.agents.native.approval.ApprovalGate`, and emits the
same neutral :class:`~gideon.integrations.llm.events.AgentEvent` stream the chat runner
already consumes from ACP. No external CLI subprocess is involved.
"""

from gideon.engine.agents.native.approval import ApprovalGate
from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.native.tools import InProcessMcpToolProvider

__all__ = [
    "ApprovalGate",
    "InProcessMcpToolProvider",
    "NativeAgentRuntime",
    "NativeBuiltinToolProvider",
]
