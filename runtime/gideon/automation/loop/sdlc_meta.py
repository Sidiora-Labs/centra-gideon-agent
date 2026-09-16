"""SDLC stage vocabulary for the code loop kind — the canonical ladder + the lateral
entry types. Pure data, no deps; lives in the unified ``loop`` package so the code kind
+ its classifier don't reach back into the legacy ``code`` package (cutover Slice 2e —
making ``loop/`` self-contained before the legacy engines are deleted)."""

from __future__ import annotations

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
