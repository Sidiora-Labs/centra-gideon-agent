from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.admission import (
    AdmissionPolicy,
    AdmissionRequest,
    Hold,
    Limits,
    Scope,
    compose,
    default_policies,
)
from gideon.automation.workflows.bindings import (
    BindingContext,
    BindingError,
    resolve_expr,
)
from gideon.automation.workflows.conditions import evaluate as evaluate_condition
from gideon.automation.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    ItemErrorPolicy,
    JoinMode,
    LoopMode,
    Node,
    NodeKind,
    lane_for,
    walk,
)
from gideon.automation.workflows.validator import EDGE_BINDING, dep_edges_for_root


@dataclass
class ReadyNode:
    path: str
    node: Node
    lane: str
    item: Any = None
    has_item: bool = False
    iter_index: int | None = None

    @property
    def node_id(self) -> str:
        return self.node.id


@dataclass
class Frontier:
    ready: list[ReadyNode] = field(default_factory=list)
    deferred: list[ReadyNode] = field(default_factory=list)
    wip_held: list[str] = field(default_factory=list)
    running: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    to_skip: list[str] = field(default_factory=list)
    complete: bool = False
    blocked: bool = False
    block_reason: str = ""
    outcome: InstanceState | None = None

    @property
    def has_work(self) -> bool:
        return bool(self.ready)

    @property
    def is_idle(self) -> bool:
        return not self.ready and not self.running and not self.waiting


@dataclass(frozen=True)
class Ordering:
    deps: dict[str, tuple[tuple[str, str, bool], ...]] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    iterated: tuple[str, ...] = ()


_SEVERITY = (
    InstanceState.CANCELLED,
    InstanceState.BLOCKED,
    InstanceState.ESCALATED,
    InstanceState.SCOPE_VIOLATION,
    InstanceState.FAILED,
    InstanceState.SKIPPED,
    InstanceState.DISCARDED,
    InstanceState.NO_CHANGE,
    InstanceState.DEGRADED,
    InstanceState.DONE,
)


def edge_key(src: str, dst: str) -> str:
    return f"{src}->{dst}"


def _state_of(states: dict[str, InstanceState], path: str) -> InstanceState:
    return states.get(path, InstanceState.PENDING)


def _is_terminal(st: InstanceState) -> bool:
    return st in TERMINAL_STATES


def _is_success(st: InstanceState) -> bool:
    return st in SUCCESS_STATES


def tolerate_failures(
    children: list[Node], child_states: list[InstanceState]
) -> list[InstanceState]:
    if not children or len(children) != len(child_states):
        return child_states
    result = []
    for child, state in zip(children, child_states):
        enabled = bool((getattr(child, "config", None) or {}).get("allow_failure"))
        result.append(
            InstanceState.DEGRADED
            if enabled and state == InstanceState.FAILED
            else state
        )
    return result


def _worst(states: list[InstanceState]) -> InstanceState:
    return next(
        (candidate for candidate in _SEVERITY if candidate in states),
        InstanceState.DONE,
    )


def container_outcome(
    child_states: list[InstanceState], *, join: JoinMode = JoinMode.ALL, quorum: int = 0
) -> InstanceState:
    if not child_states:
        return InstanceState.DONE
    unfinished = (
        InstanceState.PENDING,
        InstanceState.READY,
        InstanceState.RUNNING,
        InstanceState.WAITING,
    )
    if any(state in unfinished for state in child_states):
        return InstanceState.RUNNING
    successes = sum(1 for state in child_states if _is_success(state))
    if join == JoinMode.ANY:
        enough = bool(successes)
    elif join == JoinMode.QUORUM:
        enough = successes >= max(1, quorum)
    else:
        if successes == len(child_states):
            return (
                InstanceState.DEGRADED
                if InstanceState.DEGRADED in child_states
                else InstanceState.DONE
            )
        if all(state == InstanceState.SKIPPED for state in child_states):
            return InstanceState.SKIPPED
        return _worst(child_states)
    return InstanceState.DONE if enough else _worst(child_states)


