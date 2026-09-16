"""Turn manifests, bounded backup storage and journaled file restoration."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from gideon.core.atomic_write import atomic_write, atomic_write_bytes

logger = logging.getLogger(__name__)

CHECKPOINT_DIR_NAME = "checkpoints"

NEVER_CAPTURE_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    ".envrc",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*_rsa",
    "*_ed25519",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".htpasswd",
    "credentials",
    "credentials.json",
    "service-account.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    ".local_secret",
)

_IDENTITY_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".tox",
        ".cache",
        ".gideon",
    }
)

_IDENTITY_MAX_ENTRIES = 20000

_SLUG_RE = re.compile("[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class _Bounds:
    enabled: bool
    max_mb: int
    max_turns: int
    max_file_mb: int


_MAX_DIFF_BYTES = 256 * 1024


@dataclass
class RewindFile:
    path: str
    action: str
    turn: int
    reason: str = ""
    current_size: int = -1
    restored_size: int = -1
    current_sha256: str = ""
    restored_sha256: str = ""
    diff: str = ""


@dataclass
class RewindPreview:
    session_key: str
    turn: int
    files: list[RewindFile] = field(default_factory=list)
    turns_affected: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        fields = (
            "path",
            "action",
            "turn",
            "reason",
            "current_size",
            "restored_size",
            "current_sha256",
            "restored_sha256",
            "diff",
        )
        return dict(
            session=self.session_key,
            turn=self.turn,
            turns_affected=self.turns_affected,
            warnings=self.warnings,
            files=[{key: getattr(item, key) for key in fields} for item in self.files],
        )


@dataclass
class RewindResult:
    ok: bool
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    journal: str = ""
    safety_turn: int = 0

    def to_dict(self) -> dict:
        return {
            key: getattr(self, key)
            for key in (
                "ok",
                "restored",
                "deleted",
                "skipped",
                "errors",
                "refused",
                "journal",
                "safety_turn",
            )
        }


_STAGE_SUFFIX = ".gideon-rewind"


def _bounds() -> _Bounds:
    try:
        from gideon.core.config.loader import AppConfig

        limits = AppConfig.load().checkpoints
        return _Bounds(
            bool(limits.enabled),
            max(0, int(limits.max_mb)),
            max(1, int(limits.max_turns)),
            max(0, int(limits.max_file_mb)),
        )
    except Exception:
        logger.debug(
            "checkpoint configuration unavailable; using defaults", exc_info=True
        )
        return _Bounds(True, 200, 50, 8)


def _home() -> Path:
    from gideon.core.config.loader import config_dir

    return Path(config_dir())


def session_slug(session_key: str) -> str:
    identity = (session_key or "unknown").strip()
    label = _SLUG_RE.sub("-", identity).strip("-.")[:48] or "session"
    signature = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()
    return "-".join((label, signature[:12]))


def store_root() -> Path:
    return _home().joinpath(CHECKPOINT_DIR_NAME)


def session_dir(session_key: str) -> Path:
    return store_root().joinpath(session_slug(session_key))


def _blob_dir(session_key: str) -> Path:
    return session_dir(session_key).joinpath("blobs")


def _turn_dir(session_key: str, turn: int) -> Path:
    return session_dir(session_key).joinpath(f"turn-{turn:06d}")


def _state_path(session_key: str) -> Path:
    return session_dir(session_key).joinpath("state.json")


def _journal_path(session_key: str, token: str) -> Path:
    return session_dir(session_key).joinpath(f"rewind-{token}.json")


def _read_json(path: Path, default: dict) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict):
            return document
    except (OSError, ValueError):
        pass
    return dict(default)


def _write_json(path: Path, payload: dict) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, encoded)


def is_never_captured(path: Path | str) -> bool:
    literal = Path(path)
    try:
        canonical = literal.resolve()
    except OSError:
        logger.debug("checkpoint capture cannot resolve %s", literal, exc_info=True)
        return True
    candidates = [literal] if literal == canonical else [literal, canonical]
    if any(
        fnmatch(candidate.name.lower(), pattern.lower())
        for candidate in candidates
        for pattern in NEVER_CAPTURE_GLOBS
    ):
        return True
    try:
        from gideon.security.security import is_sensitive_path

        return any(bool(is_sensitive_path(str(candidate))) for candidate in candidates)
    except Exception:
        logger.debug("checkpoint capture cannot classify %s", literal, exc_info=True)
        return True


@dataclass
class IdentityWalk:
    root: Path
    entries: list[dict] = field(default_factory=list)

    def scan(self) -> tuple[list[dict], bool]:
        for directory, children, filenames in os.walk(self.root):
            children[:] = [
                name
                for name in children
                if name not in _IDENTITY_SKIP_DIRS and not name.startswith(".git")
            ]
            for name in filenames:
                if len(self.entries) >= _IDENTITY_MAX_ENTRIES:
                    return self.entries, True
                path = Path(directory, name)
                try:
                    status = path.stat()
                    relative = path.relative_to(self.root)
                except (OSError, ValueError):
                    continue
                self.entries.append(
                    dict(
                        path=str(relative),
                        size=status.st_size,
                        mtime=round(status.st_mtime, 3),
                    )
                )
        return self.entries, False


def _identity_set(cwd: Path) -> tuple[list[dict], bool]:
    return IdentityWalk(cwd.resolve()).scan()


@dataclass(frozen=True)
class TurnArchive:
    session: str

    def manifest_path(self, turn: int) -> Path:
        return _turn_dir(self.session, turn) / "manifest.json"

    def manifest(self, turn: int) -> dict:
        return _read_json(self.manifest_path(turn), {})

    def open_turn(self, cwd: Path | str | None, bounds: _Bounds) -> int:
        session_dir(self.session).mkdir(parents=True, exist_ok=True)
        state = _read_json(_state_path(self.session), {"current_turn": 0})
        turn = int(state.get("current_turn") or 0) + 1
        state.update(current_turn=turn, session_key=self.session)
        _write_json(_state_path(self.session), state)
        base = Path(cwd).resolve() if cwd else None
        identity, truncated = (
            _identity_set(base) if base is not None and base.is_dir() else ([], False)
        )
        _write_json(
            self.manifest_path(turn),
            dict(
                turn=turn,
                session_key=self.session,
                cwd=str(base) if base else "",
                started_at=time.time(),
                identity=identity,
                identity_truncated=truncated,
                files=[],
            ),
        )
        _enforce_turn_cap(self.session, bounds)
        return turn

    def entries(self, turns: list[int]):
        for turn in turns:
            for entry in self.manifest(turn).get("files") or []:
                if isinstance(entry, dict):
                    yield turn, entry

    def collect_blobs(self) -> int:
        directory = _blob_dir(self.session)
        if not directory.is_dir():
            return 0
        referenced = _referenced_shas(self.session)
        total = 0
        for blob in directory.iterdir():
            if blob.stem in referenced:
                continue
            try:
                total += blob.stat().st_size
                blob.unlink()
            except OSError:
                continue
        return total


def begin_turn(session_key: str, *, cwd: Path | str | None = None) -> int:
    limits = _bounds()
    if not limits.enabled:
        return 0
    try:
        return TurnArchive(session_key).open_turn(cwd, limits)
    except Exception:
        logger.warning(
            "checkpoint turn creation failed for %s", session_key, exc_info=True
        )
        return 0


def current_turn(session_key: str) -> int:
    state = _read_json(_state_path(session_key), {})
    return int(state.get("current_turn") or 0)


@dataclass
class CaptureOperation:
    session: str
    path: Path
    turn: int
    bounds: _Bounds
    manifest: dict
    manifest_path: Path
    files: list

    def record(self, result: str, **fields) -> str:
        self.files.append(
            dict(path=str(self.path), captured_at=self.captured_at, **fields)
        )
        self.manifest["files"] = self.files
        _write_json(self.manifest_path, self.manifest)
        return result

    def record_roots(self, cwd: Path | str | None) -> list:
        roots = self.manifest.get("roots")
        if not isinstance(roots, list):
            roots = []
        if cwd:
            try:
                root = str(Path(cwd).resolve())
                if root not in roots:
                    roots.append(root)
            except OSError:
                logger.debug(
                    "checkpoint capture cannot resolve cwd %s", cwd, exc_info=True
                )
        self.manifest["roots"] = roots
        return roots

    def execute(self, cwd: Path | str | None) -> str:
        roots = self.record_roots(cwd)
        if any(
            isinstance(entry, dict) and entry.get("path") == str(self.path)
            for entry in self.files
        ):
            if roots:
                _write_json(self.manifest_path, self.manifest)
            return "deduped"
        self.captured_at = time.time()
        target = self.path
        if is_never_captured(target):
            return self.record("secret", skipped="secret", existed=target.is_file())
        if not target.exists():
            return self.record("absent", existed=False)
        if target.is_dir():
            return self.record("error", skipped="directory", existed=True)
        try:
            data = target.read_bytes()
        except OSError:
            return self.record("error", skipped="unreadable", existed=True)
        size = len(data)
        if self.bounds.max_file_mb and size > self.bounds.max_file_mb * 1024 * 1024:
            return self.record(
                "too_large", skipped="too_large", existed=True, size=size
            )
        if not _make_room(self.session, size, self.bounds, keep_turn=self.turn):
            return self.record("too_large", skipped="over_cap", existed=True, size=size)
        digest = hashlib.sha256(data).hexdigest()
        blob = _blob_dir(self.session) / f"{digest}.bin"
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(blob, data, mode=0o600)
        return self.record("captured", existed=True, sha256=digest, size=size)


def capture_pre_edit(
    session_key: str, path: Path | str, *, cwd: Path | str | None = None
) -> str:
    limits = _bounds()
    if not limits.enabled:
        return "disabled"
    try:
        target = Path(path)
        turn = current_turn(session_key) or begin_turn(session_key, cwd=cwd)
        if turn <= 0:
            return "error"
        manifest_path = TurnArchive(session_key).manifest_path(turn)
        manifest = _read_json(manifest_path, dict(turn=turn, files=[]))
        entries = manifest.get("files")
        return CaptureOperation(
            session_key,
            target,
            turn,
            limits,
            manifest,
            manifest_path,
            entries if isinstance(entries, list) else [],
        ).execute(cwd)
    except Exception:
        logger.warning("checkpoint capture failed for %s", path, exc_info=True)
        return "error"


def _turn_numbers(session_key: str) -> list[int]:
    directory = session_dir(session_key)
    if not directory.is_dir():
        return []
    turns = []
    for path in directory.iterdir():
        if not path.is_dir() or not path.name.startswith("turn-"):
            continue
        try:
            turns.append(int(path.name.removeprefix("turn-")))
        except ValueError:
            continue
    return sorted(turns)


def recorded_file_entries(session_key: str, *, max_turns: int = 20) -> list[dict]:
    turns = _turn_numbers(session_key)[-max(1, max_turns) :]
    return [
        {**entry, "turn": turn}
        for turn, entry in TurnArchive(session_key).entries(turns)
        if entry.get("path")
    ]


def store_bytes(session_key: str) -> int:
    directory = _blob_dir(session_key)
    if not directory.is_dir():
        return 0
    sizes = []
    for path in directory.iterdir():
        try:
            sizes.append(path.stat().st_size)
        except OSError:
            continue
    return sum(sizes)


def _referenced_shas(session_key: str) -> set[str]:
    return {
        str(entry["sha256"])
        for _, entry in TurnArchive(session_key).entries(_turn_numbers(session_key))
        if entry.get("sha256")
    }


def _gc_blobs(session_key: str) -> int:
    return TurnArchive(session_key).collect_blobs()


def _drop_turn(session_key: str, turn: int) -> None:
    shutil.rmtree(_turn_dir(session_key, turn), ignore_errors=True)


def _enforce_turn_cap(session_key: str, b: _Bounds) -> int:
    turns = _turn_numbers(session_key)
    expired = turns[: max(0, len(turns) - b.max_turns)]
    for turn in expired:
        _drop_turn(session_key, turn)
    if expired:
        _gc_blobs(session_key)
    return len(expired)


def _make_room(session_key: str, incoming: int, b: _Bounds, *, keep_turn: int) -> bool:
    if b.max_mb <= 0:
        return True
    ceiling = b.max_mb * 1024 * 1024
    if incoming > ceiling:
        return False
    while store_bytes(session_key) + incoming > ceiling:
        oldest = next(
            (turn for turn in _turn_numbers(session_key) if turn != keep_turn), None
        )
        if oldest is None:
            return store_bytes(session_key) + incoming <= ceiling
        _drop_turn(session_key, oldest)
        _gc_blobs(session_key)
    return True


def prune_session(session_key: str) -> bool:
    directory = session_dir(session_key)
    existed = directory.exists()
    if existed:
        shutil.rmtree(directory, ignore_errors=True)
    return existed and not directory.exists()


def prune_orphans(live_session_keys: list[str] | set[str]) -> int:
    root = store_root()
    if not root.is_dir():
        return 0
    live = set(map(session_slug, live_session_keys))
    orphaned = [
        path for path in root.iterdir() if path.is_dir() and path.name not in live
    ]
    for path in orphaned:
        shutil.rmtree(path, ignore_errors=True)
    return len(orphaned)


def _sha_of(path: Path) -> str:
    try:
        body = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(body).hexdigest()


def _text_or_none(data: bytes) -> str | None:
    if len(data) > _MAX_DIFF_BYTES or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unified(old: bytes, new: bytes, label: str) -> str:
    before, after = map(_text_or_none, (old, new))
    if before is None or after is None:
        return ""
    differences = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{label} (current)",
        tofile=f"{label} (restored)",
        n=3,
    )
    return "".join(differences)


@dataclass(frozen=True)
class FileProjection:
    session: str
    turn: int
    entry: dict

    def render(self, path: str) -> RewindFile:
        target = Path(path)
        exists = target.is_file()
        size = target.stat().st_size if exists else -1
        record = RewindFile(path, "not_captured", self.turn, current_size=size)
        skipped = str(self.entry.get("skipped") or "")
        if skipped:
            record.reason = skipped
        elif not self.entry.get("existed"):
            record.action = "delete" if exists else "unchanged"
        else:
            digest = str(self.entry.get("sha256") or "")
            blob = _blob_dir(self.session) / f"{digest}.bin"
            if not digest or not blob.is_file():
                record.reason = "blob missing"
            else:
                try:
                    restored = blob.read_bytes()
                except OSError:
                    return RewindFile(
                        path, "not_captured", self.turn, reason="blob unreadable"
                    )
                current = target.read_bytes() if exists else b""
                record.current_sha256 = (
                    hashlib.sha256(current).hexdigest() if exists else ""
                )
                same = exists and record.current_sha256 == digest
                record.action = "unchanged" if same else "restore"
                record.restored_size, record.restored_sha256 = len(restored), digest
                record.diff = "" if same else _unified(current, restored, path)
                return record
        record.current_sha256 = _sha_of(target) if exists else ""
        return record


@dataclass
class PreviewAssembly:
    session: str
    turn: int

    def render(self) -> RewindPreview:
        preview = RewindPreview(self.session, self.turn)
        current = current_turn(self.session)
        warning = ""
        if current <= 0:
            warning = "no checkpoints recorded for this session"
        elif self.turn < 0:
            warning = "turn must be >= 0"
        elif self.turn >= current:
            warning = f"turn {self.turn} is not before the current turn ({current}) — nothing to undo"
        if warning:
            preview.warnings.append(warning)
            return preview
        preview.turns_affected = [
            turn for turn in _turn_numbers(self.session) if turn > self.turn
        ]
        if not preview.turns_affected:
            preview.warnings.append(f"no recorded turns after {self.turn}")
            return preview
        oldest = min(_turn_numbers(self.session), default=current)
        if oldest > self.turn + 1:
            preview.warnings.append(
                f"turns {self.turn + 1}..{oldest - 1} were pruned (cap reached) — their file states are no longer recoverable"
            )
        seen = {}
        for turn, entry in TurnArchive(self.session).entries(preview.turns_affected):
            path = str(entry.get("path") or "")
            if path and path not in seen:
                seen[path] = FileProjection(self.session, turn, entry).render(path)
        preview.files = [seen[path] for path in sorted(seen)]
        notices = {
            "secret": "never captured (credential-shaped file) — it will NOT be restored",
            "too_large": "not captured (over the per-file cap) — not restorable",
        }
        for record in preview.files:
            if record.action == "not_captured" and record.reason in notices:
                preview.warnings.append(f"{record.path}: {notices[record.reason]}")
        return preview


def preview_rewind(session_key: str, turn: int) -> RewindPreview:
    return PreviewAssembly(session_key, turn).render()


@dataclass
class RestoreRoots:
    paths: dict[str, Path] = field(default_factory=dict)

    def include(self, raw: object) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        try:
            resolved = Path(text).resolve()
        except OSError:
            return
        self.paths.setdefault(str(resolved), resolved)


def session_roots(session_key: str) -> list[Path]:
    roots = RestoreRoots()
    archive = TurnArchive(session_key)
    for turn in _turn_numbers(session_key):
        manifest = archive.manifest(turn)
        roots.include(manifest.get("cwd"))
        recorded = manifest.get("roots")
        if isinstance(recorded, list):
            for entry in recorded:
                roots.include(entry)
    try:
        from gideon.core.config.loader import workspace_root

        roots.include(workspace_root())
    except Exception:
        logger.debug("checkpoint restore cannot resolve workspace", exc_info=True)
    return list(roots.paths.values())


def is_within_roots(path: Path | str, roots: list[Path]) -> bool:
    if not roots:
        return False
    try:
        canonical = Path(path).resolve()
    except OSError:
        return False
    return any(canonical.is_relative_to(root) for root in roots)


@dataclass
class RewindTransaction:
    session: str
    turn: int
    preview: RewindPreview
    result: RewindResult = field(default_factory=lambda: RewindResult(False))
    actionable: list[RewindFile] = field(default_factory=list)
    staged: list[Path] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)

    def admit(self) -> None:
        roots = session_roots(self.session)
        for record in self.preview.files:
            if record.action not in ("restore", "delete"):
                continue
            if is_within_roots(record.path, roots):
                self.actionable.append(record)
            else:
                self.result.refused.append(record.path)
                self.result.errors.append(
                    f"refused (outside the session's workspace roots): {record.path}"
                )

    def stage(self, record: RewindFile) -> None:
        if record.action == "delete":
            self.plan.append(dict(path=record.path, op="delete"))
            return
        data = (_blob_dir(self.session) / f"{record.restored_sha256}.bin").read_bytes()
        target = Path(record.path)
        stage = target.with_name(target.name + _STAGE_SUFFIX)
        stage.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(stage, data)
        self.staged.append(stage)
        self.plan.append(
            dict(
                path=record.path,
                op="restore",
                stage=str(stage),
                sha256=record.restored_sha256,
                size=len(data),
            )
        )

    def execute(self) -> RewindResult:
        self.admit()
        result = self.result
        if not self.actionable:
            result.ok = not result.refused
            result.skipped = [record.path for record in self.preview.files]
            return result
        result.safety_turn = begin_turn(self.session, cwd=None)
        for record in self.actionable:
            capture_pre_edit(self.session, record.path)
        token = uuid.uuid4().hex[:16]
        journal = _journal_path(self.session, token)
        try:
            for record in self.actionable:
                self.stage(record)
            _write_json(
                journal,
                dict(
                    token=token,
                    session_key=self.session,
                    to_turn=self.turn,
                    safety_turn=result.safety_turn,
                    staged_at=time.time(),
                    plan=self.plan,
                ),
            )
        except OSError as error:
            for path in self.staged:
                try:
                    path.unlink()
                except OSError:
                    pass
            result.errors.append(f"staging failed, no files were modified: {error}")
            return result
        result.journal = str(journal)
        result.restored, result.deleted, errors = _commit_journal(self.session, token)
        result.errors.extend(errors)
        result.ok = not result.errors
        result.skipped = [
            record.path
            for record in self.preview.files
            if record.action not in ("restore", "delete")
        ]
        return result


def apply_rewind(session_key: str, turn: int, *, preview: RewindPreview | None = None):
    selected = preview if preview is not None else preview_rewind(session_key, turn)
    return RewindTransaction(session_key, turn, selected).execute()


@dataclass
class JournalReplay:
    path: Path
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def apply(self, step: dict) -> None:
        target = Path(str(step.get("path") or ""))
        operation = str(step.get("op") or "")
        try:
            if operation == "delete":
                if target.exists():
                    target.unlink()
                self.deleted.append(str(target))
                return
            stage = Path(str(step.get("stage") or ""))
            expected = str(step.get("sha256") or "")
            if stage.exists():
                os.replace(stage, target)
                self.restored.append(str(target))
            elif expected and _sha_of(target) == expected:
                self.restored.append(str(target))
            else:
                self.errors.append(
                    f"{target}: staged body missing and target does not match"
                )
        except OSError as error:
            self.errors.append(f"{target}: {error}")

    def execute(self) -> tuple[list[str], list[str], list[str]]:
        journal = _read_json(self.path, {})
        for step in journal.get("plan") or []:
            if isinstance(step, dict):
                self.apply(step)
        if not self.errors:
            try:
                self.path.unlink()
            except OSError:
                pass
        return self.restored, self.deleted, self.errors


def _commit_journal(
    session_key: str, token: str
) -> tuple[list[str], list[str], list[str]]:
    return JournalReplay(_journal_path(session_key, token)).execute()


def pending_rewinds(session_key: str) -> list[str]:
    directory = session_dir(session_key)
    if not directory.is_dir():
        return []
    return sorted(
        path.name[len("rewind-") : -len(".json")]
        for path in directory.iterdir()
        if path.is_file()
        and path.name.startswith("rewind-")
        and path.name.endswith(".json")
    )


def resume_incomplete_rewind(session_key: str) -> dict:
    resumed, errors = [], []
    for token in pending_rewinds(session_key):
        restored, deleted, failures = _commit_journal(session_key, token)
        resumed.append(
            dict(token=token, restored=restored, deleted=deleted, errors=failures)
        )
        errors.extend(failures)
    return dict(resumed=resumed, errors=errors)
