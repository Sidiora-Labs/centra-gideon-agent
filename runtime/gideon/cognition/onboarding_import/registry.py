"""The source registry — the one list of tools we can import from.

A source is (name, display name, env var, default root, scan function). Adding a
tool is adding one module under :mod:`~gideon.cognition.onboarding_import.sources` and
one row here; nothing downstream changes, which is why broader source coverage was
explicitly not a v1 bar.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gideon.cognition.onboarding_import.import_projection import (
    RecordProjection,
    SourceDirectory,
)
from gideon.cognition.onboarding_import.model import ScanResult
from gideon.cognition.onboarding_import.sources import claude_code, codex, hermes


@dataclass(frozen=True)
class ImportSource:
    name: str
    display_name: str
    env_var: str
    default_root: str
    scan: Callable[..., ScanResult]
    resolve_root: Callable[[], Path]

    def to_dict(self) -> dict:
        return RecordProjection.render("source", self)


def _source(module) -> ImportSource:
    return SourceDirectory.describe(ImportSource, module)


SOURCES: tuple[ImportSource, ...] = (_source(claude_code), _source(codex), _source(hermes))

_BY_NAME: dict[str, ImportSource] = {src.name: src for src in SOURCES}


def list_sources() -> tuple[ImportSource, ...]:
    return SOURCES


def get_source(name: str) -> ImportSource:
    """Look up a source by name. Unknown names raise — never a silent no-op scan."""
    return SourceDirectory.lookup(_BY_NAME, name)
