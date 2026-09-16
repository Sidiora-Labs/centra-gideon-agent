"""Derive evaluation fixtures, structural checks and judge requirements from templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.contracts import (
    MACHINE_VERIFIED_GATES,
    derive_contracts,
    resolve_unfilled_inputs,
)

FIXTURES_PER_TEMPLATE = 3

_DETERMINISTIC_NOTE = (
    "all nodes are deterministic — the outputs are facts, not opinions"
)
_NO_STANDARD_NOTE = (
    "no example_outputs declared, so there is no stated standard to grade against"
)
_MISSING = object()


@dataclass
class Fixture:
    """One representative intent and what it should produce.

    `expected_params` is the required subset, not the full set: asserting the exact set would make
    every added optional parameter a test failure, which trains a maintainer to update expectations
    without reading them.
    """

    intent: str
    expected_template: str
    expected_params: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "expected_template": self.expected_template,
            "expected_params": list(self.expected_params),
        }


@dataclass
class EvalSpec:
    """One template's derived benchmark.

    The three check lists are separate because they fail for different reasons and cost different
    amounts. Structural checks are free and deterministic; parameterization checks are free and
    catch the launch-form drift; graded checks cost a model call and are the only ones that can be
    wrong about a passing template.
    """

    template: str
    fixtures: list[Fixture] = field(default_factory=list)
    structural_checks: list[str] = field(default_factory=list)
    parameterization_checks: list[str] = field(default_factory=list)
    graded_checks: list[str] = field(default_factory=list)
    graded_note: str = ""

    @property
    def free(self) -> bool:
        """Whether this spec runs with no model call. The CI-eligible subset."""
        return not self.graded_checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
            "structural_checks": list(self.structural_checks),
            "parameterization_checks": list(self.parameterization_checks),
            "graded_checks": list(self.graded_checks),
            "graded_note": self.graded_note,
            "free": self.free,
        }


def derive_eval_spec(
    name: str, spec: dict[str, Any], metadata: dict[str, Any]
) -> EvalSpec:
    """Derive one template's eval spec from the template itself.

    Everything comes from the artifact: fixtures from the match metadata a user would actually type,
    parameterization from the tree's own bindings, structural checks from the nodes that are
    present. Nothing is asserted that the template does not itself claim — an eval that tested an
    aspiration would fail on a correct template.
    """
    fixtures = _fixtures(name, spec, metadata)
    structural = _structural_checks(spec)
    parameterization = _param_checks(spec)
    graded, note = _judge_standard(spec, metadata)
    return EvalSpec(
        template=name,
        fixtures=fixtures,
        structural_checks=structural,
        parameterization_checks=parameterization,
        graded_checks=graded,
        graded_note=note,
    )


def _fixtures(
    name: str, spec: dict[str, Any], metadata: dict[str, Any]
) -> list[Fixture]:
    """Fixture intents built from the template's own matchable surface.

    Keywords first (what a user types), then example outputs phrased as a request (an intent
    resembles its desired output more than it resembles prose about a workflow — session 40's T2
    finding). Required parameters come from the derived schema so a fixture cannot ask for a
    parameter the tree does not read.
    """
    required = tuple(
        param.name for param in resolve_unfilled_inputs(spec) if param.required
    )
    return [
        Fixture(
            intent=intent,
            expected_template=name,
            expected_params=list(required),
        )
        for intent in list(_matchable_intents(name, metadata))[:FIXTURES_PER_TEMPLATE]
    ]


def _matchable_intents(name: str, metadata: dict[str, Any]) -> list[str]:
    keyword_fragments = list(
        filter(lambda value: str(value).strip(), metadata.get("keywords") or [])
    )
    keywords = list(map(str, keyword_fragments))
    intents = [" ".join(keywords[:4])] if keywords else []
    examples = (metadata.get("example_outputs") or [])[:2]
    for value in map(str, examples):
        trimmed = value.strip()
        if trimmed:
            intents.extend(("I need " + trimmed[0].lower() + trimmed[1:],))
    return intents or [name.replace("-", " ")]


def _structural_checks(spec: dict[str, Any]) -> list[str]:
    nodes = _walk(spec.get("root"))
    kinds = {node.get("kind") for node in nodes}
    identified = sorted(str(node.get("id")) for node in nodes if node.get("id"))
    checks = (
        ["every node id present: " + ", ".join(identified[:8])] if identified else []
    )
    contracts = derive_contracts(spec)
    verified = sum(
        1
        for contract in contracts
        if contract.verification in MACHINE_VERIFIED_GATES or contract.verifiable
    )
    if verified:
        checks.append(f"{verified} of {len(contracts)} stages remain machine-verified")
    for kind, statement in (
        ("loop", "every loop keeps a bounded exit condition"),
        ("foreach", "every foreach keeps its items binding"),
    ):
        if kind in kinds:
            checks.append(statement)
    if "gate" in kinds:
        gate_ids = sorted(
            str(node.get("id")) for node in nodes if node.get("kind") == "gate"
        )
        checks.append("gates survive parameterization: " + ", ".join(gate_ids))
    return checks


def _param_checks(spec: dict[str, Any]) -> list[str]:
    """Checks over the derived launch form.

    These catch the two failures session 42 measured on the real library, in both directions: a
    declared input nothing reads (a control that silently does nothing) and a binding with no
    declared input (a run that dies on its first binding).
    """
    params = resolve_unfilled_inputs(spec)
    if not params:
        return ["takes no parameters — launch form stays empty"]
    groups: dict[str, list[str]] = {
        "required parameters asked": [],
        "optional parameters defaulted, not asked": [],
    }
    for param in params:
        label = (
            "required parameters asked"
            if param.required
            else "optional parameters defaulted, not asked"
        )
        groups[label].append(param.name)
    checks = [
        f"{label}: {', '.join(sorted(names))}"
        for label, names in groups.items()
        if names
    ]
    checks.append("no declared input goes unread by the tree")
    return checks


def _spends_tokens(spec: dict[str, Any]) -> bool:
    """Whether any node makes a model call, using the engine's OWN `LLM_KINDS`.

    Measured: testing for `stage` alone reported `deep-research` — eight `infer` calls — as fully
    deterministic, so it was filed as needing no judge. An `infer` is one bounded model call, which
    is exactly the kind of output only a judge can assess. Reusing the engine's set means a
    thirteenth node kind cannot quietly land on the wrong side of this line.
    """
    from gideon.automation.workflows.models import LLM_KINDS

    present_kinds = {node.get("kind") for node in _walk(spec.get("root"))}
    model_kinds = {kind.value for kind in LLM_KINDS}
    return bool(present_kinds & model_kinds)


def _stated_outputs(metadata: dict[str, Any], limit: int) -> list[str]:
    """The template's declared example outputs, trimmed and capped before filtering.

    Capping first is the artifact's own ordering: the first declared outputs are the claims the
    template leads with, so an empty string in slot two does not promote slot four.
    """
    outputs = (metadata.get("example_outputs") or [])[:limit]
    return [text for text in (str(output).strip() for output in outputs) if text]


def _judge_standard(
    spec: dict[str, Any], metadata: dict[str, Any]
) -> tuple[list[str], str]:
    """The one decision behind both graded checks and the graded note.

    Derived from the template's own `example_outputs`: the template's claim about what it produces
    IS the acceptance criterion, and grading anything else would be inventing a standard the
    template never claimed. Templates whose nodes are all deterministic get nothing here — an
    action either succeeded or returned a typed error, and asking a model for an opinion about
    arithmetic spends a call to add doubt.
    """
    if not _spends_tokens(spec):
        return [], _DETERMINISTIC_NOTE
    if not (metadata.get("example_outputs") or []):
        return [], _NO_STANDARD_NOTE
    checks = [
        f"output is recognizably: {text}" for text in _stated_outputs(metadata, 3)
    ]
    return checks, ""


def _graded_checks(spec: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """What a judge would have to grade, named rather than graded."""
    return _judge_standard(spec, metadata)[0]


def _graded_note(spec: dict[str, Any], metadata: dict[str, Any]) -> str:
    if _spends_tokens(spec):
        return "" if metadata.get("example_outputs") or [] else _NO_STANDARD_NOTE
    return _DETERMINISTIC_NOTE


def _coerce_node(node_type: Any, root: Any) -> Any:
    """The engine's typed node for a raw root, or `_MISSING` when none can be built."""
    if not isinstance(root, dict):
        return _MISSING
    try:
        return node_type.from_dict(root)
    except Exception:
        return _MISSING


