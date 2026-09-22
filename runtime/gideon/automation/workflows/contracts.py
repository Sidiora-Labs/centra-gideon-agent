"""Derive launch inputs, work verification and decision authority from workflow structure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

WORK_KINDS = frozenset({"stage", "infer", "action", "transform", "subworkflow"})

MACHINE_VERIFIED_GATES = frozenset(
    {"judge", "expression", "verify_command", "verify_script", "ladder"}
)


@dataclass
class ParamSpec:
    """One derived parameter — a field the launch form must ask for."""

    name: str
    used_by: list[str] = field(default_factory=list)
    required: bool = True
    declared_type: str = "string"
    help_text: str = ""
    default: Any = None
    auto_filled: bool = False

    def to_dict(self) -> dict[str, Any]:
        record = dict(
            name=self.name, used_by=list(self.used_by), required=self.required
        )
        record.update(
            type=self.declared_type,
            help=self.help_text,
            default=self.default,
            auto=self.auto_filled,
        )
        return record


def resolve_unfilled_inputs(spec: dict[str, Any]) -> list[ParamSpec]:
    return _InputCatalog(spec).derive()


def declared_but_unused(spec: dict[str, Any]) -> list[str]:
    referenced = {parameter.name for parameter in resolve_unfilled_inputs(spec)}
    raw = spec.get("inputs")
    names = raw if isinstance(raw, dict) else {}
    return sorted(name for name in names if name not in referenced)


def template_types(spec: dict[str, Any]) -> str:
    parameters = resolve_unfilled_inputs(spec)
    if not parameters:
        return "(this template takes no parameters)"
    rows = []
    for parameter in parameters:
        name = parameter.name + ("" if parameter.required else "?")
        hint = parameter.help_text or "used by: " + ", ".join(parameter.used_by)
        rows.append(f"  {name}: {parameter.declared_type},  // {hint}")
    return "\n".join(("{", *rows, "}"))


@dataclass
class Extraction:
    """The result of filling parameters from a conversation.

    `all_filled` is deliberately RECOMPUTED rather than trusted: the plan requires re-validating
    the model's output against the schema, because a model that says `all_filled: true` while
    omitting a required field produces a run that dies on its first binding.
    """

    extracted: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    follow_up: str = ""
    failed: bool = False

    @property
    def all_filled(self) -> bool:
        return not self.missing and not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "extracted": dict(self.extracted),
            "missing": list(self.missing),
            "follow_up": self.follow_up,
            "all_filled": self.all_filled,
            "extraction_failed": self.failed,
        }


def apply_extraction(
    params: list[ParamSpec],
    raw: Any,
    *,
    declined: set[str] | None = None,
) -> Extraction:
    fill = _ParameterFill(params, declined or set())
    return fill.apply(raw)


def extract_inputs(
    spec: dict[str, Any],
    candidates: Any,
    *,
    declined: Iterable[str] = (),
) -> Extraction:
    """Fill a workflow's derived parameters from conversation candidates.

    Only unfenced user content is eligible.  This keeps the input contract next to the
    parameter derivation and gives every surface the same latest-value, schema-filtering,
    default, and follow-up rules.
    """
    parameters = resolve_unfilled_inputs(spec)
    if not isinstance(candidates, (list, tuple)):
        return apply_extraction(parameters, None, declined=set(declined))

    extracted: dict[str, Any] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("role", "")) != "user":
            continue
        if candidate.get("fenced") or candidate.get("pasted"):
            continue
        values = candidate.get("values")
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if value not in (None, ""):
                extracted[str(key)] = value
    return apply_extraction(
        parameters, {"extracted": extracted}, declined=set(declined)
    )


def _follow_up(missing: list[str], by_name: dict[str, ParamSpec]) -> str:
    descriptions = []
    for name in missing:
        parameter = by_name.get(name)
        if parameter and parameter.help_text:
            descriptions.append(name + " (" + parameter.help_text + ")")
        else:
            descriptions.append(str(name))
    prefix = "I need one more thing: " if len(descriptions) == 1 else "I still need: "
    return prefix + "; ".join(descriptions)


@dataclass
class StageContract:
    """One stage's reviewable claim."""

    node_id: str
    kind: str = ""
    scope: str = ""
    done_means: str = ""
    exclusions: list[str] = field(default_factory=list)
    verification: str = ""
    feeds_verified: bool = False

    @property
    def verifiable(self) -> bool:
        return bool(self.verification)

    def to_dict(self) -> dict[str, Any]:
        record = dict(
            node_id=self.node_id,
            kind=self.kind,
            scope=self.scope,
            done_means=self.done_means,
            exclusions=list(self.exclusions),
            verification=self.verification,
            verifiable=self.verifiable,
            feeds_verified=self.feeds_verified,
        )
        if not self.verifiable:
            record["review_note"] = (
                "unverifiable — needs an approval gate or a human check"
            )
        return record


