"""Refinement placement and acceptance criteria for incremental workflow planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.intent import Intent, Rigor

REFINE_GATE_ID = "refine-spec"

CRITERIA_KEY = "acceptance_criteria"


_FAST_WORDS = frozenset({Rigor.FAST.value, "minimal"})


def is_fast(intent: Intent | None = None, *, requested: str = "") -> bool:
    normalized = (requested or "").strip().lower()
    if normalized in _FAST_WORDS:
        return True
    return False if not intent else intent.rigor is Rigor.FAST


def schedule_refinement(spec: dict[str, Any]) -> dict[str, Any]:
    candidate = spec.get("root")
    if not isinstance(candidate, dict) or _has_node(candidate, REFINE_GATE_ID):
        return spec
    prompt = (
        "First output is in. Refine the plan against it, or continue as-is?\n"
        "This is the fast path's refinement point — the spec was deliberately thin."
    )
    refinement = dict(
        kind="gate", id=REFINE_GATE_ID, config=dict(kind="approval", prompt=prompt)
    )
    return dict(
        spec,
        root=_insert_after_first_work(candidate, refinement),
        rigor=Rigor.FAST.value,
    )


def _has_node(node: Any, node_id: str) -> bool:
    return any(candidate.get("id") == node_id for candidate in _refinement_nodes(node))


def _insert_after_first_work(
    root: dict[str, Any], gate: dict[str, Any]
) -> dict[str, Any]:
    children = root.get("children")
    if root.get("kind") != "sequence" or not isinstance(children, list):
        return dict(kind="sequence", id="root", children=[root, gate])
    position = next(
        (
            index + 1
            for index, child in enumerate(children)
            if isinstance(child, dict) and _is_work(child)
        ),
        len(children),
    )
    return dict(root, children=[*children[:position], gate, *children[position:]])


def _is_work(node: dict[str, Any]) -> bool:
    """A node that produces something to refine AGAINST. A gate produces a decision, not an
    artifact, so refining after one would refine against nothing new."""
    return node.get("kind") in (
        "stage",
        "action",
        "foreach",
        "loop",
        "parallel",
        "sequence",
    )


def specify_prompt(intent_text: str) -> str:
    """The Specify prompt: a rough intent → one runnable stage.

    Deliberately constrained to ONE stage. Specify exists for the case where planning costs more
    than doing, and a Specify that emitted a five-node graph would have quietly become the planner
    it was meant to bypass.
    """
    return (
        "Rewrite this rough intent as ONE runnable instruction for a single work stage.\n\n"
        "HARD REQUIREMENTS:\n"
        "- Exactly one stage. Do not decompose, do not add review or verification steps.\n"
        "- Keep the user's own words and scope. Do not broaden the task or add goals.\n"
        "- State what the output should BE (a file, an answer, a list), since that is what makes "
        "the single stage checkable.\n"
        "- If the intent is too vague for one stage to be meaningful, say exactly "
        "`TOO_VAGUE` and nothing else — a one-stage spec built on a guess wastes the run it was "
        "meant to save.\n\n"
        f"INTENT: {intent_text}\n\n"
        "Respond with ONLY the instruction text."
    )


def specify_spec(instruction: str, *, name: str = "specified") -> dict[str, Any] | None:
    prompt = (instruction or "").strip()
    if prompt in ("", "TOO_VAGUE"):
        return None
    stage = dict(
        kind="stage", id="do", config=dict(prompt=prompt, model_tier="standard")
    )
    return dict(name=name, rigor=Rigor.FAST.value, root=stage)


@dataclass
class Defect:
    """One defect observed in a run's output, plus what fixed it.

    `fix` is what becomes an acceptance criterion. A defect recorded without its fix is a complaint;
    with it, it is a check the next run has to pass.
    """

    observed: str
    fix: str = ""
    node_id: str = ""

    def criterion(self) -> str:
        """The acceptance criterion this defect earns.

        Phrased as a requirement rather than as a bug report: "the summary cites its sources" is
        checkable by a judge, "the summary had no sources" is a description of one past run.
        """
        if self.fix:
            return self.fix
        return f"does not repeat: {self.observed}"


def ratchet_criteria(spec: dict[str, Any], defects: list[Defect]) -> dict[str, Any]:
    return _RevisionDraft(spec).with_defects(defects).document


@dataclass
class ArtifactRevision:
    """A revision derived from what a run actually produced.

    The three fields are separate because they have different authority: the run's output is a
    fact, the user's reaction is a judgement, and the criteria are the durable residue. Merging
    them would make it impossible to tell a defect the user reported from one the system inferred.
    """

    from_run: str = ""
    reaction: str = ""
    defects: list[Defect] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        defects = []
        for defect in self.defects:
            defects.append(
                dict(observed=defect.observed, fix=defect.fix, node_id=defect.node_id)
            )
        criteria = list(map(lambda defect: defect.criterion(), self.defects))
        return dict(
            from_run=self.from_run,
            reaction=self.reaction,
            defects=defects,
            criteria=criteria,
        )


def revise_from_artifact(
    spec: dict[str, Any], revision: ArtifactRevision
) -> dict[str, Any]:
    draft = _RevisionDraft(spec).with_defects(revision.defects)
    if revision.from_run:
        draft.record_origin(revision.from_run)
    return draft.document


def rigor_note(intent: Intent, *, requested: str = "") -> str:
    if is_fast(intent, requested=requested):
        selected = (requested or "").strip().lower()
        reason = (
            "you asked for it"
            if selected in _FAST_WORDS
            else intent.reason or "classified fast"
        )
        return (
            f"Fast path ({reason}) — deliberately thin spec, with a refinement gate after the first "
            "output rather than questions up front."
        )
    if intent.rigor is Rigor.TRIVIAL:
        return "No workflow needed — this is answerable directly."
    if intent.rigor is Rigor.DEEP:
        reason, label, behavior = (
            intent.reason or "classified deep",
            "Deep",
            "structured interrogation first.",
        )
    else:
        reason, label, behavior = (
            intent.reason or "default",
            "Standard",
            "grounding and contracts, no grill.",
        )
    return f"{label} path ({reason}) — {behavior}"


def _refinement_nodes(value: Any):
    if not isinstance(value, dict):
        return
    yield value
    for collection in ("children", "branches"):
        for child in value.get(collection) or []:
            yield from _refinement_nodes(child)
    for slot in ("body", "then", "otherwise"):
        yield from _refinement_nodes(value.get(slot))


class _RevisionDraft:
    def __init__(self, source: dict[str, Any]):
        self.document = dict(source)

    def with_defects(self, defects: list[Defect]) -> _RevisionDraft:
        criteria = list(self.document.get(CRITERIA_KEY) or [])
        known = set(criteria)
        for defect in defects:
            candidate = defect.criterion()
            if not candidate or candidate in known:
                continue
            known.add(candidate)
            criteria.extend((candidate,))
        if criteria:
            self.document.update({CRITERIA_KEY: criteria})
        return self

    def record_origin(self, run_id: str) -> None:
        extra = dict(self.document.get("extra") or {})
        origins = list(extra.get("revised_from_runs") or [])
        if run_id not in origins:
            origins.extend((run_id,))
        extra.update(revised_from_runs=origins)
        self.document.update(extra=extra)
