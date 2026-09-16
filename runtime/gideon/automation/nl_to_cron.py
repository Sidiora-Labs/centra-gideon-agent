"""Convert a cadence request into a validated recurring schedule."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _first_response_line(raw: str) -> str:
    unfenced = re.sub(r"```[a-z]*", "", (raw or "").strip()).replace("`", "")
    lines = (line.strip() for line in unfenced.splitlines())
    return next(filter(None, lines), "")


def parse_cron_response(raw: str) -> tuple[str, str]:
    from gideon.automation.schedule import validate_cron_expr

    candidate = _first_response_line(raw)
    if candidate.upper() in ("", "NONE"):
        return "", "Not a recurring schedule — use a one-off time (at / delay) instead."
    if len(candidate.split()) != 5:
        return "", f"Could not parse a 5-field cron expression from: {candidate[:80]!r}"
    if validate_cron_expr(candidate):
        return candidate, ""
    return "", f"Generated an invalid cron expression: {candidate!r}"


async def _ask_scheduler(prompt: str) -> str:
    from gideon.integrations.llm_helpers import one_shot_completion
    from gideon.security.guardrails.audit import caller_scope

    with caller_scope("nl_to_cron"):
        return await one_shot_completion(prompt, use_case="background")


async def nl_to_cron(request: str, *, ask=None) -> tuple[str, str]:
    normalized = (request or "").strip()
    if normalized:
        from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

        prompt = render_use_case_prompt("nl_to_cron", dict(request=normalized))
        if prompt:
            complete = _ask_scheduler if ask is None else ask
            try:
                response = await complete(prompt)
            except Exception:
                logger.debug("nl_to_cron LLM call failed", exc_info=True)
                return "", "Could not reach a model to interpret the schedule."
            return parse_cron_response(response)
        return "", "Could not load the schedule-interpretation prompt."
    return "", "Empty request."