def ordering_for(root: Node) -> Ordering:
    ordered: dict[str, dict[tuple[str, str, bool], None]] = {}
    for dependency in dep_edges_for_root(root):
        if dependency.ordered:
            identity = (
                dependency.producer_path,
                dependency.producer_id,
                dependency.origin == EDGE_BINDING,
            )
            ordered.setdefault(dependency.reader_path, {}).setdefault(identity, None)
    nodes = dict(walk(root))
    bodies = [
        path + ".body"
        for path, node in nodes.items()
        if node.body is not None and node.kind in (NodeKind.FOREACH, NodeKind.LOOP)
    ]
    bodies.sort(key=len, reverse=True)
    return Ordering(
        deps={reader: tuple(entries) for reader, entries in ordered.items()},
        nodes=nodes,
        iterated=tuple(bodies),
    )


def _producer_instance(
    producer_spec: str, order: Ordering, inst: dict[str, str]
) -> str | None:
    enclosing = next(
        (
            body
            for body in order.iterated
            if producer_spec == body or producer_spec.startswith(body + ".")
        ),
        None,
    )
    if enclosing is None:
        return producer_spec
    instance = inst.get(enclosing)
    return instance + producer_spec[len(enclosing) :] if instance is not None else None


def _edge_declined(declined: set[str], src: str, dst: str) -> bool:
    return edge_key(src, dst) in declined


def _mark_unreachable(
    path: str, states: dict[str, InstanceState], fr: Frontier
) -> None:
    if _is_terminal(_state_of(states, path)):
        return
    if path not in fr.to_skip:
        fr.to_skip.append(path)


def case_key(value: Any) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def _binding_expression(value: str) -> str:
    expression = value.strip()
    if expression.startswith("{{") and expression.endswith("}}"):
        return expression[2:-2].strip()
    return expression


def _select_case(node: Node, ctx: BindingContext) -> tuple[str, Node] | None:
    expression = str((node.config or {}).get("on", "") or "")
    if expression:
        inner = _binding_expression(expression)
        try:
            value = resolve_expr(inner, ctx)
        except BindingError:
            return None
        selected = case_key(value)
        if selected in node.cases:
            return selected, node.cases[selected]
        if node.default_case is not None:
            return "__default__", node.default_case
    return None


def _resolve_items(node: Node, ctx: BindingContext) -> list[Any] | None:
    source = (node.config or {}).get("items")
    if isinstance(source, list):
        return list(source)
    if isinstance(source, str) and source.strip():
        inner = _binding_expression(source)
        try:
            resolved = resolve_expr(inner, ctx)
        except BindingError:
            return None
        if resolved is None or isinstance(resolved, list):
            return resolved
        return [resolved]
    return None


def item_error_policy(node: Node) -> ItemErrorPolicy:
    configured = str((node.config or {}).get("on_item_error", "skip") or "skip")
    try:
        policy = ItemErrorPolicy(configured)
    except ValueError:
        policy = ItemErrorPolicy.SKIP
    return policy


def foreach_outcome(
    policy: ItemErrorPolicy, item_states: list[InstanceState]
) -> InstanceState:
    if policy == ItemErrorPolicy.HALT:
        return container_outcome(item_states)
    if not all(map(_is_terminal, item_states)):
        return InstanceState.RUNNING
    if policy == ItemErrorPolicy.SKIP:
        tolerated = any(state == InstanceState.FAILED for state in item_states)
        return InstanceState.DEGRADED if tolerated else container_outcome(item_states)
    if policy == ItemErrorPolicy.COLLECT:
        return container_outcome(item_states)
    raise AssertionError(
        f"no branch for ItemErrorPolicy.{getattr(policy, 'name', policy)} — a new member must "
        "declare its own behaviour here rather than inherit another policy's"
    )


def _on_error(node: Node) -> str:
    return str((node.config or {}).get("on_error", "null_continue") or "null_continue")


def _join_mode(config: dict[str, Any]) -> JoinMode:
    try:
        return JoinMode(str(config.get("join", "all") or "all"))
    except ValueError:
        return JoinMode.ALL


def _case_suffix(label: str) -> str:
    return ".default" if label == "__default__" else f".cases[{label}]"


