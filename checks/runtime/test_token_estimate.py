from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from gideon.automation.workflows.engine import _estimate_tokens as workflow_tokens
from gideon.automation.workflows.longrun import BufferState
from gideon.cognition.knowledge.session_brief import BriefItem, compose
from gideon.cognition.learning.surfacing import count_tokens
from gideon.core import token_estimate
from gideon.integrations.tool_providers import savings
from gideon.interfaces.dashboard.handlers.knowledge import (
    _estimate_tokens as knowledge_tokens,
)

ROOT = Path(__file__).resolve().parents[2] / "runtime" / "gideon"
NON_TOKEN_DIVISIONS = {
    ("cognition/archive_relevance.py", "len(conversations) / 4.0"),
    ("cognition/archive_episodes.py", "len(blob) // 4"),
    ("cognition/archive_episodes.py", "len(row['embedding']) // 4"),
    ("cognition/archive_lessons.py", "len(blob) // 4"),
    ("cognition/memory_record.py", "len(blob) // 4"),
    ("automation/schedule_history.py", "_MAX_RECORDS_PER_JOB // 4"),
    ("integrations/tool_providers/prose_compress.py", "_PROMPT_INPUT_CAP * 2 // 3"),
    ("integrations/tool_providers/projection.py", "cap // 4"),
    ("integrations/tool_providers/base.py", "cap * 2 // 3"),
    ("cognition/knowledge/retrieval.py", "len(blob) // 4"),
    ("cognition/knowledge/reports.py", "span * step / 4"),
    ("cognition/knowledge/vector_index.py", "len(blob) // 4"),
    ("cognition/knowledge/embedder.py", "len(data) // 4"),
    ("cognition/learning/measure.py", "MIN_SAMPLES_FOR_TUNING // 4"),
    ("automation/workflows/admission.py", "total // 3"),
    ("automation/workflows/matcher.py", "gap / 4.0"),
    ("interfaces/dashboard/screen_context.py", "len(self.b64) * 3 // 4"),
    ("interfaces/dashboard/screen_context.py", "len(payload) * 3 // 4"),
}


def _local_estimates(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        label = name.id.upper()
                        if "CHAR" in label and "PER_TOKEN" in label:
                            yield node
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Div, ast.FloorDiv))
            and isinstance(node.right, ast.Constant)
            and node.right.value in (3, 4)
        ):
            yield node


def test_constants_are_stable_and_module_is_a_leaf():
    assert token_estimate.NOMINAL_CHARS_PER_TOKEN == 4
    assert type(token_estimate.NOMINAL_CHARS_PER_TOKEN) is int
    assert token_estimate.CONSERVATIVE_CHARS_PER_TOKEN == 3.0
    assert type(token_estimate.CONSERVATIVE_CHARS_PER_TOKEN) is float
    tree = ast.parse(Path(token_estimate.__file__).read_text())
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)
    )


def test_source_census_has_no_local_character_token_ratios():
    paths = sorted(ROOT.rglob("*.py"))
    assert paths and (ROOT / "engine/agents/native/runtime.py") in paths
    violations = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if relative == "core/token_estimate.py":
            continue
        for node in _local_estimates(ast.parse(path.read_text())):
            expression = ast.unparse(node)
            if (relative, expression) not in NON_TOKEN_DIVISIONS:
                violations.append(f"{relative}:{node.lineno}: {expression}")
    assert not violations, "Use core.token_estimate constants:\n" + "\n".join(
        violations
    )


@pytest.mark.parametrize(
    "source",
    [
        "CHARS_PER_TOKEN = 4",
        "_EST_CHARS_PER_TOKEN: float = 3.0",
        "class Estimator:\n    _CHARS_PER_TOKEN = 4",
        "tokens = len(prompt) // 4",
        "tokens = (len(text) + 3) // 4",
        "tokens = characters / 3.0",
    ],
)
def test_census_detects_local_estimate_regressions(source):
    assert list(_local_estimates(ast.parse(source)))


@pytest.mark.parametrize(
    "text, expected",
    [("", 0), ("abc", 0), ("abcd", 1), ("abcdefg", 1), ("abcdefgh", 2)],
)
def test_nominal_consumers_preserve_rounding(text, expected):
    assert knowledge_tokens(text) == expected
    assert workflow_tokens(text, "") == max(1, expected)
    assert workflow_tokens(text, text) == max(
        1, len(text) * 2 // token_estimate.NOMINAL_CHARS_PER_TOKEN
    )


def test_buffer_estimate_uses_serialized_character_count():
    buffer = BufferState(items=[{"text": "évidence"}])
    characters = len(json.dumps(buffer.items, ensure_ascii=False, default=str))
    assert (
        buffer.approx_tokens() == characters // token_estimate.NOMINAL_CHARS_PER_TOKEN
    )


def test_tokenizer_rejection_uses_nominal_ceiling():
    assert count_tokens("<|endoftext|>") == 4
    assert count_tokens("<|endoftext|>xxxx") == 5


def test_brief_budget_uses_nominal_characters():
    item = BriefItem(body="abcd")
    assert compose([item], max_tokens=10).items == []
    assert compose([item], max_tokens=11).items == [item]


def test_savings_estimate_uses_nominal_floor():
    savings.record_saving(
        month="2026-09", model="local", compressor="test", chars_in=19, chars_out=4
    )
    result = savings.summary()
    assert result["saved_chars"] == 15
    assert result["saved_tokens_estimated"] == 3


def test_native_occupancy_uses_conservative_ratio(tmp_path, monkeypatch):
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.scripted import ScriptedProvider
    from gideon.integrations.model_windows import model_context_window

    script = tmp_path / "script.json"
    script.write_text(json.dumps({"version": 1, "turns": [{"text": "ready"}]}))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(script))
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="Estimate", provider="native", model="unknown"
        ),
        model_provider=ScriptedProvider(),
        cwd=tmp_path,
    )
    runtime._messages = [{"role": "user", "content": "x" * 600}]
    expected = (
        100
        * (600 / token_estimate.CONSERVATIVE_CHARS_PER_TOKEN)
        / model_context_window("unknown")
    )
    assert runtime._estimated_context_pct() == pytest.approx(expected)
