"""The ONE authoritative installer per proposal kind (LEARN §7).

Accepting a learning proposal means INSTALLING it. Before this registry that mapping
was a chain of ``elif``s in the dashboard handler — re-typed a second time in
:mod:`gideon.cognition.proposals_contract` — whose last branch was an implicit
fall-through: a kind no branch claimed installed nothing and the queue still recorded
``accepted``. The proposal was then deleted, the decision was remembered (so a refile
was blocked as "already decided"), and nothing had happened. A kind nobody had written
an installer for yet — ``tier_migration`` is the current one — produced exactly that,
and it looked like success from every surface.

So the mapping is DATA, and it is total: every :class:`~learning.proposals.Kind` member
has a row, and each row states one of three things.

* **Installs** — the writer that must succeed before the decision is recorded.
* **Nothing to install** — the recorded decision IS the whole effect, declared on
  purpose (an ordinary ``lesson_batch`` already lives in the lesson store).
* **Unsupported** — something was meant to be installed and there is no writer for it.
  Accepting is REFUSED, the row stays pending, and it becomes acceptable the day a
  writer is declared here. This is the state that used to be indistinguishable from
  success.

A caller that owns a specialized writer still passes its own installer to
:func:`~learning.proposals.accept` — :mod:`gideon.cognition.knowledge.updates` builds
one bound to the knowledge item being edited. This registry is what every other
surface resolves, so "the UI accepted it" and "an agent tool accepted it" install the
same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from gideon.cognition.learning.proposals import Kind


class UnsupportedProposal(Exception):
    """This kind has no installer, so accepting it would record an empty success.

    Carries the structured refusal the surfaces render: which ``kind`` was refused,
    ``reason`` (why there is nothing to run) and ``retryable`` — always true, because
    the row is left pending exactly so it can be accepted once a writer exists.
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(f"{kind!r} proposals cannot be installed: {reason}")
        self.kind = kind
        self.reason = reason
        self.retryable = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "refusal": "unsupported_kind",
            "kind": self.kind,
            "reason": self.reason,
            "retryable": self.retryable,
        }


@dataclass
class InstallContext:
    """What an installer needs beyond the proposal itself.

    ``memory_service`` is the self-model store (only the ``lesson_batch`` row uses it);
    ``result`` is where an installer records what it wrote, for a surface that reports
    it back to the user (the ``template_diff`` row puts the new version there).
    """

    memory_service: Any = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Binding:
    """One row of the registry: what accepting this kind runs, and why."""

    note: str
    install: Callable[[dict[str, Any], InstallContext], None] | None = None
    supported: bool = True

    @property
    def installs(self) -> bool:
        return self.install is not None


def _installs(note: str):
    def _bind(fn: Callable[[dict[str, Any], InstallContext], None]) -> Binding:
        return Binding(note=note, install=fn)

    return _bind


def _nothing_to_install(note: str) -> Binding:
    return Binding(note=note)


def _unsupported(note: str) -> Binding:
    return Binding(note=note, supported=False)


def _install_skill(data: dict[str, Any], context: InstallContext) -> None:
    from gideon.cognition.learning import skill_promotion

    context.result["skill"] = skill_promotion.install_accepted_skill(data)


def _install_project_context(data: dict[str, Any], context: InstallContext) -> None:
    from gideon.cognition.learning import project_context_review

    project_context_review.install_accepted_project_context(data)


def _install_prompt_card(data: dict[str, Any], context: InstallContext) -> None:
    from gideon.extensions.packs import prompt_cards

    context.result["installed"] = prompt_cards.install_accepted_prompt_card(data)


def _install_template(data: dict[str, Any], context: InstallContext) -> None:
    """A ``template`` from the prompt-card importer writes its card; anything else is
    refused.

    Four producers file this kind and only the importer ships a writer, so the kind
    alone does not say what accepting should do — the card TAG does (the importer's own
    :func:`~packs.prompt_cards.is_prompt_card_proposal` owns that question). A mined or
    run-end template has no writer at all, which is a missing feature, not a proposal
    whose acceptance is its own effect.
    """
    from gideon.extensions.packs import prompt_cards

    if not prompt_cards.is_prompt_card_proposal(data):
        raise UnsupportedProposal(
            Kind.TEMPLATE.value,
            "only a template filed by the prompt-card importer has a writer; a mined "
            "or run-end template has none yet, so accepting it would install nothing",
        )
    _install_prompt_card(data, context)