class _StateProjection:
    def __init__(
        self,
        states: dict[str, InstanceState],
        edges: set[str],
        iterations: dict[str, int],
        context: BindingContext,
    ) -> None:
        self.states, self.edges, self.iterations, self.context = (
            states,
            edges,
            iterations,
            context,
        )

    def children(self, node: Node, path: str) -> list[InstanceState]:
        return [
            self.state(child, f"{path}.children[{index}]")
            for index, child in enumerate(node.children)
        ]

    def state(self, node: Node, path: str) -> InstanceState:
        stored = _state_of(self.states, path)
        if not node.is_container or stored in (
            InstanceState.SKIPPED,
            InstanceState.CANCELLED,
            InstanceState.DISCARDED,
        ):
            return stored
        handlers = (
            (NodeKind.SEQUENCE, self.sequence),
            (NodeKind.PARALLEL, self.parallel),
            (NodeKind.FOREACH, self.foreach),
            (NodeKind.LOOP, self.loop),
            (NodeKind.BRANCH, self.branch),
        )
        for kind, handler in handlers:
            if node.kind == kind:
                return handler(node, path, stored)
        return stored

    def sequence(self, node: Node, path: str, stored: InstanceState) -> InstanceState:
        return container_outcome(
            tolerate_failures(node.children, self.children(node, path))
        )

    def parallel(self, node: Node, path: str, stored: InstanceState) -> InstanceState:
        settings = node.config or {}
        join = _join_mode(settings)
        quorum = settings.get("quorum", 0)
        states = self.children(node, path)
        return container_outcome(
            tolerate_failures(node.children, states),
            join=join,
            quorum=quorum if isinstance(quorum, int) else 0,
        )

    def foreach(self, node: Node, path: str, stored: InstanceState) -> InstanceState:
        if node.body is None:
            return InstanceState.DONE
        values = _resolve_items(node, self.context)
        if values is None:
            return InstanceState.PENDING
        if not values:
            return InstanceState.DONE
        states = [
            self.state(node.body, f"{path}.body#{index}")
            for index in range(len(values))
        ]
        return foreach_outcome(item_error_policy(node), states)

    def loop(self, node: Node, path: str, stored: InstanceState) -> InstanceState:
        if node.body is None:
            return InstanceState.DONE
        iteration = int(self.iterations.get(path, 0))
        body = self.state(node.body, f"{path}.body@{iteration}")
        if _is_terminal(body):
            return stored if _is_terminal(stored) else InstanceState.RUNNING
        return InstanceState.RUNNING if body == InstanceState.RUNNING else stored

    def branch(self, node: Node, path: str, stored: InstanceState) -> InstanceState:
        if _is_terminal(stored) and _is_success(stored):
            selected = _select_case(node, self.context)
            if selected is None:
                return InstanceState.PENDING
            label, target = selected
            return self.state(target, path + _case_suffix(label))
        return stored


def _derive(
    node: Node,
    path: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
) -> InstanceState:
    return _StateProjection(states, edges, iterations, ctx).state(node, path)


def derive_state(
    node: Node,
    path: str,
    states: dict[str, InstanceState],
    *,
    declined_edges: set[str] | None = None,
    outputs: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    iterations: dict[str, int] | None = None,
) -> InstanceState:
    edges = set(declined_edges or ())
    counters = dict(iterations or {})
    context = BindingContext(
        inputs=dict(inputs or {}), node_outputs=dict(outputs or {})
    )
    return _derive(node, path, states, edges, counters, context)


@dataclass(frozen=True)
class _Cursor:
    node: Node
    path: str
    spec: str
    instances: dict[str, str]
    item: Any = None
    has_item: bool = False
    iteration: int | None = None

    def child(self, node: Node, suffix: str) -> _Cursor:
        return _Cursor(
            node,
            self.path + suffix,
            self.spec + suffix,
            self.instances,
            self.item,
            self.has_item,
            self.iteration,
        )

    def body(
        self, marker: str, index: int, value: Any = None, has_value: bool = False
    ) -> _Cursor:
        spec = self.spec + ".body"
        path = f"{self.path}.body{marker}{index}"
        if self.node.body is None:
            raise ValueError(f"{self.path} has no loop body")
        return _Cursor(
            self.node.body,
            path,
            spec,
            {**self.instances, spec: path},
            value,
            has_value,
            index,
        )


