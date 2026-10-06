"""Restart-safe vector reuse and request-bounded native tool selection."""

import json

from gideon.engine.agents.native import tool_vectors
from gideon.engine.agents.native.tool_retrieval import (
    ToolRetriever,
    schema_budget_chars,
)
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition

_CORE = ("bash", "read_file", "write_file", "edit_file", "grep", "glob", "list_dir")


def _tool(name: str, size: int = 700, description: str = "") -> ToolDefinition:
    properties = {
        f"arg{index}": {"type": "string", "description": "x" * 80}
        for index in range(max(1, size // 120))
    }
    return ToolDefinition(
        name=name,
        description=description or f"Does {name.replace('_', ' ')}.",
        provider="native-scenario",
        parameters={"type": "object", "properties": properties},
        requires_approval=False,
        risk_level=RiskLevel.SAFE,
    )


def test_restart_safe_vectors_and_request_scoped_budgeted_selection(tmp_path):
    model = "local:semantic-model"
    cache_path = tmp_path / tool_vectors.TOOL_VECTORS_FILE
    texts = ("bash: execute a command", "read_file: read a file")
    cached = {
        tool_vectors._digest(model, texts[0]): tool_vectors._pack([0.6, 0.8]),
        tool_vectors._digest(model, texts[1]): tool_vectors._pack([0.8, 0.6]),
    }
    cache_path.write_text(
        json.dumps({"version": 1, "vectors": cached}), encoding="utf-8"
    )
    vectors = tool_vectors.ToolVectors().vectors(cache_path, model, texts)
    after_restart = tool_vectors.ToolVectors().vectors(cache_path, model, texts)
    assert vectors == after_restart and len(vectors) == 2

    definitions = [_tool(name, 600) for name in _CORE]
    definitions += [
        _tool("automation_create", 2640, "Create an automation for an event."),
        _tool("web_fetch", 600, "Fetch the page at a URL."),
        _tool("task_ready", 700, "Check which tasks are ready."),
    ]
    definitions += [
        _tool(f"knowledge_tool_{index}", 900, f"Search knowledge capability {index}.")
        for index in range(100)
    ]
    retriever = ToolRetriever(definitions)
    budget = schema_budget_chars(32_768)
    request = "When a new PDF lands, set that up as an automation."
    selected = retriever.select(request, budget_chars=budget)
    names = {definition.name for definition in selected}
    assert set(_CORE) <= names
    assert "automation_create" in names
    assert "task_ready" not in names
    assert sum(retriever._chars[name] for name in names) <= budget

    answer = "It's ~/Notes/Garden/kitchen.md."
    assert "automation_create" in {
        definition.name for definition in retriever.select(answer, budget_chars=budget)
    }
    assert "automation_create" not in {
        definition.name
        for definition in retriever.select("Thanks, that is all.", budget_chars=budget)
    }
    assert any(
        row["name"] == "knowledge_tool_99"
        for row in retriever.search("knowledge capability 99")
    )