def _walk(root: Any) -> list[dict[str, Any]]:
    """Every node in the tree, via the ENGINE's own walk.

    Measured: a hand-rolled walk over guessed key names (`branches`, `then`, `otherwise`) found 4 of
    13 nodes in `deep-research` and 2 of 8 in `audit-sweep` — the engine's branch children live
    under `cases`/`default_case`, so every branch subtree was silently skipped. The eval then
    reported `deep-research` as having no model-bearing nodes at all, which is a confident false
    claim about the exact thing the eval exists to check.

    Typing a spec through `Node.from_dict` first is what makes the walk correct rather than
    plausible: the node algebra is defined in one place, and a second traversal that has to be kept
    in sync with it is a traversal that will drift.
    """
    from gideon.automation.workflows.models import Node, walk

    typed = _coerce_node(Node, root)
    if typed is _MISSING:
        return []
    return [node.to_dict() for _path, node in walk(typed)]


def suite(specs: list[EvalSpec]) -> dict[str, Any]:
    """The whole suite as one artifact, with the free/graded split made explicit.

    `free_fixtures` is what CI runs. Reporting the graded count separately rather than folding it in
    is what keeps a passing CI run from being read as "every template was evaluated" — separating
    validation failures from ungraded quality is what makes the eval actionable at all.
    """
    free_only: list[str] = []
    needs_judge: list[str] = []
    fixture_total = 0
    structural_total = 0
    graded_total = 0
    for spec in specs:
        target = free_only if spec.free else needs_judge
        target.append(spec.template)
        fixture_total += len(spec.fixtures)
        structural_total += len(spec.structural_checks)
        graded_total += len(spec.graded_checks)
    return {
        "templates": len(specs),
        "free_fixtures": fixture_total,
        "free_only_templates": free_only,
        "needs_judge": needs_judge,
        "structural_checks": structural_total,
        "graded_checks": graded_total,
        "specs": [spec.to_dict() for spec in specs],
    }