class _FrontierWalk:
    def __init__(
        self,
        states: dict[str, InstanceState],
        edges: set[str],
        iterations: dict[str, int],
        ctx: BindingContext,
        fr: Frontier,
        order: Ordering,
        policies: tuple[AdmissionPolicy, ...] = (),
    ) -> None:
        self.states, self.edges, self.iterations = states, edges, iterations
        self.context, self.result, self.order, self.policies = ctx, fr, order, policies
        self.projection = _StateProjection(states, edges, iterations, ctx)

    def offer(self, cursor: _Cursor) -> None:
        self.result.ready.append(
            ReadyNode(
                path=cursor.path,
                node=cursor.node,
                lane=lane_for(cursor.node.kind),
                item=cursor.item,
                has_item=cursor.has_item,
                iter_index=cursor.iteration,
            )
        )

    def visit(self, cursor: _Cursor, enabled: bool = True) -> None:
        stored = _state_of(self.states, cursor.path)
        if stored == InstanceState.RUNNING:
            self.result.running.append(cursor.path)
            return
        if stored == InstanceState.WAITING:
            self.result.waiting.append(cursor.path)
            return
        effective = (
            self.projection.state(cursor.node, cursor.path)
            if cursor.node.is_container
            else stored
        )
        if _is_terminal(effective) or not enabled:
            return
        if not self.ordering(cursor):
            return
        handlers = (
            (NodeKind.SEQUENCE, self.sequence),
            (NodeKind.PARALLEL, self.parallel),
            (NodeKind.FOREACH, self.foreach),
            (NodeKind.LOOP, self.loop),
            (NodeKind.BRANCH, self.branch),
        )
        for kind, handler in handlers:
            if cursor.node.kind == kind:
                handler(cursor)
                return
        self.offer(cursor)

    def ordering(self, cursor: _Cursor) -> bool:
        dependencies = self.order.deps.get(cursor.spec)
        if not dependencies:
            return True
        waiting = False
        for producer, identity, dataflow in dependencies:
            if cursor.node.id and _edge_declined(self.edges, identity, cursor.node.id):
                _mark_unreachable(cursor.path, self.states, self.result)
                return False
            address = _producer_instance(producer, self.order, cursor.instances)
            if address is None:
                continue
            owner = self.order.nodes.get(producer)
            state = (
                self.projection.state(owner, address)
                if owner is not None
                else _state_of(self.states, address)
            )
            if state == InstanceState.SKIPPED and dataflow:
                _mark_unreachable(cursor.path, self.states, self.result)
                return False
            if not _is_terminal(state):
                waiting = True
        return not waiting

    def sequence(self, cursor: _Cursor) -> None:
        for index, node in enumerate(cursor.node.children):
            child = cursor.child(node, f".children[{index}]")
            stored = _state_of(self.states, child.path)
            self.visit(child)
            if not _is_terminal(self.projection.state(node, child.path)):
                return
            if stored == InstanceState.FAILED and _on_error(node) == "fail_run":
                return

    def parallel(self, cursor: _Cursor) -> None:
        for index, node in enumerate(cursor.node.children):
            child = cursor.child(node, f".children[{index}]")
            if not _is_terminal(self.projection.state(node, child.path)):
                self.visit(child)

    def started(self, path: str) -> bool:
        return any(
            record == path or record.startswith(path + ".") for record in self.states
        )

    def foreach(self, cursor: _Cursor) -> None:
        node = cursor.node
        if node.body is None:
            return
        values = _resolve_items(node, self.context)
        if values is None:
            return
        policy = item_error_policy(node)
        for index, _ in enumerate(values):
            recorded = _state_of(self.states, f"{cursor.path}.body#{index}")
            if recorded == InstanceState.FAILED and policy == ItemErrorPolicy.HALT:
                return
        admission = compose(
            self.policies,
            AdmissionRequest(scope=Scope.CONTAINER, key=cursor.path, node=node),
        )
        occupied = 0
        if admission.bounded:
            for index in range(len(values)):
                path = f"{cursor.path}.body#{index}"
                if self.started(path) and not _is_terminal(
                    self.projection.state(node.body, path)
                ):
                    occupied += 1
        for index, value in enumerate(values):
            path = f"{cursor.path}.body#{index}"
            if _is_terminal(_state_of(self.states, path)):
                continue
            if admission.bounded and not self.started(path):
                if not admission.admits(occupied):
                    if admission.hold == Hold.WIP_HELD:
                        self.result.wip_held.append(path)
                    continue
                occupied += 1
            self.visit(cursor.body("#", index, value, True))

    def loop(self, cursor: _Cursor) -> None:
        if cursor.node.body is None:
            return
        index = int(self.iterations.get(cursor.path, 0))
        address = f"{cursor.path}.body@{index}"
        if not _is_terminal(_state_of(self.states, address)):
            self.visit(cursor.body("@", index))

    def branch(self, cursor: _Cursor) -> None:
        routed = _state_of(self.states, cursor.path)
        if not _is_terminal(routed):
            self.offer(cursor)
            return
        if not _is_success(routed):
            return
        selected = _select_case(cursor.node, self.context)
        if selected is None:
            return
        label, node = selected
        child = cursor.child(node, _case_suffix(label))
        for other_label in cursor.node.cases:
            other_path = f"{cursor.path}.cases[{other_label}]"
            if other_path != child.path and not _is_terminal(
                _state_of(self.states, other_path)
            ):
                self.result.to_skip.append(other_path)
        if cursor.node.default_case is not None:
            default_path = cursor.path + ".default"
            if default_path != child.path and not _is_terminal(
                _state_of(self.states, default_path)
            ):
                self.result.to_skip.append(default_path)
        if not _is_terminal(_state_of(self.states, child.path)):
            self.visit(child)


