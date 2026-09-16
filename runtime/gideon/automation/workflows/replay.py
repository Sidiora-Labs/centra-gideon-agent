"""Reconstruct workflow trajectories from recorded outcomes and clock observations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from gideon.assurance.ledger import CLOCK_READ, hash_value
from gideon.automation.workflows import execution_hints
from gideon.automation.workflows import journal as journal_mod
from gideon.automation.workflows import store
from gideon.automation.workflows.bindings import BindingContext
from gideon.automation.workflows.engine import resolve_config
from gideon.automation.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeKind,
    walk,
)
from gideon.automation.workflows.tick import frontier

_MAX_TICKS_PER_NODE = 8


@dataclass(frozen=True)
class TrajectoryStep:
    """One node's place in a run's decision path.

    Equality IS the diff: two steps are the same iff every field matches. The discriminating
    field is `prompt_hash` — the resolved prompt is a node's real input, and comparing the
    RE-RESOLVED prompt (replay) against the RECORDED one (original) is what catches a template
    edit at the node it edited and a perturbed upstream response at that node's first consumer.
    `clock` is non-empty only for a node the wall clock resolved (a `wait`/`gate`), and carries
    the recorded value so a replay that read a live clock instead diverges here.
    """

    path: str
    node_id: str
    kind: str
    state: str
    prompt_hash: str
    output_ref: str
    clock: str = ""


@dataclass
class Divergence:
    """The first node whose replayed step differs from the original, and how."""

    index: int
    path: str
    node_id: str
    field: str
    original: Any
    replayed: Any

    def describe(self) -> str:
        subject = repr(self.node_id or self.path)
        position = f"node {subject} (step {self.index})"
        return " ".join(
            (
                position,
                f"diverged on {self.field}:",
                f"recorded {self.original!r}, replayed {self.replayed!r}",
            )
        )


@dataclass
class ReplayResult:
    """The outcome of a replay. `identical` and `first_divergence` are the two things a caller
    reads; the two trajectories are kept for a detailed diff or a UI."""

    run_id: str
    identical: bool
    original: list[TrajectoryStep] = field(default_factory=list)
    replayed: list[TrajectoryStep] = field(default_factory=list)
    first_divergence: Divergence | None = None


@dataclass(frozen=True)
class _Recorded:
    """One node's recorded terminal outcome, as the ledger holds it."""

    state: str
    output_ref: str
    prompt_ref: str


class RecordedResponses:
    """The recorded-response provider — a node's OWN output, keyed off `output_ref` (PP-4).

    A completed run journaled a terminal event per executed leaf, each carrying the `output_ref`
    the run's output was spilled to. Replay hands that same output back so downstream routing and
    downstream prompts see what the run saw — the run does not re-call any model. Keyed by output
    ref rather than recomputed, because the whole point is to reproduce the recorded response, not
    a fresh one.
    """

    def __init__(self, run_id: str, by_path: dict[str, _Recorded]) -> None:
        self._run_id = run_id
        self._by_path = by_path

    def has(self, path: str) -> bool:
        return path in self._by_path

    def state(self, path: str) -> InstanceState:
        record = self._by_path.get(path)
        if record is not None:
            try:
                return InstanceState(record.state)
            except ValueError:
                pass
        return InstanceState.FAILED

    def output_ref(self, path: str) -> str:
        rec = self._by_path.get(path)
        return rec.output_ref if rec else ""

    def output(self, path: str) -> Any:
        record = self._by_path.get(path)
        if record is not None and record.output_ref:
            return _read_ref(self._run_id, record.output_ref)
        return None

    def prompt_hash(self, path: str) -> str:
        record = self._by_path.get(path)
        body = ""
        if record and record.prompt_ref:
            body = _read_ref(self._run_id, record.prompt_ref)
        return hash_value(body if isinstance(body, str) else "")


class RecordedClock:
    """The recorded wall clock, substituted for the live one at replay (PP-6).

    Built from the run's `clock_read` envelope, keyed by the instance path the clock resolved. A
    node the run resolved against the clock gets its recorded value back; everything else gets "".
    This is the seam that makes a `wait`'s resolution reproducible — reading `time.time()` here
    instead would put a live timestamp in the trajectory that the recorded run could never match,
    which is exactly what the determinism test asserts against.
    """

    def __init__(self, by_path: dict[str, str]) -> None:
        self._by_path = by_path

    def now(self, path: str) -> str:
        return self._by_path.get(path, "")

    def has(self, path: str) -> bool:
        return path in self._by_path


