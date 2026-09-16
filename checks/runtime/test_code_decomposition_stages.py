"""decomposition_to_stage_plan must keep EVERY approved phase as its own gating stage,
even when several share a canonical stage id. A code decomposition routinely emits 5
"implementation" phases (P1..P5) with distinct titles; the engine keys a phase by its
stage id (sdlc.phase_key), so identical ids collapse to one key — active_stage_index
never advances past the first and the worker grinds P1 to the cycle budget (observed live
on a TicTacToe build). The projection disambiguates repeats: implementation,
implementation-2, … — distinct keys, recognizably the same stage, verification untouched.
"""

from __future__ import annotations

from gideon.automation.loop import kinds
from gideon.automation.loop.code_plan_briefs import decomposition_to_stage_plan


def test_repeated_stage_ids_get_distinct_keys():
    art = {
        "phases": [
            {"stage": "implementation", "title": "P1 Scaffold"},
            {"stage": "implementation", "title": "P2 Engine"},
            {"stage": "implementation", "title": "P3 AI"},
            {"stage": "implementation", "title": "P4 UI"},
            {"stage": "verification", "title": "P5 CI gate"},
        ]
    }
    out = decomposition_to_stage_plan(art)
    stages = [p["stage"] for p in out]
    assert stages == [
        "implementation",
        "implementation-2",
        "implementation-3",
        "implementation-4",
        "verification",
    ]
    kinds.ensure_loaded()
    s = kinds.get("code")
    keys = [s.phase_key(p) for p in out]
    assert len(set(keys)) == len(keys), "every phase must have a distinct stage key"
    assert "verification" in keys

    from gideon.automation.loop.loop import Loop

    loop = Loop(
        id="x",
        name="n",
        kind="code",
        task="build it",
        plan=out,
        phase_status={"implementation": "done"},
    )
    assert s.active_stage_index(loop) == 1


def test_single_stage_each_is_unchanged():
    art = {
        "phases": [
            {"stage": "decomposition", "title": "Plan"},
            {"stage": "implementation", "title": "Build"},
            {"stage": "verification", "title": "Verify"},
        ]
    }
    out = decomposition_to_stage_plan(art)
    assert [p["stage"] for p in out] == [
        "decomposition",
        "implementation",
        "verification",
    ]


def test_gate_commands_lifted_from_test_strategy_ci_gate():
    """A code loop's verification stage gates on the LLM judge ONLY unless the
    deterministic verify/test commands are populated — but the walkthrough never set
    them, so a fully-tested engine stage (coverage 100%, never-loses green) stuck for
    cycles because a conservative judge won't take test-execution claims on transcript
    alone (observed live, Run 22). The planner authors the exact commands in the
    test_strategy `ci_gate`; project_to_spec must lift them into kind_config so the
    gate runs the build chain + test runner as ground truth."""
    from gideon.automation.loop.code_plan_briefs import gate_commands_from_test_strategy

    art = {
        "ci_gate": [
            {"order": 1, "step": "typecheck", "cmd": "tsc --noEmit"},
            {"order": 2, "step": "lint", "cmd": "eslint . --max-warnings=0"},
            {"order": 3, "step": "coverage", "cmd": "vitest run --coverage"},
            {"order": 4, "step": "build", "cmd": "vite build"},
        ]
    }
    verify, test = gate_commands_from_test_strategy(art)
    assert test == "vitest run --coverage"
    assert verify == "tsc --noEmit && eslint . --max-warnings=0 && vite build"

    assert gate_commands_from_test_strategy({}) == ("", "")
    assert gate_commands_from_test_strategy({"ci_gate": "nope"}) == ("", "")
    only_test = {"ci_gate": [{"order": 1, "step": "test", "cmd": "npm test"}]}
    assert gate_commands_from_test_strategy(only_test) == ("", "npm test")
