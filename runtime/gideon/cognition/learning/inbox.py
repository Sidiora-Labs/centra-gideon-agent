"""The Proposal Inbox: one surface for six kinds, and the accept gate (§6.1 / §7 — S75).

§7's criterion 1 has two halves: one inbox shows all six proposal kinds with provenance, evidence
manifests, and risk-tier metadata — **and the model cannot accept its own proposals under any trust
mode**. The second half is the load-bearing one, and this module is where it becomes a control
rather than a coincidence.

**Measured before writing.** `learning/proposals.accept()` takes `(pid, installer=...)` and NOTHING
in it knows WHO is accepting: no actor, no caller, no trust check. The invariant held only because
no agent tool happened to call it — an ABSENCE, not a control. Add one MCP tool next month and it is
gone, silently, with no test failing. `require_human` is the gate; `test_an_agent_can_never_accept`
is the regression.

The rest of the module is the inbox's own discipline:

* **Six kinds, one queue.** `proposals.Kind` already has exactly six, so this reuses them rather
than
  minting a parallel vocabulary — a second kind list is how a surface silently stops showing one.
* **Risk tier is METADATA, never a lane.** §3.1 is explicit: any "auto" tier is guardrail-violating.
  Tiers order and filter the queue; they never decide.
* **`manifest_valid=false` surfaces, never rejects.** §3.1's validation is lenient-but-recording: a
  proposal with a broken manifest is still reviewable, flagged, because dropping it would hide a
  refiner bug behind an empty inbox.
* **Provenance is required to render a row.** A proposal whose source cannot be shown is one a
  reviewer cannot weigh, and an unweighable row trains people to bulk-accept.

Pure decisions and view models. The store stays in `learning.proposals`; nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _actor_enum():
    from gideon.automation.workflows.verified_done import Actor

    return Actor


ACCEPT_ACTORS: frozenset[str] = frozenset({"user"})

REJECT_ACTORS: frozenset[str] = frozenset({"user"})

FILE_ACTORS: frozenset[str] = frozenset({"user", "agent", "engine"})


class Denial(str, Enum):
    """Why a review action was refused. Typed because the refusal is audited.

    `SELF_ACCEPT` is the one §7 names. Kept distinct from a generic permission denial so the SEL row
    says what was actually attempted — "an agent tried to accept its own proposal" is an incident,
    while "an unknown actor tried to accept" is a bug.
    """

    SELF_ACCEPT = "self_accept"
    NOT_A_REVIEWER = "not_a_reviewer"
    UNKNOWN_ACTOR = "unknown_actor"
    ALREADY_RESOLVED = "already_resolved"


@dataclass
class Gate:
    """Whether a review action may proceed."""

    allowed: bool
    denial: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "denial": self.denial, "reason": self.reason}


def require_human(*, action: str, actor: str, status: str = "pending") -> Gate:
    return _ReviewAuthority(action, actor, status).decide()


def can_file(actor: str) -> bool:
    """Whether this actor may FILE a proposal. All three may — filing is the safe verb.

    Separated from `require_human` so the asymmetry is explicit in the code rather than implied: the
    whole design depends on non-human proposers, and only the DECISION is human-only.
    """
    return actor in FILE_ACTORS


def _tier_order() -> list[str]:
    from gideon.cognition.learning.refiner import RiskTier

    return [RiskTier.LOW.value, RiskTier.REVIEW.value, RiskTier.MANUAL_ONLY.value]


BULK_ACCEPTABLE_TIERS: frozenset[str] = frozenset({"low", "review"})


@dataclass
class Row:
    """One inbox row: everything a reviewer needs to decide without opening anything else.

    §6.1 names the fields, and each is here because its absence produces a specific bad review:
    without provenance the reviewer cannot weigh the source, without the evidence manifest they
    cannot check the claim, without `manifest_valid` they cannot tell a flagged proposal from a
    and without the reinforcement count they cannot tell one observation from twenty.
    """

    id: str
    kind: str
    title: str
    provenance: str = ""
    source_cadence: str = ""
    source_excerpt: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    evidence_strength: str = ""
    reinforcements: int = 0
    confidence: float = 0.0
    manifest_valid: bool = True
    manifest_issues: list[str] = field(default_factory=list)
    risk_tier: str = "review"
    status: str = "pending"
    gate: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)

    @property
    def renderable(self) -> bool:
        """Whether this row can be shown honestly.

        Requires a kind, a title, and PROVENANCE. A proposal whose source cannot be shown is one a
        reviewer cannot weigh — and a queue of unweighable rows trains people to bulk-accept, which
        defeats the human-installs invariant while appearing to honour it.
        """
        return bool(self.kind and self.title and self.provenance)

    @property
    def bulk_acceptable(self) -> bool:
        """Whether a bulk control may include this row.

        Four conditions, each excluding a different mistake: a `manual_only` tier (destructive), an
        invalid manifest (the claim is unverified), a missing evidence ref (nothing to check), and —
        measured while probing — an UNRENDERABLE row.

        That last one was the defect: a proposal with no title or provenance came back
        `bulk_acceptable=True` while `renderable=False`, so a row the UI cannot honestly show was
        eligible for a control that accepts without opening it. Bulk-accepting something a reviewer
        could not have read is the human-installs invariant in name only.
        """
        return (
            self.renderable
            and self.risk_tier in BULK_ACCEPTABLE_TIERS
            and self.manifest_valid
            and bool(self.evidence_refs)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "provenance": self.provenance,
            "source_cadence": self.source_cadence,
            "source_excerpt": self.source_excerpt,
            "evidence_refs": list(self.evidence_refs),
            "evidence_strength": self.evidence_strength,
            "reinforcements": self.reinforcements,
            "confidence": round(self.confidence, 4),
            "manifest_valid": self.manifest_valid,
            "manifest_issues": list(self.manifest_issues),
            "risk_tier": self.risk_tier,
            "status": self.status,
            "gate": dict(self.gate),
            "replay": dict(self.replay),
            "renderable": self.renderable,
            "bulk_acceptable": self.bulk_acceptable,
        }


def row_from_proposal(prop: Any, *, risk_tier: str = "") -> Row:
    return _InboxProjection.project(prop, risk_tier or "review")


def order_rows(rows: list[Row]) -> list[Row]:
    return _InboxProjection.ordered(rows, _tier_order())


def filter_rows(
    rows: list[Row], *, kind: str = "", tier: str = "", flagged_only: bool = False
) -> list[Row]:
    selected = list(rows or [])
    predicates = []
    if kind:
        predicates.append(lambda row: row.kind == kind)
    if tier:
        predicates.append(lambda row: row.risk_tier == tier)
    if flagged_only:
        predicates.append(lambda row: not row.manifest_valid)
    for predicate in predicates:
        selected = list(filter(predicate, selected))
    return selected


@dataclass
class InboxView:
    """The whole inbox, ordered and counted.

    Counts per kind and per tier are here because §6.1's surface offers filtering: a filter chip
    with no count is a chip a user has to click to discover is empty.
    """

    rows: list[Row] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Row count. A property rather than only a `to_dict` key: a caller checking the queue size
        should not have to serialize the whole view to learn it."""
        return len(self.rows)

    @property
    def by_kind(self) -> dict[str, int]:
        return _InboxProjection.count(self.rows, "kind")

    @property
    def by_tier(self) -> dict[str, int]:
        return _InboxProjection.count(self.rows, "risk_tier")

    @property
    def flagged(self) -> int:
        return sum(1 for row in self.rows if not row.manifest_valid)

    @property
    def unrenderable(self) -> list[str]:
        """Ids of rows that cannot be shown honestly.

        Reported rather than silently dropped: a proposal missing its provenance is a PROPOSER bug,
        and an inbox that quietly hides them makes that bug invisible.
        """
        return [row.id for row in self.rows if not row.renderable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "total": len(self.rows),
            "by_kind": self.by_kind,
            "by_tier": self.by_tier,
            "flagged": self.flagged,
            "unrenderable": self.unrenderable,
            "bulk_acceptable": sum(1 for r in self.rows if r.bulk_acceptable),
        }


