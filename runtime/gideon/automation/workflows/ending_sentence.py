"""Derive the concise sentence that explains a failed workflow's ending."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator

from gideon.automation.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeKind,
    spec_path,
    walk,
)
from gideon.automation.workflows.tick import derive_state, tolerate_failures

if TYPE_CHECKING:
    from gideon.automation.workflows.controller import RunController


_LAST_SEGMENT = re.compile(
    r"\.(children\[(\d+)\]|cases\[[^\]]*\]|default|body(?:[#@]\d+)?)$"
)
_FAULTS = frozenset(
    {
        InstanceState.FAILED,
        InstanceState.SCOPE_VIOLATION,
        InstanceState.BLOCKED,
        InstanceState.ESCALATED,
    }
)
_RAN = SUCCESS_STATES | _FAULTS
_KINDS = ("failed", "escalated", "blocked")


def clause(text: str) -> str:
    return " ".join(str(text or "").split()).rstrip(" .")


def _name(ctl: RunController, nodes: dict[str, Node], path: str) -> str:
    node = nodes.get(spec_path(path))
    label = str((getattr(node, "label", "") or getattr(node, "id", "")) or path)
    inst = ctl.instances.get(path)
    item = str(getattr(inst, "item_label", "") or "") if inst is not None else ""
    return f"“{label}” ({item})" if item else f"“{label}”"


def _kind(state: InstanceState) -> str:
    if state == InstanceState.ESCALATED:
        return "escalated"
    if state == InstanceState.BLOCKED:
        return "blocked"
    return "failed"


def _verb(kind: str, *, plural: bool = False) -> str:
    if kind == "blocked":
        return "were blocked" if plural else "was blocked"
    return kind


def _listed(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _first_part(
    ctl: RunController, nodes: dict[str, Node], path: str, *, joined: str = ""
) -> str:
    inst = ctl.instances[path]
    cause = clause(inst.failure.cause_plain if inst.failure else "")
    head = f"{_name(ctl, nodes, path)}{joined} {_verb(_kind(inst.state))}"
    return f"{head}: {cause}." if cause else f"{head}."


def _failed_steps(ctl: RunController) -> list[str]:
    states = {path: inst.state for path, inst in ctl.instances.items()}

    def derived(node: Node, path: str) -> InstanceState:
        return derive_state(
            node,
            path,
            states,
            declined_edges=ctl._declined_edges,
            outputs=ctl._outputs,
            inputs=ctl.run.inputs,
            iterations=ctl._iterations,
        )

    found: list[str] = []

    def visit(node: Node, path: str, state: InstanceState) -> None:
        if state not in _FAULTS:
            return
        if not node.is_container or states.get(path) in _FAULTS:
            found.append(path)
            return
        for child, child_path, child_state in _parts(ctl, node, path, derived):
            visit(child, child_path, child_state)

    visit(ctl.root, "root", derived(ctl.root, "root"))
    return found


def _parts(
    ctl: RunController, node: Node, path: str, derived: Any
) -> Iterator[tuple[Node, str, InstanceState]]:
    if node.kind in (NodeKind.SEQUENCE, NodeKind.PARALLEL):
        paths = [f"{path}.children[{i}]" for i in range(len(node.children))]
        masked = tolerate_failures(
            node.children,
            [
                derived(child, child_path)
                for child, child_path in zip(node.children, paths)
            ],
        )
        yield from zip(node.children, paths, masked)
    elif node.kind == NodeKind.FOREACH and node.body is not None:
        prefix = f"{path}.body#"
        items: set[int] = set()
        for other in ctl.instances:
            head = (
                other[len(prefix) :].split(".", 1)[0]
                if other.startswith(prefix)
                else ""
            )
            if head.isdigit():
                items.add(int(head))
        for index in sorted(items):
            item_path = f"{prefix}{index}"
            yield node.body, item_path, derived(node.body, item_path)
    elif node.kind == NodeKind.BRANCH:
        for label, case in node.cases.items():
            case_path = f"{path}.cases[{label}]"
            yield case, case_path, derived(case, case_path)
        if node.default_case is not None:
            default_path = f"{path}.default"
            yield node.default_case, default_path, derived(
                node.default_case, default_path
            )


def _followers(ctl: RunController, path: str, nodes: dict[str, Node]) -> list[str]:
    out: list[str] = []
    cursor = path
    while True:
        match = _LAST_SEGMENT.search(cursor)
        if match is None:
            return out
        parent = cursor[: match.start()]
        container = nodes.get(spec_path(parent))
        if (
            match.group(2) is not None
            and container is not None
            and container.kind == NodeKind.SEQUENCE
        ):
            index = int(match.group(2))
            out.extend(
                f"{parent}.children[{i}]"
                for i in range(index + 1, len(container.children))
            )
        cursor = parent


def _ran_after(ctl: RunController, path: str, nodes: dict[str, Node]) -> bool:
    for later in _followers(ctl, path, nodes):
        prefix = f"{later}."
        if any(
            (other == later or other.startswith(prefix)) and inst.state in _RAN
            for other, inst in ctl.instances.items()
        ):
            return True
    return False


def for_failures(ctl: RunController) -> str:
    """Explain causative failures, excluding tolerated failures and parallel siblings."""
    paths = _failed_steps(ctl)
    if not paths:
        return ""
    nodes = dict(walk(ctl.root))
    went_on = [path for path in paths if _ran_after(ctl, path, nodes)]
    first, rest = paths[0], paths[1:]
    if went_on == [first] and not rest:
        return (
            f"The run continued past {_first_part(ctl, nodes, first, joined=', which')}"
        )
    parts = []
    if went_on:
        parts.append(
            f"The run continued past {_listed([_name(ctl, nodes, path) for path in went_on])}."
        )
    parts.append(_first_part(ctl, nodes, first))
    for kind in _KINDS:
        named = [
            _name(ctl, nodes, path)
            for path in rest
            if _kind(ctl.instances[path].state) == kind
        ]
        if named:
            parts.append(f"{_listed(named)} {_verb(kind, plural=len(named) > 1)} too.")
    return " ".join(parts)
