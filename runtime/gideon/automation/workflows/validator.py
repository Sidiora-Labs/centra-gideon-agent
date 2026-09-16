"""Workflow authoring checks and the dependency graph used by runtime admission."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.bindings import BindingError, node_deps, refs_in
from gideon.automation.workflows.models import (
    LLM_KINDS,
    GateKind,
    ItemErrorPolicy,
    JoinMode,
    LoopMode,
    Node,
    NodeKind,
    valid_name,
    walk,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
EDGE_BINDING = "binding"
EDGE_NEEDS = "needs"
_PROMPT_KEYS = frozenset({"prompt", "system", "instruction", "instructions", "message"})
_UNTRUSTED_ROOTS = frozenset({"trigger", "payload", "webhook", "fetched"})
_SANITIZING_PIPES = frozenset({"xml_escape", "truncate", "slugify", "json", "tojson"})
_MAX_DEPTH = 12
_MAX_NODES = 500


@dataclass
class Issue:
    code: str
    message: str
    path: str = ""
    severity: str = SEVERITY_ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    levels: list[list[str]] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "levels": [list(lv) for lv in self.levels],
        }

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "Spec is valid."

        def render(issue):
            location = " at " + issue.path if issue.path else ""
            return f"[{issue.severity}] {issue.code}{location}: {issue.message}"

        return "\n".join(map(render, self.issues))


@dataclass(frozen=True)
class DepEdge:
    """One ordering edge between two nodes, and whether the engine can honour it.

    ONE list carries both origins (`PP-2`): a `{{nodes.<producer>…}}` binding and a
    hand-written `needs` are the same relation seen from two sides, and deriving them
    separately is precisely the two-edge-lists defect this plan exists to remove. Every
    consumer — the ordering rule, the contract rule, the `needs` cross-check and the
    frontier's admission gate — reads this one list and filters on `origin`.

    Materialized as a value rather than turned straight into an issue, because a rule that
    examined no edges is indistinguishable from a rule that examined many and liked them
    all — both report nothing. Only a caller that can count the edges considered can tell
    the two apart, which is what the vacuity floor in the tests does.
    """

    reader_path: str
    reader_id: str
    producer_path: str
    producer_id: str
    ordered: bool
    reason: str
    output_reads: tuple[tuple[str, ...], ...] = ()
    origin: str = EDGE_BINDING


@dataclass(frozen=True)
class ContractRead:
    """One path taken through a producer's output, judged against that producer's contract.

    Materialized as a value for the same reason `DepEdge` is: a rule that resolved NOTHING
    against a real contract reports exactly what a rule that resolved fifty satisfiable
    paths reports — silence. Only a caller that can count the reads whose `guaranteed` is
    non-`None` can tell the two apart, which is what the vacuity floor in the tests does.
    """

    reader_path: str
    reader_id: str
    producer_id: str
    producer_path: str
    path: tuple[str, ...]
    declared: bool
    guaranteed: tuple[str, ...] | None
    satisfiable: bool


def _add(
    res: ValidationResult,
    code: str,
    msg: str,
    path: str = "",
    sev: str = SEVERITY_ERROR,
):
    res.issues.append(Issue(code=code, message=msg, path=path, severity=sev))


class _TreeInspection:
    def __init__(self, root: Node):
        self.nodes = walk(root)
        self.tree = dict(self.nodes)
        self.ids: dict[str, str] = {}

    def inspect(self, strict: bool) -> ValidationResult:
        result = ValidationResult()
        count = len(self.nodes)
        if count > _MAX_NODES:
            _add(
                result,
                "WF_SPEC_TOO_LARGE",
                f"spec has {count} nodes; the cap is {_MAX_NODES}",
                "root",
            )
        for location, node in self.nodes:
            if location.count(".") > _MAX_DEPTH:
                _add(
                    result,
                    "WF_SPEC_TOO_DEEP",
                    f"nesting exceeds {_MAX_DEPTH}",
                    location,
                )
            if node.id:
                if node.id in self.ids:
                    _add(
                        result,
                        "WF_DUPLICATE_NODE_ID",
                        f"node id {node.id!r} is already used at {self.ids[node.id]}",
                        location,
                    )
                else:
                    self.ids[node.id] = location
            _validate_shape(result, location, node, tree=self.tree)
            _validate_bindings(result, location, node, strict=strict)
        _validate_binding_targets(result, self.nodes, self.ids)
        levels = _kahn_levels(result, self.nodes, self.ids)
        edges = dep_ordering_edges(self.nodes, self.ids)
        for check in (
            lambda: _validate_dep_ordering(result, edges),
            lambda: _validate_output_contract(result, self.nodes, edges),
            lambda: _validate_needs(result, edges),
        ):
            check()
        if result.ok:
            result.levels = levels
        return result


def validate_node_tree(root: Node, *, strict: bool = False) -> ValidationResult:
    return _TreeInspection(root).inspect(strict)


def _in_reapable_parallel(path: str, tree: dict[str, Node]) -> bool:
    parent_path = None
    for match in re.finditer(r"\.children\[\d+\]", path):
        parent_path = path[: match.start()]
    if parent_path is None:
        return False
    container = tree.get(parent_path)
    if container is None or container.kind != NodeKind.PARALLEL:
        return False
    mode = str((container.config or {}).get("join", "all") or "all")
    if mode not in (JoinMode.ANY.value, JoinMode.QUORUM.value):
        return False
    for child in container.children:
        if child.kind != NodeKind.LOOP:
            return True
        if (
            str((child.config or {}).get("mode", "") or "")
            != LoopMode.UNTIL_CANCELLED.value
        ):
            return True
    return False


def _has_wait(node: Node) -> bool:
    return any(candidate.kind == NodeKind.WAIT for _, candidate in walk(node))


def _positive_int(raw: Any) -> bool:
    return isinstance(raw, int) and not isinstance(raw, bool) and raw > 0


class _ShapeRules:
    def __init__(self, result: ValidationResult, path: str, node: Node, tree):
        self.result, self.path, self.node, self.tree = result, path, node, tree
        self.config = node.config or {}

    def issue(self, code, message, severity=SEVERITY_ERROR):
        _add(self.result, code, message, self.path, severity)

    def choice(self, enum, field, default, code, label, fallback=None):
        text = str(self.config.get(field, default) or default)
        try:
            return enum(text)
        except ValueError:
            self.issue(code, f"unknown {label} {text!r}")
            return fallback

    def container(self):
        node, cfg = self.node, self.config
        if not node.children:
            self.issue("WF_EMPTY_CONTAINER", f"{node.kind.value} has no children")
        if node.kind != NodeKind.PARALLEL:
            return
        join = self.choice(
            JoinMode, "join", "all", "WF_BAD_JOIN", "join mode", JoinMode.ALL
        )
        if join == JoinMode.QUORUM:
            value = cfg.get("quorum")
            if not isinstance(value, int) or not 1 <= value <= max(
                1, len(node.children)
            ):
                self.issue(
                    "WF_BAD_QUORUM", f"quorum must be an int in 1..{len(node.children)}"
                )

    def foreach(self):
        if self.node.body is None:
            self.issue("WF_MISSING_BODY", "foreach has no body")
        if not self.config.get("items"):
            self.issue("WF_MISSING_ITEMS", "foreach needs an `items` binding")
        self.choice(
            ItemErrorPolicy,
            "on_item_error",
            "skip",
            "WF_BAD_ITEM_ERROR",
            "on_item_error",
        )

    def loop(self):
        cfg = self.config
        if self.node.body is None:
            self.issue("WF_MISSING_BODY", "loop has no body")
        mode = self.choice(
            LoopMode,
            "mode",
            "counted",
            "WF_BAD_LOOP_MODE",
            "loop mode",
            LoopMode.COUNTED,
        )
        bounded = {
            LoopMode.COUNTED: (
                "n",
                None,
                "WF_BAD_LOOP_COUNT",
                "counted loop needs a positive `n`",
            ),
            LoopMode.UNTIL_DRY: (
                "streak",
                1,
                "WF_BAD_STREAK",
                "until_dry needs a positive `streak`",
            ),
        }
        if mode in bounded:
            key, default, code, message = bounded[mode]
            value = cfg.get(key, default)
            if not isinstance(value, int) or value < 1:
                self.issue(code, message)
        elif mode == LoopMode.UNTIL:
            if not cfg.get("condition"):
                self.issue("WF_MISSING_CONDITION", "until loop needs a `condition`")
        elif mode == LoopMode.UNTIL_CANCELLED:
            if not _in_reapable_parallel(self.path, self.tree or {}) and not cfg.get(
                "max_iterations"
            ):
                self.issue(
                    "WF_UNREAPABLE_WATCHER",
                    "until_cancelled needs either a `join: any`/`quorum` parallel sibling to reap it or a `max_iterations` cap — otherwise the run never ends",
                )
            if self.node.body is not None and not _has_wait(self.node.body):
                self.issue(
                    "WF_WATCHER_NO_WAIT",
                    "until_cancelled body has no `wait` — it will cycle as fast as the model responds and exhaust the run budget",
                )
        if "supervisor" in cfg:
            _validate_supervisor(self.result, self.path, cfg.get("supervisor"))

    def branch(self):
        cfg, node = self.config, self.node
        if not cfg.get("on"):
            self.issue("WF_MISSING_ON", "branch needs an `on` binding")
        if not node.cases:
            self.issue("WF_EMPTY_BRANCH", "branch has no cases")
        values = cfg.get("enum")
        if isinstance(values, list) and values:
            absent = [str(value) for value in values if str(value) not in node.cases]
            if absent and node.default_case is None:
                self.issue(
                    "WF_BRANCH_COVERAGE",
                    f"no case for {', '.join(absent)} and no default",
                )

    def llm(self):
        if not self.config.get("prompt"):
            self.issue("WF_MISSING_PROMPT", f"{self.node.kind.value} needs a `prompt`")
        tier = self.config.get("model_tier")
        if tier is not None and tier not in ("reasoning", "standard", "fast"):
            self.issue(
                "WF_BAD_MODEL_TIER",
                f"model_tier {tier!r} must be reasoning|standard|fast",
            )

    def action(self):
        cfg = self.config
        if not cfg.get("provider"):
            self.issue("WF_MISSING_PROVIDER", "action needs a `provider`")
            return
        if "with" in cfg or "config" in cfg:
            return
        extras = sorted(
            key for key in cfg if key not in ("provider", "context", "payload")
        )
        if extras:
            self.issue(
                "WF_ACTION_ARGS_NOT_NESTED",
                "action arguments go under `config.with` — move "
                + ", ".join(extras)
                + " into it",
            )
        else:
            self.issue(
                "WF_ACTION_NO_ARGS",
                "action has no `config.with` — fine if the provider needs no arguments",
                SEVERITY_WARNING,
            )

    def wait(self):
        cfg = self.config
        seal = cfg.get("seal")
        if seal is not None and not isinstance(seal, dict):
            self.issue("WF_BAD_SEAL", "wait `seal` must be an object")
            seal = None
        if not isinstance(seal, dict):
            if not (cfg.get("duration_secs") or cfg.get("until_ts")):
                self.issue(
                    "WF_MISSING_WAIT", "wait needs duration_secs, until_ts or seal"
                )
            return
        if not _positive_int(seal.get("threshold")) and not _positive_int(
            seal.get("tokens")
        ):
            self.issue(
                "WF_BAD_SEAL",
                "buffer seal needs a positive `threshold` (items) or `tokens`",
            )
        elif not _positive_int(seal.get("flush_stale_after_secs")):
            self.issue(
                "WF_SEAL_NO_FLUSH",
                "buffer seal has no `flush_stale_after_secs` — a trickle of items would never reach the threshold and never synthesize",
                SEVERITY_WARNING,
            )

    def gate(self):
        raw = str(self.config.get("kind", "") or "")
        gate = self.choice(GateKind, "kind", "", "WF_BAD_GATE_KIND", "gate kind")
        if gate in (
            GateKind.VERIFY_COMMAND,
            GateKind.VERIFY_SCRIPT,
        ) and not self.config.get("verify"):
            self.issue("WF_MISSING_VERIFY", f"{raw} gate needs a `verify` block")
        if gate == GateKind.EXPRESSION and not self.config.get("expr"):
            self.issue("WF_MISSING_EXPR", "expression gate needs an `expr`")

    def subworkflow(self):
        reference = str(self.config.get("ref", "") or "")
        if not reference:
            self.issue("WF_MISSING_REF", "subworkflow needs a `ref`")
        elif not valid_name(reference.split("@", 1)[0]):
            self.issue(
                "WF_BAD_REF", f"subworkflow ref {reference!r} is not a valid name"
            )

    def run(self):
        kind = self.node.kind
        handlers = {
            NodeKind.SEQUENCE: self.container,
            NodeKind.PARALLEL: self.container,
            NodeKind.FOREACH: self.foreach,
            NodeKind.LOOP: self.loop,
            NodeKind.BRANCH: self.branch,
            NodeKind.ACTION: self.action,
            NodeKind.WAIT: self.wait,
            NodeKind.GATE: self.gate,
            NodeKind.SUBWORKFLOW: self.subworkflow,
        }
        # LLM aliases share their model's identity; preserve the model vocabulary.
        if kind in LLM_KINDS:
            self.llm()
        elif kind in handlers:
            handlers[kind]()
        elif kind == NodeKind.TRANSFORM:
            if not self.config.get("expr") and not self.config.get("skeleton"):
                self.issue(
                    "WF_MISSING_EXPR",
                    "transform needs an `expr` binding (or a `skeleton` artifact to render)",
                )
        elif kind == NodeKind.VISUALIZE and not self.config.get("data"):
            self.issue("WF_MISSING_DATA", "visualize needs a `data` binding")


def _validate_shape(
    res: ValidationResult, path: str, node: Node, *, tree: dict[str, Node] | None = None
) -> None:
    _ShapeRules(res, path, node, tree).run()


def _validate_supervisor(res: ValidationResult, path: str, raw: Any) -> None:
    from gideon.automation.workflows.supervisor_policy import (
        FAILURE_CLASS_VALUES,
        HITL_POSTURE_VALUES,
        LADDER_RUNG_VALUES,
        POLICY_FIELDS,
        SUPERVISOR_MODEL_TIERS,
    )

    if not isinstance(raw, dict):
        _add(res, "WF_SUPERVISOR_NOT_OBJECT", "supervisor must be an object", path)
        return
    for name in raw:
        if name not in POLICY_FIELDS:
            _add(
                res,
                "WF_SUPERVISOR_UNKNOWN_FIELD",
                f"unknown supervisor field {name!r}",
                path,
            )
    entries = (
        (
            "judge_model_tier",
            SUPERVISOR_MODEL_TIERS,
            "WF_SUPERVISOR_BAD_TIER",
            "judge_model_tier {!r} must be reasoning|standard|fast",
            None,
        ),
        (
            "escalation_ladder",
            LADDER_RUNG_VALUES,
            "WF_SUPERVISOR_BAD_RUNG",
            "unknown escalation rung {!r}",
            list,
        ),
        (
            "failure_mutations",
            FAILURE_CLASS_VALUES,
            "WF_SUPERVISOR_BAD_FAILURE_CLASS",
            "unknown failure class {!r}",
            dict,
        ),
        (
            "hitl_posture",
            HITL_POSTURE_VALUES,
            "WF_SUPERVISOR_BAD_HITL",
            "hitl_posture {!r} must be afk|hitl",
            None,
        ),
    )
    for name, vocabulary, code, message, collection in entries:
        value = raw.get(name)
        if collection is None:
            values = () if value is None else (value,)
        else:
            values = value if isinstance(value, collection) else ()
        for entry in values:
            if str(entry) not in vocabulary:
                _add(res, code, message.format(entry), path)


def _validate_bindings(
    res: ValidationResult, path: str, node: Node, *, strict: bool
) -> None:
    config = node.config or {}
    for key, value in config.items():
        for expression in refs_in(value):
            segments = expression.split("|")
            pipe_names = []
            for segment in segments[1:]:
                name = segment.strip().split("(", 1)[0].strip()
                from gideon.automation.workflows.bindings import PIPES

                pipe_names.append(name)
                if name and name not in PIPES:
                    _add(res, "WF_UNKNOWN_PIPE", f"unknown pipe {name!r}", path)
            head = segments[0].strip()
            root = head.split(".")[0].strip()
            if (
                key in _PROMPT_KEYS
                and root in _UNTRUSTED_ROOTS
                and not _SANITIZING_PIPES.intersection(pipe_names)
            ):
                _add(
                    res,
                    "WF_UNFENCED_UNTRUSTED",
                    f"{root!r} is untrusted input flowing into {key!r} unsanitized — add a sanitization pipe (xml_escape/truncate/json)",
                    path,
                )
            known = (
                root in ("inputs", "nodes", "item", "iter", "last")
                or head.startswith("secret:")
                or root in _UNTRUSTED_ROOTS
            )
            if strict and not known:
                if head.startswith("block:"):
                    code = "WF_UNRESOLVED_BLOCK"
                    message = f"shared block reference {head!r} was not resolved — the block may not exist, or this spec bypassed the authoring path that substitutes them"
                else:
                    code = "WF_UNKNOWN_BINDING_ROOT"
                    message = f"binding root {root!r} is not a known source"
                _add(res, code, message, path, SEVERITY_WARNING)
    for key, value in config.items():
        if isinstance(value, str) and _looks_like_secret(value):
            _add(
                res,
                "WF_INLINE_SECRET",
                f"{key!r} looks like an inline credential — use {{{{secret:KEY}}}}",
                path,
            )


def _looks_like_secret(value: str) -> bool:
    from gideon.automation.workflows.secrets import looks_like_credential

    token = value.strip()
    return False if " " in token else looks_like_credential(token)


def _validate_binding_targets(
    res: ValidationResult, nodes: list[tuple[str, Node]], ids: dict[str, str]
) -> None:
    for path, node in nodes:
        groups = (
            (
                node_deps(node.config or {}),
                "WF_UNKNOWN_NODE_REF",
                "binding references unknown node id {!r}",
            ),
            (node.needs, "WF_UNKNOWN_NEEDS", "needs {!r} names no node in this spec"),
        )
        for targets, code, template in groups:
            for target in targets:
                if target not in ids:
                    _add(res, code, template.format(target), path)


def _parent_slots(
    nodes: list[tuple[str, Node]],
) -> dict[str, tuple[str, str, int, str]]:
    links = {}
    for path, node in nodes:
        links.update(
            (f"{path}.children[{index}]", (path, "children", index, ""))
            for index in range(len(node.children))
        )
        if node.body is not None:
            links[path + ".body"] = (path, "body", -1, "")
        links.update(
            (f"{path}.cases[{label}]", (path, "cases", -1, label))
            for label in node.cases
        )
        if node.default_case is not None:
            links[path + ".default"] = (path, "default", -1, "default")
    return links


def _chain(path: str, parents: dict[str, tuple[str, str, int, str]]) -> list[str]:
    ancestors = []
    visited = set()
    while path not in visited:
        ancestors.append(path)
        visited.add(path)
        if path not in parents:
            break
        path = parents[path][0]
    return ancestors


def _slot_label(slot: tuple[str, str, int, str]) -> str:
    if slot[1] == "children":
        return f"child {slot[2]}"
    if slot[1] == "cases":
        return f"case {slot[3]!r}"
    return "the default case" if slot[1] == "default" else "the body"


def _needs_reaches(parent: Node, from_idx: int, to_idx: int) -> bool:
    indices = {
        child.id: index for index, child in enumerate(parent.children) if child.id
    }
    if not parent.children[to_idx].id:
        return False
    discovered = {from_idx}
    pending = deque((from_idx,))
    while pending:
        source = pending.popleft()
        targets = (indices.get(name) for name in parent.children[source].needs)
        for target in targets:
            if target is None or target in discovered:
                continue
            if target == to_idx:
                return True
            discovered.add(target)
            pending.append(target)
    return False


class _OrderingIndex:
    def __init__(self, tree, parents):
        self.tree, self.parents = tree, parents

    def compare(self, reader: str, producer: str) -> tuple[bool, str]:
        if reader == producer:
            return False, "the binding reads the reader's own output"
        reader_line = _chain(reader, self.parents)
        producer_line = _chain(producer, self.parents)
        if producer in reader_line:
            return False, (
                f"it ENCLOSES the reader ({producer}), and a container's output is not available until after the children that produce it"
            )
        if reader in producer_line:
            return (
                False,
                f"it runs INSIDE the reader ({producer}), so it cannot precede it",
            )
        reader_positions = {
            ancestor: index for index, ancestor in enumerate(reader_line)
        }
        intersection = next(
            (
                (index, ancestor)
                for index, ancestor in enumerate(producer_line[1:], 1)
                if ancestor in reader_positions
            ),
            None,
        )
        if intersection is None or not intersection[1]:
            return False, "the reader and the producer are in unrelated trees"
        producer_index, common = intersection
        producer_slot = self.parents[producer_line[producer_index - 1]]
        reader_slot = self.parents[reader_line[reader_positions[common] - 1]]
        parent = self.tree.get(common)
        kind = parent.kind if parent is not None else None
        children = producer_slot[1] == reader_slot[1] == "children"
        left, right = producer_slot[2], reader_slot[2]
        if children and kind is NodeKind.SEQUENCE:
            if left < right:
                return (
                    True,
                    f"the sequence at {common} runs child {left} before child {right}",
                )
            return (
                False,
                f"the sequence at {common} runs it AFTER the reader (child {left} vs child {right}) — move it before the reader",
            )
        if children and kind is NodeKind.PARALLEL:
            assert parent is not None
            if _needs_reaches(parent, right, left):
                return True, f"a `needs` chain inside the parallel at {common}"
            return (
                True,
                f"the ordering edge holds the reader until it finishes, across the concurrent legs of the parallel at {common}",
            )
        producer_label, reader_label = _slot_label(producer_slot), _slot_label(
            reader_slot
        )
        if kind is NodeKind.BRANCH:
            return (
                False,
                f"the branch at {common} runs {producer_label} and {reader_label} exclusively — only one of the two ever runs",
            )
        return (
            False,
            f"nothing can order {producer_label} before {reader_label} in the {kind.value if kind else 'node'} at {common}",
        )


def _ordering_verdict(
    reader_path: str,
    producer_path: str,
    tree: dict[str, Node],
    parents: dict[str, tuple[str, str, int, str]],
) -> tuple[bool, str]:
    return _OrderingIndex(tree, parents).compare(reader_path, producer_path)


def _output_reads(config: dict[str, Any]) -> dict[str, tuple[tuple[str, ...], ...]]:
    references: dict[str, dict[tuple[str, ...], None]] = {}
    for expression in refs_in(config):
        parts = tuple(
            part for part in expression.split("|", 1)[0].strip().split(".") if part
        )
        if len(parts) >= 3 and parts[0] == "nodes" and parts[2] == "output":
            references.setdefault(parts[1], {})[parts[3:]] = None
    return {name: tuple(paths) for name, paths in references.items()}


def dep_ordering_edges(
    nodes: list[tuple[str, Node]], ids: dict[str, str]
) -> list[DepEdge]:
    index = _OrderingIndex(dict(nodes), _parent_slots(nodes))
    edges = []
    for location, node in nodes:
        config = node.config or {}
        reads = _output_reads(config)
        origins = ((EDGE_BINDING, sorted(node_deps(config))), (EDGE_NEEDS, node.needs))
        for origin, targets in origins:
            for target in targets:
                source = ids.get(target)
                if source is None:
                    continue
                ordered, reason = index.compare(location, source)
                paths = reads.get(target, ()) if origin == EDGE_BINDING else ()
                edges.append(
                    DepEdge(
                        location,
                        node.id,
                        source,
                        target,
                        ordered,
                        reason,
                        paths,
                        origin,
                    )
                )
    return edges


def _first_ids(nodes):
    result = {}
    for location, node in nodes:
        if node.id:
            result.setdefault(node.id, location)
    return result


def dep_edges_for_root(root: Node) -> list[DepEdge]:
    nodes = walk(root)
    return dep_ordering_edges(nodes, _first_ids(nodes))


def _validate_dep_ordering(res: ValidationResult, edges: list[DepEdge]) -> None:
    if "WF_CYCLE" in {issue.code for issue in res.issues}:
        return
    invalid = (
        edge for edge in edges if edge.origin == EDGE_BINDING and not edge.ordered
    )
    for edge in invalid:
        label = repr(edge.reader_id) if edge.reader_id else edge.reader_path
        _add(
            res,
            "WF_UNORDERED_DEP",
            f"{label} binds {{{{nodes.{edge.producer_id}…}}}}, but nothing guarantees {edge.producer_id!r} has finished when {label} is admitted: {edge.reason}",
            edge.reader_path,
        )


def _validate_needs(res: ValidationResult, edges: list[DepEdge]) -> None:
    cycle = "WF_CYCLE" in {issue.code for issue in res.issues}
    implied = {
        (edge.reader_path, edge.producer_path)
        for edge in edges
        if edge.origin == EDGE_BINDING and edge.ordered
    }
    for edge in filter(lambda edge: edge.origin == EDGE_NEEDS, edges):
        label = repr(edge.reader_id) if edge.reader_id else edge.reader_path
        if edge.ordered:
            if (edge.reader_path, edge.producer_path) in implied:
                _add(
                    res,
                    "WF_REDUNDANT_NEEDS",
                    f"{label} declares needs {edge.producer_id!r} and also binds {{{{nodes.{edge.producer_id}…}}}} — the binding already orders it, so the `needs` can go; keep `needs` for ordering that is not dataflow",
                    edge.reader_path,
                    SEVERITY_WARNING,
                )
        elif not cycle:
            _add(
                res,
                "WF_UNSATISFIABLE_NEEDS",
                f"{label} declares needs {edge.producer_id!r}, but that edge can never be honoured: {edge.reason}",
                edge.reader_path,
            )


def _declared_contracts(nodes: list[tuple[str, Node]]) -> dict[str, dict[str, Any]]:
    declarations = {}
    for _, node in nodes:
        candidate = (node.config or {}).get("output_contract")
        if node.id and isinstance(candidate, dict) and candidate:
            declarations.setdefault(node.id, candidate)
    return declarations


def _guaranteed_keys(contract: dict[str, Any]) -> tuple[str, ...] | None:
    if contract.get("must_be_json"):
        required = contract.get("required_keys")
        if isinstance(required, list) and required:
            return tuple(map(str, required))
    return None


def output_contract_reads(
    nodes: list[tuple[str, Node]], edges: list[DepEdge]
) -> list[ContractRead]:
    declarations = _declared_contracts(nodes)
    resolved = []
    for edge in edges:
        contract = declarations.get(edge.producer_id)
        keys = None if contract is None else _guaranteed_keys(contract)
        resolved.extend(
            ContractRead(
                edge.reader_path,
                edge.reader_id,
                edge.producer_id,
                edge.producer_path,
                path,
                contract is not None,
                keys,
                keys is None or path[0] in keys,
            )
            for path in edge.output_reads
            if path
        )
    return resolved


def contract_reads_for_root(root: Node) -> list[ContractRead]:
    nodes = walk(root)
    return output_contract_reads(nodes, dep_ordering_edges(nodes, _first_ids(nodes)))


def _validate_output_contract(
    res: ValidationResult, nodes: list[tuple[str, Node]], edges: list[DepEdge]
) -> None:
    reads = output_contract_reads(nodes, edges)
    active = bool(_declared_contracts(nodes))
    missing = {}
    for read in reads:
        label = repr(read.reader_id) if read.reader_id else read.reader_path
        field = ".".join(read.path)
        if read.satisfiable:
            if active and not read.declared:
                location, entries = missing.setdefault(
                    read.producer_id, (read.producer_path, {})
                )
                entries[f"{label} reads output.{field}"] = None
            continue
        assert read.guaranteed is not None
        _add(
            res,
            "WF_UNSATISFIABLE_OUTPUT_REF",
            f"{label} reads {{{{nodes.{read.producer_id}.output.{field}}}}}, but {read.producer_id!r} declares output_contract.required_keys {list(read.guaranteed)} — {read.path[0]!r} is not among the keys it guarantees, so the binding cannot resolve",
            read.reader_path,
        )
    for name, (location, entries) in missing.items():
        _add(
            res,
            "WF_UNCONTRACTED_OUTPUT_REF",
            f"{name!r} declares no output_contract, so nothing checks the paths read from it: {'; '.join(entries)}",
            location,
            SEVERITY_WARNING,
        )


def _kahn_levels(
    res: ValidationResult, nodes: list[tuple[str, Node]], ids: dict[str, str]
) -> list[list[str]]:
    prerequisites = {name: set() for name in ids}
    for _, node in nodes:
        if node.id:
            prerequisites[node.id].update(
                target for target in node_deps(node.config or {}) if target in ids
            )
            prerequisites[node.id].update(
                target for target in node.needs if target in ids
            )
    dependents = {name: [] for name in ids}
    counts = {}
    for name, requirements in prerequisites.items():
        counts[name] = len(requirements)
        for required in requirements:
            dependents[required].append(name)
    ready = sorted(name for name, count in counts.items() if count == 0)
    levels = []
    while ready:
        levels.append(ready)
        following = []
        for name in ready:
            del counts[name]
            for dependent in dependents[name]:
                counts[dependent] -= 1
                if counts[dependent] == 0:
                    following.append(dependent)
        ready = sorted(following)
    if counts:
        blocked = sorted(counts)
        _add(
            res,
            "WF_CYCLE",
            f"dependency cycle among: {', '.join(blocked)}",
            ids.get(blocked[0], "root"),
        )
        return []
    return levels


def validate_spec(spec: dict[str, Any], *, strict: bool = False) -> ValidationResult:
    result = ValidationResult()
    if not isinstance(spec, dict):
        _add(result, "WF_NOT_AN_OBJECT", "spec must be a JSON object")
        return result
    name = str(spec.get("name", "") or "")
    if name and not valid_name(name):
        _add(
            result,
            "WF_BAD_NAME",
            "name must be lowercase alphanumeric with hyphens, 1-63 chars",
        )
    value = spec.get("root")
    if not isinstance(value, dict):
        _add(result, "WF_MISSING_ROOT", "spec needs a `root` node")
        return result
    parsed = None
    try:
        parsed = Node.from_dict(value)
    except ValueError as error:
        _add(result, "WF_UNKNOWN_NODE_KIND", str(error), "root")
    except BindingError as error:
        _add(result, "WF_BAD_BINDING", str(error), "root")
    if parsed is None:
        return result
    tree = validate_node_tree(parsed, strict=strict)
    result.issues += tree.issues
    _validate_wip_invariant(result, spec, parsed)
    if result.ok:
        result.levels = tree.levels
    return result


def _validate_wip_invariant(
    res: ValidationResult, spec: dict[str, Any], root: Node
) -> None:
    from gideon.automation.workflows.execution_hints import from_runtime_hints

    if from_runtime_hints(spec.get("runtime_hints")).single_active_feature:
        candidates = (
            (location, node)
            for location, node in walk(root)
            if node.kind is NodeKind.FOREACH
        )
        for location, node in candidates:
            limit = (node.config or {}).get("max_concurrency")
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 1:
                _add(
                    res,
                    "WF_WIP_CONTRADICTION",
                    f"`single_active_feature` declares WIP=1, but this foreach declares max_concurrency={limit} — the engine will run one item at a time, so the declaration is false. Drop one of the two.",
                    location,
                )
