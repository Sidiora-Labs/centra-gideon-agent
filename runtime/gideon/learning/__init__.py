"""The learning subsystem — one gate, one hygiene policy, one staging log.

Three capture cadences observe different signals (per-turn, session-end, run-end),
and before this package each one decided independently whether it was allowed to
run and what it was allowed to look at. That is how a capture path silently
disagrees with its neighbour: two copies of an eligibility rule drift, and a
filter that exists on one path is simply absent on another.

So the three questions every cadence has to answer are answered in exactly one
place each:

- **May I capture at all?** → :mod:`gideon.learning.gate` (``LearningGate``)
- **What am I allowed to look at?** → :mod:`gideon.learning.hygiene`
- **Where does the raw signal land?** → :mod:`gideon.learning.staging`
- **How does a change get made?** → :mod:`gideon.learning.proposals`
- **Is it still relevant?** → :mod:`gideon.learning.decay` + `.usage` + `.curator`
- **What reaches the prompt?** → :mod:`gideon.learning.surfacing`

A cadence composes them in that order: gate first (cheapest, and a denial means
nothing else runs), then hygiene on the text, then staging for what survives, and
finally a proposal for anything that would durably change behaviour.

That last module carries the flywheel's trust anchor: **autonomous synthesis
proposes; the human installs.** The system may notice anything and change nothing
on its own.
"""

from __future__ import annotations

from gideon.learning.curator import Candidate, CuratorReport, MutationLog, run_aging
from gideon.learning.decay import DecayVerdict
from gideon.learning.decay import evaluate as evaluate_decay
from gideon.learning.decay import strength
from gideon.learning.gate import (
    Cadence,
    GateDecision,
    GateReason,
    LearningGate,
)
from gideon.learning.hygiene import (
    MIN_EVIDENCE_DEFAULT,
    HygieneVerdict,
    is_system_injected,
    scrub,
    session_score,
)
from gideon.learning.proposals import (
    ChangeManifest,
    Kind,
    Proposal,
    Status,
    Verdict,
    content_fingerprint,
)
from gideon.learning.staging import (
    FlushOutcome,
    StagingStore,
    input_hash,
)
from gideon.learning.surfacing import (
    Allocation,
)
from gideon.learning.surfacing import Candidate as SurfacingCandidate
from gideon.learning.surfacing import (
    Tier,
    allocate,
    classify_intent,
)
from gideon.learning.usage import UsageRecord, UsageStore, promotion_ready

__all__ = [
    "Allocation",
    "Cadence",
    "Candidate",
    "ChangeManifest",
    "CuratorReport",
    "DecayVerdict",
    "FlushOutcome",
    "GateDecision",
    "GateReason",
    "HygieneVerdict",
    "Kind",
    "LearningGate",
    "MutationLog",
    "MIN_EVIDENCE_DEFAULT",
    "Proposal",
    "StagingStore",
    "Status",
    "SurfacingCandidate",
    "Tier",
    "Verdict",
    "UsageRecord",
    "UsageStore",
    "allocate",
    "classify_intent",
    "content_fingerprint",
    "evaluate_decay",
    "input_hash",
    "is_system_injected",
    "promotion_ready",
    "run_aging",
    "scrub",
    "session_score",
    "strength",
]
