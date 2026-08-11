"""Second-opinion handoff + the ``ProposerBackend`` seam (EXECUTION-ISOLATION §4, EI-7).

``proposer`` is "ask an outside brain one question, and only believe it if the disk agrees".

* :mod:`~gideon.proposer.contract` — the four-member :class:`ProposerBackend` protocol and
  the normalised result record every consumer reads.
* :mod:`~gideon.proposer.brief` — the one-shot handoff brief (fresh diff, fenced excerpts).
* :mod:`~gideon.proposer.selection` — pick a DIFFERENT cataloged runner than the one that
  stalled; the exclusion is structural.
* :mod:`~gideon.proposer.verify` — the disk re-diff that decides acceptance.
* :mod:`~gideon.proposer.backends` — the per-runner backend and the ``subagent`` fallback.
* :mod:`~gideon.proposer.service` — fire-wait-verify, SEL-audited, one definition of
  "accepted" for all three consumers.
"""

from gideon.proposer.brief import HandoffBrief, build_brief
from gideon.proposer.contract import (
    CLAIM_MARKER,
    InvocationRef,
    PreparedInvocation,
    ProposerBackend,
    ProposerResult,
    parse_claimed_paths,
)
from gideon.proposer.selection import Selection, select_target
from gideon.proposer.service import (
    SecondOpinionOutcome,
    choose_backend,
    run_second_opinion,
)
from gideon.proposer.verify import (
    DiffVerification,
    DiskBaseline,
    rediff,
    snapshot_workspace,
)

__all__ = [
    "CLAIM_MARKER",
    "DiffVerification",
    "DiskBaseline",
    "HandoffBrief",
    "InvocationRef",
    "PreparedInvocation",
    "ProposerBackend",
    "ProposerResult",
    "SecondOpinionOutcome",
    "Selection",
    "build_brief",
    "choose_backend",
    "parse_claimed_paths",
    "rediff",
    "run_second_opinion",
    "select_target",
    "snapshot_workspace",
]
