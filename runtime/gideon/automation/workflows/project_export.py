"""Project transfer manifests, admission and portable record projections."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

MANIFEST_SCHEMA = 1
PORTABLE_FILES = (
    "project.json",
    "context/overview.md",
    "context/decisions.md",
    "context/not-yet-specified.md",
    "context/out-of-scope.md",
)
PORTABLE_DIRS = ("context", "templates")
PORTABLE_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml", ".txt", ".csv"})
NEVER_EXPORT_DIRS = frozenset(
    {"worktrees", "secrets", "__pycache__", ".git", "node_modules"}
)
MAX_FILE_BYTES = 1 * 1024 * 1024
EXCLUDED_SECRET = "secret"
EXCLUDED_DIR = "never_exported_dir"
EXCLUDED_DOTFILE = "dotfile"
EXCLUDED_TYPE = "unportable_type"
EXCLUDED_EMPTY = "empty_path"
EXCLUSION_TEXT = {
    EXCLUDED_SECRET: "secret — presence flag only, never the value",
    EXCLUDED_DIR: "in a directory that is never exported (size or secrets)",
    EXCLUDED_DOTFILE: "dotfile",
    EXCLUDED_EMPTY: "empty path",
}
_SLOT_RE = re.compile(r"^(?P<base>.*?)(?: \(imported-(?P<n>\d+)\))?$")


@dataclass
class Entry:
    path: str
    size: int
    sha256: str
    kind: str = "file"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExportPlan:
    project_id: str
    project_name: str = ""
    entries: list[Entry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    secrets_present: list[str] = field(default_factory=list)
    artifact_count: int = 0
    run_count: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.entries)

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            **{key: getattr(self, key) for key in ("project_id", "project_name")},
            "entries": [item.to_dict() for item in self.entries],
            "secrets": sorted(self.secrets_present),
            **{key: getattr(self, key) for key in ("artifact_count", "run_count")},
            "skipped": self.skipped.copy(),
            "total_bytes": self.total_bytes,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.manifest()


@dataclass
class ImportIssue:
    path: str
    code: str
    message: str
    fatal: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImportPlan:
    project_name: str
    accepted: list[str] = field(default_factory=list)
    refused: list[ImportIssue] = field(default_factory=list)
    secrets_expected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.accepted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "accepted": self.accepted.copy(),
            "refused": [item.to_dict() for item in self.refused],
            "secrets_expected": self.secrets_expected.copy(),
            "ok": self.ok,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exclusion_text(reason: str) -> str:
    return EXCLUSION_TEXT.get(reason, reason)


def secret_basenames() -> frozenset[str]:
    try:
        from gideon.workspace.portability import EXPORT_EXCLUDE

        names = EXPORT_EXCLUDE
    except Exception:
        names = (
            ".env",
            ".local_secret",
            "credentials",
            "sel_hmac.key",
            "session_map.json",
        )
    return frozenset(names)


class MemberPolicy:
    @staticmethod
    def exclusion(name):
        path = PurePosixPath(name)
        if not path.parts:
            return EXCLUDED_EMPTY
        if NEVER_EXPORT_DIRS.intersection(path.parts):
            return EXCLUDED_DIR
        basename = path.parts[-1]
        if basename in secret_basenames():
            return EXCLUDED_SECRET
        if basename.startswith("."):
            return EXCLUDED_DOTFILE
        suffix = PurePosixPath(basename).suffix.lower()
        if suffix and suffix not in PORTABLE_SUFFIXES:
            return f"{EXCLUDED_TYPE}: {suffix} is not a portable content type"
        return ""

    @staticmethod
    def unsafe_reason(name):
        if not name or name != name.strip():
            return "empty or whitespace-padded member name"
        if name.startswith("/"):
            return "absolute path"
        components = PurePosixPath(name).parts
        if ".." in components:
            return "path traversal"
        if NEVER_EXPORT_DIRS.intersection(components):
            return "member is in a never-exported directory"
        if "\x00" in name:
            return "null byte in member name"
        if len(name) > 512:
            return "member name is implausibly long"
        return ""


def excluded(rel_path: str) -> tuple[bool, str]:
    reason = MemberPolicy.exclusion(rel_path)
    return bool(reason), reason


def safe_member(name: str) -> tuple[bool, str]:
    reason = MemberPolicy.unsafe_reason(name)
    return not reason, reason


def _canonical(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


class ExportSelection:
    def __init__(self, plan):
        self.plan = plan

    def add(self, path, data, kind="file"):
        self.plan.entries.append(Entry(path, len(data), sha256_bytes(data), kind))

    def files(self, files):
        for path, data in sorted(files.items()):
            blocked, reason = excluded(path)
            if blocked:
                self.plan.skipped.append(f"{path}: {exclusion_text(reason)}")
                if reason == EXCLUDED_SECRET:
                    self.plan.secrets_present.append(PurePosixPath(path).name)
            elif len(data) > MAX_FILE_BYTES:
                self.plan.skipped.append(
                    f"{path}: {len(data)} bytes exceeds the per-file cap"
                )
            else:
                self.add(path, data)

    def metadata(self, artifacts, runs):
        for records, path, counter, kind in (
            (artifacts, "artifacts.json", "artifact_count", "metadata"),
            (runs, "runs.json", "run_count", "digest"),
        ):
            if records:
                payload = _canonical(records)
                setattr(self.plan, counter, len(records))
                self.add(path, payload, kind)


def plan_export(
    project_id: str,
    *,
    project_name: str = "",
    files: dict[str, bytes] | None = None,
    artifact_metadata: list[dict[str, Any]] | None = None,
    run_digests: list[dict[str, Any]] | None = None,
    secret_names: list[str] | None = None,
) -> ExportPlan:
    selection = ExportSelection(ExportPlan(project_id, project_name))
    selection.plan.secrets_present.extend(secret_names or [])
    selection.files(files or {})
    selection.metadata(artifact_metadata, run_digests)
    return selection.plan


def artifact_digest(artifact: dict[str, Any]) -> dict[str, Any]:
    lineage = {
        key: value
        for key, value in (artifact.get("meta") or {}).items()
        if str(key).startswith("lineage_")
    }
    return {
        **{key: str(artifact.get(key, "") or "") for key in ("slug", "name", "kind")},
        "version": int(artifact.get("version", 1) or 1),
        "updated_at": str(artifact.get("updated_at", "") or ""),
        "lineage": lineage or dict((artifact.get("lineage") or {}).items()),
    }


def run_digest(run: dict[str, Any]) -> dict[str, Any]:
    fields = ("id", "workflow_name", "status", "created_at", "completed_at")
    return {
        **{key: str(run.get(key, "") or "") for key in fields},
        "total_tokens": int(run.get("total_tokens", 0) or 0),
    }


def verify_entry(entry: dict[str, Any], data: bytes) -> ImportIssue | None:
    path = str(entry.get("path", "") or "")
    digest = str(entry.get("sha256", "") or "")
    if not digest:
        return ImportIssue(
            path,
            "no_digest",
            "manifest entry carries no sha256, so its contents cannot be verified",
        )
    actual = sha256_bytes(data)
    if digest != actual:
        return ImportIssue(
            path,
            "digest_mismatch",
            f"sha256 mismatch: manifest says {digest[:12]}…, contents hash {actual[:12]}…",
        )
    size = entry.get("size")
    if isinstance(size, int) and size != len(data):
        return ImportIssue(
            path,
            "size_mismatch",
            f"manifest says {size} bytes, contents are {len(data)}",
        )
    return None


def collision_name(name: str, existing: list[str]) -> str:
    occupied = set(existing or [])
    if name not in occupied:
        return name
    match = _SLOT_RE.match(name)
    base = (match.group("base") if match else name) or name
    index, candidate = 0, name
    while candidate in occupied:
        index += 1
        candidate = f"{base} (imported-{index})"
    return candidate


class ImportAdmission:
    def __init__(self, manifest, contents, existing_names):
        self.manifest = manifest
        self.contents = contents
        name = str(manifest.get("project_name", "") or "imported project")
        self.plan = ImportPlan(collision_name(name, existing_names or []))

    def entry(self, entry):
        path = str(entry.get("path", "") or "")
        safe, reason = safe_member(path)
        if not safe:
            return ImportIssue(path, "unsafe_member", reason)
        if path not in self.contents:
            return ImportIssue(
                path,
                "missing_content",
                "the manifest lists this entry but the archive does not contain it",
            )
        return verify_entry(entry, self.contents[path])

    def evaluate(self):
        schema = self.manifest.get("schema")
        if schema != MANIFEST_SCHEMA:
            self.plan.refused.append(
                ImportIssue(
                    "manifest.json",
                    "schema_mismatch",
                    f"manifest schema {schema!r} is not {MANIFEST_SCHEMA} — refusing rather than guessing at a shape this build does not know",
                )
            )
            return self.plan
        self.plan.secrets_expected = [
            str(name) for name in self.manifest.get("secrets") or []
        ]
        for entry in self.manifest.get("entries") or []:
            if isinstance(entry, dict):
                issue = self.entry(entry)
                if issue is None:
                    self.plan.accepted.append(str(entry.get("path", "") or ""))
                else:
                    self.plan.refused.append(issue)
        return self.plan


def plan_import(
    manifest: dict[str, Any],
    contents: dict[str, bytes],
    *,
    existing_names: list[str] | None = None,
) -> ImportPlan:
    return ImportAdmission(manifest, contents, existing_names).evaluate()


def import_summary(plan: ImportPlan) -> str:
    count = len(plan.accepted)
    fragments = [f"{count} {'entity' if count == 1 else 'entities'} imported"]
    if plan.refused:
        fragments.append(f"{len(plan.refused)} refused")
    if plan.secrets_expected:
        fragments.append(
            f"{len(plan.secrets_expected)} credential(s) must be re-entered ({', '.join(plan.secrets_expected[:3])})"
        )
    return f"{plan.project_name}: " + "; ".join(fragments)
