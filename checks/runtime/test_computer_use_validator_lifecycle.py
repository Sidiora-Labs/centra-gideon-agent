import ast
import importlib.util
from pathlib import Path

import pytest

from gideon.integrations.computer_use import service

SOURCE = (
    Path(__file__).resolve().parents[2] / "tooling/scripts/dcu3_stale_index_validate.py"
)


@pytest.fixture
def validator():
    spec = importlib.util.spec_from_file_location("gideon_live_validator", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function(name):
    return next(
        node
        for node in ast.parse(SOURCE.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _calls(node):
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def test_driver_counter_restores_real_driver_even_on_failure(validator):
    original = service._run_driver
    with pytest.raises(RuntimeError, match="validation failed"):
        with validator._install_driver_counter():
            assert service._run_driver is not original
            validator._DRIVER_OPS.extend(["snapshot", "set_value"])
            raise RuntimeError("validation failed")
    assert service._run_driver is original
    assert validator._DRIVER_OPS == []


def test_operation_window_is_drained_before_acting(validator):
    validator._DRIVER_OPS.extend(["snapshot", "snapshot"])
    assert validator._driver_ops_since() == ["snapshot", "snapshot"]
    assert validator._driver_ops_since() == []
    for name in ("_write_by_index", "_expect_stale"):
        calls = _calls(_function(name))
        drain = sorted(
            call.lineno
            for call in calls
            if ast.unparse(call.func) == "_driver_ops_since"
        )
        acting = next(
            call.lineno for call in calls if ast.unparse(call.func) == "_dispatch"
        )
        assert len(drain) == 2
        assert drain[0] < acting < drain[1]


@pytest.mark.parametrize(
    "title,expected",
    [
        ("document-A.txt", True),
        ("document-A", True),
        ("document-A.txt — Edited", True),
        ("document-A.txt - Edited", True),
        ("document-B.txt", False),
        ("document-A.txt-backup", False),
        ("Untitled", False),
    ],
)
def test_document_wait_accepts_autosave_title_but_not_other_windows(
    validator, title, expected
):
    assert validator._document_title_matches(title, Path("document-A.txt")) is expected


def test_opened_document_wait_is_bounded_and_used_after_each_open():
    wait = _function("_wait_document")
    loop = next(node for node in wait.body if isinstance(node, ast.For))
    assert ast.literal_eval(loop.iter.args[0]) == 30
    guard = next(node for node in loop.body if isinstance(node, ast.If))
    assert "_document_title_matches" in ast.unparse(guard.test)
    assert 'outcome == "ok"' in ast.unparse(
        guard.test
    ) or "outcome == 'ok'" in ast.unparse(guard.test)
    assert any(isinstance(node, ast.Return) for node in guard.body)
    assert isinstance(wait.body[-1], ast.Raise)
    phase = _function("_phase_stale_interaction")
    opens = [
        i
        for i, node in enumerate(phase.body)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "subprocess.run"
    ]
    assert len(opens) == 3
    for index in opens:
        assert ast.unparse(phase.body[index + 1].value.func) == "_wait_document"


def test_teardown_covers_failures_and_preserves_autosaved_documents():
    phase = _function("phase_stale")
    counted = next(node for node in phase.body if isinstance(node, ast.With))
    assert ast.unparse(counted.items[0].context_expr) == "_install_driver_counter()"
    guarded = next(node for node in counted.body if isinstance(node, ast.Try))
    assert any(
        ast.unparse(call.func) == "_phase_stale_interaction" for call in _calls(guarded)
    )
    cleanup = next(node for node in guarded.finalbody if isinstance(node, ast.For))
    assert ast.unparse(cleanup.iter) == "sorted(launched)"
    assert any(ast.unparse(call.func) == "subprocess.run" for call in _calls(cleanup))
    source = ast.unparse(phase)
    assert "tempfile.mkdtemp" in source
    assert "TextEdit may save edits" in source
    assert "unlink" not in source
    assert "left running; scratch documents may remain open" in source
