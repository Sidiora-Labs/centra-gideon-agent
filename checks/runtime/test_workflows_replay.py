"""`workflow replay <run_id>` — the recorded-response replay and trajectory diff (PP-6).

The atom's bar, verbatim: replay a completed multi-node run to a BYTE-IDENTICAL trajectory, and an
edited prompt diverges at EXACTLY the edited node and nowhere earlier. Divergence is a first-class
outcome, not a failure — these tests assert the verb reports WHERE, and never that it raises.

Three falsification hooks live here as real assertions, so the mutations in the PR description have
something to turn red:

* `test_completed_pipeline_replays_byte_identical` — perturb a recorded response and this reds
  (the replay actually compares, it does not rubber-stamp). The permanent
  `test_perturbed_response_diverges_at_its_consumer` proves the same mechanism forwards.
* `test_edited_prompt_diverges_at_exactly_that_node` — make the diff always report node 0 and this
  reds (divergence localizes to the edited node, not the first).
* `test_wait_run_replays_byte_identical_through_the_clock_seam` — read the wall clock instead of the
  recorded clock in the replayed path and this reds (the clock seam is load-bearing).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from gideon.assurance.ledger import CLOCK_READ
from gideon.automation.workflows import journal as journal_mod
from gideon.automation.workflows import replay as replay_mod
from gideon.automation.workflows import store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Own config dir per test — replay reads the run store, and GIDEON_HOME is unset in the
    gate, so a real home must never be touched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("gideon.automation.workflows.store.config_dir", lambda: home)
    return home


_CLOCK_BASE = 1_000_000.0


class _FastClock:
    """A wall-clock seam that advances FAST in real time, so a `wait(duration_secs=1)` resolves in
    a few milliseconds instead of a real second — and stays anchored near `_CLOCK_BASE`, nowhere
    near a real timestamp."""

    def __init__(self, base: float = _CLOCK_BASE, speed: float = 10_000.0) -> None:
        self._base = base
        self._start = time.monotonic()
        self._speed = speed

    def __call__(self) -> float:
        return self._base + (time.monotonic() - self._start) * self._speed


async def _echo(
    prompt: str, *, use_case: str = "background", output_type: Any = None
) -> str:
    """A deterministic recorded response: echo the resolved prompt, so a node's output embeds its
    input and perturbing an upstream output visibly changes a downstream prompt."""
    return f"[{prompt}]"


def _pipeline_spec() -> dict[str, Any]:
    """Three infer nodes in a binding chain: b reads a's output, c reads b's. A prompt edit or a
    perturbed output at one link shows up at the NEXT link's resolved prompt."""
    return {
        "name": "replay-pipeline",
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
                {
                    "kind": "infer",
                    "id": "c",
                    "config": {"prompt": "use {{nodes.b.output}}"},
                },
            ],
        },
    }


def _wait_spec() -> dict[str, Any]:
    """A run whose middle node is a `wait`, so `_wake_due_nodes` reads the clock and journals the
    envelope. `duration_secs` must be >= 1: the engine floors it to an int, and a fractional value
    resolves instantly with no WAITING state and no clock read."""
    return {
        "name": "replay-wait",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "infer", "id": "a", "config": {"prompt": "seed"}},
                {"kind": "wait", "id": "w", "config": {"duration_secs": 1}},
                {"kind": "infer", "id": "c", "config": {"prompt": "done"}},
            ],
        },
    }


async def _drive(spec: dict[str, Any], *, clock: Any = None) -> str:
    run = store.create(WorkflowRun(id="", workflow_name=spec.get("name", "wf")))
    store.write_spec(run.id, spec)
    services = EngineServices(completion=_echo, clock=clock)
    controller = RunController(run, spec, services=services)
    status = await controller.run_to_completion(timeout=20)
    assert status.value == "complete", f"run did not complete: {status}"
    return run.id


async def test_completed_pipeline_replays_byte_identical() -> None:
    """Replaying an unedited completed run reproduces its trajectory exactly.

    FALSIFICATION 1: perturb a recorded response (see the sibling test) and this assertion reds —
    the replay compares real re-resolved prompts, it does not rubber-stamp.
    """
    run_id = await _drive(_pipeline_spec())

    result = replay_mod.replay_run(run_id)

    assert result.identical, (
        None if result.first_divergence is None else result.first_divergence.describe()
    )
    assert result.first_divergence is None
    assert [s.node_id for s in result.original] == ["a", "b", "c"]
    assert result.original == result.replayed
    prompt_hashes = {s.prompt_hash for s in result.original}
    assert (
        len(prompt_hashes) == 3
    ), "the three nodes must have distinct resolved prompts"


