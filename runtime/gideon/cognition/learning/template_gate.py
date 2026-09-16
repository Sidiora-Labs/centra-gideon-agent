"""The ad-hoc→template gate's CALL SITE: decide, record the refusal, file a draft.

``learning.detectors`` holds the decision (a deterministic chain plus a structural score) and is
documented as pure — "nothing here calls a model, writes memory, or files a proposal". That purity
is worth keeping, and it is also exactly why the chain shipped inert: ``detectors.gate()`` had
**zero production callers**, so no real run was ever evaluated, no refusal was ever recorded, and
the ``Skip`` enum's whole premise — that "a count per reason says which gate is doing the work and
which is dead weight" — had no data behind it. A gate nothing calls cannot decline anything.

This module is the missing half, and only that half:

* **decide** by delegating to ``detectors.gate`` (no second copy of the thresholds);
* **record** every negative decision as a ``FLUSH_SKIPPED`` row carrying the TYPED ``Skip`` value,
  reusing the same ledger ``gate.record_denial`` writes to rather than minting a parallel one — two
  skip ledgers would make "why did nothing get captured this week" a two-query question with two
  possible answers;
* **file** an accepted candidate as a PENDING ``template`` proposal.

Filing is never installing. ``proposals.enqueue`` returns a row a human accepts or rejects, so even
the ``AUTO_FILE`` branch — which deliberately spends zero model calls — cannot put a template into
the library on its own. That is what makes a free auto-file safe: the cost it skips is the model's,
not the reviewer's.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from gideon.cognition.learning import detectors
from gideon.cognition.learning.admission_policy import RefusalLedger, TemplateFiling
from gideon.cognition.learning.detectors import Action, Candidate, GateDecision

logger = logging.getLogger(__name__)

LEDGER_PREFIX = "template_gate"


@dataclass
class GateOutcome:
    """What the call site did — the decision, whether it was recorded, and what it filed.

    The three are reported separately on purpose. A decision that was reached but not recorded is
    the defect this module exists to fix, so collapsing them into "did it work" would hide the
    regression it is meant to prevent.
    """

    decision: GateDecision
    recorded: bool = False
    proposal_id: str = ""

    @property
    def filed(self) -> bool:
        return bool(self.proposal_id)

    def to_dict(self) -> dict[str, object]:
        return {
            **dict(self.decision.to_dict()),
            "recorded": self.recorded,
            "proposal_id": self.proposal_id,
        }


def record_skip(decision: GateDecision, *, detail: str = "") -> bool:
    """Persist one negative gate decision, keyed by its TYPED skip reason.

    Returns True iff a row was written. A non-skip decision writes nothing and returns False — the
    ledger's ``FLUSH_SKIPPED`` outcome means "declined", and recording an accept under it would
    corrupt the very counts the reasons exist to produce.

    Best-effort, like ``gate.record_denial``: recording is observability, and a staging-store
    failure must not lose a template proposal that the chain already approved.
    """
    return RefusalLedger.template(sys.modules[__name__], decision, detail)


def skip_counts(*, days: int = 30) -> dict[str, int]:
    """Counts per typed skip reason over a window — the "negative space" §3.2 tunes against.

    Reads the same ``flush_records`` rows :func:`record_skip` writes and keys them by the ``Skip``
    value parsed back out of ``detail``. Only reasons this module actually wrote are counted, so a
    capture-gate denial sharing the ledger cannot inflate a template-gate reason.

    Returns ``{}`` rather than raising when the store is unavailable: a statistics read is never
    worth failing a caller over.
    """
    return RefusalLedger.counts(sys.modules[__name__], days)


def _file_template(
    candidate: Candidate,
    decision: GateDecision,
    *,
    session_key: str,
    title: str,
    body: str,
) -> str:
    """File the accepted candidate as a PENDING template proposal. Returns its id or ``""``."""
    return TemplateFiling.enqueue(
        sys.modules[__name__], candidate, decision, session_key, title, body
    )


def evaluate(
    candidate: Candidate,
    *,
    session_key: str = "",
    title: str = "",
    body: str = "",
    file_proposal: bool = True,
) -> GateOutcome:
    """Run the chain on a real candidate, record any refusal, and file an accepted one.

    ``CONSULT`` files nothing here. §3.2 pays for a model only in the ambiguous middle band, and
    this call site has no model to pay with — reporting the band honestly is better than promoting
    the candidate on a score the design said was inconclusive, and better than recording it as a
    skip it was not.
    """
    decision = detectors.gate(candidate)
    result = GateOutcome(decision=decision)
    result.recorded = record_skip(decision, detail=candidate.run_id)
    if decision.action != Action.AUTO_FILE.value or not file_proposal:
        return result
    heading = title or f"Template from run {candidate.run_id or 'ad-hoc'}"
    content = body or candidate.text
    result.proposal_id = _file_template(
        candidate, decision, session_key=session_key, title=heading, body=content
    )
    return result
