"""Planner draft inspection, grounding assembly and repair emission contracts."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
MAX_REPAIR_ATTEMPTS = 3
MAX_REPORTED_ISSUES = 6
_WORK_KINDS = frozenset({"stage", "infer", "action", "transform", "subworkflow"})
_ID_KINDS = ("stage", "infer", "action", "transform", "branch")
_BINDING_ROOTS = frozenset(
    {
        "inputs",
        "nodes",
        "item",
        "iter",
        "last",
        "siblings",
        "previous",
        "brief",
        "secret",
    }
)

_HARD_REQUIREMENTS = (
    "# Produce a workflow spec",
    "",
    "## Hard requirements — not suggestions",
    "",
    "1. Use ONLY the node kinds, providers and binding roots listed below. Anything else does not "
    "exist and the spec will be rejected.",
    "2. Every work node needs an `id`. Ids must be unique.",
    "3. Action arguments go under `config.with` — never flat beside `provider`.",
    "4. Every plan needs a machine-checkable stopping condition: a gate, a verification, or a "
    "bounded loop. A plan that cannot say when it is done is not a plan.",
    '5. If you cannot plan this with what exists, return `{"cannot_plan": "<why>"}` instead of a '
    "spec. Declining is a correct answer; a plausible spec for an impossible request is not.",
    "",
)


@dataclass
class SelfCheck:
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def note(self) -> str:
        if not self.issues:
            return ""
        limit = min(MAX_REPORTED_ISSUES, len(self.issues))
        numbered = [f"{index + 1}. {self.issues[index]}" for index in range(limit)]
        remainder = len(self.issues) - limit
        if remainder:
            numbered += [
                f"({remainder} further issues will be reported after these are fixed.)"
            ]
        return "\n".join(
            (
                "This spec was rejected. Fix exactly these and return the CORRECTED spec:",
                *numbered,
                "Return the whole corrected spec, not a diff, and change nothing that was not listed.",
            )
        )


def _child_values(node):
    yield from node.get("children") or []
    body = node.get("body")
    if isinstance(body, dict):
        yield body
    cases = node.get("cases")
    if isinstance(cases, dict):
        yield from cases.values()
    default = node.get("default")
    if isinstance(default, dict):
        yield default


def _walk(node: Any, out: list[dict] | None = None) -> list[dict]:
    result = [] if out is None else out
    cursors = [iter((node,))]
    while cursors:
        try:
            candidate = next(cursors[-1])
        except StopIteration:
            cursors.pop()
            continue
        if isinstance(candidate, dict):
            result.append(candidate)
            cursors.append(iter(_child_values(candidate)))
    return result


class _DraftRules:
    def __init__(self, nodes):
        self.nodes = nodes

    def ids(self):
        occurrences = {}
        for node in self.nodes:
            name = str(node.get("id", "") or "")
            if name:
                count = occurrences.get(name, 0)
                occurrences[name] = count + 1
                if count:
                    yield f"node id `{name}` is used more than once — ids must be unique, because `{{{{nodes.{name}.output}}}}` would be ambiguous"
            elif node.get("kind") in _ID_KINDS:
                yield f"a `{node.get('kind')}` node has no `id` — give every work node an id so later nodes can bind to its output"

    def kinds(self):
        from gideon.automation.workflows.models import NodeKind

        vocabulary = {kind.value for kind in NodeKind}
        for node in self.nodes:
            kind = str(node.get("kind", "") or "")
            if kind not in vocabulary:
                yield f"`{kind or '(missing)'}` is not a node kind. The only kinds are: " + ", ".join(
                    sorted(vocabulary)
                )

    def configurations(self, kind):
        for node in self.nodes:
            if node.get("kind") == kind:
                yield node, node.get("config") or {}

    def gates(self):
        for node, config in self.configurations("gate"):
            kind = str(config.get("kind", "") or "")
            label = node.get("id", "?")
            if not kind:
                yield f"gate `{label}` has no `config.kind` — set it to approval, judge, expression, verify_command, verify_script, event or ladder"
            required = {
                "judge": (
                    "prompt",
                    f"judge gate `{label}` has no `config.prompt` — a judge with no criteria approves everything, which is worse than no gate",
                ),
                "expression": (
                    "expr",
                    f"expression gate `{label}` has no `config.expr`",
                ),
            }.get(kind)
            if required is not None and not config.get(required[0]):
                yield required[1]

    def foreach(self):
        for node, config in self.configurations("foreach"):
            label = node.get("id", "?")
            if not config.get("items"):
                yield f"foreach `{label}` has no `config.items` binding — it needs to know what to iterate over"
            if not isinstance(node.get("body"), dict):
                yield f"foreach `{label}` has no `body`"

    def loops(self):
        for node, config in self.configurations("loop"):
            label = node.get("id", "?")
            mode = str(config.get("mode", "counted") or "counted")
            requirement = {
                "until": (
                    "condition",
                    f"loop `{label}` is mode `until` with no `config.condition` — it would exit immediately",
                ),
                "counted": ("n", f"loop `{label}` is mode `counted` with no `n`"),
                "until_cancelled": (
                    "max_iterations",
                    f"loop `{label}` is `until_cancelled` — it needs either a `join: any` parallel sibling to stop it or a `max_iterations` cap, or the run never ends",
                ),
            }.get(mode)
            if requirement is not None and not config.get(requirement[0]):
                yield requirement[1]
            if not isinstance(node.get("body"), dict):
                yield f"loop `{label}` has no `body`"

    def terminal(self):
        workers = filter(
            lambda node: str(node.get("kind", "")) in _WORK_KINDS, self.nodes
        )
        if next(workers, None) is None:
            yield "this spec has no work node (stage/infer/action/transform/subworkflow) — containers and gates alone produce nothing"

    def stopping(self):
        work_count = sum(
            str(node.get("kind", "")) in _WORK_KINDS for node in self.nodes
        )
        if work_count < 2:
            return
        for node in self.nodes:
            kind = str(node.get("kind", ""))
            config = node.get("config") or {}
            if kind in ("gate", "branch"):
                return
            if (
                kind == "loop"
                and str(config.get("mode", "counted") or "counted") != "until_cancelled"
            ):
                return
        yield "nothing in this spec says when the work is DONE. Add a gate (judge, expression or verify_command) after the work, or bound a loop with a condition — a sequence of stages reports success whether or not it achieved the goal"

    def bindings(self, spec):
        from gideon.automation.workflows.bindings import refs_in

        names = {str(node.get("id", "") or "") for node in self.nodes if node.get("id")}
        expressions = set(refs_in(spec))
        for expression in expressions:
            target = expression.split("|", 1)[0].strip()
            root = target.split(".")[0].split(":")[0]
            if root not in _BINDING_ROOTS:
                yield f"`{{{{{expression}}}}}` starts with `{root}`, which is not a binding root. The roots are: " + ", ".join(
                    sorted(_BINDING_ROOTS)
                )
            else:
                segments = tuple(part for part in target.split(".") if part)
                if root == "nodes" and len(segments) > 1 and segments[1] not in names:
                    yield f"`{{{{{expression}}}}}` references node `{segments[1]}`, which does not exist in this spec. Existing ids: " + ", ".join(
                        sorted(names)
                    )


def _check_ids(nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).ids())


def _check_kinds(nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).kinds())


def _check_gates(nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).gates())


def _check_foreach(nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).foreach())


def _check_loops(nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).loops())


def _check_terminal(root: dict, nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).terminal())


def _check_stopping_condition(nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).stopping())


def _check_slots(spec: dict[str, Any], check: SelfCheck, *, shape_name: str) -> None:
    placeholders = {
        match.group(1) for match in re.finditer(r"<<([a-z_]+)>>", json.dumps(spec))
    }
    check.issues.extend(
        f"the `{slot}` slot was never filled — replace `<<{slot}>>` with the real content"
        for slot in sorted(placeholders)
    )


def _check_bindings(spec: dict[str, Any], nodes: list[dict], check: SelfCheck) -> None:
    check.issues.extend(_DraftRules(nodes).bindings(spec))


def self_check(spec: dict[str, Any], *, shape_name: str = "") -> SelfCheck:
    result = SelfCheck()
    root = spec.get("root")
    if not isinstance(root, dict):
        result.issues += [
            "the spec has no `root` object — every spec needs one root node"
        ]
        return result
    nodes = _walk(root)
    if not nodes:
        result.issues += ["`root` has no nodes"]
        return result
    rules = _DraftRules(nodes)
    for phase in (
        rules.ids,
        rules.kinds,
        rules.gates,
        rules.foreach,
        rules.loops,
        rules.terminal,
        rules.stopping,
    ):
        result.issues.extend(phase())
    _check_slots(spec, result, shape_name=shape_name)
    result.issues.extend(rules.bindings(spec))
    return result


class _PromptSections:
    def __init__(self):
        self.sections = [list(_HARD_REQUIREMENTS)]

    def grounding(self, bundle):
        if bundle is None:
            return
        self.sections.append([bundle.index(), ""])
        if not getattr(bundle, "structured_output", False):
            self.sections.append(
                [
                    "Return ONE JSON object and nothing else — no prose before or after it, no markdown fence.",
                    "",
                ]
            )

    def shape(self, shape, reason):
        from gideon.automation.workflows import patterns

        if shape is None:
            self.sections.append([patterns.catalog(), ""])
            return
        self.sections.append(
            [
                f"## Use this shape: `{shape.name}`",
                shape.summary,
                f"(chosen because: {reason})" if reason else "",
                "",
                "Fill its slots. The structure below already runs — replace the `<<slot>>` placeholders and adjust ids/prompts, but do not restructure it without reason:",
                "```json",
                json.dumps(shape.skeleton, indent=2),
                "```",
                f"NOT the right shape when: {shape.when_not}",
                "",
                "If this shape genuinely does not fit, say so in one line and generate freeform.",
                "",
            ]
        )

    def context(self, title, content):
        if content:
            self.sections.append([title, content, ""])

    def finish(self, intent):
        self.sections.append(["## The intent", intent])
        return "\n".join(
            line for section in self.sections for line in section if line is not None
        )


def planning_prompt(
    intent: str,
    *,
    bundle: Any,
    shape: Any = None,
    shape_reason: str = "",
    brief: str = "",
    codebase_context: str = "",
) -> str:
    from gideon.automation.workflows import patterns

    prompt = _PromptSections()
    prompt.grounding(bundle)
    prompt.shape(shape, shape_reason)
    prompt.context("## What is already known about this project", brief)
    prompt.context("## The codebase this targets", codebase_context)
    return prompt.finish(intent)


def repair_prompt(spec: dict[str, Any], check: SelfCheck, *, attempt: int) -> str:
    title = f"## Correction (attempt {attempt} of {MAX_REPAIR_ATTEMPTS})"
    guidance = check.note()
    original = json.dumps(spec, indent=2)
    return f"{title}\n\n{guidance}\n\nThe spec you produced:\n```json\n{original}\n```"


def parse_emission(raw: Any) -> tuple[dict[str, Any] | None, str]:
    decoded = raw
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError):
            return None, ""
    if isinstance(decoded, dict):
        if decoded.get("cannot_plan"):
            return None, str(decoded["cannot_plan"])
        if isinstance(decoded.get("root"), dict):
            return decoded, ""
        nested = decoded.get("spec")
        if isinstance(nested, dict):
            if isinstance(nested.get("root"), dict):
                return nested, ""
    return None, ""


def spec_json_schema() -> dict[str, Any]:
    """The `oneOf[WorkflowSpec, {cannot_plan}]` schema for structured emission.

    Deliberately shallow on the node tree: a fully recursive JSON Schema for the twelve node kinds
    is large enough to crowd out the grounding it is meant to complement, and the mechanical
    self-check catches structural problems more cheaply than a schema does. The schema's job here
    is to make DECLINING a first-class option the model can see in its output contract.
    """
    from gideon.automation.workflows.models import NodeKind

    node = {
        "type": "object",
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": [k.value for k in NodeKind]},
            "id": {"type": "string"},
            "config": {"type": "object"},
            "children": {"type": "array", "items": {"type": "object"}},
            "body": {"type": "object"},
            "cases": {"type": "object"},
        },
    }
    return {
        "oneOf": [
            {
                "type": "object",
                "required": ["root"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "inputs": {"type": "object"},
                    "root": node,
                },
            },
            {
                "type": "object",
                "required": ["cannot_plan"],
                "properties": {"cannot_plan": {"type": "string"}},
            },
        ]
    }
