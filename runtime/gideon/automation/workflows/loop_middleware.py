"""Loop-node middleware — the escalation VOCABULARY, the nudge texts, and steering.

**The decision moved out (PP-15).** This module used to own a second convergence
decision (`check_middleware`, over a mutable `LoopState`, returning a
`MiddlewareVerdict`) alongside `loop.tick.evaluate`'s. Two implementations of "is this
loop converging?" is how the two engines drifted, so the decision was folded into
`loop.tick.evaluate` — the pure one — and the copy here was DELETED rather than kept
behind a flag. `TickState` now carries the counters `LoopState` held, `Decision` carries
what `MiddlewareVerdict` carried, and `tick.Action` carries the four verdict actions.

What stays is what the decision READS and what acting on it needs: the failure
taxonomy (`FailureClass`, `classify_failure`), the call fingerprint, the ladder vocabulary
(`Rung`, `DEFAULT_LADDER`, `CLASS_ENTRY_RUNG`, `_resolve_ladder`), the corrective
instructions (`nudge_for`), the human-facing brief, and the steering queue. These are
reused BY `loop.tick`, not duplicated in it.

`resilience.check_breaker` remains the trip detector for max iterations, the same error
N times, byte-identical output, and a token cap. The tiers below need more than those
counters, and the response machinery for when one trips lives here.

**Continue → Nudge → Halt, not Continue → Halt.** The existing breaker is binary: it
trips or it doesn't. But most thrash is recoverable if you tell the worker what it is
doing wrong, and halting a run that one corrective sentence would fix is expensive in
exactly the way autonomous execution cannot afford. The nudge tier costs one injected
instruction; the halt tier costs a human.

**Tool-argument fingerprinting.** The same error twice is a signal; the same error from
the same *call* twice is a much stronger one. A worker retrying `pytest tests/foo.py`
identically three times has not learned anything from the failure, and that is
detectable without a model.

**Recoverable classes get headroom, not equality.** A rate limit is not a stall — it is
the world saying "wait". Treating a 429 like a wrong answer burns the escalation ladder
on something that would have resolved itself, so recoverable classes get a wider window
and, crucially, **do not consume an escalation rung**.

**Failure classes route to different arms.** Malformed output wants a cheap resume with
feedback. Wrong work wants an expensive fresh session. A rate limit wants a wait and no
escalation at all. One retry policy for all three is the policy that is wrong twice.

**The interrupt queue is consumed atomically at iteration boundaries.** Mid-iteration
injection would race the worker's own state; consuming at the boundary means the
instruction lands where the worker can act on it, and single-use consumption means a
double-resume cannot replay it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FailureClass(str, Enum):
    """What KIND of failure this is. The retry arm follows from it.

    A closed enum because the routing table is exhaustive by construction: an
    unclassified failure falls to `UNKNOWN`, which routes conservatively rather than
    picking an arm by accident.
    """

    MALFORMED_OUTPUT = "malformed_output"
    WRONG_WORK = "wrong_work"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    CONTEXT_OVERFLOW = "context_overflow"
    ENVIRONMENT = "environment"
    GAVE_UP = "gave_up"
    UNKNOWN = "unknown"


RECOVERABLE = frozenset({FailureClass.RATE_LIMIT, FailureClass.TRANSIENT})

RECOVERABLE_HEADROOM = 3

_CLASS_PATTERNS: tuple[tuple[FailureClass, str], ...] = (
    (
        FailureClass.RATE_LIMIT,
        r"\b(?:429|rate ?limit(?:ed|ing)?|too many requests|throttl)",
    ),
    (
        FailureClass.CONTEXT_OVERFLOW,
        r"\b(?:context_length_exceeded|prompt is too long|prompt_too_long|maximum context"
        r"|context window (?:exceeded|full))",
    ),
    (
        FailureClass.ENVIRONMENT,
        r"\b(?:command not found|no such file or directory|permission denied"
        r"|modulenotfounderror|executable not found)",
    ),
    (
        FailureClass.TRANSIENT,
        r"\b(?:5\d\d\b|timed? ?out|timeout|connection (?:reset|refused|aborted)"
        r"|temporarily unavailable|service unavailable|econnreset)",
    ),
    (
        FailureClass.MALFORMED_OUTPUT,
        r"\b(?:json ?decode|invalid json|schema (?:validation )?(?:error|failed)"
        r"|failed to parse|unexpected token|validationerror|parse error)",
    ),
    (
        FailureClass.GAVE_UP,
        r"\b(?:i (?:could ?n[o']?t|was unable to)|giv(?:e|ing) up|needs? human)",
    ),
)


def classify_failure(text: str, *, hint: str = "") -> FailureClass:
    if hint:
        try:
            return FailureClass(hint)
        except ValueError:
            pass
    candidate = (text or "").lower()
    matches = (
        category
        for category, pattern in _CLASS_PATTERNS
        if re.search(pattern, candidate)
    )
    return next(matches, FailureClass.UNKNOWN)


def call_fingerprint(tool: str, args: Any) -> str:
    try:
        serialized = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(args)
    digest = hashlib.sha256()
    digest.update(f"{tool}\x1f{serialized}".encode("utf-8"))
    return digest.hexdigest()[:12]


class Rung(str, Enum):
    """The escalation ladder, in order. Each rung is more expensive than the last."""

    CLASSIFIED_RETRY = "classified_retry"
    FRESH_SESSION = "fresh_session"
    MODEL_SWITCH = "model_switch"
    RESTART_FROM_SCRATCH = "restart_from_scratch"
    SURFACE = "surface"


DEFAULT_LADDER = (
    Rung.CLASSIFIED_RETRY,
    Rung.FRESH_SESSION,
    Rung.MODEL_SWITCH,
    Rung.RESTART_FROM_SCRATCH,
    Rung.SURFACE,
)

CLASS_ENTRY_RUNG: dict[FailureClass, Rung] = {
    FailureClass.MALFORMED_OUTPUT: Rung.CLASSIFIED_RETRY,
    FailureClass.WRONG_WORK: Rung.FRESH_SESSION,
    FailureClass.CONTEXT_OVERFLOW: Rung.FRESH_SESSION,
    FailureClass.GAVE_UP: Rung.FRESH_SESSION,
    FailureClass.ENVIRONMENT: Rung.SURFACE,
    FailureClass.UNKNOWN: Rung.CLASSIFIED_RETRY,
}

DEFAULT_NO_PROGRESS_STOP = 5
DEFAULT_HYPOTHESIS_ABANDON = 3
DEFAULT_FINGERPRINT_WINDOW = 3


def _resolve_ladder(esc: dict[str, Any]) -> tuple[Rung, ...]:
    declaration = esc.get("ladder")
    if not isinstance(declaration, list):
        return DEFAULT_LADDER
    selected = tuple(_RungSequence(declaration).recognized())
    if not selected:
        return DEFAULT_LADDER
    return selected if selected[-1] is Rung.SURFACE else (*selected, Rung.SURFACE)


_STALL_NUDGES: dict[str, str] = {
    "identical_call": (
        "You have run the identical command with identical arguments several times and it "
        "failed the same way each time. Do not run it again unchanged — either change the "
        "command, or investigate why it fails before retrying."
    ),
    "hypothesis_exhausted": (
        "You have applied the same fix repeatedly and it has not worked. Your DIAGNOSIS is "
        "wrong, not your execution. State a different hypothesis before changing any more code."
    ),
    "no_progress": (
        "Several iterations have not improved the outcome. Stop refining the current approach "
        "and state explicitly what is blocking progress, then try a structurally different one."
    ),
}


def nudge_for(
    cls: FailureClass, mutations: dict[str, str], detail: str, *, stall: str = ""
) -> str:
    advice = _RecoveryAdvice(cls, mutations, stall)
    return (
        next(advice.available(), None)
        or f"The run is not progressing ({detail}). Change your approach, not your wording."
    )


def structured_brief(
    *,
    goal: str,
    attempts: list[dict[str, Any]],
    where_stuck: str,
    recommendation: str,
    options: list[str] | None = None,
) -> dict[str, Any]:
    history = [
        _AttemptSummary(position, record).project()
        for position, record in enumerate(attempts[:8], 1)
    ]
    return {
        "goal": goal,
        "attempts": history,
        "where_stuck": where_stuck,
        "recommendation": recommendation,
        "options": options
        or ["reassign", "decompose", "revise", "accept_with_limitations", "defer"],
    }


@dataclass
class Interrupt:
    """One queued mid-run steering instruction."""

    id: str
    text: str
    created_ts: float
    consumed_ts: float = 0.0

    @property
    def consumed(self) -> bool:
        return self.consumed_ts > 0


class InterruptQueue:
    def __init__(self) -> None:
        self._instructions: dict[int, Interrupt] = {}
        self._sequence = 0

    def push(self, text: str, *, now: float | None = None) -> Interrupt | None:
        if not text or not text.strip():
            return None
        self._sequence += 1
        instruction = Interrupt(
            f"int-{self._sequence}", text.strip(), now if now is not None else 0.0
        )
        self._instructions[self._sequence] = instruction
        return instruction

    def pending(self) -> list[Interrupt]:
        return list(
            filter(
                lambda instruction: not instruction.consumed,
                self._instructions.values(),
            )
        )

    def consume(self, *, now: float | None = None) -> list[Interrupt]:
        stamp = now if now is not None else time.time()
        selected = self.pending()
        for instruction in selected:
            instruction.consumed_ts = stamp or 1.0
        return selected

    def as_steering_prompt(self, items: list[Interrupt]) -> str:
        if not items:
            return ""
        prompt = ["[NEW INSTRUCTIONS FROM THE USER — mid-run]"]
        prompt.extend("- " + instruction.text for instruction in items)
        prompt.extend(
            [
                "",
                "Before your next work cycle: re-rank your remaining sub-goals against these "
                "instructions. They may supersede or reprioritise what you planned, not merely "
                "add to it. State briefly what changed, then continue.",
            ]
        )
        return "\n".join(prompt)


_CLASS_NUDGES = {
    FailureClass.MALFORMED_OUTPUT: (
        "Your last output did not match the required schema. Return ONLY the JSON "
        "object described, with no prose or code fence."
    ),
    FailureClass.WRONG_WORK: (
        "Re-read the task statement before continuing. Your last attempt addressed "
        "something the task did not ask for."
    ),
    FailureClass.GAVE_UP: (
        "Do not stop at the first obstacle. State precisely what blocked you, then "
        "try a different approach to the same goal."
    ),
    FailureClass.CONTEXT_OVERFLOW: (
        "Summarize your progress so far into the handoff fields, then continue from "
        "the summary rather than the full history."
    ),
}


class _RungSequence:
    def __init__(self, declaration: list[Any]):
        self.declaration = declaration

    def recognized(self):
        for value in self.declaration:
            try:
                yield Rung(str(value))
            except ValueError:
                logger.debug("unknown escalation rung %r — dropped", value)


class _RecoveryAdvice:
    def __init__(self, category: FailureClass, mutations: dict[str, str], stall: str):
        self.category, self.mutations, self.stall = category, mutations, stall

    def available(self):
        readers = (
            lambda: self.mutations.get(self.category.value),
            lambda: _CLASS_NUDGES.get(self.category),
            lambda: _STALL_NUDGES.get(self.stall),
        )
        for read in readers:
            candidate = read()
            if candidate:
                yield candidate


class _AttemptSummary:
    def __init__(self, position: int, record: dict[str, Any]):
        self.position, self.record = position, record

    def project(self) -> dict[str, Any]:
        result = {"n": self.position, "class": self.record.get("class", "")}
        for field, limit in (("error_signature", 400), ("what_changed", 200)):
            result[field] = str(self.record.get(field, ""))[:limit]
        return result
