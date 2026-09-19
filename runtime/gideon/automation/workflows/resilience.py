"""Retry record codecs, prioritized breaker rules and soft budget projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.models import (
    Failure,
    FailureClass,
    Node,
    NodeKind,
    walk,
)

MUTATION_HINTS = {
    FailureClass.PROTOCOL: (
        "Your previous response could not be parsed in the required format. Return ONLY "
        "the requested structure, with no prose before or after it and no markdown fence."
    ),
    FailureClass.TIMEOUT: (
        "The previous attempt ran out of time. Produce a shorter, more direct response; "
        "do not restate the task or explain your approach."
    ),
    FailureClass.USER: (
        "The previous attempt failed because an input was missing or malformed. Work only "
        "from the inputs actually provided; do not invent values for absent fields."
    ),
    FailureClass.BUDGET: (
        "The previous attempt exceeded its budget. Be substantially more concise."
    ),
    FailureClass.TRANSIENT: (
        "The previous attempt failed for a transient reason. Retry the same work."
    ),
    FailureClass.NETWORK: (
        "The previous attempt failed on a network error. Retry the same work."
    ),
    FailureClass.PERMISSION: (
        "The previous attempt was refused for lack of permission. Do not retry the "
        "refused operation; report what access is required."
    ),
    FailureClass.INTERNAL: (
        "The previous attempt failed unexpectedly. Retry, and prefer the simplest "
        "approach that satisfies the task."
    ),
}

MAX_DIGEST_ATTEMPTS = 3

DEFAULT_ERROR_STREAK = 3

DEFAULT_IDENTICAL_STREAK = 2


def _short_digest(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _coerce_str(value: Any, fallback: str = "") -> str:
    return str(value or fallback)


def _coerce_int(value: Any, fallback: int = 0) -> int:
    return int(value or fallback)


def _coerce_float(value: Any, fallback: float = 0.0) -> float:
    return float(value or fallback)


@dataclass
class Attempt:
    """One try at one node. Journaled per attempt so a retry loop gets actionable"""

    attempt: int
    failure_class: str = ""
    error: str = ""
    expected: str = ""
    actual: str = ""
    evidence: str = ""
    fix_instruction: str = ""
    severity: str = "error"
    error_signature: str = ""
    tokens: int = 0
    duration_secs: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = {
            name: getattr(self, name) for name, _coerce, _fallback in _ATTEMPT_FIELDS
        }
        payload["duration_secs"] = round(self.duration_secs, 3)
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Attempt:
        raw = d or {}
        return cls(
            **{
                name: convert(raw.get(name, default), default)
                for name, convert, default in _ATTEMPT_FIELDS
            }
        )


def _failure_hint(failure: Failure) -> str:
    if failure.remediation:
        return failure.remediation
    return MUTATION_HINTS.get(failure.failure_class, "")


def attempt_from_failure(
    n: int, failure: Failure, *, tokens: int = 0, duration_secs: float = 0.0
) -> Attempt:
    """Build the attempt record from a typed failure. `fix_instruction` comes from the"""
    return Attempt(
        attempt=n,
        failure_class=failure.failure_class.value,
        error=failure.cause_plain[:500],
        fix_instruction=_failure_hint(failure),
        error_signature=error_signature(failure),
        tokens=tokens,
        duration_secs=duration_secs,
    )


def error_signature(failure: Failure) -> str:
    """A stable short hash of (class, normalized message)."""
    message = (failure.cause_plain or "").lower()
    digit_blanks = {ord(ch): " " for ch in message if ch.isdigit()}
    normalized = " ".join(message.translate(digit_blanks).split())[:200]
    return _short_digest(f"{failure.failure_class.value}|{normalized}")


def _correction_hint(record: Attempt) -> str:
    try:
        mode = FailureClass(record.failure_class)
    except ValueError:
        mode = FailureClass.INTERNAL
    return record.fix_instruction or MUTATION_HINTS.get(mode, "")


def retry_prompt(base_prompt: str, attempts: list[Attempt]) -> str:
    if not attempts:
        return base_prompt
    recent = attempts[-MAX_DIGEST_ATTEMPTS:]
    hint = _correction_hint(recent[-1])
    parts = [base_prompt, "", "--- PREVIOUS ATTEMPTS FAILED ---"]
    parts.extend(line for record in recent for line in _attempt_lines(record))
    if hint:
        parts.extend(("", f"CORRECTION: {hint}"))
    return "\n".join(parts)


@dataclass
class BreakerState:
    """Per-loop-node evidence the breaker reasons over. Cheap counters only — the point is
    to catch a thrash at zero model cost."""

    iterations: int = 0
    error_signatures: list[str] = field(default_factory=list)
    output_hashes: list[str] = field(default_factory=list)
    tokens: int = 0

    def record(
        self, *, signature: str = "", output: Any = None, tokens: int = 0
    ) -> None:
        self.iterations += 1
        self.tokens += int(tokens)
        self.error_signatures.append(signature or "")
        self.output_hashes.append(_hash_output(output))


def _hash_output(value: Any) -> str:
    try:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canonical = str(value)
    return _short_digest(canonical)


@dataclass
class BreakerVerdict:
    tripped: bool = False
    reason: str = ""
    detail: str = ""


def _positive_setting(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _streak_setting(cfg: dict[str, Any], key: str, default: int) -> int:
    value = cfg.get(key, default)
    return value if isinstance(value, int) and value >= 1 else default


def _iteration_rule(cfg: dict[str, Any], state: BreakerState) -> BreakerVerdict | None:
    cap = _positive_setting(cfg.get("max_iterations"))
    if cap is not None and state.iterations >= cap:
        return BreakerVerdict(True, "max_iterations", f"reached {cap} iterations")
    return None


def _error_rule(cfg: dict[str, Any], state: BreakerState) -> BreakerVerdict | None:
    streak = _streak_setting(cfg, "error_streak", DEFAULT_ERROR_STREAK)
    seen = [sig for sig in state.error_signatures if sig][-streak:]
    if len(seen) == streak and len(set(seen)) == 1:
        return BreakerVerdict(
            True, "repeated_error", f"the same error {streak}x in a row ({seen[-1]})"
        )
    return None


def _output_rule(cfg: dict[str, Any], state: BreakerState) -> BreakerVerdict | None:
    window_size = _streak_setting(cfg, "identical_streak", DEFAULT_IDENTICAL_STREAK) + 1
    window = state.output_hashes[-window_size:]
    if len(window) == window_size and len(set(window)) == 1:
        return BreakerVerdict(
            True, "identical_output", f"byte-identical output {window_size}x"
        )
    return None


def _token_rule(cfg: dict[str, Any], state: BreakerState) -> BreakerVerdict | None:
    cap = _positive_setting(cfg.get("max_tokens"))
    if cap is not None and state.tokens >= cap:
        return BreakerVerdict(
            True, "token_cap", f"spent {state.tokens} of {cap} tokens"
        )
    return None


def check_breaker(node: Node, state: BreakerState) -> BreakerVerdict:
    """Should this loop stop? Deterministic, LLM-free, evaluated before each iteration."""
    cfg = node.config or {}
    for rule in (_iteration_rule, _error_rule, _output_rule, _token_rule):
        verdict = rule(cfg, state)
        if verdict is not None:
            return verdict
    return BreakerVerdict(False)


ESCALATION_OPTIONS = (
    "reassign",
    "decompose",
    "revise",
    "accept_with_limitations",
    "defer",
)


def escalation_artifact(
    node_id: str,
    *,
    reason: str,
    detail: str = "",
    attempts: list[Attempt] | None = None,
) -> dict[str, Any]:
    payload: dict = dict(
        kind="escalation", node_id=node_id, reason=reason, detail=detail
    )
    payload.update(
        options=list(ESCALATION_OPTIONS),
        attempts=[item.to_dict() for item in (attempts or [])],
    )
    return payload


WARN_FRACTION = 0.8


@dataclass
class BudgetVerdict:
    over: bool = False
    warn: bool = False
    reason: str = ""
    spent: int = 0
    cap: int = 0

    @property
    def fraction(self) -> float:
        if not self.cap:
            return 0.0
        return self.spent / self.cap


def check_budget(
    spent_tokens: int,
    cap_tokens: int,
    *,
    spent_cost: float = 0.0,
    cap_cost: float = 0.0,
) -> BudgetVerdict:
    """Evaluate a soft budget. `cap == 0` means unbounded, which is the default: a cap the"""
    warn = bool(cap_tokens) and spent_tokens >= int(cap_tokens * WARN_FRACTION)
    over = False
    reason = ""
    if cap_tokens and spent_tokens >= cap_tokens:
        over, reason = True, "token budget reached"
    elif cap_cost and spent_cost >= cap_cost:
        over, reason = True, f"cost budget reached (${spent_cost:.2f})"
    elif warn:
        reason = "approaching token budget"
    return BudgetVerdict(over, warn, reason, spent_tokens, cap_tokens)


# ── call estimate ────────────────────────────────────────────────────────────

_MODEL_CALL_KINDS = (NodeKind.STAGE, NodeKind.INFER, NodeKind.VISUALIZE)


def _repeated_body_calls(node: Node) -> int:
    if node.kind == NodeKind.LOOP:
        if node.body is None:
            return 0
        declared = (node.config or {}).get("n")
        rounds = declared if isinstance(declared, int) and declared > 1 else 1
    elif node.kind == NodeKind.FOREACH:
        if node.body is None:
            return 0
        items = (node.config or {}).get("items")
        rounds = len(items) if isinstance(items, list) else 1
    else:
        return 0
    extra_rounds = max(rounds, 1) - 1
    if extra_rounds < 1:
        return 0
    return extra_rounds * sum(
        1 for _path, child in walk(node.body) if child.kind in _MODEL_CALL_KINDS
    )


def estimate_calls(root: Node) -> dict[str, int]:
    """Static model-call estimate from the spec's topology, for a plan review."""
    nodes = 0
    llm_calls = 0
    actions = 0
    for _path, node in walk(root):
        nodes += 1
        if node.kind in _MODEL_CALL_KINDS:
            llm_calls += 1
        elif node.kind == NodeKind.ACTION:
            actions += 1
        llm_calls += _repeated_body_calls(node)
    return {"nodes": nodes, "llm_calls": llm_calls, "actions": actions}


_ATTEMPT_FIELDS = (
    ("attempt", _coerce_int, 0),
    ("failure_class", _coerce_str, ""),
    ("error", _coerce_str, ""),
    ("expected", _coerce_str, ""),
    ("actual", _coerce_str, ""),
    ("evidence", _coerce_str, ""),
    ("fix_instruction", _coerce_str, ""),
    ("severity", _coerce_str, "error"),
    ("error_signature", _coerce_str, ""),
    ("tokens", _coerce_int, 0),
    ("duration_secs", _coerce_float, 0.0),
)


def _attempt_lines(record: Attempt):
    detail = record.error or record.failure_class or "unknown failure"
    yield f"Attempt {record.attempt}: [{record.failure_class}] {detail}"
    if record.expected and record.actual:
        yield f"  expected: {record.expected}"
        yield f"  actual:   {record.actual}"
