"""Interpreter compatibility from the installed distribution's Python metadata."""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

DISTRIBUTION = "gideon-agent-harness"


@dataclass(frozen=True)
class PythonSupport:
    version: str
    requires: str
    supported: bool | None
    unknown: str = ""


def python_support(version: str, *, distribution: str = DISTRIBUTION) -> PythonSupport:
    release = re.match(r"\d+\.\d+(?:\.\d+)?", version)
    try:
        requires = (
            metadata.metadata(distribution).get("Requires-Python") or ""
        ).strip()
    except metadata.PackageNotFoundError:
        return PythonSupport(
            version, "", None, "no installed package metadata to check"
        )
    except Exception:
        return PythonSupport(
            version, "", None, "installed package metadata is unreadable"
        )
    if not requires:
        return PythonSupport(
            version, "", None, "installed package declares no Python range"
        )
    try:
        spec = SpecifierSet(requires)
    except InvalidSpecifier:
        return PythonSupport(
            version, requires, None, "installed Python range is unreadable"
        )
    if release is None:
        return PythonSupport(
            version, requires, None, "interpreter version is unreadable"
        )
    return PythonSupport(
        version, requires, spec.contains(release.group(0), prereleases=True)
    )


def rebuild_command(requires: str, *, environment: Path | None = None) -> str:
    root = Path(sys.prefix) if environment is None else environment
    if (root / "uv-receipt.toml").is_file():
        return f"uv tool upgrade --python {shlex.quote(requires)} {DISTRIBUTION}"
    return ""
