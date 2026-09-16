"""Shared framing for autonomous, no-user-present agent turns.

Single source of truth for the instruction prepended to every unattended run
(cron, goal loops, spaces) so the model doesn't fall back to conversational
affordances. The preamble text is the bundled snippet ``autonomous-turn-preamble``
(editable in Settings → Prompts); :data:`AUTONOMOUS_TURN_PREAMBLE` is the
shipped fallback when the prompt store can't resolve.
"""

from __future__ import annotations

_SNIPPET_KEY = "autonomous-turn-preamble"

AUTONOMOUS_TURN_PREAMBLE = (
    "[AUTONOMOUS RUN — no user is present to reply]\n"
    "You are running unattended. Your output is read later as a report; no one "
    "can answer questions or pick options during this run. Therefore:\n"
    "- Do NOT ask the user questions or wait for input.\n"
    '- Do NOT offer interactive menus or option lists (no "which would you like?").\n'
    "- Complete the task end-to-end with the tools available, then report what "
    "you did and found.\n"
    "- If a decision is genuinely blocked on missing input, state the blocker "
    "and your recommended default, and proceed with that default where safe.\n"
    "[END AUTONOMOUS RUN CONTEXT]"
)


def _load_preamble() -> str:
    """Resolve the preamble from the prompt store, falling back to the constant."""
    from gideon.integrations.prompt_providers.runtime import render_snippet_block

    resolved = render_snippet_block(_SNIPPET_KEY)
    return resolved if resolved else AUTONOMOUS_TURN_PREAMBLE


def _preamble() -> str:
    """The autonomous-run preamble — the bound snippet, or the shipped fallback."""
    return _load_preamble()


def with_autonomous_framing(prompt: str) -> str:
    """Prepend the autonomous-run preamble to ``prompt``.

    Returns the framing alone when ``prompt`` is empty, so a bare trigger still
    carries the instruction.
    """
    framing = _preamble()
    body = prompt or ""
    return framing if not body.strip() else f"{framing}\n\n{body}"
