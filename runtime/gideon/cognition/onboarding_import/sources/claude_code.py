"""Claude Code scanner — ``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``).

A pure function of a directory: :func:`scan` opens files, applies the floors, and
returns a :class:`~..model.ScanResult`. It holds no store, no session and no config
handle, so it is fixture-testable against a throwaway root — and it never writes to
the root it reads (importing from another tool must not modify that tool).

What it maps:

===================  ==========================================================
``CLAUDE.md``        ``instructions``
``memories/*.md``    ``memories``
``.mcp.json``        ``mcp_servers`` (one item per server)
``skills/<n>/``      ``skills`` (a dir with a ``SKILL.md``)
``settings.json``    ``settings`` (one item; review-gated, never live config)
===================  ==========================================================

Anything else in the root is ignored rather than guessed at.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from gideon.cognition.onboarding_import.capture_pipeline import SourceCapture
from gideon.cognition.onboarding_import.floors import (
    read_json_safely,
    read_text_safely,
    refuses,
)
from gideon.cognition.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ScanResult,
)

NAME = "claude_code"
DISPLAY_NAME = "Claude Code"
ENV_VAR = "CLAUDE_CONFIG_DIR"
DEFAULT_ROOT = "~/.claude"

_INSTRUCTION_FILES = ("CLAUDE.md",)
_MEMORIES_DIR = "memories"
_SKILLS_DIR = "skills"
_MCP_FILE = ".mcp.json"
_SETTINGS_FILE = "settings.json"


def resolve_root() -> Path:
    """Env var first, documented default second (no other search paths)."""
    return SourceCapture.root(sys.modules[__name__])


def scan(root: Path | str | None = None) -> ScanResult:
    phases = (
        "_scan_instructions",
        "_scan_memories",
        "_scan_mcp",
        "_scan_skills",
        "_scan_settings",
        "_scan_conversations",
        "_count_withheld_files",
    )
    return SourceCapture.survey(sys.modules[__name__], root, phases)


def _count_withheld_files(base: Path, result: ScanResult) -> None:
    """Count the credential FILES at the root that we deliberately never opened.

    ``.credentials.json`` sits next to ``settings.json`` in a real root. It is not in
    any category's map, so nothing would ever read it — but saying so is the point:
    the user learns a credential file was present and left alone, and the floor is
    visible instead of implicit. Files the category scanners already accounted for
    are excluded so nothing is counted twice.
    """
    SourceCapture(sys.modules[__name__], base, result).root_secrets()


def _scan_instructions(base: Path, result: ScanResult) -> None:
    SourceCapture(sys.modules[__name__], base, result).instructions()


def _scan_memories(base: Path, result: ScanResult) -> None:
    SourceCapture(sys.modules[__name__], base, result).memories()


def _scan_mcp(base: Path, result: ScanResult) -> None:
    SourceCapture(sys.modules[__name__], base, result).mcp()


def _scan_skills(base: Path, result: ScanResult) -> None:
    SourceCapture(sys.modules[__name__], base, result).skills()


def _scan_settings(base: Path, result: ScanResult) -> None:
    capture = SourceCapture(sys.modules[__name__], base, result)
    capture.settings(_SETTINGS_FILE, capture.structured(_SETTINGS_FILE))


def _scan_conversations(base: Path, result: ScanResult) -> None:
    from gideon.cognition.onboarding_import.transcripts import scan_transcripts

    scan_transcripts(base, result, source=NAME, pattern="projects/*/*.jsonl", format="claude")
