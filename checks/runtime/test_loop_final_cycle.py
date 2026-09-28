from __future__ import annotations

from gideon.automation.loop.tick import Action, StepConfig, TickConfig, TickState, evaluate
from gideon.automation.workflows.models import Node
from gideon.automation.workflows.tick import loop_should_continue


def test_counted_loop_completion_wins_on_its_final_allowed_iteration():
    node = Node.from_dict(
        {
            "kind": "loop",
            "id": "last-cycle",
            "config": {"mode": "counted", "n": 1, "max_iterations": 1},
            "children": [{"kind": "transform", "id": "step", "config": {"expr": "1"}}],
        }
    )
    assert loop_should_continue(node, iteration=1) == (False, "counted_complete")


def test_step_plan_completion_wins_on_final_cycle_budget_boundary():
    config = TickConfig(steps=(StepConfig(),), max_cycles=1)
    state = TickState(step_index=1, step_started_at=0.0, total_cycles=1)
    decision = evaluate(config, state, now=1.0)
    assert decision.action is Action.COMPLETE
    assert decision.reason == "all steps complete"
