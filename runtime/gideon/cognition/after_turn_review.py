"""After-turn self-improvement review — continuous, per-turn learning.

Gideon learns at consolidation time (session-end, batched). This adds a
**continuous** layer: after a learning-worthy turn, a bounded background review
captures durable memory — so a correction at turn 3 is learned before turn 4,
not reconstructed at session end. Absorbs the memory half of the old
"auto-capture corrections" port (the correction heuristic lives here).

Two hard guardrails (the difference between learning and self-sabotage):
- **Never learn environment-dependent failures** — "tool X is broken", "not
  allowed here", "command failed" harden into refusals the agent later cites
  against itself. A deny-filter blocks these from becoming durable memory.
- **Skip sensitive / incognito / temporary sessions.**

**Eligibility is not decided here.** ``LearningGate``
(:mod:`gideon.cognition.learning.gate`) computes it once per event and every cadence
consumes that one decision — this module used to own a ``should_review`` that
callers recomputed independently, which is how two capture paths in the same turn
came to disagree. What stays here is what is genuinely this module's own: the
correction heuristic, the environment-failure deny-filter, and the capture itself.

Writes flow through ``write_lesson`` (→ the contradiction judge from #18), so a
captured correction is deduped + contradiction-checked like any other lesson.

The heuristic + guardrail are pure, testable functions; the actual capture
(``run_after_turn_review``) is best-effort and never blocks the turn.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from gideon.security.guardrails.audit import caller_scope
from gideon.security.security import fence_untrusted

logger = logging.getLogger(__name__)

_STRONG_CORRECTION_RE = re.compile(
    r"\b(nope|wrong|incorrect|that'?s not|not what|why did you|i said|"
    r"you should(?:n'?t| not))\b",
    re.IGNORECASE,
)

_OPENING_CORRECTION_RE = re.compile(
    r"^(?:please\s+|ok(?:ay)?[,\s]+|hey[,\s]+|well[,\s]+|um+[,\s]+)?"
    r"(no|nope|don'?t|do not|stop|actually|instead|never|rather)\b",
    re.IGNORECASE,
)

_ENV_FAILURE_RE = re.compile(
    r"\b(is (?:broken|down|unavailable|not (?:working|installed|available|allowed))|"
    r"not allowed here|no permission|permission denied|can'?t access|cannot access|"
    r"failed to (?:connect|run|start)|times? out|timed out|not configured|"
    r"doesn'?t (?:work|exist)|tool .* (?:broken|unavailable|missing)|exit code|"
    r"command (?:failed|not found)|"
    r"connection (?:refused|reset|aborted|closed)|"
    r"econnrefused|econnreset|econnaborted|etimedout|ehostunreach|enetunreach|enotfound|"
    r"rate ?limited|being rate ?limited|too many requests|"
    r"429 (?:from|error|response|status)|status 429|"
    r"(?:5[0-9]{2}) (?:from|error|response)|bad gateway|service unavailable|"
    r"temporarily unavailable|network (?:error|unreachable|is unreachable)|"
    r"ssl (?:error|handshake)|certificate (?:expired|verify failed)|"
    r"quota exceeded|insufficient quota|no such host|dns (?:failure|error))\b",
    re.IGNORECASE,
)


def is_correction_signal(user_message: str) -> bool:
    """True if a user message looks like a correction of the prior turn.

    Strong signals ("wrong", "that's not", "why did you") count anywhere.
    Directional negations ("no", "don't", "actually", "stop", "never", "instead")
    only count when they LEAD the message — mid-sentence they are almost always
    task instructions ("…do not use tools", "remember to never commit secrets"),
    not corrections, and treating them as corrections poisons the lesson store
    with "User correction to honor: <the whole instruction>".
    """
    if not user_message:
        return False
    if _STRONG_CORRECTION_RE.search(user_message):
        return True
    return bool(_OPENING_CORRECTION_RE.match(user_message.lstrip()))


def is_environment_failure_claim(text: str) -> bool:
    """True if ``text`` is an environment-dependent failure not worth learning.

    The non-negotiable guardrail: these claims must never become a durable
    lesson/skill, or the agent learns to refuse valid actions later.
    """
    return bool(text and _ENV_FAILURE_RE.search(text))


STUMBLE_TRIGGERS = ("correction", "failure_retry", "rejection")


@dataclass(frozen=True)
class StumbleSignal:
    """A turn that used a skill and still went wrong.

    ``trigger`` is one of :data:`STUMBLE_TRIGGERS`; ``detail`` is a short, non-user-text
    fragment for the log (a tool name, or the empty string) — never the user's message, so a
    log line can be quoted in a bug report without carrying session content.
    """

    trigger: str
    detail: str = ""


def detect_stumble(
    *,
    user_message: str,
    assistant_text: str,
    used_skills: list[str],
    tool_outcomes: list[tuple[str, str]] | None = None,
) -> StumbleSignal | None:
    """Classify this turn as a stumble, or ``None``. See the block comment above for the
    full fires-on / ignores contract.

    ``used_skills`` must be the skills whose content actually REACHED the prompt (the
    ``ADMITTED``/``REDUCED`` allocation, which is what ``chat_runner`` already narrows for
    the LV-2 chip) — not the candidate index. The candidate list is every indexed skill, so
    passing it would make "a skill was loaded" true on every turn.

    ``tool_outcomes`` is the turn's ``(tool, outcome)`` sequence in ORDER, as drained from
    the provider. Order is load-bearing: `failure_retry` is a failure *followed by* another
    call to the same tool, and the same multiset with the calls reversed is a success the
    agent then broke, which is not a procedure gap.
    """
    if not used_skills:
        return None
    if is_environment_failure_claim(user_message) or is_environment_failure_claim(
        assistant_text
    ):
        return None
    outcomes = list(tool_outcomes or [])
    if is_correction_signal(user_message):
        return StumbleSignal("correction")
    denied = _denied_without_recovery(outcomes)
    if denied:
        return StumbleSignal("rejection", denied)
    retried = _failed_then_retried(outcomes)
    if retried:
        return StumbleSignal("failure_retry", retried)
    return None


def _failed_then_retried(outcomes: list[tuple[str, str]]) -> str:
    """The first tool that FAILED and was then invoked again, or ``""``.

    The retry is what makes this a signal rather than noise: it says the agent had to work
    around the procedure it was given. A failure at the very end of the turn — nothing after
    it — is an abandoned step, and returns ``""``.
    """
    for i, (tool, outcome) in enumerate(outcomes):
        if outcome != "failed":
            continue
        if any(later == tool for later, _o in outcomes[i + 1 :]):
            return tool
    return ""


def _denied_without_recovery(outcomes: list[tuple[str, str]]) -> str:
    """The first tool the user DENIED and that never succeeded afterwards, or ``""``.

    A denial the agent recovered from (the same tool succeeding later) is the user steering a
    parameter, not refusing the procedure — so it is not a stumble. Only a denial that stood
    is the user saying no to what the skill told the agent to do.
    """
    for i, (tool, outcome) in enumerate(outcomes):
        if outcome != "denied":
            continue
        if not any(later == tool and o == "success" for later, o in outcomes[i + 1 :]):
            return tool
    return ""


def record_procedural_outcomes(
    service, outcomes, *, scope_ref: str | None = None
) -> int:
    """Mine this turn's ``(tool, outcome)`` pairs into procedural memory (M5d).

    ``outcome`` comes from the runtime's drain and must be a member of
    ``memory_service.PROCEDURAL_OUTCOMES``; an unknown value is DROPPED and logged
    rather than stored, because a row no surfacing rule classifies is a row nothing
    will ever read.

    Records one observation per DISTINCT (tool, outcome) — successes become
    'tool X works for this shape' priors, while failures and denials feed
    failure-synthesis (they are never surfaced raw). Returns the count recorded.
    Best-effort; never raises into the turn."""
    from gideon.cognition.memory_service import PROCEDURAL_OUTCOMES

    if service is None or not getattr(service, "has_vector", False) or not outcomes:
        return 0
    seen: set[tuple[str, str]] = set()
    n = 0
    for tool, outcome in outcomes:
        if outcome not in PROCEDURAL_OUTCOMES:
            logger.warning(
                "procedural capture: unknown outcome %r for %s", outcome, tool
            )
            continue
        sig = (tool, outcome)
        if sig in seen:
            continue
        seen.add(sig)
        try:
            if service.record_procedural(
                tool=tool, task_shape=tool, outcome=outcome, scope_ref=scope_ref
            ):
                n += 1
        except Exception:
            logger.debug("procedural capture failed for %s", tool, exc_info=True)
    if n:
        logger.info("Procedural memory: captured %d tool-outcome prior(s)", n)
    return n


def capture_preference_facet(service, user_message: str) -> str | None:
    """No-LLM preference-facet capture (C15): run the cheap heuristic detector over the
    user message and upsert a typed, decaying facet when it fires — a "never do X" →
    veto (routed to write_lesson), a style nudge → a style facet. Reinforces on
    recurrence via upsert. Best-effort; returns the facet text learned, or None.

    Reuses the after-turn pass (no new LLM call). Vetoes unify with the lesson store
    (upsert_facet returns None for veto; the caller writes the lesson)."""
    if service is None or not getattr(service, "has_vector", False):
        return None
    try:
        from gideon.cognition.preference_facets import (
            detect_facet_candidate,
            upsert_facet,
        )
    except Exception:
        return None
    cand = detect_facet_candidate(user_message or "")
    if not cand:
        return None
    cls, text, cue = cand
    vs = getattr(service, "_vs", None)
    if vs is None:
        return None
    try:
        if cls == "veto":
            service.write_lesson(
                f"Never: {text}", category="preference", source="facet_veto"
            )
            return text
        upsert_facet(vs, cls, text, cue=cue)
        return text
    except Exception:
        logger.debug("preference-facet capture failed", exc_info=True)
        return None


def capture_slot_lines(
    service,
    slot: str,
    lines,
    *,
    reinforce: bool = True,
    on_trim_needed=None,
) -> int:
    """Append reflection output into a memory slot. Append-only (MGAV-8). Returns lines written.

    The reflection half of the slots feature: the after-turn pass observes something worth
    keeping as standing state and offers it here. Three constraints, all delegated to
    `memory_slots.append` so they cannot drift apart from the primitive that owns them:

    * **Append-only.** Existing lines are never rewritten or reordered — a change is a new line
      plus a tombstone. That is what makes the memory event log (WAL) and `undo_event` a real
      history of the slot rather than a series of overwrites.
    * **Human tombstones are final.** A line the user deleted is never re-added, however many
      times the reflection pass re-derives it. Re-adding it does not read as a duplicate row to
      a user; it reads as the assistant overruling them.
    * **Over-cap is reported, not swallowed.** A full slot raises a trim proposal, which is
      handed to *on_trim_needed* if the caller supplied one. Without a handler the proposal is
      logged at WARNING and the line is not written — the one thing never done is a silent
      truncation of what the user just said.

    *reinforce* bumps an existing line's `reinforcements` count instead of duplicating it, which
    is how repeated observation accumulates evidence without growing the slot.
    """
    if service is None or not getattr(service, "has_vector", False):
        return 0
    vs = getattr(service, "_vs", None)
    if vs is None:
        return 0
    from gideon.cognition import memory_slots

    written = 0
    for text in lines or []:
        candidate = str(text or "").strip()
        if not candidate:
            continue
        before = len(memory_slots.live_lines(memory_slots.load(vs, slot)))
        try:
            after = memory_slots.append(vs, slot, candidate, reinforce=reinforce)
        except memory_slots.SlotCapExceeded as exc:
            if on_trim_needed is not None:
                on_trim_needed(exc.proposal)
            else:
                logger.warning("slot %r append refused: %s", slot, exc.proposal.message)
            continue
        except Exception:
            logger.debug("slot append failed for %r", slot, exc_info=True)
            continue
        if len(memory_slots.live_lines(after)) > before:
            written += 1
    return written


def run_after_turn_review(
    *,
    service,
    user_message: str,
    assistant_text: str,
    correction: bool,
    judge=None,
    capture_facets: bool = True,
) -> str | None:
    """Best-effort: capture a durable lesson from a corrected turn. Returns the
    learned text (for the chip) or None.

    Scope (deliberately narrow for v1): the high-signal **correction** case — a
    user correction + the agent's adjusted behavior become a lesson, UNLESS it's
    an environment-failure claim (guardrail). The write goes through
    ``write_lesson`` so it's deduped + contradiction-judged (#18). The broader
    LLM skill-ladder review layers on later; this lands the timely memory win
    + the guardrail that protects the whole learning loop.

    Also runs the no-LLM preference-facet detector (C15) on EVERY reviewed turn (not
    just corrections) — a style nudge / veto becomes a typed decaying facet that the
    ambient USER PROFILE block renders.
    """
    if capture_facets:
        capture_preference_facet(service, user_message)
    if service is None or not service.has_vector or not correction:
        return None
    correction_text = (user_message or "").strip()
    if not correction_text:
        return None
    if is_environment_failure_claim(correction_text) or is_environment_failure_claim(
        assistant_text
    ):
        logger.info("after-turn review: skipped env-failure claim (guardrail)")
        return None
    rule = f"User correction to honor: {correction_text[:240]}"
    try:
        if judge is not None:
            service.set_contradiction_judge(judge)
        ok = service.write_lesson(
            rule, category="preference", source="after_turn_review"
        )
    except Exception:
        logger.debug("after-turn review: write_lesson failed", exc_info=True)
        return None
    if ok:
        logger.info("after-turn review: learned a correction")
        return rule
    return None


_LADDER_SCHEMA_HINT = (
    '{"action": "none|refine|support_file|create|template", '
    '"slug": "kebab-case-skill-name", "description": "one line", '
    '"triggers": "comma, separated", "procedure_md": "the steps", '
    '"steps": ["one step per entry (template only)"], '
    '"target": "existing skill name (refine/support_file only)", '
    '"rationale": "why, one line"}'
)


def _build_ladder_prompt(
    *, user_message: str, assistant_text: str, loaded_skills: list[str]
) -> str:
    loaded = ", ".join(loaded_skills) if loaded_skills else "(none loaded this turn)"
    return (
        "You review one completed assistant turn and decide whether a REUSABLE "
        "how-to-do-a-class-of-task skill should be captured. Follow this preference "
        "ladder and pick the EARLIEST that fits (bias hard toward refining what "
        "exists over creating new):\n"
        "  1. refine — improve a currently-loaded skill.\n"
        "  2. refine — improve an existing umbrella skill (name it in 'target').\n"
        "  3. support_file — add a reference/template to an existing skill.\n"
        "  4. create — mint a NEW skill (last resort, only for a genuinely new class).\n"
        "  5. template — the turn ran a repeatable multi-step PROCEDURE (a plan someone "
        "would run again with different inputs) rather than teaching a how-to. Put one "
        "step per entry in 'steps'. A deterministic gate scores these, so list the real "
        "steps and use {{placeholders}} wherever a value would change between runs.\n\n"
        "Return STRICT JSON, no prose:\n" + _LADDER_SCHEMA_HINT + "\n\n"
        "Rules: action='none' unless the turn genuinely taught a reusable procedure "
        "(most turns are 'none'). NEVER capture environment-specific failures, tool "
        "errors, or 'X is broken/not allowed' — those are not skills. Keep procedure_md "
        "concrete and generalizable.\n\n"
        f"Currently-loaded skills: {loaded}\n\n"
        + fence_untrusted(
            f"USER: {user_message[:1500]}\n\nASSISTANT: {assistant_text[:2500]}",
            source="turn",
        )
    )


def _parse_ladder_json(raw: str) -> dict | None:
    """Extract the JSON object from a one-shot response (tolerant of code fences)."""
    import json

    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE
        ).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


async def _template_already_surfaced(slug: str) -> bool:
    """Whether a workflow definition for ``slug`` already exists.

    Resolved against the real def registry rather than left at the dataclass default. The gate's
    ``TEMPLATE_EXISTS`` branch is the one pre-gate that depends on library state, so leaving it
    False would make that branch unreachable in production — a skip reason that can never fire is
    indistinguishable from a gate that does not work.
    """
    if not slug:
        return False
    try:
        from gideon.automation.workflows.service import list_defs

        result = await list_defs()
    except Exception:
        logger.debug("template gate: def listing unavailable", exc_info=True)
        return False
    wanted = slug.strip().lower()
    return any(
        str(d.get("name", "")).strip().lower() == wanted
        for d in result.get("defs") or []
    )


async def _review_template_candidate(decision: dict, *, session_key: str) -> str | None:
    """The ladder's fifth branch: route a procedure-shaped turn through the ad-hoc→template gate.

    The other four branches enqueue a SKILL proposal directly. This one does not decide anything
    itself — it hands the candidate to ``learning.template_gate``, which owns the chain and its
    typed refusal ledger. A refusal is a real result here: it returns None (no chip) but the reason
    is recorded, which is the whole point of §3.2's negative space.
    """
    from gideon.cognition.learning.detectors import Candidate
    from gideon.cognition.learning.template_gate import evaluate

    raw_steps = decision.get("steps")
    steps = (
        [str(s).strip() for s in raw_steps if str(s).strip()]
        if isinstance(raw_steps, list)
        else []
    )
    slug = str(decision.get("slug", "")).strip()
    description = str(decision.get("description", "")).strip()
    if not steps or not slug:
        return None
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        cleaned: list[str] = []
        for step in steps:
            step, _ = redact_exfiltration_urls(step)
            step, _ = redact_credentials(step)
            cleaned.append(step)
        steps = cleaned
    except Exception:
        pass

    candidate = Candidate(
        run_id=session_key,
        steps=steps,
        template_surfaced=await _template_already_surfaced(slug),
        intent=description,
    )
    outcome = evaluate(
        candidate,
        session_key=session_key,
        title=description or f"Template: {slug}",
        body="\n".join(f"- {s}" for s in steps),
    )
    logger.info(
        "template gate: %s for %s (score %.2f, recorded=%s)",
        outcome.decision.skip_reason or outcome.decision.action,
        slug,
        outcome.decision.score.total,
        outcome.recorded,
    )
    if not outcome.filed:
        return None
    return f"Proposed template: {slug}"


_LADDER_VERDICT_LEVEL: dict[str, int] = {
    "provider_error": logging.WARNING,
    "unparsable": logging.WARNING,
    "incomplete_decision": logging.WARNING,
    "enqueue_failed": logging.WARNING,
    "internal_error": logging.WARNING,
    "env_failure_claim": logging.INFO,
    "no_action": logging.INFO,
    "enqueue_skipped": logging.INFO,
    "filed": logging.INFO,
    "template_filed": logging.INFO,
    "template_declined": logging.INFO,
}


def _log_ladder_verdict(
    verdict: str, elapsed_ms: float, session_key: str, detail: str
) -> None:
    """Emit EXACTLY ONE line per ladder pass, carrying its verdict (`G47`).

    Before this, a pass had eight silent exits: the failure path was ``logger.debug`` and the
    ``action == "none"`` path — the common one — logged nothing at all, so a ladder pass that
    died as ``provider_error`` at 60,010 ms and a ladder pass that ran fine were the same
    observation (namely, none). The elapsed time is on the line because "expensive and dead"
    and "cheap and idle" need opposite responses.
    """
    logger.log(
        _LADDER_VERDICT_LEVEL.get(verdict, logging.WARNING),
        "skill-ladder review: %s in %d ms (session=%s)%s",
        verdict,
        int(elapsed_ms),
        session_key or "-",
        f" — {detail}" if detail else "",
    )


async def run_skill_ladder_review(
    *,
    session_key: str,
    user_message: str,
    assistant_text: str,
    loaded_skills: list[str],
    completion=None,
) -> str | None:
    """Forked-LLM skill-axis review (5-tier ladder). Enqueues at most one skill or
    template PROPOSAL (never writes live) and returns a short summary for the chip, or None.

    Attributed and LOGGED (`G47`): the pass runs inside ``audit.caller_scope("skill_ladder")``
    so every model attempt it makes carries its subsystem on the ledger row, and it emits
    exactly one terminal line naming its verdict and its elapsed time. Both halves are the
    same defect — an expensive unattended pass that can be dead in production with no surface
    saying so — and neither half alone closes it: the log line says the pass died, the ledger
    row says what it spent dying.
    """
    started = time.monotonic()
    verdict, detail, summary = "internal_error", "", None
    try:
        with caller_scope("skill_ladder"):
            verdict, detail, summary = await _ladder_pass(
                session_key=session_key,
                user_message=user_message,
                assistant_text=assistant_text,
                loaded_skills=loaded_skills,
                completion=completion,
            )
    finally:
        _elapsed_ms = (time.monotonic() - started) * 1000.0
        _log_ladder_verdict(verdict, _elapsed_ms, session_key, detail)
        _record_ladder_review(verdict, _elapsed_ms, session_key, detail)
    return summary


def _record_ladder_review(
    verdict: str, elapsed_ms: float, session_key: str, detail: str
) -> None:
    """Persist the pass's verdict for the API. Never raises: see `record_review`."""
    try:
        from gideon.extensions.skills import proposals as _proposals

        _proposals.record_review(
            verdict=verdict,
            elapsed_ms=elapsed_ms,
            session_key=session_key,
            detail=detail,
        )
    except Exception:  # pragma: no cover - defence in depth around a `finally`
        logger.debug("skill-ladder review: last-run marker failed", exc_info=True)