def derive_contracts(spec: dict[str, Any]) -> list[StageContract]:
    root = _contract_root(spec)
    if root is _NO_ROOT:
        return []
    from gideon.automation.workflows.models import walk

    return _ContractCatalog(list(walk(root))).derive()


def _mark_feeds_verified(
    nodes: list[tuple[str, Any]], by_id: dict[str, StageContract]
) -> None:
    for _, node in nodes:
        contract = by_id.get(node.id or "")
        if contract is None or not contract.verifiable:
            continue
        for source in _bound_names(node.config or {}, "nodes"):
            producer = by_id.get(source)
            if producer is not None and producer is not contract:
                producer.feeds_verified = True


def _scope_of(node: Any, cfg: dict[str, Any]) -> str:
    for key in ("label", "prompt"):
        text = str(cfg.get(key, "") or "")
        if text:
            return text if key == "label" else " ".join(text.split())[:140]
    provider = str(cfg.get("provider", "") or "")
    return node.kind.value if not provider else f"calls `{provider}`"


def _verification_for(
    index: int, path: str, nodes: list[tuple[str, Any]], cfg: dict[str, Any]
) -> str:
    if cfg.get("required_artifacts"):
        return "artifact"
    parent = path.rsplit(".", 1)[0] if "." in path else path
    for candidate_path, candidate in nodes[index + 1 :]:
        if candidate_path.startswith(parent):
            mechanism = _verification_node(candidate)
            if mechanism:
                return mechanism
    for candidate_path, candidate in nodes:
        if candidate.kind.value == "loop" and path.startswith(candidate_path + "."):
            mechanism = _verification_node(candidate)
            if mechanism:
                return mechanism
    return ""


def contract_issues(contracts: list[StageContract]) -> list[str]:
    if not contracts:
        return []
    issues = []
    machine_verified = any(
        contract.verification in ("gate", "loop-condition", "artifact")
        for contract in contracts
    )
    if not machine_verified and any(
        contract.kind in ("stage", "infer") for contract in contracts
    ):
        issues.append(
            "no stage in this plan has a machine-checkable verification — add a "
            "judge/expression gate, a bounded loop, or a required artifact. Goal, "
            "verification and stopping condition is the minimal triple"
        )
    for contract in contracts:
        if contract.verifiable:
            if contract.verification == "approval":
                issues.append(
                    f"stage `{contract.node_id}` is verified only by a human approval — fine for a "
                    "judgement call, but it is not a machine check"
                )
        elif not contract.feeds_verified and contract.kind not in (
            "action",
            "transform",
        ):
            issues.append(
                f"stage `{contract.node_id}` is unverifiable — it needs an approval gate or a "
                "human check, or the run will report success without anyone confirming it"
            )
    return issues


@dataclass
class DecisionNode:
    """A gate, typed by whether the run can proceed without its answer."""

    node_id: str
    blocking: bool
    reason: str
    gate_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "blocking": self.blocking,
            "severity": "blocking" if self.blocking else "open-decision",
            "reason": self.reason,
            "gate_kind": self.gate_kind,
        }


def type_decisions(spec: dict[str, Any]) -> list[DecisionNode]:
    root = _contract_root(spec)
    if root is _NO_ROOT:
        return []
    from gideon.automation.workflows.models import walk

    nodes = list(walk(root))
    consumed = {
        name for _, node in nodes for name in _bound_names(node.config or {}, "nodes")
    }
    return [
        _decision_for(path, node, consumed)
        for path, node in nodes
        if node.kind.value == "gate"
    ]


def open_decisions(decisions: list[DecisionNode]) -> list[dict[str, Any]]:
    """The non-blocking ones, for the finished run's summary."""
    return [d.to_dict() for d in decisions if not d.blocking]


_NO_ROOT = object()


def _contract_root(spec, *, report=False):
    from gideon.automation.workflows.models import Node

    raw = spec.get("root")
    if not isinstance(raw, dict):
        return _NO_ROOT
    try:
        return Node.from_dict(raw)
    except Exception:
        if report:
            logger.debug("unparseable root — no derived params", exc_info=True)
        return _NO_ROOT


def _bound_names(configuration, namespace):
    from gideon.automation.workflows.bindings import refs_in

    for expression in refs_in(configuration):
        parts = list(filter(None, expression.partition("|")[0].strip().split(".")))
        if len(parts) >= 2 and parts[0] == namespace:
            yield parts[1]


