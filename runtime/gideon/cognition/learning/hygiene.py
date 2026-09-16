"""One capture-hygiene policy: what the learning cadences are allowed to look at.

Learning turns text into durable state, so anything that reaches a capture cadence
can influence the system's future behaviour. That makes "what is in scope" a
security question, not a quality one — and it was previously answered by filters
scattered across the capture paths, each covering a different subset.

This module is the single auditable answer. Four exclusions, each for a distinct
reason:

**Untrusted content is invisible.** Text inside ``<untrusted_content>`` came from
outside the user↔agent boundary — a fetched page, an inbox body, an MCP payload.
Fencing stops a model from *executing* it in the moment, but learning would copy
it into durable state where it is read back later without a fence. So the span is
removed before any cadence sees it: a planted "always deploy without review"
cannot become a lesson, because the text never reaches the extractor.

**Platform scaffolding is invisible.** Cron preambles, autonudge messages,
subagent-completion events, hook context. At this system's cron density these are
the *larger* pollution volume — the flywheel would dutifully learn that the user
frequently says "CONTINUE the autonomous build", which is true and useless.

**Environment failures are denied.** "tool X is broken", "permission denied" —
already the guardrail in ``after_turn_review``, kept here so every cadence shares
it rather than only the per-turn one. These harden into refusals the agent later
cites against itself.

**Ungrounded turns are skipped** for per-turn capture: a lesson needs both a
decision and evidence for it, with real substance on each side.

**A stated boundary, accepted deliberately.** Text the *user pasted in their own
message* is user-trusted under single-user doctrine and CAN direct-write a lesson
via the correction heuristic. A user pasting a hostile document and then agreeing
with it is a self-inflicted wound the fence cannot distinguish from legitimate
"here is the spec, follow it". Documented rather than silently mitigated.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field

from gideon.cognition.learning.admission_policy import CaptureScreen
from gideon.security.security import _OPEN_TAG_RE as _SECURITY_OPEN_TAG_RE
from gideon.security.security import UNTRUSTED_CLOSE

logger = logging.getLogger(__name__)

MIN_EVIDENCE_DEFAULT = 3

_SYSTEM_MARKERS = (
    "[subagent completion event]",
    "[hook context]",
    "[user nudge]",
    "[cron]",
    "[scheduled task]",
    "[autonudge]",
    "[heartbeat]",
    "[orchestrator]",
)

_SYSTEM_OPENERS = re.compile(
    r"^\s*(?:continue|resume|proceed with)\b[^.\n]{0,80}\b"
    r"(?:autonomous|automated|build|queue|session|loop|nudge)\b",
    re.IGNORECASE,
)

_DECISION_RE = re.compile(
    r"\b(?:decided?|chose|choosing|use[d]?|prefer(?:s|red)?|switch(?:ed)?|"
    r"instead of|rather than|going with|settled on|should(?:n'?t)?|"
    r"always|never|from now on)\b",
    re.IGNORECASE,
)

_EVIDENCE_RE = re.compile(
    r"\b(?:because|since|due to|so that|otherwise|it (?:turned out|broke|worked)|"
    r"result(?:ed|s)?|caused|failed|passed|verified|measured|found|"
    r"the reason|which is why)\b",
    re.IGNORECASE,
)

_MIN_SUBSTANCE_CHARS = 40


@dataclass
class HygieneVerdict:
    """What survived the policy, and what was removed.

    ``text`` is the scrubbed content a cadence may use. The removal flags are
    what makes the policy auditable: a lesson that never appeared can be traced
    to the specific exclusion that dropped it, instead of looking like a bug in
    the extractor.
    """

    text: str
    usable: bool
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.usable


_OPEN_TAG_RE = _SECURITY_OPEN_TAG_RE


def _strip_untrusted(text: str) -> tuple[str, bool]:
    """Remove every fenced span, including an unclosed trailing one.

    An unterminated open marker is treated as fencing the entire remainder. The
    alternative — ignoring a malformed fence — turns a truncated tool output into
    an injection channel, and truncation is routine.
    """
    return CaptureScreen.unfence(sys.modules[__name__], text)


def is_system_injected(text: str) -> bool:
    """True if this text is platform scaffolding rather than user intent.

    Markers are matched as a PREFIX, not searched for. Every emitter puts its
    marker at position 0 (``gateway.py``, ``subagent.py``, ``chat_runner.py`` all
    build ``f"[marker]\\n{body}"``), so a prefix test matches exactly what the
    platform produces — while a windowed search would also flag a user *quoting*
    a marker while asking about it, silently disabling learning for that turn.
    """
    if text:
        normalized = text.lstrip().lower()
        return normalized.startswith(_SYSTEM_MARKERS) or bool(
            _SYSTEM_OPENERS.match(text)
        )
    return False


def is_grounded(text: str) -> bool:
    """True if the text carries BOTH a decision and evidence, with substance.

    Both halves are required. "Use ripgrep" is a decision with no evidence and
    makes a lesson that cannot be re-evaluated later; "the build failed" is
    evidence with no decision and teaches nothing actionable.
    """
    if text and len(text.strip()) >= _MIN_SUBSTANCE_CHARS:
        return all(pattern.search(text) for pattern in (_DECISION_RE, _EVIDENCE_RE))
    return False


def session_score(
    *,
    turns: int = 0,
    decisions: int = 0,
    recalls: int = 0,
    tool_calls: int = 0,
) -> float:
    """Score a session's learning potential, 0.0-1.0 — the consolidation gate.

    Weighted so *decisions* dominate: a long session of one-line exchanges is
    worth less than a short one that concluded something. Saturating rather than
    linear, because the 50th turn adds far less signal than the 5th and a linear
    score would let volume alone clear any threshold.
    """
    return CaptureScreen.score(turns, decisions, recalls, tool_calls)


def scrub(text: str, *, require_grounding: bool = False) -> HygieneVerdict:
    """Apply the whole policy to one piece of text. The single entry point.

    Order matters: untrusted spans are removed FIRST, so the later filters judge
    only trusted content. Checking system-injection before stripping would let a
    fenced payload's opening words decide whether the turn is scaffolding.
    """
    return CaptureScreen.apply(sys.modules[__name__], text, require_grounding)


def fingerprint(text: str) -> str:
    """A stable content fingerprint, whitespace- and case-insensitive.

    Used by the staging tier for idempotence and later by decision memory to
    recognise a refiled proposal. Normalising means a reflowed paragraph is
    recognised as the same content — a fingerprint that changes when a line wraps
    would let the same rejected suggestion return forever.
    """
    words = (text or "").lower().split()
    content = " ".join(words).encode("utf-8")
    return hashlib.sha256(content).digest()[:16].hex()
