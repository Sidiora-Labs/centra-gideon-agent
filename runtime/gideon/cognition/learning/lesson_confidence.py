"""Lesson confidence — one derivation, one gate, one precedence rule.

Before this module a lesson was injected at full strength the moment it was
written. A rule observed once by an inference pass and a rule the user has taught
and re-taught a hundred times entered the prompt identically, because the only
question anyone asked was *does a row exist*. Existence is not evidence, and a
prompt built on existence cannot answer either half of what a user actually asks:
"why is it still doing that" or "why did it stop doing that".

**Confidence is DERIVED, never assigned.** Nothing writes a confidence number.
:func:`derive` reads an evidence record — how many times the lesson was observed,
how recently, how often it was contradicted, whether a correction reversed it —
and computes the value fresh. `semantic_memory.confidence` (``1.0`` for
``user_explicit``, ``0.9`` otherwise) is a *source constant* used for write
conflict resolution and is deliberately NOT this number: it says who wrote the
row, not how well the row is supported.

**Retained-but-not-injected is a declared state, not deletion.** A lesson below
the gate stays in the store, keeps accumulating observations, and is reported as
:attr:`LessonStanding.RETAINED`. Discarding weak signal destroys exactly the
evidence that would later make it strong — the second time a rule is observed is
only meaningful because the first was kept.

**The precedence rule (stated ONCE — cite this docstring, do not restate it).**
When evidence both supports and refutes a lesson, refutation wins, in this order:

1. **A REVERSAL voids every observation that preceded it.** Corroboration counts
   only observations recorded *after* the last reversal, so a lesson the user
   un-taught must earn its evidence again from scratch rather than returning at
   the strength it held before.
2. **Each CONTRADICTION cancels one surviving observation** before the
   corroboration curve is read. A lesson observed three times and contradicted
   twice therefore stands exactly where a once-observed lesson stands, and never
   above it.
3. **Only then** do corroboration and recency multiply into a confidence.

The consequence is the property the atom asks for: a lesson can never be injected
alongside its own refutation, because refutation is *subtracted from* the
evidence rather than scored beside it.

**Why an OBSERVATION count and not a usage count.** ``learning.usage`` exempts
lessons from the usage store on purpose — a lesson renders as an always-on block,
so "surfaced" degenerates into "a session happened" and measures how much the
user talks. That exemption is not a claim that lessons have no evidence axis; it
names the wrong one. The right axis is the *capture* side: every time the world
produces this rule again, that is one observation, and the dedup path that used
to drop a repeat silently was throwing that signal away.

**Recency rides the ONE decay kernel.** :mod:`gideon.cognition.learning.decay` is the
single answer to "is this still relevant?", and a second private curve here would
be the third-implementation defect that module was written to end. Human-authored
lessons ride it at ``importance=1.0`` — five times slower, still not never, which
is that module's own "importance is an axis, not an exemption" doctrine rather
than a carve-out invented here.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from gideon.cognition.learning import decay
from gideon.cognition.learning.admission_policy import ConfidencePolicy
from gideon.cognition.learning.hygiene import MIN_EVIDENCE_DEFAULT
from gideon.cognition.learning.lesson_repository import EvidenceCache, LessonRepository

logger = logging.getLogger(__name__)

DECAY_KIND = "lesson"

HUMAN_AUTHORED_IMPORTANCE = 1.0

CORROBORATION_HALFLIFE = 2.0


def corroboration(observations: int) -> float:
    """How corroborated ``observations`` sightings are, in [0.0, 1.0).

    ``1`` yields exactly ``0.0``: a single unrepeated observation is an anecdote,
    and an anecdote has no corroboration to report. The curve saturates rather
    than capping, so the hundredth sighting still counts for a little more than
    the tenth without ever reaching certainty.
    """
    return ConfidencePolicy.support(observations, CORROBORATION_HALFLIFE)


DEFAULT_MIN_CONFIDENCE = corroboration(MIN_EVIDENCE_DEFAULT)


class LessonStanding(str, Enum):
    """Where a lesson stands relative to the injection gate.

    A closed enum rather than a bare boolean because "retained" is a *declared*
    state with its own meaning — still stored, still accumulating evidence, just
    not in the prompt — and a boolean would let a reader mistake it for absence.
    """

    INJECTED = "injected"
    RETAINED = "retained"


@dataclass(frozen=True)
class LessonEvidence:
    """What is known about one lesson's support. The only input to a confidence.

    ``voided`` is the observation count at the moment of the most recent reversal
    — step 1 of the precedence rule in the module docstring. Stored rather than
    recomputed because the observations themselves are counters, not rows: the
    only way to know which sightings preceded a reversal is to record where the
    line fell.
    """

    observations: int = 0
    contradictions: int = 0
    reversals: int = 0
    voided: int = 0
    human_authored: bool = False
    first_observed_at: str = ""
    last_observed_at: str = ""
    last_reversed_at: str = ""

    @property
    def surviving_observations(self) -> int:
        """Observations that survive the precedence rule (module docstring, 1+2)."""
        return max(0, self.observations - self.voided - self.contradictions)


@dataclass(frozen=True)
class LessonVerdict:
    """One lesson's derived confidence, its standing, and why.

    ``reason`` is a sentence a user can read. "Why did it stop doing that" is only
    answerable if the negative decision explains itself — the same rule
    ``GateReason`` follows for capture denials.
    """

    confidence: float
    standing: LessonStanding
    reason: str
    evidence: LessonEvidence

    @property
    def injected(self) -> bool:
        return self.standing is LessonStanding.INJECTED


def derive(evidence: LessonEvidence, *, active_days_idle: float = 0.0) -> float:
    """Derive a confidence in [0.0, 1.0] from evidence alone.

    Pure: no clock, no store, no config. The caller owns the calendar (the same
    contract ``decay.strength`` states) which is what lets the active-days clock
    exist and what makes this testable without freezing time.

    Applies the precedence rule from the module docstring in its stated order —
    reversal voids, contradictions cancel, then corroboration × recency.
    """
    return ConfidencePolicy.derive(sys.modules[__name__], evidence, active_days_idle)


def classify(
    evidence: LessonEvidence,
    *,
    threshold: float = DEFAULT_MIN_CONFIDENCE,
    active_days_idle: float = 0.0,
) -> LessonVerdict:
    """Derive the confidence AND the standing, with a readable reason.

    One function so the gate and the user-facing report can never disagree: the
    number the Memory studio shows is the number the injection filter compared.
    """
    return ConfidencePolicy.classify(
        sys.modules[__name__], evidence, threshold, active_days_idle
    )


def configured_threshold() -> float:
    """The live injection floor from ``learning.min_lesson_confidence``.

    The config READER for that field. Clamped to [0.0, 1.0] here rather than only
    at the PATCH bounds, because ``config.json`` is hand-editable and a negative
    floor is not a looser gate — it is no gate, which would silently restore the
    inject-on-existence behaviour this module replaces. A config load that fails
    falls back to the stated default rather than to "inject everything".
    """
    return ConfidencePolicy.configured(sys.modules[__name__])


def _injected_reason(
    evidence: LessonEvidence, confidence: float, threshold: float
) -> str:
    return ConfidencePolicy.injected_reason(evidence, confidence, threshold)


def _retained_reason(
    evidence: LessonEvidence, confidence: float, threshold: float
) -> str:
    """Why this lesson is held back. Ordered like the precedence rule it reports."""
    return ConfidencePolicy.retained_reason(evidence, confidence, threshold)


class LessonEvidenceStore:
    """Evidence counters for lessons, in ``learning.db`` beside the staging log.

    Deliberately NOT in ``memory.db`` next to the lessons themselves: this is a
    learning-side observation log, it is written on a hot path, and a corrupt
    counter file must never take semantic memory down with it — the same reason
    the staging log lives here.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._repository = LessonRepository(self, base_dir)

    @property
    def path(self) -> Path:
        return self._staging.path

    def close(self) -> None:
        self._staging.close()

    def _ensure(self) -> None:
        self._repository.ensure()

    # ── Recording ──

    def record_observation(
        self, lesson_key: str, *, human_authored: bool = False
    ) -> int:
        """Count one sighting of ``lesson_key``. Returns the new observation total.

        Called for EVERY write that resolves to this lesson — a fresh insert, a
        write the dedup pass suppressed, and the winner of a supersession alike.
        The suppressed case is the one that matters: that repeat used to return
        ``False`` and vanish, which is how a rule the world produced ten times
        stayed indistinguishable from one produced once.

        ``human_authored`` is sticky: once the user has taught a rule directly, a
        later inference pass observing the same rule must not demote it to a
        hypothesis.
        """
        return self._repository.record(
            sys.modules[__name__], "observation", lesson_key, human_authored
        )

    def record_contradiction(self, lesson_key: str) -> None:
        """Record that a later observation contradicted ``lesson_key``.

        Step 2 of the precedence rule (module docstring): this cancels one
        surviving observation, so the contradicted lesson loses confidence rather
        than sitting in the prompt beside its own refutation.
        """
        self._repository.record(sys.modules[__name__], "contradiction", lesson_key)

    def record_reversal(self, lesson_key: str) -> None:
        """Record that a correction REVERSED ``lesson_key`` — the user un-taught it.

        Step 1 of the precedence rule (module docstring): every observation up to
        now is voided. The row is kept, not deleted, because the counters are the
        answer to "why did it stop doing that" — and because the same
        deterministic key comes back if the rule is ever written again, at which
        point it must re-earn its evidence rather than resume at its old strength.
        """
        self._repository.record(sys.modules[__name__], "reversal", lesson_key)

    def carry_forward(self, old_key: str, new_key: str) -> None:
        """Move ``old_key``'s evidence onto the lesson that superseded it.

        A supersession is the same rule said better, so its corroboration belongs
        to the survivor. Dropping it would make "restate a lesson more precisely"
        an evidence reset — the user would watch a well-supported rule fall out of
        the prompt for having been improved.
        """
        self._repository.transfer(sys.modules[__name__], old_key, new_key)

    def evidence_for(self, lesson_key: str) -> LessonEvidence:
        """One lesson's evidence. An absent row reads as zero evidence, not an error."""
        return self._repository.one(sys.modules[__name__], lesson_key)

    def evidence_map(self, lesson_keys: list[str]) -> dict[str, LessonEvidence]:
        """Evidence for many lessons in ONE query — the injection filter's read.

        A per-lesson query on the render path would put fifty round-trips in front
        of every turn.
        """
        return self._repository.many(sys.modules[__name__], lesson_keys)

    def active_days(self) -> list[str]:
        """Every day the user was present — the vacation-proof recency clock.

        Empty when nothing has marked a day yet (a young install, or a session
        that never flushed usage). That reads as ZERO idle days, so recency is
        1.0: a missing clock must never be the reason every lesson silently drops
        out of the prompt.
        """
        return self._repository.days()

    def idle_active_days(self, evidence: LessonEvidence) -> float:
        """Active days since this lesson was last observed."""
        observed = evidence.last_observed_at
        return (
            decay.active_days_between(self.active_days(), observed) if observed else 0.0
        )


def _to_evidence(row: object) -> LessonEvidence:
    return LessonRepository.decode(sys.modules[__name__], row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_INSTANCES: dict[str, LessonEvidenceStore] = {}
_INSTANCE_LOCK = threading.Lock()


def get_store(base_dir: Path | str | None = None) -> LessonEvidenceStore:
    """The shared evidence store for one home directory.

    Cached PER DIRECTORY rather than process-globally: the writer
    (``SemanticArchive``) derives this directory from its own ``memory.db``
    location, so a test pointing at ``tmp_path`` gets a store beside its own
    database instead of one bound to the real home at import time.
    """
    return EvidenceCache.acquire(sys.modules[__name__], base_dir)


def reset_store() -> None:
    """Drop every cached instance (tests, and home-directory switches)."""
    EvidenceCache.clear(sys.modules[__name__])
