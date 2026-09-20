import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _breaker_reasons(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if (
                node.func.id == "BreakerVerdict"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
            ):
                if isinstance(node.args[1].value, str) and node.args[1].value:
                    values.add(node.args[1].value)
    return values


def _tick_escalation_reasons(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    values: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "Decision" and len(node.args) > 2:
            action = node.args[0]
            reason = node.args[2]
            if (
                isinstance(action, ast.Attribute)
                and action.attr == "ESCALATE"
                and isinstance(reason, ast.Constant)
            ):
                if isinstance(reason.value, str):
                    values.add(reason.value)
        if node.func.id == "_stall_response":
            for keyword in node.keywords:
                if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        values.add(keyword.value.value)
    return values


def test_escalation_panel_covers_the_producer_reason_vocabulary() -> None:
    resilience = ROOT / "runtime/gideon/automation/workflows/resilience.py"
    tick = ROOT / "runtime/gideon/automation/loop/tick.py"
    panel_reasons = ROOT / "apps/console/src/features/workflows/escalationReasons.ts"

    produced = _breaker_reasons(resilience) | _tick_escalation_reasons(tick)
    rendered = set(
        re.findall(r"^\s{2}([a-z][a-z_]+):\s", panel_reasons.read_text(), re.MULTILINE)
    )

    assert produced == rendered
