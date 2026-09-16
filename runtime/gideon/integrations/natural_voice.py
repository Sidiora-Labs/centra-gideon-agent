"""Resolve natural-voice preferences and append the editable prompt snippet.

REJECTED ALTERNATIVE: a post-hoc rewriting pass costs twice and can change meaning.
The instruction belongs in the composing turn, preserving the user's original text.
"""

from __future__ import annotations

import functools
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)
NATURAL_VOICE_PRECEDENCE: tuple[str, ...] = ("conversation", "agent", "platform")
PLATFORM_DEFAULT: bool = False
_CONVERSATION_STATES: dict[str, bool] = {"on": True, "off": False}


class NaturalVoice(NamedTuple):
    enabled: bool
    source: str


def normalize_conversation_choice(value: object) -> str:
    try:
        candidate = str(value or "").strip().lower()
        return next(
            (choice for choice in _CONVERSATION_STATES if choice == candidate), ""
        )
    except Exception:
        return ""


def resolve(conversation: object = "", agent: bool = False) -> NaturalVoice:
    choice = normalize_conversation_choice(conversation)
    declarations = {"platform": NaturalVoice(PLATFORM_DEFAULT, "platform")}
    if agent:
        declarations["agent"] = NaturalVoice(True, "agent")
    if choice:
        declarations["conversation"] = NaturalVoice(
            _CONVERSATION_STATES[choice], "conversation"
        )
    selected = next(
        (
            declarations[scope]
            for scope in NATURAL_VOICE_PRECEDENCE
            if scope in declarations
        ),
        None,
    )
    if selected is None:
        raise AssertionError(
            f"no scope in NATURAL_VOICE_PRECEDENCE={NATURAL_VOICE_PRECEDENCE!r} stated a value"
        )
    return selected


def agent_default(agent_name: str) -> bool:
    name = str(agent_name or "").strip()
    if name:
        try:
            from gideon.core.config.loader import AppConfig

            agents = AppConfig.load().agents
            profile = agents.get(name)
            return bool(getattr(profile, "natural_voice", False))
        except Exception:
            logger.debug(
                "Could not read natural-voice preference for %r", name, exc_info=True
            )
    return False


@functools.lru_cache(maxsize=1)
def instruction() -> str:
    from gideon.integrations.prompt_providers import runtime

    return runtime.render_snippet_block("natural-voice")


def maybe_inject(message: str, conversation: object = "", agent: bool = False) -> str:
    decision = resolve(conversation, agent)
    if decision.enabled:
        try:
            snippet = instruction()
        except Exception:
            logger.warning("Natural-voice instruction render failed", exc_info=True)
        else:
            if snippet:
                return "{}\n\n{}\n".format(message, snippet)
    return message