def _install_lesson_batch(data: dict[str, Any], context: InstallContext) -> None:
    """A self-model principle is projected into the memory store; an ordinary
    correction-derived batch already lives in the lesson store and writes nothing.

    The ``source_cadence`` field decides, the same durable field
    :func:`~learning.self_model_observer.is_self_model_proposal` reads. With no store
    reachable the principle is REFUSED rather than deferred: a "best-effort projection"
    that is recorded as accepted never happens, and the row cannot be retried once the
    store is back because the decision already blocks the refile.
    """
    from gideon.cognition.learning import self_model_observer

    if not self_model_observer.is_self_model_proposal(data):
        return
    if context.memory_service is None:
        raise UnsupportedProposal(
            Kind.LESSON_BATCH.value,
            "this is a self-model principle and no memory store is reachable to "
            "project it into; it stays pending so it can be accepted once one is",
        )
    self_model_observer.install_accepted_principle(context.memory_service, data)


def _install_template_diff(data: dict[str, Any], context: InstallContext) -> None:
    from gideon.cognition.learning.template_diff import install_accepted_template_diff

    context.result["applied"] = install_accepted_template_diff(data)


REGISTRY: dict[Kind, Binding] = {
    Kind.SKILL: _installs("writes the promoted auto/ skill")(_install_skill),
    Kind.LESSON_BATCH: _installs(
        "projects a self-model principle; an ordinary lesson batch is already stored"
    )(_install_lesson_batch),
    Kind.TEMPLATE: _installs("writes a prompt-card template")(_install_template),
    Kind.TEMPLATE_DIFF: _installs("applies the typed ops as a new template version")(
        _install_template_diff
    ),
    Kind.RETIREMENT: _nothing_to_install(
        "the recorded decision IS the effect: a retirement proposal asks a human to "
        "agree that something is unused, and the agreement is what the flywheel reads"
    ),
    Kind.TIER_MIGRATION: _unsupported(
        "workflow tier analysis files these, and nothing applies a tier change yet"
    ),
    Kind.PROJECT_INSTRUCTION: _installs("appends the project instruction")(
        _install_project_context
    ),
    Kind.PROJECT_FILE: _installs("writes the project context file")(
        _install_project_context
    ),
    Kind.PROJECT_SKILL: _installs("writes the project skill")(_install_project_context),
    Kind.KNOWLEDGE_DRAFT: _unsupported(
        "a knowledge draft is installed by the knowledge updater that owns the item "
        "being edited, which passes its own installer; there is no generic writer"
    ),
    Kind.PROMPT: _installs("writes the prompt-card prompt")(_install_prompt_card),
    Kind.AGENT: _installs("writes the prompt-card agent")(_install_prompt_card),
}


def binding_for(kind: str) -> Binding:
    """The registry row for a proposal kind. Raises for a kind with no row at all."""
    try:
        member = Kind(str(kind or ""))
    except ValueError:
        raise UnsupportedProposal(
            str(kind or ""), "not a proposal kind this build knows"
        ) from None
    binding = REGISTRY.get(member)
    if binding is None:
        raise UnsupportedProposal(
            member.value, "no installer is declared for this kind"
        )
    return binding


def install(data: dict[str, Any], context: InstallContext) -> Binding:
    """Run the authoritative installer for one proposal record.

    Raises :class:`UnsupportedProposal` when the kind has no writer and whatever the
    writer raises when it has one and it fails — either way ``accept`` does not record
    the decision, so the row stays pending and retryable.
    """
    binding = binding_for(str(data.get("kind") or ""))
    if not binding.supported:
        raise UnsupportedProposal(str(data.get("kind") or ""), binding.note)
    if binding.install is not None:
        binding.install(data, context)
    return binding


def installer_for(context: InstallContext | None = None):
    """The ``installer=`` callable :func:`~learning.proposals.accept` takes."""
    ctx = context if context is not None else InstallContext()

    def _install(proposal) -> None:
        install(proposal.to_dict(), ctx)

    return _install
