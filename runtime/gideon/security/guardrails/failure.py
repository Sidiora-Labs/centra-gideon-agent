"""Failure-mode taxonomy + typed errors for the model-call chokepoint.

Every attempt through :class:`~gideon.security.guardrails.model_call.ModelCallGuard`
is classified into exactly one :class:`FailureMode` (or ``None`` on success). The
mode drives two decisions: whether the attempt is retried, and what correction
note is injected into the retry prompt.

The taxonomy is deliberately small and provider-agnostic — it classifies what the
GUARD observed (a timeout, an open breaker, a schema miss), not a vendor's error
code. Vendor SDK exceptions collapse to ``provider_error``; the human-facing
mapping of those stays in ``llm_helpers.humanize_provider_error``.
"""

from __future__ import annotations

from enum import Enum


class FailureMode(str, Enum):
    """Why one model-call attempt failed (recorded on every attempt record).

    ``NONE`` marks a passing attempt so the audit trail carries a uniform field.
    """

    NONE = "none"
    SCHEMA_VIOLATION = "schema_violation"
    CONSTRAINT_VIOLATION = "constraint_violation"
    INJECTION_BLOCKED = "injection_blocked"
    SECRET_LEAK = "secret_leak"
    BUDGET_EXCEEDED = "budget_exceeded"
    TOKEN_OVERFLOW = "token_overflow"
    TIMEOUT = "timeout"
    FIRST_TOKEN_TIMEOUT = "first_token_timeout"
    CIRCUIT_OPEN = "circuit_open"
    PROVIDER_ERROR = "provider_error"


NON_RETRYABLE: frozenset[FailureMode] = frozenset(
    {
        FailureMode.INJECTION_BLOCKED,
        FailureMode.SECRET_LEAK,
        FailureMode.BUDGET_EXCEEDED,
        FailureMode.CIRCUIT_OPEN,
        FailureMode.FIRST_TOKEN_TIMEOUT,
    }
)


_CORRECTION_NOTES: dict[FailureMode, str] = {
    FailureMode.SCHEMA_VIOLATION: (
        "Your previous response could not be parsed. Return ONLY a single valid "
        "JSON value of the requested shape — no prose, no markdown fences, nothing "
        "before or after the JSON."
    ),
    FailureMode.CONSTRAINT_VIOLATION: (
        "Your previous response did not satisfy the required constraints. Re-read "
        "the constraints and return a response that satisfies every one of them."
    ),
    FailureMode.TOKEN_OVERFLOW: (
        "Your previous response was too long and was cut off. Respond more "
        "concisely so the full answer fits."
    ),
    FailureMode.TIMEOUT: (
        "The previous attempt timed out. Respond more concisely and directly so "
        "the answer completes quickly."
    ),
}


def correction_note(mode: FailureMode) -> str:
    """The prompt correction note for a retryable ``mode``, or ``""`` if none."""
    return _CORRECTION_NOTES.get(mode, "")


def is_retryable(mode: FailureMode) -> bool:
    """Whether an attempt that failed with ``mode`` may be retried at all."""
    return mode not in NON_RETRYABLE and mode is not FailureMode.NONE


class GuardError(Exception):
    """Base for errors the model-call guard raises to its caller."""

    mode: FailureMode = FailureMode.PROVIDER_ERROR


class AnswerCutOff(GuardError):
    """A response ended before its protocol declared the answer finished."""

    def __init__(self, *, adapter: str, missing: str, model: str = "") -> None:
        self.adapter = adapter
        self.missing = missing
        self.model = model
        whose = f"{adapter} stream for {model}" if model else f"{adapter} stream"
        super().__init__(f"{whose} ended before {missing}: the answer was cut off")

    def sentence(self) -> str:
        return (
            "The model's answer was cut off before its provider said it was finished. "
            "The text received so far is incomplete. Try again; if this continues, "
            "check the model's provider and the gateway log."
        )

    def chat_meta(self) -> dict[str, dict[str, str]]:
        record = {"adapter": self.adapter, "missing": self.missing}
        if self.model:
            record["model"] = self.model
        return {"cut_off": record}


def answer_cut_off(error: object) -> AnswerCutOff | None:
    seen = error if isinstance(error, BaseException) else None
    for _ in range(5):
        if isinstance(seen, AnswerCutOff):
            return seen
        if seen is None:
            break
        seen = seen.__cause__ or seen.__context__
    return None


class ModelCallTimeout(GuardError):
    """A single model-call attempt exceeded its hard wall-clock timeout."""

    mode = FailureMode.TIMEOUT