async def test_perturbed_response_diverges_at_its_consumer() -> None:
    """Perturbing a recorded response moves the FIRST consumer that binds it, not the node itself.

    This is falsification 1 as a permanent, forward test: the replay reads the perturbed output of
    `a`, re-resolves `b`'s prompt from it, and finds it differs from what `b` actually ran on.
    """
    run_id = await _drive(_pipeline_spec())
    clean = replay_mod.replay_run(run_id)
    assert clean.identical

    store.write_output(run_id, "root.children[0]", "PERTURBED")

    result = replay_mod.replay_run(run_id)
    assert not result.identical
    div = result.first_divergence
    assert div is not None
    assert (
        div.node_id == "b"
    ), f"expected the consumer b to diverge, got {div.node_id!r}"
    assert div.field == "prompt_hash"
    assert div.index == 1


async def test_edited_prompt_diverges_at_exactly_that_node() -> None:
    """An edited prompt diverges at EXACTLY the edited node and nowhere earlier.

    FALSIFICATION 2: make `_diff` always report index 0 and this reds — the divergence must
    localize to node b (index 1), with node a proven identical first.
    """
    run_id = await _drive(_pipeline_spec())

    edited = _pipeline_spec()
    edited["root"]["children"][1]["config"]["prompt"] = "USE {{nodes.a.output}}"
    store.write_spec(run_id, edited)

    result = replay_mod.replay_run(run_id)

    assert not result.identical
    div = result.first_divergence
    assert div is not None
    assert div.index == 1
    assert div.node_id == "b"
    assert div.field == "prompt_hash"
    assert result.original[0] == result.replayed[0]
    assert result.original[0].node_id == "a"


async def test_wait_run_replays_byte_identical_through_the_clock_seam() -> None:
    """A run with a `wait` replays byte-identical — including the recorded clock the wait resolved
    against.

    FALSIFICATION 3: read `time.time()` instead of the recorded clock in the replayed path and this
    reds — the recorded value is anchored near _CLOCK_BASE (~1e6), a live clock is ~1.7e9.
    """
    run_id = await _drive(_wait_spec(), clock=_FastClock())

    clock_events = [
        e for e in journal_mod.ledger(run_id) if e.get("kind") == CLOCK_READ
    ]
    assert (
        clock_events
    ), "the wait run journalled no clock_read — the seam recorded nothing"
    wait_clock = clock_events[0]["clock"]
    assert (
        _CLOCK_BASE <= wait_clock < _CLOCK_BASE + 1_000_000
    ), "the recorded clock read the injected seam, not a real wall clock"

    result = replay_mod.replay_run(run_id)

    assert result.identical, (
        None if result.first_divergence is None else result.first_divergence.describe()
    )
    steps = {s.node_id: s for s in result.replayed}
    assert steps["w"].clock == f"{wait_clock:.6f}"
    assert steps["a"].clock == ""
    assert steps["c"].clock == ""
    assert result.original == result.replayed


async def test_replay_of_unknown_run_raises_not_diverges() -> None:
    """A run that cannot be replayed at all is an ERROR, distinct from a divergence."""
    with pytest.raises(replay_mod.ReplayError):
        replay_mod.replay_run("does-not-exist")


async def test_cli_workflow_replay_reports_and_exits_clean(capsys) -> None:
    """The CLI verb prints the outcome and returns 0 for BOTH identical and divergent replays —
    divergence is not a failure exit."""
    from types import SimpleNamespace

    from gideon.interfaces.cli.main import _workflow_cmd

    run_id = await _drive(_pipeline_spec())

    args = SimpleNamespace(workflow_command="replay", run_id=run_id, json=False)
    assert _workflow_cmd(args) == 0
    assert "byte-identical" in capsys.readouterr().out

    edited = _pipeline_spec()
    edited["root"]["children"][1]["config"]["prompt"] = "USE {{nodes.a.output}}"
    store.write_spec(run_id, edited)
    args = SimpleNamespace(workflow_command="replay", run_id=run_id, json=False)
    assert _workflow_cmd(args) == 0
    assert "DIVERGED" in capsys.readouterr().out