class _LaneAllocation:
    def __init__(
        self, policies: tuple[AdmissionPolicy, ...], running: dict[str, int] | None
    ) -> None:
        self.policies, self.running = policies, running

    def apply(self, result: Frontier) -> None:
        result.ready.sort(key=lambda candidate: candidate.path)
        admitted, deferred = [], []
        used = dict(self.running or {})
        for candidate in result.ready:
            rule = compose(
                self.policies, AdmissionRequest(scope=Scope.LANE, key=candidate.lane)
            )
            if rule.admits(used.get(candidate.lane, 0)):
                used[candidate.lane] = used.get(candidate.lane, 0) + 1
                admitted.append(candidate)
            else:
                deferred.append(candidate)
        result.ready, result.deferred = admitted, deferred


def frontier(
    root: Node,
    states: dict[str, InstanceState],
    *,
    limits: Limits | None = None,
    declined_edges: set[str] | None = None,
    outputs: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    iterations: dict[str, int] | None = None,
    running_lanes: dict[str, int] | None = None,
    single_active_feature: bool = False,
) -> Frontier:
    policies = default_policies(
        limits or Limits(), single_active_feature=single_active_feature
    )
    declined = set(declined_edges or ())
    result = Frontier()
    context = BindingContext(
        inputs=dict(inputs or {}), node_outputs=dict(outputs or {})
    )
    counters = dict(iterations or {})
    order = ordering_for(root)
    _FrontierWalk(states, declined, counters, context, result, order, policies).visit(
        _Cursor(root, "root", "root", {})
    )
    _LaneAllocation(policies, running_lanes).apply(result)
    outcome = _derive(root, "root", states, declined, dict(iterations or {}), context)
    if _is_terminal(outcome):
        result.complete, result.outcome = True, outcome
    elif not any(
        (result.ready, result.deferred, result.running, result.waiting, result.to_skip)
    ):
        result.blocked = True
        result.block_reason = "no runnable nodes and none in flight"
    return result