def build_view(
    proposals: list[Any],
    *,
    tiers: dict[str, str] | None = None,
    kind: str = "",
    tier: str = "",
    flagged_only: bool = False,
) -> InboxView:
    projected = []
    for proposal in proposals or []:
        assigned = (tiers or {}).get(str(getattr(proposal, "id", "")), "")
        projected.append(row_from_proposal(proposal, risk_tier=assigned))
    visible = filter_rows(projected, kind=kind, tier=tier, flagged_only=flagged_only)
    return InboxView(rows=order_rows(visible))


def audit_denial(*, action: str, actor: str, pid: str, gate: Gate) -> dict[str, Any]:
    """The SEL row for a refused review action.

    §3 requires a SEL audit of accepts; a refused accept is at least as worth recording. A blocked
    self-accept in particular is the signal that something is calling the wrong path — and it would
    be invisible if only successes were logged.
    """
    return {
        "operation": f"learning_proposal_{action}",
        "outcome": "blocked",
        "actor": actor,
        "proposal": pid,
        "denial": gate.denial,
        "reason": gate.reason,
    }


class _ReviewAuthority:
    def __init__(self, action, actor, status):
        self.action = action
        self.actor = actor
        self.status = status

    def decide(self):
        known = set(item.value for item in _actor_enum())
        denial = self._identity(known)
        if denial is None:
            denial = self._resolution()
        if denial is None:
            denial = self._reviewer()
        return Gate(allowed=True) if denial is None else Gate(False, *denial)

    def _identity(self, known):
        if self.actor in known:
            return None
        reason = (
            f"unrecognized actor {self.actor!r}; expected one of {', '.join(sorted(known))} — an "
            "unknown caller is denied rather than assumed human"
        )
        return Denial.UNKNOWN_ACTOR.value, reason

    def _resolution(self):
        if self.status in ("pending", "draft"):
            return None
        reason = f"the proposal is already {self.status}; re-deciding it would overwrite a recorded decision"
        return Denial.ALREADY_RESOLVED.value, reason

    def _reviewer(self):
        permitted = ACCEPT_ACTORS if self.action == "accept" else REJECT_ACTORS
        if self.actor in permitted:
            return None
        if self.actor != "agent":
            return (
                Denial.NOT_A_REVIEWER.value,
                f"{self.actor!r} may not {self.action} proposals; only a human reviewer installs behaviour",
            )
        return (
            Denial.SELF_ACCEPT.value,
            f"an agent may propose but never {self.action} — a worker whose self-report installs "
            "itself is not reviewed at all",
        )


