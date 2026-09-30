"""Concurrent planning loops keep scratch files in their own durable directories."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from gideon.automation.loop import files as loop_files
from gideon.automation.loop import kinds
from gideon.automation.loop.loop import Loop
from gideon.automation.loop.plan_walkthrough import (
    ARTIFACT_SENTINEL,
    STEPS_SENTINEL,
    _planner_brief_for_loop,
    _planner_session,
)
from gideon.cognition.planning import runner
from gideon.cognition.planning.session import PlanSession, PlanStep
from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.mark.asyncio
async def test_concurrent_loops_keep_planner_artifacts_in_own_directories(
    tmp_path, monkeypatch
):
    config = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config.mkdir()
    workspace.mkdir()
    (workspace / "project-input.md").write_text("the planner can inspect this")
    (workspace / STEPS_SENTINEL).write_text("user-owned steps")
    (workspace / ARTIFACT_SENTINEL).write_text("user-owned artifact")
    monkeypatch.setattr(loop_files, "config_dir", lambda: config)

    loops = [
        Loop(
            id="0a1b2c3d",
            name="first",
            kind="code",
            task="first plan",
            workspace_dir=str(workspace),
        ),
        Loop(
            id="1a2b3c4d",
            name="second",
            kind="code",
            task="second plan",
            workspace_dir=str(workspace),
        ),
    ]
    state = ConsoleState(None, time.time())
    outputs: dict[str, Path] = {}
    providers: dict[str, NativeBuiltinToolProvider] = {}

    for loop in loops:
        directory = loop_files.loop_dir(loop.id)
        assert directory is not None
        outputs[loop.id] = directory
        session = _planner_session(state, loop, "planner", str(directory))
        assert session.workspace_dir == str(directory)
        assert session._extra_tool_roots == [str(workspace.resolve())]
        providers[loop.id] = NativeBuiltinToolProvider(
            cwd=Path(session.workspace_dir),
            extra_roots=[Path(root) for root in session._extra_tool_roots],
            agent="planner",
            session_key=session.key,
        )

    workspace_read = await providers[loops[0].id].invoke(
        "read_file", {"path": str(workspace / "project-input.md")}
    )
    assert workspace_read.success
    assert "the planner can inspect this" in str(workspace_read.output)
    cross_loop_read = await providers[loops[0].id].invoke(
        "read_file", {"path": str(outputs[loops[1].id] / "private.md")}
    )
    assert not cross_loop_read.success

    # The code, goal, design, and research brief builders all direct planner output
    # to the same exact per-loop files path, independent of kind-specific prompts.
    kinds.ensure_loaded()
    step = PlanStep(
        id="step-0", kind="problem_framing", title="Scope", objective="Frame it"
    )
    for kind in ("code", "goal", "design", "research"):
        walkthrough = kinds.get(kind).walkthrough()
        brief = walkthrough.build_step_brief(
            "keep planning state isolated",
            step,
            approved=[],
            workspace_dir=str(workspace),
        )
        scoped = _planner_brief_for_loop(
            brief,
            ARTIFACT_SENTINEL,
            outputs[loops[0].id] / ARTIFACT_SENTINEL,
        )
        assert str(outputs[loops[0].id] / ARTIFACT_SENTINEL) in scoped
        assert f"`{ARTIFACT_SENTINEL}` in your current directory" not in scoped

    for kind in ("code", "design"):
        walkthrough = kinds.get(kind).walkthrough()
        brief = walkthrough.build_design_brief(
            "keep planning state isolated", str(workspace)
        )
        scoped = _planner_brief_for_loop(
            brief,
            STEPS_SENTINEL,
            outputs[loops[0].id] / STEPS_SENTINEL,
        )
        assert str(outputs[loops[0].id] / STEPS_SENTINEL) in scoped
        assert f"`{STEPS_SENTINEL}` in your current directory" not in scoped

    async def write_outputs(loop: Loop) -> None:
        provider = providers[loop.id]
        directory = outputs[loop.id]
        for sentinel, content in (
            (STEPS_SENTINEL, f"steps for {loop.id}"),
            (ARTIFACT_SENTINEL, f"artifact for {loop.id}"),
        ):
            result = await provider.invoke(
                "write_file",
                {"path": str(directory / sentinel), "content": content},
            )
            assert result.success, result.error

    await asyncio.gather(*(write_outputs(loop) for loop in loops))

    for loop in loops:
        directory = outputs[loop.id]
        for sentinel, expected in (
            (STEPS_SENTINEL, f"steps for {loop.id}"),
            (ARTIFACT_SENTINEL, f"artifact for {loop.id}"),
        ):
            assert (
                runner.read_sentinel(str(directory), str(directory), sentinel)
                == expected
            )

        session = PlanSession(
            project_id=loop.id,
            steps=[PlanStep(id="step-0", kind="intent", title=loop.name)],
        )
        loop_files.write_plan_session(session)
        reopened = loop_files.read_plan_session(loop.id)
        assert reopened is not None
        assert reopened.steps[0].title == loop.name
        runner.clear_sentinels(
            str(directory), str(directory), [STEPS_SENTINEL, ARTIFACT_SENTINEL]
        )
        assert not (directory / STEPS_SENTINEL).exists()
        assert not (directory / ARTIFACT_SENTINEL).exists()

    assert (workspace / STEPS_SENTINEL).read_text() == "user-owned steps"
    assert (workspace / ARTIFACT_SENTINEL).read_text() == "user-owned artifact"
    assert outputs[loops[0].id] != outputs[loops[1].id]
