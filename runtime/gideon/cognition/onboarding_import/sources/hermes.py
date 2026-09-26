"""Hermes profile notes and exported conversation scanner."""

from __future__ import annotations

import os
from pathlib import Path

from gideon.cognition.onboarding_import.model import ScanResult
from gideon.cognition.onboarding_import.transcripts import scan_hermes_cli, scan_transcripts

NAME = "hermes"
DISPLAY_NAME = "Hermes"
ENV_VAR = "HERMES_HOME"
DEFAULT_ROOT = "~/.hermes"


def resolve_root() -> Path:
    configured = os.environ.get(ENV_VAR, "").strip()
    root = Path(configured or DEFAULT_ROOT).expanduser()
    if configured:
        return root
    active = root / "active_profile"
    try:
        profile = active.read_text(encoding="utf-8").strip().lower()
    except OSError:
        profile = ""
    return root / "profiles" / profile if profile and profile != "default" else root


def scan(root: Path | str | None = None) -> ScanResult:
    base = resolve_root() if root is None else Path(root).expanduser()
    result = ScanResult(source=NAME, display_name=DISPLAY_NAME, root=str(base), present=base.is_dir())
    if not result.present:
        return result
    scan_transcripts(base, result, source=NAME, pattern="exports/**/*.jsonl", format="hermes")
    scan_transcripts(base, result, source=NAME, pattern="sessions/**/*.jsonl", format="hermes")
    if root is None:
        scan_hermes_cli(result)
    result.note_withheld()
    return result
