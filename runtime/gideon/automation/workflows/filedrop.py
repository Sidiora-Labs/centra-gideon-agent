"""Run file-drop policy, immutable input storage and published artifact projection."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.automation.workflows import store

logger = logging.getLogger(__name__)

DROP_DIR = "dropped"

DROP_MANIFEST = "manifest.json"

MAX_DROPPED_FILES = 50

_SAFE_NAME_RE = re.compile(r"[^\w.\-]")

SPEC_KEY = "file_drop"


def safe_filename(raw: str) -> str:
    basename = Path(str(raw or "")).name
    characters = (char if char.isalnum() or char in "_.-" else "_" for char in basename)
    cleaned = "".join(characters).strip("._")
    return (cleaned or "dropped")[:120]


@dataclass
class DropPolicy:
    """Whether a run accepts dropped files, and which of them skip the human gate."""

    enabled: bool = False
    auto_accept_mimes: list[str] = field(default_factory=list)
    reason: str = ""

    def auto_accepts(self, mime: str) -> bool:
        requested = (mime or "").strip().lower()
        if not requested:
            return False
        patterns = (pattern.strip().lower() for pattern in self.auto_accept_mimes)
        return any(
            pattern
            and (
                (pattern.endswith("/*") and requested.startswith(pattern[:-1]))
                or pattern == requested
            )
            for pattern in patterns
        )


def parse_policy(spec: dict[str, Any] | None) -> DropPolicy:
    raw = (spec or {}).get(SPEC_KEY)
    primitives = (
        (None, "this workflow does not declare a file drop"),
        (False, "the workflow disabled its file drop"),
        (True, ""),
    )
    for value, reason in primitives:
        if raw is value:
            return DropPolicy(enabled=value is True, reason=reason)
    if not isinstance(raw, dict):
        return DropPolicy(reason=f"{SPEC_KEY} must be a boolean or an object")
    if raw.get("enabled") is False:
        return DropPolicy(reason="the workflow disabled its file drop")
    values = raw.get("auto_accept_mimes") or []
    if isinstance(values, list):
        return DropPolicy(
            enabled=True,
            auto_accept_mimes=[str(value) for value in values if str(value).strip()],
        )
    return DropPolicy(reason="auto_accept_mimes must be a list of MIME types")


def approval_required(
    policy: DropPolicy, mime: str, *, confirmed: bool
) -> tuple[bool, str]:
    reasons = (
        (
            lambda: policy.auto_accepts(mime),
            "auto-accepted by the workflow's declared MIME types",
        ),
        (lambda: confirmed, "approved by the operator"),
    )
    accepted = next((reason for applies, reason in reasons if applies()), None)
    return (
        (False, accepted)
        if accepted is not None
        else (
            True,
            "this workflow gates every file it did not declare as auto-accepted",
        )
    )


def drop_dir(run_id: str) -> Path:
    return store.run_dir(run_id) / DROP_DIR


def _manifest_path(run_id: str) -> Path:
    return drop_dir(run_id) / DROP_MANIFEST


def read_manifest(run_id: str) -> list[dict[str, Any]]:
    return _DropManifest(run_id).read()


def record_drop(run_id: str, entry: dict[str, Any]) -> None:
    retained = read_manifest(run_id)
    _DropManifest(run_id).replace(retained, entry)


def store_dropped_bytes(run_id: str, filename: str, data: bytes) -> dict[str, Any]:
    safe = safe_filename(filename)
    zone = _DropZone(drop_dir(run_id))
    zone.write(safe, filename, data)
    return dict(
        filename=safe,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        lifecycle="immutable",
    )


def read_dropped_text(run_id: str, filename: str, *, limit: int = 64 * 1024) -> str:
    from gideon.security.security import fence_untrusted

    zone = _DropZone(drop_dir(run_id))
    text = zone.read(safe_filename(filename), limit)
    return (
        ""
        if text is None
        else fence_untrusted(text, source=f"dropped file {safe_filename(filename)}")
    )


def outbox_entries(run_id: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in store.read_jsonl(run_id, "publishes.jsonl"):
        slug = str(record.get("slug") or "")
        if slug:
            fields = {
                name: str(record.get(name) or "")
                for name in ("artifact", "kind", "action", "change_note", "node_id")
            }
            latest[slug] = dict(
                slug=slug,
                **fields,
                updated_at=str(record.get("ts") or ""),
                self_contained=bool(
                    (record.get("media") or {}).get("self_contained", True)
                ),
            )
    return sorted(
        latest.values(),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )


class _DropManifest:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.path = _manifest_path(run_id)

    def read(self) -> list[dict[str, Any]]:
        if self.path.is_file():
            try:
                entries = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning(
                    "run %s: unreadable drop manifest", self.run_id, exc_info=True
                )
                return []
            if isinstance(entries, list):
                return list(filter(lambda entry: isinstance(entry, dict), entries))
        return []

    def replace(self, entries: list[dict[str, Any]], incoming: dict[str, Any]) -> None:
        from gideon.core.atomic_write import atomic_write

        updated = []
        for entry in entries:
            if entry.get("filename") != incoming.get("filename"):
                updated.append(entry)
        updated.append(incoming)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(updated[-MAX_DROPPED_FILES:], indent=2))


@dataclass(frozen=True)
class _DropZone:
    directory: Path

    def write(self, name: str, original: str, data: bytes) -> None:
        from gideon.core.atomic_write import atomic_write_bytes

        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / name
        if destination.resolve().is_relative_to(self.directory.resolve()):
            atomic_write_bytes(destination, data)
            return
        raise ValueError(f"dropped filename escapes the drop dir: {original!r}")

    def read(self, name: str, limit: int) -> str | None:
        try:
            contents = (self.directory / name).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return None
        return contents[:limit]
