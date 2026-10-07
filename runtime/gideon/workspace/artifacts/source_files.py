"""Admission for live file-backed artifact sources."""

from __future__ import annotations

import hashlib
import os

from gideon.core import file_roots
from gideon.workspace.artifacts.models import MAX_CONTENT_BYTES


def places() -> list[str]:
    """The same writable roots that Files surfaces; read-only grants are excluded."""
    return [root for _label, root in file_roots.dashboard_roots()]


def admitted(raw: str, roots: list[str] | None = None) -> str | None:
    if not raw or not os.path.isabs(raw):
        return None
    admitted_path = file_roots.admit(raw, places() if roots is None else roots)
    if admitted_path is None or not os.path.isfile(admitted_path):
        return None
    return admitted_path


def admit(raw: str) -> str:
    canonical = admitted(raw)
    if canonical is None:
        raise ValueError(
            f"{raw} can't be an artifact source. Choose an existing file in a Files workspace, "
            "outbox, or uploads folder; credential and secret files are not allowed."
        )
    return canonical


def revision_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read(raw: str, roots: list[str] | None = None) -> tuple[str, str]:
    canonical = admitted(raw, roots)
    if canonical is None:
        raise ValueError(f"artifact source is no longer admitted: {raw}")
    try:
        with open(canonical, "rb") as source:
            raw_content = source.read(MAX_CONTENT_BYTES + 1)
        if len(raw_content) > MAX_CONTENT_BYTES:
            raise ValueError("artifact source exceeds the text size limit")
        content = raw_content.decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        raise ValueError(f"artifact source cannot be read: {raw}") from exc
    return content, hashlib.sha256(raw_content).hexdigest()