def _visit(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    enabled: bool,
    order: Ordering,
    inst: dict[str, str],
    item: Any = None,
    has_item: bool = False,
    iter_index: int | None = None,
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    traversal = _FrontierWalk(states, edges, iterations, ctx, fr, order, policies)
    cursor = _Cursor(node, path, spec, inst, item, has_item, iter_index)
    traversal.visit(cursor, enabled)


def _visit_parallel(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    item: Any,
    has_item: bool,
    iter_index: int | None,
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    traversal = _FrontierWalk(states, edges, iterations, ctx, fr, order, policies)
    cursor = _Cursor(node, path, spec, inst, item, has_item, iter_index)
    traversal.parallel(cursor)


def _ordering_satisfied(
    node: Node,
    path: str,
    spec: str,
    *,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
) -> bool:
    traversal = _FrontierWalk(states, edges, iterations, ctx, fr, order, ())
    cursor = _Cursor(node, path, spec, inst)
    return traversal.ordering(cursor)


def _visit_foreach(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    traversal = _FrontierWalk(states, edges, iterations, ctx, fr, order, policies)
    cursor = _Cursor(node, path, spec, inst)
    traversal.foreach(cursor)


def _visit_loop(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    traversal = _FrontierWalk(states, edges, iterations, ctx, fr, order, policies)
    cursor = _Cursor(node, path, spec, inst)
    traversal.loop(cursor)


def _visit_branch(
    node: Node,
    path: str,
    *,
    spec: str,
    states: dict[str, InstanceState],
    edges: set[str],
    iterations: dict[str, int],
    ctx: BindingContext,
    fr: Frontier,
    order: Ordering,
    inst: dict[str, str],
    item: Any,
    has_item: bool,
    iter_index: int | None,
    policies: tuple[AdmissionPolicy, ...] = (),
) -> None:
    traversal = _FrontierWalk(states, edges, iterations, ctx, fr, order, policies)
    cursor = _Cursor(node, path, spec, inst, item, has_item, iter_index)
    traversal.branch(cursor)


class _LoopExit:
    def __init__(
        self,
        node: Node,
        iteration: int,
        dry_streak: int,
        context: BindingContext | None,
    ) -> None:
        self.config = node.config or {}
        self.iteration, self.dry_streak, self.context = iteration, dry_streak, context
        raw = str(self.config.get("mode", "counted") or "counted")
        try:
            self.mode = LoopMode(raw)
        except ValueError:
            self.mode = LoopMode.COUNTED

    def threshold(self, value: Any, current: int, reason: str) -> tuple[bool, str]:
        required = value if isinstance(value, int) and value > 0 else 1
        return current < required, "" if current < required else reason

    def until(self) -> tuple[bool, str]:
        expression = str(self.config.get("condition", "") or "")
        if not expression:
            return False, "missing_condition"
        try:
            satisfied = evaluate_condition(expression, self.context or BindingContext())
        except BindingError:
            return False, "condition_unresolvable"
        return not satisfied, "" if not satisfied else "condition_met"

    def decision(self) -> tuple[bool, str]:
        ceiling = self.config.get("max_iterations")
        if isinstance(ceiling, int) and ceiling > 0 and self.iteration >= ceiling:
            return False, "max_iterations"
        if self.mode == LoopMode.COUNTED:
            return self.threshold(
                self.config.get("n"), self.iteration, "counted_complete"
            )
        if self.mode == LoopMode.UNTIL:
            return self.until()
        if self.mode == LoopMode.UNTIL_CANCELLED:
            return True, ""
        return self.threshold(
            self.config.get("streak", 1), self.dry_streak, "dry_streak"
        )


def loop_should_continue(
    node: Node,
    *,
    iteration: int,
    last_output: Any = None,
    dry_streak: int = 0,
    ctx: BindingContext | None = None,
) -> tuple[bool, str]:
    return _LoopExit(node, iteration, dry_streak, ctx).decision()


def _is_until_cancelled(node: Node) -> bool:
    return (
        node.kind == NodeKind.LOOP
        and str((node.config or {}).get("mode", "") or "")
        == LoopMode.UNTIL_CANCELLED.value
    )


def _derive_child_state(
    node: Node, path: str, states: dict[str, InstanceState], iterations: dict[str, int]
) -> InstanceState:
    return _derive(node, path, states, set(), iterations, BindingContext())


class _WatcherReaper:
    def __init__(
        self, states: dict[str, InstanceState], iterations: dict[str, int]
    ) -> None:
        self.states, self.iterations = states, iterations

    def parallel(self, node: Node, path: str) -> list[str]:
        settings = node.config or {}
        join = _join_mode(settings)
        if join == JoinMode.ALL:
            return []
        watchers, workers = [], []
        for index, child in enumerate(node.children):
            address = f"{path}.children[{index}]"
            if _is_until_cancelled(child):
                watchers.append(address)
            else:
                workers.append(
                    _derive_child_state(child, address, self.states, self.iterations)
                )
        if not watchers:
            return []
        succeeded = sum(1 for state in workers if _is_success(state))
        needed = 1
        if join == JoinMode.QUORUM:
            quorum = settings.get("quorum", 0)
            needed = max(1, quorum if isinstance(quorum, int) else 0)
        if not workers or succeeded < needed:
            return []
        return [
            address
            for address in watchers
            if not _is_terminal(_state_of(self.states, address))
        ]

    def collect(self, root: Node) -> list[str]:
        retire = []
        for path, node in walk(root):
            if node.kind == NodeKind.PARALLEL:
                retire.extend(self.parallel(node, path))
        return retire


def reap_watchers(
    root: Node,
    states: dict[str, InstanceState],
    *,
    iterations: dict[str, int] | None = None,
) -> list[str]:
    return _WatcherReaper(states, dict(iterations or {})).collect(root)
