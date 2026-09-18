"""Tier proposals derived from terminal workflow runs (req 52).

Everything here drives the REAL stores: runs are written through
`workflows.store`, trajectories are projected from journal events written by the real
`Journal`, and proposals land in the real learning queue. A hand-built dict fixture would
let the analysis drift from the event stream it reads.

The three cases the requirement names each get an end-to-end test — a low-variance
successful agentic def is proposed for distillation, a repeatedly failing deterministic
def for promotion, and a high-variance or mixed record yields nothing — plus the dedupe
property and the no-model-dependency claim, which is asserted twice: over the module's own
import statements, and over its whole transitive import graph in a fresh interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gideon.automation.workflows import journal as journal_mod
from gideon.automation.workflows import store, tier_proposals
from gideon.automation.workflows.models import (
    Failure,
    FailureClass,
    GateKind,
    InstanceState,
    Node,
    NodeKind,
    RunStatus,
    WorkflowDef,
    WorkflowRun,
)
from gideon.automation.workflows.tier_proposals import (
    Action,
    Outcome,
    RunTrajectory,
    Tier,
    analyze,
    dedupe,
    derive_tier,
)

_SRC = str(Path(__file__).resolve().parents[2] / "runtime")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))


def _agentic_spec(name: str) -> dict[str, Any]:
    root = Node(
        kind=NodeKind.SEQUENCE,
        id="root",
        children=[
            Node(kind=NodeKind.ACTION, id="fetch", config={"tool": "read_file"}),
            Node(kind=NodeKind.INFER, id="decide", config={"prompt": "pick one"}),
            Node(kind=NodeKind.ACTION, id="write", config={"tool": "write_file"}),
        ],
    )
    return WorkflowDef(name=name, root=root, tags=["deterministic"]).to_dict()


def _deterministic_spec(name: str) -> dict[str, Any]:
    root = Node(
        kind=NodeKind.SEQUENCE,
        id="root",
        children=[
            Node(kind=NodeKind.ACTION, id="fetch", config={"tool": "read_file"}),
            Node(kind=NodeKind.TRANSFORM, id="shape", config={"expr": "x"}),
            Node(
                kind=NodeKind.GATE,
                id="check",
                config={"kind": GateKind.VERIFY_COMMAND.value, "command": "true"},
            ),
        ],
    )
    return WorkflowDef(name=name, root=root, tags=["agentic"]).to_dict()


def _write_run(
    *,
    name: str,
    run_id: str,
    status: RunStatus,
    spec: dict[str, Any],
    path: list[Any],
    created_at: str,
    failed_node: str = "",
) -> WorkflowRun:
    """One terminal run with a pinned spec and a real journal.

    A `path` entry is either a node id (used as its own instance path) or an
    `(instance_path, node_id)` pair, which is how a branch leg is recorded.
    """
    run = store.create(
        WorkflowRun(
            id=run_id,
            workflow_name=name,
            status=status,
            created_at=created_at,
        )
    )
    store.write_spec(run_id, spec)
    entry = journal_mod.Journal(run_id)
    for step in path:
        instance_path, node_id = step if isinstance(step, tuple) else (step, step)
        entry.step_completed(
            instance_path,
            node_id,
            epoch=0,
            cache_key=f"{run_id}:{instance_path}",
            state=InstanceState.DONE,
        )
    if failed_node:
        entry.step_failed(
            failed_node,
            failed_node,
            epoch=0,
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain="the fixed path did not fit the input",
            ),
            retries_exhausted=True,
        )
    return run


def _stamp(index: int) -> str:
    return f"2026-01-{index + 1:02d}T00:00:00Z"


def _pending(kind: str = "") -> list[Any]:
    from gideon.cognition.learning import proposals as learning_proposals

    return learning_proposals.list_pending(
        kind or learning_proposals.Kind.TIER_MIGRATION.value
    )


class TestTierFromStructure:
    def test_a_model_node_anywhere_makes_a_def_agentic(self) -> None:
        """An `infer` buried in a branch case still spends a model call — and the walk has
        to reach it, which is why the traversal is `models.walk`."""
        root = Node(
            kind=NodeKind.BRANCH,
            id="route",
            cases={
                "fast": Node(kind=NodeKind.ACTION, id="fast", config={"tool": "noop"}),
                "slow": Node(kind=NodeKind.INFER, id="slow", config={"prompt": "?"}),
            },
        )
        assert derive_tier(root) is Tier.AGENTIC

    def test_a_judge_gate_is_a_model_call_wearing_a_gate_kind(self) -> None:
        root = Node(
            kind=NodeKind.SEQUENCE,
            id="root",
            children=[
                Node(
                    kind=NodeKind.GATE,
                    id="judge",
                    config={"kind": GateKind.JUDGE.value, "prompt": "is it done?"},
                )
            ],
        )
        assert derive_tier(root) is Tier.AGENTIC

    def test_a_verify_gate_and_actions_are_deterministic(self) -> None:
        assert (
            tier_proposals.tier_of_spec(_deterministic_spec("fixed"))
            is Tier.DETERMINISTIC
        )

    def test_the_tier_ignores_a_stored_label_that_contradicts_the_structure(
        self,
    ) -> None:
        """The fixtures carry a `tags` label that says the opposite of their shape. The
        tier is read off the node tree, so the label cannot move it."""
        agentic = _agentic_spec("labelled")
        deterministic = _deterministic_spec("labelled")
        assert agentic["tags"] == ["deterministic"]
        assert deterministic["tags"] == ["agentic"]
        assert tier_proposals.tier_of_spec(agentic) is Tier.AGENTIC
        assert tier_proposals.tier_of_spec(deterministic) is Tier.DETERMINISTIC

    def test_an_unreadable_spec_has_no_tier_rather_than_a_default(self) -> None:
        assert tier_proposals.tier_of_spec(None) is None
        assert tier_proposals.tier_of_spec({"root": "not a node"}) is None


class TestDistillation:
    def test_a_low_variance_successful_agentic_def_is_proposed_for_distillation(
        self,
    ) -> None:
        spec = _agentic_spec("nightly-digest")
        for index in range(6):
            _write_run(
                name="nightly-digest",
                run_id=f"dist{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        proposal = tier_proposals.review_workflow("nightly-digest")
        assert proposal is not None
        assert proposal.action is Action.DISTILL
        assert (proposal.from_tier, proposal.to_tier) == (
            Tier.AGENTIC,
            Tier.DETERMINISTIC,
        )
        assert proposal.runs == 6 and proposal.successes == 6
        assert proposal.variance.score == 0.0

        filed = _pending()
        assert len(filed) == 1
        assert filed[0].target == proposal.dedupe_key
        assert "nightly-digest" in filed[0].title

    def test_a_cancelled_run_is_undecided_rather_than_a_failure(self) -> None:
        """A user aborting a run says nothing about the workflow's tier, so it neither
        counts as a success nor blocks the distillation the other runs justify."""
        spec = _agentic_spec("cancel-mix")
        for index in range(6):
            _write_run(
                name="cancel-mix",
                run_id=f"cm{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        _write_run(
            name="cancel-mix",
            run_id="cm-cancelled",
            status=RunStatus.CANCELLED,
            spec=spec,
            path=["fetch"],
            created_at=_stamp(7),
        )
        trajectories = tier_proposals.collect("cancel-mix")
        assert [t.outcome for t in trajectories].count(Outcome.UNDECIDED) == 1
        proposal = analyze("cancel-mix", trajectories)
        assert proposal is not None and proposal.runs == 6

    def test_a_still_running_run_is_not_evidence(self) -> None:
        spec = _agentic_spec("live")
        for index in range(6):
            _write_run(
                name="live",
                run_id=f"lv{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        _write_run(
            name="live",
            run_id="lv-running",
            status=RunStatus.RUNNING,
            spec=spec,
            path=["fetch"],
            created_at=_stamp(8),
        )
        assert [t.run_id for t in tier_proposals.collect("live")] == [
            f"lv{index}" for index in range(6)
        ]


class TestPromotion:
    def test_a_repeatedly_failing_deterministic_def_is_proposed_for_promotion(
        self,
    ) -> None:
        spec = _deterministic_spec("import-rows")
        for index in range(6):
            failing = index >= 2
            _write_run(
                name="import-rows",
                run_id=f"prom{index}",
                status=RunStatus.FAILED if failing else RunStatus.COMPLETE,
                spec=spec,
                path=["fetch"] if failing else ["fetch", "shape", "check"],
                created_at=_stamp(index),
                failed_node="shape" if failing else "",
            )
        proposal = tier_proposals.review_workflow("import-rows")
        assert proposal is not None
        assert proposal.action is Action.PROMOTE
        assert (proposal.from_tier, proposal.to_tier) == (
            Tier.DETERMINISTIC,
            Tier.AGENTIC,
        )
        assert proposal.failures == 4 and proposal.successes == 2
        assert set(proposal.evidence_run_ids) == {f"prom{i}" for i in range(2, 6)}

        filed = _pending()
        assert len(filed) == 1
        assert filed[0].target == proposal.dedupe_key

    def test_an_escalated_run_counts_as_a_failure(self) -> None:
        """An escalation is the run saying it could not finish on its own — which is the
        claim a promotion is made on."""
        spec = _deterministic_spec("escalates")
        for index in range(5):
            escalated = index >= 2
            _write_run(
                name="escalates",
                run_id=f"esc{index}",
                status=(RunStatus.ESCALATED if escalated else RunStatus.COMPLETE),
                spec=spec,
                path=["fetch"] if escalated else ["fetch", "shape", "check"],
                created_at=_stamp(index),
            )
        proposal = analyze("escalates", tier_proposals.collect("escalates"))
        assert proposal is not None and proposal.action is Action.PROMOTE
        assert proposal.failures == 3

    def test_two_failures_are_not_repeated_failure(self) -> None:
        """ "Repeatedly failing" is a count, and the sample gate is what stops a
        two-failure def from being promoted on no evidence."""
        spec = _deterministic_spec("young")
        for index in range(5):
            failing = index >= 3
            _write_run(
                name="young",
                run_id=f"yg{index}",
                status=RunStatus.FAILED if failing else RunStatus.COMPLETE,
                spec=spec,
                path=["fetch"] if failing else ["fetch", "shape", "check"],
                created_at=_stamp(index),
                failed_node="shape" if failing else "",
            )
        assert analyze("young", tier_proposals.collect("young")) is None


class TestNoProposal:
    def test_a_high_variance_successful_agentic_def_is_left_alone(self) -> None:
        """Every run succeeded, but each took its own path — there is no fixed sequence to
        distil it into."""
        spec = _agentic_spec("research")
        paths = [
            ["fetch", "decide", "write"],
            ["fetch", "decide", "decide", "write"],
            ["fetch", "decide"],
            ["fetch", "decide", "decide", "decide", "write"],
            ["decide", "write"],
            ["fetch", "write"],
        ]
        for index, path in enumerate(paths):
            _write_run(
                name="research",
                run_id=f"hv{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=path,
                created_at=_stamp(index),
            )
        trajectories = tier_proposals.collect("research")
        assert tier_proposals.variance_of(trajectories).score > (
            tier_proposals.LOW_VARIANCE_MAX
        )
        assert tier_proposals.review_workflow("research") is None
        assert _pending() == []

    def test_a_branch_that_routes_differently_is_variance_even_at_equal_length(
        self,
    ) -> None:
        """Same node count, same node ids — different case legs. The branch measure is
        what catches it."""
        spec = _agentic_spec("router")
        for index in range(6):
            leg = "a" if index % 2 == 0 else "b"
            _write_run(
                name="router",
                run_id=f"br{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", (f"root.cases[{leg}].decide", "decide"), "write"],
                created_at=_stamp(index),
            )
        trajectories = tier_proposals.collect("router")
        variance = tier_proposals.variance_of(trajectories)
        assert variance.path == 0.0 and variance.length == 0.0
        assert variance.branch > tier_proposals.LOW_VARIANCE_MAX
        assert analyze("router", trajectories) is None

    def test_a_mixed_agentic_record_is_neither_distilled_nor_promoted(self) -> None:
        """Half the runs failed: the success rate is too low to distil and the tier is
        wrong for a promotion. Silence is the honest answer."""
        spec = _agentic_spec("flaky")
        for index in range(6):
            failing = index % 2 == 0
            _write_run(
                name="flaky",
                run_id=f"mx{index}",
                status=RunStatus.FAILED if failing else RunStatus.COMPLETE,
                spec=spec,
                path=["fetch"] if failing else ["fetch", "decide", "write"],
                created_at=_stamp(index),
                failed_node="decide" if failing else "",
            )
        assert tier_proposals.review_workflow("flaky") is None
        assert _pending() == []

    def test_a_thin_record_proposes_nothing(self) -> None:
        spec = _agentic_spec("new-def")
        for index in range(3):
            _write_run(
                name="new-def",
                run_id=f"th{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        assert tier_proposals.review_workflow("new-def") is None

    def test_runs_from_a_superseded_structure_are_not_evidence(self) -> None:
        """The def was edited from deterministic to agentic. Its older runs measured a
        workflow that no longer exists, so they cannot carry the new one past the sample
        gate."""
        for index in range(5):
            _write_run(
                name="edited",
                run_id=f"old{index}",
                status=RunStatus.COMPLETE,
                spec=_deterministic_spec("edited"),
                path=["fetch", "shape", "check"],
                created_at=_stamp(index),
            )
        for index in range(2):
            _write_run(
                name="edited",
                run_id=f"new{index}",
                status=RunStatus.COMPLETE,
                spec=_agentic_spec("edited"),
                path=["fetch", "decide", "write"],
                created_at=_stamp(index + 6),
            )
        trajectories = tier_proposals.collect("edited")
        assert len(trajectories) == 7
        assert analyze("edited", trajectories) is None


class TestDedupe:
    def _trajectories(self, tier: Tier) -> list[RunTrajectory]:
        return [
            RunTrajectory(
                run_id=f"r{index}",
                tier=tier,
                outcome=Outcome.SUCCESS,
                signature="sig",
                steps=3,
            )
            for index in range(6)
        ]

    def test_equivalent_proposals_collapse_to_one(self) -> None:
        first = analyze("same", self._trajectories(Tier.AGENTIC))
        second = analyze("same", self._trajectories(Tier.AGENTIC))
        assert first is not None and second is not None
        assert first.dedupe_key == second.dedupe_key
        assert len(dedupe([first, second])) == 1

    def test_the_dedupe_key_does_not_move_with_the_measurement(self) -> None:
        """A key carrying the run count would file a fresh proposal every run."""
        six = analyze("same", self._trajectories(Tier.AGENTIC))
        nine = analyze(
            "same",
            self._trajectories(Tier.AGENTIC) + self._trajectories(Tier.AGENTIC)[:3],
        )
        assert six is not None and nine is not None
        assert six.runs != nine.runs
        assert six.dedupe_key == nine.dedupe_key

    def test_different_workflows_and_directions_stay_distinct(self) -> None:
        agentic = analyze("one", self._trajectories(Tier.AGENTIC))
        other = analyze("two", self._trajectories(Tier.AGENTIC))
        assert agentic is not None and other is not None
        assert len(dedupe([agentic, other])) == 2

    def test_refiling_the_same_finding_does_not_duplicate_in_the_queue(self) -> None:
        """The queue's `target`-keyed resolve cascade is what owns deduplication once a
        proposal is filed, and the dedupe key is the target that reaches it."""
        spec = _agentic_spec("repeat")
        for index in range(6):
            _write_run(
                name="repeat",
                run_id=f"rp{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        assert tier_proposals.review_workflow("repeat") is not None
        first = _pending()
        assert len(first) == 1
        assert tier_proposals.review_workflow("repeat") is not None
        again = _pending()
        assert len(again) == 1
        assert again[0].id == first[0].id
        assert again[0].reinforcements > first[0].reinforcements

    def test_a_newer_measurement_updates_the_row_instead_of_adding_one(self) -> None:
        """A later run changes the counts, so the body differs while the finding does not.
        The target is what keeps it one row — the queue supersedes rather than duplicates.
        """
        spec = _agentic_spec("growing")
        for index in range(6):
            _write_run(
                name="growing",
                run_id=f"gr{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        first = tier_proposals.review_workflow("growing")
        assert first is not None and first.runs == 6
        _write_run(
            name="growing",
            run_id="gr6",
            status=RunStatus.COMPLETE,
            spec=spec,
            path=["fetch", "decide", "write"],
            created_at=_stamp(6),
        )
        second = tier_proposals.review_workflow("growing")
        assert second is not None and second.runs == 7
        assert second.dedupe_key == first.dedupe_key
        filed = _pending()
        assert len(filed) == 1
        assert filed[0].target == first.dedupe_key

    def test_a_rejected_proposal_is_not_refiled(self) -> None:
        from gideon.cognition.learning import proposals as learning_proposals

        spec = _agentic_spec("rejected")
        for index in range(6):
            _write_run(
                name="rejected",
                run_id=f"rj{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        assert tier_proposals.review_workflow("rejected") is not None
        filed = _pending()
        assert len(filed) == 1
        assert learning_proposals.reject(filed[0].id)
        tier_proposals.review_workflow("rejected")
        assert _pending() == []


class TestNoModelDependency:
    def test_the_module_imports_nothing_that_can_call_a_model(self) -> None:
        import ast

        source = Path(tier_proposals.__file__).read_text(encoding="utf-8")
        imported: list[str] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        forbidden = ("llm", "anthropic", "openai", "completion", "embed", "inference")
        offenders = [
            name
            for name in imported
            if any(token in name.lower() for token in forbidden)
        ]
        assert not offenders, f"tier analysis must not import a model seam: {offenders}"
        assert "one_shot_completion" not in source

    def test_the_transitive_import_graph_has_no_model_helper(self) -> None:
        """Asserted in a FRESH interpreter, because another test in this process may have
        imported the helper already — which would make an in-process check vacuous."""
        probe = (
            "import sys;"
            "import gideon.automation.workflows.tier_proposals;"
            "print('gideon.integrations.llm_helpers' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            env={**os.environ, "PYTHONPATH": _SRC},
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False"

    def test_the_whole_analysis_runs_with_the_model_seam_poisoned(
        self, monkeypatch
    ) -> None:
        from gideon.integrations import llm_helpers

        def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("tier analysis must not call a model")

        monkeypatch.setattr(llm_helpers, "one_shot_completion", explode)
        spec = _agentic_spec("free")
        for index in range(6):
            _write_run(
                name="free",
                run_id=f"fr{index}",
                status=RunStatus.COMPLETE,
                spec=spec,
                path=["fetch", "decide", "write"],
                created_at=_stamp(index),
            )
        assert tier_proposals.review_workflow("free") is not None


class TestControllerWiring:
    """The engine's single terminal writer is the entry point, so the proposal appears
    without anyone asking for it."""

    async def _drive(self, spec: dict[str, Any]) -> str:
        from gideon.automation.workflows.controller import EngineServices, RunController

        async def echo(
            prompt: str, *, use_case: str = "background", output_type: Any = None
        ) -> str:
            return f"[{prompt}]"

        run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices(completion=echo))
        status = await controller.run_to_completion(timeout=20)
        assert status is RunStatus.COMPLETE, f"run did not complete: {status}"
        return run.id

    async def test_a_terminal_run_files_the_proposal_its_history_justifies(
        self,
    ) -> None:
        spec = {
            "name": "echo-pipeline",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "seed"}},
                    {
                        "kind": "infer",
                        "id": "b",
                        "config": {"prompt": "use {{nodes.a.output}}"},
                    },
                ],
            },
        }
        for index in range(tier_proposals.MIN_DECIDED_RUNS):
            await self._drive(spec)
            filed = _pending()
            expected = 1 if index + 1 >= tier_proposals.MIN_DECIDED_RUNS else 0
            assert len(filed) == expected, f"after {index + 1} run(s): {filed}"
        assert _pending()[0].target == (
            "workflow-tier:echo-pipeline:agentic->deterministic"
        )
