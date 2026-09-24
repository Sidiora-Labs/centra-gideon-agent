"""Pre-tool hook argument decoding accepts native and ACP event payloads."""

import ast
import json
from pathlib import Path

import pytest

from gideon.integrations.llm.base import LLMEvent
from gideon.interfaces.dashboard import chat_runner
from gideon.interfaces.dashboard.chat_utils import tool_input_to_str


@pytest.mark.parametrize(
    "value",
    [
        {"path": "/tmp/note", "content": "hello"},
        '{"path": "/tmp/note"}',
        ["a", "b"],
        None,
    ],
)
def test_real_hook_argument_expressions_coerce_provider_inputs(value):
    tree = ast.parse(Path(chat_runner.__file__).read_text())
    expressions = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_parsed_input"
            for target in node.targets
        )
        and isinstance(node.value, ast.IfExp)
    ]
    assert len(expressions) == 3
    event = LLMEvent(kind="tool_call", tool_input=value)
    expected = json.loads(tool_input_to_str(value)) if value else None
    for expression in expressions:
        calls = [node for node in ast.walk(expression) if isinstance(node, ast.Call)]
        assert any(
            isinstance(call.func, ast.Name) and call.func.id == "tool_input_to_str"
            for call in calls
        )
        code = compile(
            ast.Expression(body=expression), str(chat_runner.__file__), "eval"
        )
        assert (
            eval(
                code,
                {"json": json, "tool_input_to_str": tool_input_to_str, "event": event},
            )
            == expected
        )
