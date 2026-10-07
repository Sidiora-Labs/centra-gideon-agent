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
    """Resolve the active home without creating it."""
    if not override:
        return default.expanduser().resolve()
    candidate = Path(override).expanduser().resolve()
    protected = ("/", "/usr", "/System", "/etc")
    system = candidate == Path("/") or any(
        candidate == Path(root) or (root != "/" and Path(root) in candidate.parents)
        for root in protected
    )
    if system:
        logger.warning("GIDEON_HOME=%s is a system directory, ignoring", override)
    return default.expanduser().resolve() if system else candidate


def active_home(
    override: str | None = None, default: Path | None = None, logger=None
) -> Path:
    """Return Gideon's configured home at call time without filesystem writes."""
    import logging

    return configuration_home(
        os.environ.get("GIDEON_HOME") if override is None else override,
        default if default is not None else Path.home() / ".gideon",
        logger or logging.getLogger(__name__),
    )


def directory_partition(directory: str) -> str:
    absolute = os.path.realpath(os.path.expanduser(directory))
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", absolute).strip("_") or "root"
    if len(name) <= 120:
        return name
    fingerprint = hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:12]
    return "_".join((name[:107], fingerprint))