def derive_library_suite() -> dict[str, Any]:
    """Derive the suite for the whole bundled library.

    The entry point CI and the flywheel both call. It reads the shipped templates directly so a new
    template joins the suite by existing — a benchmark you have to remember to register is one that
    silently omits the newest template, which is the one most likely to be wrong.
    """
    from gideon.automation.workflows import bundled_defs

    specs = [
        derive_eval_spec(name, _as_spec(definition), _as_metadata(definition))
        for name, definition in (
            (name, bundled_defs.read_template(name))
            for name in sorted(bundled_defs.template_names())
        )
        if definition is not None
    ]
    return suite(specs)


def _exported(value: Any) -> Any:
    """The value as a plain mapping when it knows how, otherwise unchanged."""
    return value.to_dict() if hasattr(value, "to_dict") else value


def _as_spec(definition: Any) -> dict[str, Any]:
    root = getattr(definition, "root", None)
    inputs = dict(
        (key, _exported(value))
        for key, value in (getattr(definition, "inputs", None) or {}).items()
    )
    return dict(inputs=inputs, root={} if root is None else _exported(root))


def _as_metadata(definition: Any) -> dict[str, Any]:
    metadata = getattr(definition, "metadata", None)
    if metadata is None:
        return {}
    return _exported(metadata) if hasattr(metadata, "to_dict") else dict(metadata)
