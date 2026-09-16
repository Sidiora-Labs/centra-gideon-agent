"""Resolve configuration and workspace locations through explicit precedence rules."""

import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path


class WorkspaceLocator:
    def __init__(
        self,
        override: str | None,
        saved: Callable[[], Path],
        default: Callable[[], Path],
    ):
        self.override = override
        self.saved = saved
        self.default = default

    @staticmethod
    def create(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self) -> Path:
        if self.override:
            return self.create(Path(self.override))
        marker = self.saved()
        if marker.is_file():
            try:
                stored = marker.read_text(encoding="utf-8").strip()
                if stored:
                    return self.create(Path(stored))
            except OSError:
                pass
        return self.create(self.default())


def configuration_home(override: str | None, default: Path, logger) -> Path:
    if not override:
        return default
    candidate = Path(override).expanduser().resolve()
    system = candidate == Path("/") or candidate.parts[:2] in {
        ("/", "usr"),
        ("/", "System"),
        ("/", "etc"),
    }
    if system:
        logger.warning("GIDEON_HOME=%s is a system directory, ignoring", override)
    return default if system else candidate


def directory_partition(directory: str) -> str:
    absolute = os.path.realpath(os.path.expanduser(directory))
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", absolute).strip("_") or "root"
    if len(name) <= 120:
        return name
    fingerprint = hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:12]
    return "_".join((name[:107], fingerprint))
