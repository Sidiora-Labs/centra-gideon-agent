"""Codex scanner — ``$CODEX_HOME`` (default ``~/.codex``).

Same contract as the Claude Code scanner: a pure, read-only function of a root.
Codex keeps less in its home, so the map is shorter:

====================  =========================================================
``AGENTS.md``         ``instructions``
``config.toml``       ``mcp_servers`` (``[mcp_servers.<name>]``) + ``settings``
``config.json``       same, for a JSON-configured install
====================  =========================================================

TOML is parsed with the stdlib ``tomllib``; a root whose config can't be parsed
simply yields no config items (repairing another tool's half-written file is not
our job).
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from gideon.cognition.onboarding_import.capture_pipeline import SourceCapture
from gideon.cognition.onboarding_import.floors import (
    read_json_safely,
    read_text_safely,
    refuses,
    strip_secrets,
)
from gideon.cognition.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ScanResult,
)

NAME = "codex"
DISPLAY_NAME = "Codex"
ENV_VAR = "CODEX_HOME"
DEFAULT_ROOT = "~/.codex"

_INSTRUCTION_FILES = ("AGENTS.md",)
_TOML_CONFIG = "config.toml"
_JSON_CONFIG = "config.json"
_MCP_KEYS = ("mcp_servers", "mcpServers")


def resolve_root() -> Path:
    return SourceCapture.root(sys.modules[__name__])


def scan(root: Path | str | None = None) -> ScanResult:
    return SourceCapture.codex(sys.modules[__name__], root)


def _read_config(base: Path, result: ScanResult) -> tuple[Any, str]:
    return SourceCapture(sys.modules[__name__], base, result).codex_config()


def _scan_config(config: dict, config_name: str, result: ScanResult) -> None:
    SourceCapture(sys.modules[__name__], None, result).partition_config(
        config, config_name
    )