class _InboxProjection:
    @staticmethod
    def project(proposal, tier):
        from gideon.assurance.evals import gate as gate_lib
        from gideon.cognition.learning import replay as replay_lib

        values: dict = {}
        for name in (
            "id",
            "kind",
            "title",
            "provenance",
            "source_cadence",
            "source_excerpt",
        ):
            values[name] = str(getattr(proposal, name, "") or "")
        values["evidence_refs"] = list(
            map(str, getattr(proposal, "evidence_refs", None) or [])
        )
        for name, converter, fallback in (
            ("evidence_strength", str, ""),
            ("reinforcements", int, 0),
            ("confidence", float, 0.0),
        ):
            values[name] = converter(getattr(proposal, name, fallback) or fallback)
        values["manifest_valid"] = bool(getattr(proposal, "manifest_valid", True))
        values["manifest_issues"] = list(
            map(str, getattr(proposal, "manifest_issues", None) or [])
        )
        values["risk_tier"] = tier
        values["status"] = str(getattr(proposal, "status", "pending") or "pending")
        for name, summarize in (
            ("gate", gate_lib.summary),
            ("replay", replay_lib.summary),
        ):
            values[name] = summarize(getattr(proposal, name, None))
        return Row(**values)

    @staticmethod
    def ordered(rows, tiers):
        decorated = []
        for row in rows:
            ranking = -len(tiers) - 1
            for index, tier in enumerate(tiers):
                if tier == row.risk_tier:
                    ranking = -index
                    break
            decorated.append(
                ((ranking, -row.reinforcements, -row.confidence, row.id), row)
            )
        decorated.sort(key=lambda pair: pair[0])
        return [row for _, row in decorated]

    @staticmethod
    def count(rows, attribute):
        from collections import Counter

        counts = Counter(getattr(row, attribute) for row in rows)
        return {key: counts[key] for key in sorted(counts)}