class _InputCatalog:
    def __init__(self, spec):
        self.spec = spec

    def derive(self) -> list[ParamSpec]:
        from gideon.automation.workflows.models import walk

        root = _contract_root(self.spec, report=True)
        if root is _NO_ROOT:
            return []
        raw = self.spec.get("inputs")
        declared = raw if isinstance(raw, dict) else {}
        uses: dict[str, list[str]] = {}
        for _, node in walk(root):
            label = node.id or node.kind.value
            for name in _bound_names(node.config or {}, "inputs"):
                readers = uses.setdefault(name, [])
                if label not in readers:
                    readers.append(label)
        return [
            self.parameter(name, uses[name], declared.get(name))
            for name in sorted(uses)
        ]

    @staticmethod
    def parameter(name, readers, metadata) -> ParamSpec:
        fields = metadata if isinstance(metadata, dict) else {}
        defaulted = "default" in fields and fields.get("default") not in (None, "")
        return ParamSpec(
            name=name,
            used_by=readers,
            required=bool(fields.get("required", not defaulted)),
            declared_type=str(fields.get("type", "string") or "string"),
            help_text=str(fields.get("help", "") or ""),
            default=fields.get("default"),
        )


class _ParameterFill:
    def __init__(self, parameters, declined):
        self.parameters, self.declined = parameters, declined
        self.result = Extraction()

    def apply(self, payload) -> Extraction:
        result = self.result
        if not isinstance(payload, dict):
            result.failed = True
            result.missing = [
                parameter.name for parameter in self.parameters if parameter.required
            ]
            result.follow_up = "extraction_failed"
            return result
        supplied = payload.get("extracted")
        if not isinstance(supplied, dict):
            allowed = {parameter.name for parameter in self.parameters}
            supplied = {key: value for key, value in payload.items() if key in allowed}
        schema = {parameter.name: parameter for parameter in self.parameters}
        result.extracted = {
            key: value
            for key, value in supplied.items()
            if key in schema and value not in (None, "")
        }
        for parameter in self.parameters:
            name = parameter.name
            if name in result.extracted:
                continue
            if parameter.default not in (None, ""):
                result.extracted[name] = parameter.default
            elif parameter.required and name not in self.declined:
                result.missing.append(name)
        if result.missing:
            result.follow_up = _follow_up(result.missing, schema)
        else:
            optional = [
                parameter.name
                for parameter in self.parameters
                if not parameter.required
                and parameter.name not in result.extracted
                and parameter.name not in self.declined
            ]
            if optional:
                result.follow_up = (
                    "Optionally, you can also set: " + ", ".join(optional) + "."
                )
        return result


class _ContractCatalog:
    def __init__(self, nodes):
        self.nodes = nodes

    def derive(self) -> list[StageContract]:
        result, identified = [], {}
        for position, (path, node) in enumerate(self.nodes):
            if node.kind.value not in WORK_KINDS:
                continue
            settings = node.config or {}
            contract = StageContract(
                node_id=node.id or path,
                kind=node.kind.value,
                scope=_scope_of(node, settings),
                done_means=str(settings.get("done_means", "") or ""),
                exclusions=list(map(str, settings.get("exclusions") or [])),
            )
            contract.verification = _verification_for(
                position, path, self.nodes, settings
            )
            identified[contract.node_id] = contract
            if not contract.done_means and contract.verification:
                contract.done_means = (
                    f"established by the {contract.verification} that follows it"
                )
            result.append(contract)
        _mark_feeds_verified(self.nodes, identified)
        return result


def _verification_node(node) -> str:
    if node.kind.value == "gate":
        kind = str((node.config or {}).get("kind", "") or "")
        if kind in MACHINE_VERIFIED_GATES:
            return "gate"
        if kind == "approval":
            return "approval"
    elif node.kind.value == "loop":
        mode = str((node.config or {}).get("mode", "") or "")
        if mode in ("until", "until_dry", "counted"):
            return "loop-condition"
    return ""


def _decision_for(path, node, consumed) -> DecisionNode:
    settings = node.config or {}
    identity = node.id or path
    kind = str(settings.get("kind", "") or "")
    risk = str(settings.get("risk", "") or "").lower()
    if risk in ("destructive", "high"):
        decision = True, "destructive risk — always blocking, whatever consumes it"
    elif kind == "approval":
        decision = True, "an approval gate exists to pause for a person"
    elif identity in consumed:
        decision = (
            True,
            "its output feeds a downstream binding — the run needs the answer",
        )
    else:
        decision = (
            False,
            "nothing binds to it and no execution path changes — answerable after the run",
        )
    return DecisionNode(identity, *decision, kind)
