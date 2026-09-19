"""SDLC stage vocabulary for the code loop kind — the canonical ladder + the lateral
entry types. Pure data, no deps; lives in the unified ``loop`` package so the code kind
+ its classifier don't reach back into the legacy ``code`` package (cutover Slice 2e —
making ``loop/`` self-contained before the legacy engines are deleted)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PHASE_ID_FIELDS: tuple[str, ...] = ("stage", "step", "title")


def phase_id(phase: Mapping[str, Any]) -> str:
    for field in PHASE_ID_FIELDS:
        value = str(phase.get(field, "")).strip()
        if value:
            return value
    return ""


SDLC_STAGES: tuple[str, ...] = (
    "ideation",
    "requirements",
    "design",
    "decomposition",
    "implementation",
    "verification",
    "review",
)

LATERAL_ENTRIES: frozenset[str] = frozenset(
    {"bugfix", "cr_comments", "refactor", "investigation"}
)

ENTRY_STAGES: frozenset[str] = frozenset(SDLC_STAGES) | LATERAL_ENTRIES

PROJECT_KINDS: frozenset[str] = frozenset({"greenfield", "brownfield"})