def _read_ref(run_id: str, ref: str) -> Any:
    if not ref:
        return None
    root = store.run_dir(run_id).resolve()
    try:
        source = (root / ref).resolve()
        source.relative_to(root)
    except (ValueError, OSError):
        return None
    if source.is_file():
        try:
            record = json.loads(source.read_text(encoding="utf-8"))
            return record.get("output")
        except (OSError, ValueError):
            pass
    return None


_TERMINAL_KINDS = {
    journal_mod.STEP_COMPLETED: None,
    journal_mod.STEP_CACHED: None,
    journal_mod.STEP_FAILED: InstanceState.FAILED.value,
    journal_mod.STEP_SKIPPED: InstanceState.SKIPPED.value,
}


def load_recorded(
    run_id: str,
) -> tuple[RecordedResponses, RecordedClock, list[dict[str, Any]]]:
    tape = _TapeIndex()
    for record in journal_mod.ledger(run_id):
        tape.accept(record)
    return (
        RecordedResponses(run_id, tape.responses),
        RecordedClock(tape.clock),
        tape.terminal,
    )


def _fmt_clock(value: Any) -> str:
    """Render a clock value the SAME way on the recorded and replayed side, so a match is a
    byte match. The envelope rounds to 6 places; mirror that and stringify, so the trajectory
    compares a stable token rather than a float whose repr could drift."""
    if not isinstance(value, (int, float)):
        return ""
    return f"{float(value):.6f}"


def _original_trajectory(
    terminal: list[dict[str, Any]],
    responses: RecordedResponses,
    clock: RecordedClock,
    node_kind: dict[str, str],
) -> list[TrajectoryStep]:
    return list(_recorded_steps(terminal, responses, clock, node_kind))


def _redrive(
    root: Node,
    inputs: dict[str, Any],
    responses: RecordedResponses,
    clock: RecordedClock,
    *,
    single_active_feature: bool,
) -> list[TrajectoryStep]:
    driver = _ReplayDriver(root, inputs, responses, clock, single_active_feature)
    return driver.run()


def _replay_one(
    node: Node,
    path: str,
    outputs: dict[str, Any],
    inputs: dict[str, Any],
    responses: RecordedResponses,
    clock: RecordedClock,
) -> TrajectoryStep:
    binding = BindingContext(inputs=dict(inputs), node_outputs=dict(outputs))
    prompt = _resolve_prompt(node, binding)
    state = responses.state(path)
    reading = clock.now(path) if clock.has(path) else ""
    return TrajectoryStep(
        path,
        node.id,
        node.kind.value,
        state.value,
        hash_value(prompt),
        responses.output_ref(path),
        reading,
    )


def _resolve_prompt(node: Node, ctx: BindingContext) -> str:
    eligible = (NodeKind.INFER, NodeKind.ACTION, NodeKind.WAIT, NodeKind.VISUALIZE)
    if node.kind in eligible:
        configuration, rejected = resolve_config(node, ctx)
        if not rejected:
            return str(configuration.get("prompt", "") or "")
    return ""


# ── the diff ────────────────────────────────────────────────────────────────────


def _diff(
    original: list[TrajectoryStep], replayed: list[TrajectoryStep]
) -> Divergence | None:
    from itertools import zip_longest

    for position, (before, after) in enumerate(zip_longest(original, replayed)):
        if before is None:
            return Divergence(
                position, after.path, after.node_id, "extra_node", None, after.path
            )
        if after is None:
            return Divergence(
                position, before.path, before.node_id, "missing_node", before.path, None
            )
        if before == after:
            continue
        for key in _TRAJECTORY_FIELDS:
            old, new = getattr(before, key), getattr(after, key)
            if old != new:
                return Divergence(
                    position,
                    after.path or before.path,
                    after.node_id or before.node_id,
                    key,
                    old,
                    new,
                )
    return None