class FirstTokenTimeout(GuardError):
    """The provider exhausted startup time; repeating the same input cannot help."""

    mode = FailureMode.FIRST_TOKEN_TIMEOUT

    def __init__(self, *, model: str, provider: str, waited_secs: float) -> None:
        self.model = model
        self.provider = provider
        self.waited_secs = waited_secs
        super().__init__(
            f"{' '.join(model.split())} on {' '.join(provider.split())} "
            f"did not start answering within {waited_secs:g} "
            "seconds. Raise Request Timeout in the provider settings, or choose "
            "a faster model."
        )


class CircuitOpenError(GuardError):
    """The provider's circuit breaker is OPEN — the call was refused without work.

    Carries ``provider`` (the breaker key) and ``retry_after`` seconds so a caller
    or the health view can show when the half-open probe becomes eligible.
    """

    mode = FailureMode.CIRCUIT_OPEN

    def __init__(self, provider: str, retry_after: float) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(
            f"circuit breaker for provider {provider!r} is OPEN; "
            f"retry eligible in ~{retry_after:.0f}s"
        )


class OutputContractError(GuardError):
    """A typed ``output_type`` call could not produce a value of the requested shape.

    Raised only after the guard's targeted retry is exhausted, so a caller that
    asked for typed output gets a loud, actionable failure instead of the silent
    ``None`` degrade that ``parse_llm_json`` returned at every call site before.
    """

    mode = FailureMode.SCHEMA_VIOLATION

    def __init__(self, expected: str, raw: str) -> None:
        self.expected = expected
        self.raw = raw
        preview = (raw or "").strip().replace("\n", " ")[:160]
        super().__init__(
            f"model output did not parse as {expected} after a targeted retry; "
            f"got: {preview!r}"
        )


SPENT = "spent"  # the ceiling is reached
NO_ROOM = "no_room"  # what is spent and set aside leaves less than the call may use
UNMEASURED = "unmeasured"  # a known unit rate needs a quantity before admission
UNPRICED = "unpriced"  # a dollar ceiling cannot count a call to a model nothing prices


