from __future__ import annotations

import asyncio
import sys

import pytest

from gideon.core.cancellation import CancelScope
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.native.tool_retrieval import ToolRetriever
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.acp_agent import AcpAgentProvider
from gideon.integrations.mcp_artifacts import _list_tools
from gideon.integrations.tool_providers.base import ToolDefinition


def artifact_definitions():
    return [
        ToolDefinition(
            name=entry["name"],
            description=entry.get("description", ""),
            parameters=entry["inputSchema"],
        )
        for entry in _list_tools()
    ]


def test_native_search_discovers_visualize_and_preserves_result_limit():
    async def run():
        definitions = artifact_definitions()
        retriever = ToolRetriever(definitions)
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="search-regression"),
            model_provider=AcpAgentProvider(command=[sys.executable, "-m", "gideon", "acp"]),
        )
        runtime._tool_retriever = retriever
        result = await runtime._invoke(
            "tool_search", {"query": "genui ui docs component catalog native candidates", "limit": 10}, meta_sink={}
        )
        assert "Matching tools" in result
        assert "visualize:" in result
        matches = await retriever.search_async("", limit=2)
        assert len(matches) == 2
        assert all(set(row) == {"name", "description"} for row in matches)
        assert await retriever.search_async("no_matching_capability_927481") == []

    asyncio.run(run())


def test_cancelled_search_does_not_return_results():
    async def run():
        scope = CancelScope()
        scope.begin_turn()
        scope.request()
        retriever = ToolRetriever(artifact_definitions())
        with pytest.raises(asyncio.CancelledError):
            await retriever.search_async("genui", cancelled=lambda: scope.cancelled)

    asyncio.run(run())