def replay_run(run_id: str) -> ReplayResult:
    specification = store.read_spec(run_id)
    if specification is None:
        raise ReplayError(f"run {run_id!r} has no spec to replay")
    run = store.get(run_id)
    inputs = dict(run.inputs) if run and run.inputs else {}
    responses, clock, terminal = load_recorded(run_id)
    if not terminal:
        raise ReplayError(f"run {run_id!r} has no recorded steps to replay")
    root = Node.from_dict(specification.get("root") or {"kind": "sequence"})
    node_kinds = dict((path, node.kind.value) for path, node in walk(root))
    serial = execution_hints.from_runtime_hints(
        specification.get("runtime_hints")
    ).single_active_feature
    before = _original_trajectory(terminal, responses, clock, node_kinds)
    after = _redrive(root, inputs, responses, clock, single_active_feature=serial)
    change = _diff(before, after)
    return ReplayResult(run_id, change is None, before, after, change)


class ReplayError(Exception):
    """A run cannot be replayed at all — no spec, or no recorded steps. Distinct from a
    DIVERGENCE, which is a normal, successful replay outcome."""


_TRAJECTORY_FIELDS = (
    "path",
    "node_id",
    "kind",
    "state",
    "prompt_hash",
    "output_ref",
    "clock",
)


class _TapeIndex:
    def __init__(self):
        self.responses: dict[str, _Recorded] = {}
        self.clock: dict[str, str] = {}
        self.terminal: list[dict[str, Any]] = []

    def accept(self, record: dict[str, Any]) -> None:
        kind = record.get("kind")
        if kind == CLOCK_READ:
            path = str(record.get("instance_path", "") or "")
            if path:
                self.clock[path] = _fmt_clock(record.get("clock"))
            return
        if kind not in _TERMINAL_KINDS:
            return
        path = str(record.get("instance_path", "") or "")
        if not path:
            return
        terminal_state = _TERMINAL_KINDS[kind]
        if terminal_state is None:
            terminal_state = str(record.get("state", "") or "")
        self.responses[path] = _Recorded(
            terminal_state,
            str(record.get("output_ref", "") or ""),
            str(record.get("resolved_prompt_ref", "") or ""),
        )
        self.terminal.append(record)


def _recorded_steps(events, responses, clock, kinds):
    for event in events:
        path = str(event.get("instance_path", "") or "")
        yield TrajectoryStep(
            path=path,
            node_id=str(event.get("node_id", "") or ""),
            kind=kinds.get(path, ""),
            state=responses.state(path).value,
            prompt_hash=responses.prompt_hash(path),
            output_ref=responses.output_ref(path),
            clock=clock.now(path),
        )


class _ReplayDriver:
    def __init__(self, root, inputs, responses, clock, serial):
        self.root, self.inputs = root, inputs
        self.responses, self.clock = responses, clock
        self.serial = serial
        self.nodes = dict(walk(root))
        self.states: dict[str, InstanceState] = {}
        self.outputs: dict[str, Any] = {}
        self.steps: list[TrajectoryStep] = []

    def run(self) -> list[TrajectoryStep]:
        remaining = _MAX_TICKS_PER_NODE * (len(self.nodes) + 1)
        while remaining > 0:
            remaining -= 1
            available = frontier(
                self.root,
                dict(self.states),
                outputs=dict(self.outputs),
                inputs=self.inputs,
                single_active_feature=self.serial,
            )
            if available.complete:
                break
            previous = len(self.steps)
            self.skip(available.to_skip)
            self.admit(available.ready)
            if len(self.steps) == previous:
                break
        return self.steps

    def skip(self, paths) -> None:
        for path in sorted(paths):
            if self.states.get(path) == InstanceState.SKIPPED:
                continue
            node = self.nodes.get(path)
            self.states[path] = InstanceState.SKIPPED
            self.steps.append(
                TrajectoryStep(
                    path,
                    node.id if node else "",
                    node.kind.value if node else "",
                    InstanceState.SKIPPED.value,
                    hash_value(""),
                    "",
                )
            )

    def admit(self, ready) -> None:
        for item in ready:
            self.steps.append(
                _replay_one(
                    item.node,
                    item.path,
                    self.outputs,
                    self.inputs,
                    self.responses,
                    self.clock,
                )
            )
            self.states[item.path] = self.responses.state(item.path)
            if item.node.id and self.responses.state(item.path) in SUCCESS_STATES:
                self.outputs[item.node.id] = self.responses.output(item.path)
