"""Ambient prompt candidate and budget entrypoints."""

from __future__ import annotations

import logging

from gideon.cognition.learning.surfacing import (
    AUTHORITY_PREAMBLE,
    Allocation,
    Candidate,
    allocate,
    count_tokens,
)

from .ambient_composition import AmbientBudget, AmbientCandidates, SkillSections

logger = logging.getLogger(__name__)

BASELINE_WINDOW = 200_000
MAX_BUDGET_MULTIPLE = 5.0

SLOT_KINDS: dict[str, str] = {
    "lessons": "lesson",
    "skill_index": "skill",
    "template": "template",
    "voice": "memory",
    "self_model": "memory",
    "procedural": "lesson",
}

PROCEDURAL_SCORE = 0.8

LESSON_KEY_PREFIX = "lesson:"

HINT_CHARS = 80

_SKILL_HEADER = "[Skills:]"
_SKILL_FOOTER = "[End of skills]"


def budget_for_window(window: int | None, base: int) -> int:
    if base <= 0:
        return 0
    scale = (window or BASELINE_WINDOW) / BASELINE_WINDOW
    return int(base * max(1.0, min(MAX_BUDGET_MULTIPLE, scale)))


def lesson_candidates(block: str) -> list[Candidate]:
    return AmbientCandidates().lessons(block)


def _index_entries(block: str) -> list[str]:
    return SkillSections.entries(block)


def _hint(line: str) -> str:
    return SkillSections.hint(line, HINT_CHARS)


def always_body(block: str) -> str:
    return SkillSections.bodies(block)


def index_candidate(block: str) -> Candidate | None:
    return AmbientCandidates().index(block)


def block_candidate(name: str, block: str, *, score: float = 0.85) -> Candidate | None:
    return AmbientCandidates().whole(name, block, score)


def procedural_candidate(block: str) -> Candidate | None:
    return AmbientCandidates().whole("procedural", block, PROCEDURAL_SCORE, strict=True)


def sources_for(
    *,
    lessons: str = "",
    skill_index: str = "",
    voice: str = "",
    persona: str = "",
    self_model: str = "",
    template: str = "",
    procedural: str = "",
) -> dict[str, list[Candidate]]:
    return AmbientCandidates().pool(locals())


def _kept_a_lesson(alloc: Allocation) -> bool:
    stored = ((kind, key) for kind, key, _text in alloc.included)
    return next(
        (
            True
            for kind, key in stored
            if kind == SLOT_KINDS["lessons"] and key.startswith(LESSON_KEY_PREFIX)
        ),
        False,
    )


def render(
    *,
    lessons: str = "",
    skill_index: str = "",
    voice: str = "",
    persona: str = "",
    self_model: str = "",
    template: str = "",
    procedural: str = "",
    query: str = "",
    budget_tokens: int = 4000,
    window: int | None = None,
) -> Allocation:
    ceiling = (
        budget_tokens if window is None else budget_for_window(window, budget_tokens)
    )
    blocks = dict(
        lessons=lessons,
        skill_index=skill_index,
        voice=voice,
        persona=persona,
        self_model=self_model,
        template=template,
        procedural=procedural,
    )
    return AmbientBudget(ceiling, query).render(blocks)


def lesson_header(block: str) -> str:
    return SkillSections.header(block)


def frame(alloc: Allocation, *, lessons_block: str = "") -> str:
    return AmbientBudget(alloc.budget_tokens, "").frame(alloc, lessons_block)


def _is_lesson_block(part: str, alloc: Allocation) -> bool:
    lesson_slot = SLOT_KINDS["lessons"]
    for kind, _key, _tier in alloc.included:
        if kind == lesson_slot:
            return any(line.lstrip().startswith("- ") for line in part.split("\n"))
    return False


def report(alloc: Allocation) -> dict[str, object]:
    return AmbientBudget(alloc.budget_tokens, "").report(alloc)


def preamble_cost() -> int:
    """Token cost of the authority preamble — measured, not asserted.

    Exposed because it is the one fixed overhead in the budget: a caller choosing a very small
    `context_budget_tokens` deserves to know the floor, and S71 already measured that adding the
    preamble unconditionally blew a 50-token budget before a single item was considered.
    """
    return count_tokens(AUTHORITY_PREAMBLE)
