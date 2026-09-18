"""Learning proposal records and the public queue API."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from gideon.cognition.learning.hygiene import MIN_EVIDENCE_DEFAULT, fingerprint
from gideon.core.atomic_write import atomic_write
from gideon.core.record_ids import record_path

from .proposal_delivery import ProposalSignals
from .proposal_queue import (
    DecisionMemory,
    HumanDecision,
    ProposalFiles,
    ProposalIntake,
    ProposalText,
    available_quota,
)

logger = logging.getLogger(__name__)

_DIRNAME = "proposals"
_DECISIONS_FILE = "decisions.json"
_EXCERPT_MAX = 4_000

MAX_PENDING = 100

DEFAULT_QUOTA_PER_RUN = 5

SIM_NEW = 0.85
SIM_REINFORCE = 0.92
SIM_SUBJECT = 0.60

_COOLDOWN_DAYS = (7, 30, 90, 365)


class Kind(str, Enum):
    """The proposal kinds. Closed, so a typo cannot invent an unlisted one.

    The three ``project_*`` kinds (LEA-12) are the review's typed output: a self-updating
    project context that PROPOSES rather than writes, so a run's learnings reach the
    project's overview/ledger/inlined-file/skill only through the same human gate every
    other kind clears.

    ``KNOWLEDGE_DRAFT`` (KNOWLEDGE-SYNTHESIS §3.3/§3.4, WF2KNO-8) is the same bargain for
    the knowledge store: a gap-healing or schema-edit draft reaches
    ``workspace/knowledge`` only after a human accepts it. Before this kind existed the
    gap-healing template had no way to file one — ``enqueue`` SKIPS an unlisted kind and
    logs at debug — so the template wrote a TTL'd probe straight into the store instead,
    which is the self-citation anti-pattern the template's own doctrine warns about.

    ``PROMPT`` and ``AGENT`` (AGENT-PACKS §4.3, AP-4) are the prompt-card importer's other two
    typed outputs. A pasted card maps onto a ``PromptTemplate``, a ``WorkflowDef`` (already
    ``TEMPLATE``) or an ``AgentDefinition``, and each has to reach its own store through this
    gate — filing all three as ``TEMPLATE`` would label an agent "New template proposed" in
    the inbox and hand the wrong installer a payload it cannot write.
    """

    SKILL = "skill"
    LESSON_BATCH = "lesson_batch"
    TEMPLATE = "template"
    TEMPLATE_DIFF = "template_diff"
    RETIREMENT = "retirement"
    TIER_MIGRATION = "tier_migration"
    PROJECT_INSTRUCTION = "project_instruction"
    PROJECT_FILE = "project_file"
    PROJECT_SKILL = "project_skill"
    KNOWLEDGE_DRAFT = "knowledge_draft"
    PROMPT = "prompt"
    AGENT = "agent"


class Status(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DRAFT = "draft"
    SUPERSEDED = "superseded"


class Verdict(str, Enum):
    """The four outcomes of the deterministic resolve cascade."""

    NEW = "new"
    REINFORCE = "reinforce"
    REPLACE = "replace"
    MERGE = "merge"
    SKIP = "skip"


@dataclass
class ChangeManifest:
    """Why this change, and what it is predicted to fix (LEARN-R16).

    Validation is lenient-but-recording: a missing or thin manifest yields
    ``manifest_valid=False`` on the record and a warning in the inbox, never a
    hard reject. A proposal blocked for a metadata problem is a proposal the user
    never gets to judge, and the judgment is the point.
    """

    component: str = ""
    files: list[str] = field(default_factory=list)
    failure_pattern: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    root_cause: str = ""
    targeted_fix: str | list[dict[str, Any]] = ""
    predicted_fixes: list[str] = field(default_factory=list)
    risk_tasks: list[str] = field(default_factory=list)

    def issues(self) -> list[str]:
        required = (
            "component",
            "failure_pattern",
            "evidence_refs",
            "root_cause",
            "targeted_fix",
        )
        return [name for name in required if not getattr(self, name)]

    def is_valid(self) -> bool:
        return not self.issues()


@dataclass
class Proposal:
    """One pending, human-reviewable change."""

    id: str
    kind: str
    title: str
    body: str
    target: str = ""
    fingerprint: str = ""
    status: str = Status.PENDING.value
    created_at: str = ""
    updated_at: str = ""
    reinforcements: int = 1
    specializes: str = ""
    supersedes: str = ""
    provenance: str = "inferred"
    source_cadence: str = ""
    session_key: str = ""
    run_id: str = ""
    source_excerpt: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    staging_refs: list[int] = field(default_factory=list)
    change_manifest: dict[str, Any] = field(default_factory=dict)
    manifest_valid: bool = True
    manifest_issues: list[str] = field(default_factory=list)
    evidence_strength: str = "correlated"
    confidence: float = 0.0
    tags: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        fields = (
            "id",
            "kind",
            "title",
            "target",
            "status",
            "created_at",
            "updated_at",
            "reinforcements",
            "provenance",
            "confidence",
            "evidence_strength",
            "manifest_valid",
            "manifest_issues",
            "specializes",
        )
        compact = {name: getattr(self, name) for name in fields}
        compact.update(
            gate=dict(self.gate), replay=dict(self.replay), body_preview=self.body[:280]
        )
        return compact


@dataclass
class Decision:
    """A remembered accept/reject, keyed by fingerprint.

    Rejections are kept, not deleted. A store that forgets its rejections re-files
    the same suggestion forever, and the user's only defence is to stop reading.
    """

    fingerprint: str
    verdict: str
    kind: str
    title: str
    decided_at: str
    rejections: int = 0
    cooldown_until: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def content_fingerprint(kind: str, target: str, body: str) -> str:
    """Order-independent fingerprint of (kind, target, normalized body).

    The target is part of it because the same advice about two different templates
    is two proposals; the body is normalized (via ``hygiene.fingerprint``) so a
    reflowed paragraph is recognised as the same content rather than as a new one.
    """
    return fingerprint(f"{kind}\x1f{target}\x1f{body}")


def _dir() -> Path:
    """Resolve the config dir dynamically so a test repointing it is honored.

    A module-level ``from ... import config_dir`` would bind the original and leak
    writes into the real home — the mistake the skills queue documents.
    """
    from gideon.core.config.loader import config_dir

    return Path(config_dir()) / "learning" / _DIRNAME


def _path(proposal_id: object) -> Path:
    """The ONE expression turning a proposal id into a file in this store.

    ``proposal_id`` reaches here from ``/api/learning/proposals/{id}`` unvalidated, and
    the six sites that used to inline ``_dir() / f"<pid>.json"`` gave a traversal both a
    read and an ``unlink`` outside the home (#459). :func:`record_path` raises
    ``UnsafeRecordId`` — deliberately not a ``ValueError``, so ``_load``'s
    ``except (OSError, ValueError, TypeError)`` cannot turn the refusal back into the
    ``404`` that made this class look validated.
    """
    return record_path(_dir(), proposal_id, kind="proposal_id")


def _decisions_path() -> Path:
    return _dir() / _DECISIONS_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_decisions() -> dict[str, Decision]:
    return ProposalFiles().load_decisions()


def save_decisions(decisions: dict[str, Decision]) -> None:
    ProposalFiles().save_decisions(decisions)


def record_decision(prop: Proposal, verdict: str) -> None:
    DecisionMemory().remember(prop, verdict)


def _load(pid: str) -> Proposal | None:
    return ProposalFiles().load(pid)


def _save(prop: Proposal) -> bool:
    return ProposalFiles().save(prop)


def _all() -> list[Proposal]:
    return ProposalFiles().all()


def list_pending(kind: str = "") -> list[Proposal]:
    eligible = (
        proposal
        for proposal in _all()
        if proposal.status == Status.PENDING.value
        and (not kind or proposal.kind == kind)
    )
    return sorted(eligible, key=lambda proposal: proposal.created_at, reverse=True)


def get(pid: str) -> Proposal | None:
    return _load(pid)


def newest_gate_for_target(target: str) -> dict | None:
    reports = (
        proposal for proposal in _all() if proposal.target == target and proposal.gate
    )
    newest = max(reports, key=lambda proposal: proposal.created_at, default=None)
    return None if newest is None else dict(newest.gate)


def _polarity(text: str) -> int:
    return ProposalText.polarity(text)


_SUBJECT_STOPWORDS = frozenset(
    {
        "a",
        "always",
        "an",
        "avoid",
        "dont",
        "don't",
        "do",
        "never",
        "no",
        "not",
        "prefer",
        "should",
        "stop",
        "the",
        "to",
        "use",
        "using",
        "we",
        "you",
    }
)


def _subject_span(text: str) -> str:
    return ProposalText.subject(text, _SUBJECT_STOPWORDS)


def _numbers(text: str) -> set[str]:
    return ProposalText.numbers(text)


_NUMBER_CONFLICT_MIN_SIM = 0.75


def contradicts(a: str, b: str) -> bool:
    return ProposalText().contradicts(a, b)


def _similarity(a: str, b: str) -> float:
    return ProposalText.similarity(a, b)


def resolve(
    candidate: Proposal, existing: list[Proposal]
) -> tuple[Verdict, Proposal | None]:
    return ProposalText().resolve(candidate, existing)


def _prior_decision_blocks(fp: str, decisions: dict[str, Decision]) -> str:
    return DecisionMemory().refusal(fp, decisions)


def enqueue(
    *,
    kind: str,
    title: str,
    body: str,
    target: str = "",
    provenance: str = "inferred",
    source_cadence: str = "",
    session_key: str = "",
    run_id: str = "",
    source_excerpt: str = "",
    evidence_refs: list[str] | None = None,
    staging_refs: list[int] | None = None,
    change_manifest: ChangeManifest | dict | None = None,
    evidence_strength: str = "correlated",
    confidence: float = 0.0,
    tags: list[str] | None = None,
    occurrences: int = 0,
    min_evidence: int = MIN_EVIDENCE_DEFAULT,
) -> tuple[Verdict, Proposal | None]:
    return ProposalIntake().enqueue(locals())


_SUPERSEDED_KEEP = 50


def prune_superseded(keep: int = _SUPERSEDED_KEEP) -> int:
    return ProposalFiles().prune(keep)


def _expire_oldest(existing: list[Proposal]) -> None:
    ProposalFiles().expire(existing)


_KIND_LABELS = {
    Kind.SKILL.value: "New skill proposed",
    Kind.LESSON_BATCH.value: "Lessons to review",
    Kind.TEMPLATE.value: "New template proposed",
    Kind.TEMPLATE_DIFF.value: "Template change proposed",
    Kind.RETIREMENT.value: "Retire something unused",
    Kind.TIER_MIGRATION.value: "Move to a different tier",
    Kind.PROJECT_INSTRUCTION.value: "Project instruction proposed",
    Kind.PROJECT_FILE.value: "Project context update proposed",
    Kind.PROJECT_SKILL.value: "Project skill proposed",
    Kind.KNOWLEDGE_DRAFT.value: "Knowledge entry drafted for review",
    Kind.PROMPT.value: "New prompt proposed",
    Kind.AGENT.value: "New agent proposed",
}

_unlabelled = {k.value for k in Kind} - set(_KIND_LABELS)
if _unlabelled:  # pragma: no cover - import-time guard
    raise RuntimeError(f"proposal kinds without an inbox label: {sorted(_unlabelled)}")


def _surface_in_inbox(prop: Proposal) -> None:
    ProposalSignals().surface(prop)


def _resolve_inbox_item(pid: str, status: str) -> None:
    ProposalSignals().resolve(pid, status)


def _audit(operation: str, prop: Proposal, outcome: str) -> None:
    ProposalSignals().audit(operation, prop, outcome)


def reject(pid: str, *, actor: str = "user") -> bool:
    return HumanDecision().reject(pid, actor)


def attach_gate(pid: str, report: dict) -> bool:
    return ProposalFiles().attach(pid, "gate", report)


def attach_replay(pid: str, report: dict) -> bool:
    return ProposalFiles().attach(pid, "replay", report)


def defer(pid: str) -> bool:
    return HumanDecision().defer(pid)


class AcceptError(Exception):
    """Raised when a proposal cannot be accepted.

    ``refusal`` carries the structured reason when the accept was refused by the
    install step — ``{"refusal", "kind", "reason", "retryable"}`` — so a surface can
    tell "this kind has no installer yet" from "the installer failed" from "you may
    not accept", and say which. Empty for the gate refusals, whose reason is the
    message itself.
    """

    def __init__(self, message: str, *, refusal: dict | None = None) -> None:
        super().__init__(message)
        self.refusal = dict(refusal or {})


def accept(pid: str, *, installer=None, actor: str = "user") -> Proposal:
    return HumanDecision().accept(pid, installer, actor)


def quota_remaining(filed: int, quota: int | None = None) -> int:
    return available_quota(filed, quota, DEFAULT_QUOTA_PER_RUN)