class BudgetExceededError(GuardError):
    """A model call was refused before it was made, by an unattended run/day spend ceiling.

    Carries the ``scope`` (``run`` | ``day``), the ``dimension`` (``tokens`` | ``dollars``), the
    ``limit`` and what is ``spent`` against it, so the caller (and the pause-into-needs-input
    path) can explain exactly which ceiling bit. ``why`` says how: :data:`SPENT`, the ceiling is
    reached; :data:`NO_ROOM`, what is spent, plus what the calls running now have set aside
    (``held``), leaves less than this call to ``ref`` may use (``needed``); :data:`UNPRICED`, a
    dollar ceiling cannot count a call to ``ref``, a model nothing prices. ``unpriced`` is how
    many calls in that scope had no price, which a dollar total cannot count: the refusal says
    so, rather than presenting what it counted as all that was spent.
    """

    mode = FailureMode.BUDGET_EXCEEDED

    def __init__(
        self,
        scope: str,
        dimension: str,
        limit: float,
        spent: float,
        *,
        unpriced: int = 0,
        why: str = SPENT,
        needed: float = 0.0,
        held: float = 0.0,
        ref: str = "",
        unit: str = "",
        unpriced_for: str = "",
    ) -> None:
        self.scope = scope
        self.dimension = dimension
        self.limit = limit
        self.spent = spent
        self.unpriced = max(0, int(unpriced or 0))
        self.why = why
        self.needed = needed
        self.held = held
        self.ref = ref
        self.unit = unit
        self.unpriced_for = unpriced_for
        super().__init__(self._summary())

    def _summary(self) -> str:
        """The refusal as the logs and a chain's last error carry it."""
        head = f"{self.scope} {self.dimension} budget"
        if self.why == UNMEASURED:
            return f"{head} cannot count a call to {self.ref}: its {self.unit} quantity is unknown"
        if self.why == UNPRICED:
            return f"{head} cannot count a call to {self.ref}: it has no price"
        left_out = self._left_out()
        tail = f", {left_out}" if left_out else ""
        if self.why == NO_ROOM:
            held = f", {self.held:.4g} set aside by calls running now" if self.held > 0 else ""
            return (
                f"{head} has no room for this call: spent {self.spent:.4g} of "
                f"{self.limit:.4g}{held}, and a call to {self.ref} may use {self.needed:.4g}{tail}"
            )
        return f"{head} exceeded: spent {self.spent:.4g} of {self.limit:.4g}{tail}"

    def _left_out(self) -> str:
        """What a dollar figure leaves out; nothing for a token one, which counts every call."""
        from gideon.security.guardrails.budgets import unpriced_clause

        return unpriced_clause(self.unpriced) if self.dimension == "dollars" else ""

    def _amount(self, value: float) -> str:
        if self.dimension == "tokens":
            return f"{int(value):,} tokens"
        return f"${value:.2f}"

    def reason(self) -> str:
        """Why the call was refused, as a clause a person reads: which ceiling stopped it and
        what was spent against it (``… : <fix>`` completes it, :meth:`sentence`)."""
        which = "daily" if self.scope == "day" else "per-run"
        unit = "token" if self.dimension == "tokens" else "dollar"
        if self.why == UNMEASURED:
            return f"the {which} dollar budget cannot count {self.ref} because its {self.unit} quantity is unknown"
        if self.why == UNPRICED and self.unpriced_for:
            return f"{self.ref} has no price for {self.unpriced_for}, so the {which} dollar budget cannot count this call"
        if self.why == UNPRICED:
            return (
                f"{self.ref} has no price, so the {which} dollar budget cannot count what a call "
                "to it would spend"
            )
        left_out = self._left_out()
        if self.why == NO_ROOM:
            left = max(0.0, self.limit - self.spent - self.held)
            running = " once the calls running now are paid for" if self.held > 0 else ""
            verb = "use" if self.dimension == "tokens" else "cost"
            aside = f" ({left_out})" if left_out else ""
            return (
                f"the {which} {unit} budget has {self._amount(left)} left of "
                f"{self._amount(self.limit)}{running}{aside}, and a call to {self.ref} may "
                f"{verb} {self._amount(self.needed)}"
            )
        if self.dimension == "tokens":
            figure = f"{int(self.spent):,} of {int(self.limit):,} tokens"
        else:
            figure = f"${self.spent:.2f} of ${self.limit:.2f}"
            if left_out:
                figure = f"{figure}, {left_out}"
        return f"the {which} {unit} budget is spent ({figure})"

    def fix(self) -> str:
        """Where the refusal is lifted, as a clause."""
        if self.why == UNMEASURED:
            return "provide a measurable quantity before starting unattended work"
        if self.why == UNPRICED:
            return "set its price in Settings → Usage → Model prices, or $0 if it costs nothing"
        if self.scope == "day":
            return "it resets tomorrow, or raise it in Settings → Guardrails"
        return "raise it in Settings → Guardrails"

    def sentence(self) -> str:
        """The refusal as a person reads it: which ceiling stopped the call, what was spent
        against it, and where it is changed."""
        reason = self.reason()
        if self.why != UNPRICED:  # a model's ref opens an unpriced one, spelled as it is
            reason = reason[:1].upper() + reason[1:]
        return f"{reason}: {self.fix()}."

    def remedy(self) -> str:
        """What lifts the refusal, as a step's suggested fix reads it."""
        if self.why == UNMEASURED:
            return self.sentence()
        if self.why == UNPRICED:
            return (
                f"{self.ref} has no price, so the {self.scope} dollar budget cannot count it; set "
                "its price in Settings → Usage → Model prices, or $0 if it costs nothing"
            )
        state = "has no room for this call" if self.why == NO_ROOM else "is spent"
        return (
            f"the {self.scope} {self.dimension} budget {state}; raise it in Settings → "
            "Guardrails, or wait for the daily budget to reset"
        )


class SecretLeakBlocked(GuardError):
    """An outbound prompt was refused at the scan stage in ``block`` mode.

    Carries the count of secret/PII findings that triggered the block. Never
    retried (retrying would let a payload brute-force the scan).
    """

    mode = FailureMode.SECRET_LEAK

    def __init__(self, findings: int) -> None:
        self.findings = findings
        super().__init__(
            f"outbound prompt blocked: {findings} secret/PII finding(s) in block mode"
        )


class PromptInjectionBlocked(GuardError):
    """An outbound prompt was refused because it matched an INJECTION pattern (§2.2 — S156).

    Distinct from :class:`SecretLeakBlocked` on purpose. Both are non-retryable, but for
    opposite reasons: a secret must not be re-sent, while an injection must not be given a
    second attempt to brute-force the guard. Collapsing them would leave an operator unable to
    tell a credential slip from an attack in the audit trail — and ``INJECTION_BLOCKED`` was
    declared, listed in ``NON_RETRYABLE``, and recordable by nothing until this existed.

    Carries the matched pattern ``group`` because a block that cannot be explained cannot be
    appealed — the same rule §1.3 sets for the fire-path screen's ledger row.
    """

    mode = FailureMode.INJECTION_BLOCKED

    def __init__(self, findings: int, group: str = "") -> None:
        self.findings = findings
        self.group = group
        detail = f" (pattern: {group})" if group else ""
        super().__init__(
            f"outbound prompt blocked: prompt-injection pattern matched{detail}"
        )
