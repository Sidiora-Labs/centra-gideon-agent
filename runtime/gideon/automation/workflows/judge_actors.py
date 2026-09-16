"""Judge authority, independent-session planning and ordered evidence redaction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from gideon.automation.workflows.judge_contract import Isolation

logger = logging.getLogger(__name__)


class Actor(str, Enum):
    """Who is asking for a transition."""

    WORKER = "worker"
    JUDGE = "judge"
    GATE = "gate"
    ENGINE = "engine"
    OWNER = "owner"


WORKER_ALLOWED = frozenset({"running", "waiting", "review", "failed", "no_change"})

TERMINAL_ACTORS = frozenset({Actor.JUDGE, Actor.GATE, Actor.ENGINE, Actor.OWNER})

TERMINAL_STATES = frozenset({"done", "degraded", "escalated", "cancelled", "discarded"})


@dataclass(frozen=True)
class TransitionRuling:
    """Whether a transition is permitted, and why not."""

    allowed: bool
    reason: str = ""
    redirected_to: str = ""


def check_transition(actor: Actor | str, target_state: str) -> TransitionRuling:
    label = str(getattr(actor, "value", actor))
    authority = next((member for member in Actor if member.value == label), None)
    if authority is None:
        return TransitionRuling(
            False, f"unknown actor {actor!r} — refusing terminal authority"
        )
    state = str(target_state or "").lower()
    if authority in TERMINAL_ACTORS or state in WORKER_ALLOWED:
        return TransitionRuling(True)
    reasons = {
        "done": "the worker actor may not complete its own work — routed to review for adjudication"
    }
    return TransitionRuling(
        False,
        reasons.get(state, f"the worker actor may not set state {state!r}"),
        "review",
    )


def resolve_transition(actor: Actor | str, target_state: str) -> tuple[str, str]:
    ruling = check_transition(actor, target_state)
    if not ruling.allowed:
        if ruling.redirected_to:
            logger.info("actor invariant: %s", ruling.reason)
        return ruling.redirected_to or "failed", ruling.reason
    return str(target_state), ""


@dataclass
class JudgeSessionSpec:
    """How to spawn a judge, given what produced the work."""

    fresh_session: bool = True
    avoid_family: str = ""
    require_different_family: bool = False
    strip_provenance: bool = True
    reason: str = ""


def _family_of(model_id: str) -> str:
    normalized = (model_id or "").lower()
    known = (
        "claude",
        "gpt",
        "gemini",
        "llama",
        "qwen",
        "mistral",
        "deepseek",
        "cohere",
    )
    return next(
        (family for family in known if family in normalized),
        normalized.partition("-")[0],
    )


def plan_judge_session(
    *,
    isolation: Isolation | str = Isolation.FRESH,
    worker_session_key: str = "",
    worker_model: str = "",
) -> JudgeSessionSpec:
    label = str(getattr(isolation, "value", isolation))
    mode = next(
        (member for member in Isolation if member.value == label), Isolation.FRESH
    )
    cross_model = mode is Isolation.CROSS_MODEL
    reason = f"isolated from worker session {worker_session_key or '<unknown>'}"
    family = _family_of(worker_model) if cross_model else ""
    if cross_model:
        reason += f"; must not use the {family or 'worker'} family"
    return JudgeSessionSpec(
        fresh_session=True,
        avoid_family=family,
        require_different_family=cross_model,
        strip_provenance=True,
        reason=reason,
    )


def validate_judge_model(
    spec: JudgeSessionSpec, candidate_model: str
) -> tuple[bool, str]:
    if spec.require_different_family:
        family = _family_of(candidate_model)
        refusals = (
            (
                lambda: not family,
                lambda: "cannot determine the candidate judge's model family",
            ),
            (
                lambda: family == spec.avoid_family,
                lambda: f"cross_model isolation requires a different family; {candidate_model!r} is also {family!r}",
            ),
        )
        for applies, describe in refusals:
            if applies():
                return False, describe()
    return True, ""


def blind_provenance(text: str) -> str:
    if not text:
        return text
    from functools import reduce

    return reduce(
        lambda content, rule: rule[0].sub(rule[1], content), _provenance_rules(), text
    )


JUDGE_EVIDENCE_ROLES = frozenset({"user", "spec", "tool_call", "tool_output", "system"})


def assemble_judge_evidence(messages: list[dict], *, blind: bool = True) -> list[dict]:
    return list(_EvidenceProjection(blind).records(messages or []))


def _provenance_rules():
    import re

    patterns = (
        (r"\battempt\s+\d+(?:\s*(?:of|/)\s*\d+)?\b", "[attempt redacted]"),
        (r"\bretry\s*#?\d+\b", "[retry redacted]"),
        (r"\biteration\s+\d+(?:\s*(?:of|/)\s*\d+)?\b", "[iteration redacted]"),
        (r"\bcycle\s+\d+(?:\s*(?:of|/)\s*\d+)?\b", "[cycle redacted]"),
        (r"\b(?:final|last)\s+(?:attempt|try|chance)\b", "[attempt redacted]"),
    )
    return (
        (re.compile(pattern, re.IGNORECASE), replacement)
        for pattern, replacement in patterns
    )


class _EvidenceProjection:
    def __init__(self, blind: bool):
        self.blind = blind

    def records(self, messages: list[dict]):
        for message in messages:
            if str(message.get("role", "")).lower() in JUDGE_EVIDENCE_ROLES:
                projected = dict(message)
                if self.blind:
                    content = projected.get("content")
                    if isinstance(content, str):
                        projected.update(content=blind_provenance(content))
                yield projected
