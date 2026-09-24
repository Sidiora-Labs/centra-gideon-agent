"""The retired fast-model contract must not return to deterministic detection."""

import ast
import inspect
import json
from dataclasses import fields
from pathlib import Path

from gideon.cognition.knowledge import contradiction
from gideon.integrations.action_providers import knowledge_persist_provider


def test_retired_helpers_and_conflict_basis_are_absent():
    retired = {"conflict_prompt", "memo_key", "parse_model_verdict", "_int"}
    definitions = {
        node.name
        for node in ast.parse(inspect.getsource(contradiction)).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not definitions & retired
    assert "basis" not in {field.name for field in fields(contradiction.Conflict)}
    assert "basis" not in contradiction.Conflict().to_dict()
    assert "conflict_prompt" not in inspect.getsource(knowledge_persist_provider)


def test_workflow_judge_uses_separate_candidates_without_retired_basis():
    root = Path(contradiction.__file__).resolve().parents[2]
    workflow = json.loads(
        (
            root / "automation/workflows/bundled/contradiction-review/workflow.json"
        ).read_text()
    )
    judge = next(
        node for node in workflow["root"]["children"] if node["id"] == "judge_conflicts"
    )
    prompt = judge["config"]["prompt"]
    assert "basis" not in prompt
    assert "{{nodes.persist.output.conflicts}}" in prompt
    assert "{{nodes.persist.output.conflict_candidates}}" in prompt
    assert "do not re-litigate" in prompt