async def _ladder_pass(
    *,
    session_key: str,
    user_message: str,
    assistant_text: str,
    loaded_skills: list[str],
    completion=None,
) -> tuple[str, str, str | None]:
    """One ladder pass → ``(verdict, detail, summary)``.

    Split out from :func:`run_skill_ladder_review` so every exit names its verdict: the
    wrapper owns the single log line, and a `return` that named nothing is what made a dead
    pass unobservable. ``verdict`` is a key of :data:`_LADDER_VERDICT_LEVEL`; ``summary`` is
    the chip text (or None), unchanged.

    The fifth tier is the ad-hoc→template branch: it routes a procedure-shaped turn through
    ``learning.template_gate`` instead of the skill queue, because a repeatable plan and a how-to
    are different artifacts and the gate that judges plans is deterministic.

    ``completion`` is an injectable ``async (prompt)->str`` (defaults to
    ``one_shot_completion``) so tests drive it without a real model. Best-effort;
    never raises into the turn."""
    if is_environment_failure_claim(user_message) or is_environment_failure_claim(
        assistant_text
    ):
        return "env_failure_claim", "", None
    if completion is None:
        from gideon.integrations.llm_helpers import one_shot_completion

        async def completion(p: str) -> str:  # noqa: E306
            return await one_shot_completion(p, use_case="background")

    prompt = _build_ladder_prompt(
        user_message=user_message,
        assistant_text=assistant_text,
        loaded_skills=loaded_skills,
    )
    try:
        raw = await completion(prompt)
    except Exception as exc:
        logger.debug("skill-ladder review: completion failed", exc_info=True)
        return "provider_error", type(exc).__name__, None
    decision = _parse_ladder_json(raw)
    if not decision:
        return "unparsable", f"{len(raw or '')} chars returned", None
    action = str(decision.get("action", "none")).strip().lower()
    if action == "template":
        summary = await _review_template_candidate(decision, session_key=session_key)
        return ("template_filed" if summary else "template_declined"), "", summary
    if action not in ("refine", "support_file", "create"):
        return "no_action", f"action={action or '-'}", None

    slug = str(decision.get("slug", "")).strip()
    description = str(decision.get("description", "")).strip()
    procedure_md = str(decision.get("procedure_md", "")).strip()
    triggers = str(decision.get("triggers", "")).strip()
    target = str(decision.get("target", "")).strip()
    if not slug or not description or not procedure_md:
        return "incomplete_decision", f"action={action}", None
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        procedure_md, _ = redact_exfiltration_urls(procedure_md)
        procedure_md, _ = redact_credentials(procedure_md)
    except Exception:
        pass

    try:
        from gideon.extensions.skills import proposals
        from gideon.extensions.skills.loader import AutoSkillProvenance

        prop = proposals.enqueue(
            slug=slug,
            description=description,
            triggers=triggers,
            procedure_md=procedure_md,
            session_key=session_key,
            created_at=AutoSkillProvenance.now_iso(),
            kind="refine" if action in ("refine", "support_file") else "new",
            refine_target=target if action in ("refine", "support_file") else "",
            source_excerpt=f"[after-turn skill-ladder: {action}] {assistant_text}",
        )
    except Exception as exc:
        logger.debug("skill-ladder review: enqueue failed", exc_info=True)
        return "enqueue_failed", type(exc).__name__, None
    if prop is None:
        return "enqueue_skipped", f"{action} {slug}", None
    verb = {"refine": "refine", "support_file": "add file to", "create": "new skill"}[
        action
    ]
    return "filed", f"{action} {prop.slug}", f"Proposed skill ({verb}): {slug}"
