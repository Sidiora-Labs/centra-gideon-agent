"""Installed-pack ledger (AGENT-PACKS §9, AP-3).

``<home>/packs/installed.json`` records what each imported pack put on this machine: its
components, the resolution of each connector requirement (§3.3), and its post-install
setup skill (§3.4). It is the READER surface behind two done_when contracts:

* a **skipped connector** degrades with a machine-readable ``connector_missing:<name>``
  marker recorded here, so a connector-dependent feature (and the pack detail page) can
  read "this is unavailable" without re-deriving it;
* a **setup skill** is re-runnable — the ledger keeps ``setup_pending`` true while a pack
  carries one, so the "Finish setup" chip always has something to re-invoke.

It is NOT the import journal (:class:`packs.import_._Journal` — the crash-safe rollback
ledger, deleted on success). This ledger is the durable post-install record; it is written
only after a commit fully succeeds, so it never describes a rolled-back pack.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from gideon.core.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

LEDGER_FILE = "installed.json"
_LEDGER_LOCK_TIMEOUT = 5.0
_ledger_thread_locks: dict[str, threading.Lock] = {}
_ledger_thread_locks_guard = threading.Lock()


class InstalledPackLedgerError(OSError):
    """The installed-pack ledger could not be read or updated safely."""


@contextmanager
def _ledger_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    from gideon.operations.durability.home_paths import LinkInTheWay, guard_path

    try:
        guard_path(path)
        guard_path(lock_path)
    except (LinkInTheWay, ValueError) as error:
        raise InstalledPackLedgerError(str(error)) from error
    key = os.path.realpath(lock_path)
    with _ledger_thread_locks_guard:
        thread_lock = _ledger_thread_locks.setdefault(key, threading.Lock())
    if not thread_lock.acquire(timeout=_LEDGER_LOCK_TIMEOUT):
        raise InstalledPackLedgerError("installed-pack ledger lock timed out")
    fd = -1
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            lock_path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        opened = os.fstat(fd)
        current = lock_path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise InstalledPackLedgerError(
                "installed-pack ledger lock is linked or changed"
            )
        os.fchmod(fd, 0o600)
        deadline = time.monotonic() + _LEDGER_LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise InstalledPackLedgerError(
                        "could not lock installed-pack ledger"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise InstalledPackLedgerError(
                        "installed-pack ledger lock timed out"
                    ) from exc
                time.sleep(0.02)
        yield
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        thread_lock.release()


def _read_ledger(path: Path) -> dict[str, Any]:
    from gideon.operations.durability.home_paths import LinkInTheWay, guard_path

    try:
        guard_path(path, read=True)
    except (LinkInTheWay, ValueError) as error:
        raise InstalledPackLedgerError(str(error)) from error
    if not os.path.lexists(path):
        return {}
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise InstalledPackLedgerError(
                "installed-pack ledger is not a regular file"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if isinstance(exc, InstalledPackLedgerError):
            raise
        raise InstalledPackLedgerError("installed-pack ledger is unreadable") from exc
    if not isinstance(raw, dict):
        raise InstalledPackLedgerError("installed-pack ledger is not an object")
    return raw


@contextmanager
def installed_ledger(home: Path | None = None) -> Iterator[dict[str, Any]]:
    """Hold the cross-process ledger lock for a read/modify/write transaction.

    Changes are atomically published on normal exit; an exception leaves the original
    ledger bytes untouched.
    """
    from gideon.core.atomic_write import atomic_write

    path = _ledger_path(home)
    with _ledger_lock(path):
        original = _read_ledger(path)
        current = json.loads(json.dumps(original))
        try:
            yield current
        except BaseException:
            raise
        else:
            if current != original:
                atomic_write(
                    path, json.dumps(current, indent=2, ensure_ascii=False) + "\n"
                )


@dataclass
class InstalledPack:
    """One installed pack's durable record.

    ``connector_markers`` holds every ``connector_missing:<name>`` for a skipped connector
    (the degraded-completion surface). ``setup_skill`` is the committed skill id of the
    pack's ``setup/SKILL.md`` (empty when the pack ships none); ``setup_pending`` stays true
    while a setup skill exists — the re-runnable "Finish setup" affordance never expires on
    its own (a user re-runs the interview whenever they want).
    """

    name: str
    version: str
    components: list[str] = field(default_factory=list)
    connectors: list[dict[str, Any]] = field(
        default_factory=list
    )  # ConnectorResolution dicts
    connector_markers: list[str] = field(default_factory=list)
    setup_skill: str = ""
    setup_pending: bool = False
    installed_at: str = ""
    bindings: list[dict[str, Any]] = field(default_factory=list)
    bound: dict[str, str] = field(default_factory=dict)
    roster: list[dict[str, Any]] = field(default_factory=list)
    pack_owned: list[str] = field(default_factory=list)
    component_locks: dict[str, dict[str, str]] = field(default_factory=dict)
    staged_triggers: list[str] = field(default_factory=list)

    @property
    def unbound(self) -> list[str]:
        """Required binding keys with no answer yet — what "Finish setup" still owes."""
        return [
            str(b.get("key"))
            for b in self.bindings
            if b.get("required", True)
            and not str(self.bound.get(str(b.get("key")), "")).strip()
        ]

    def to_dict(self) -> dict[str, Any]:
        """The PERSISTED shape — pure fields only, so no derived value is ever stored."""
        return asdict(self)

    def to_view(self) -> dict[str, Any]:
        """The API shape: the record plus ``unbound``, derived once here so two readers
        cannot disagree about whether a pack's setup is actually finished."""
        out = asdict(self)
        out["unbound"] = self.unbound
        return out


def _ledger_path(home: Path | None = None) -> Path:
    return (home or config_dir()) / "packs" / LEDGER_FILE


def load_installed(home: Path | None = None) -> list[InstalledPack]:
    """Every recorded installed pack (empty when nothing has been imported)."""
    path = _ledger_path(home)
    if not os.path.lexists(path):
        return []
    try:
        with installed_ledger(home) as raw:
            return _installed_packs(raw)
    except InstalledPackLedgerError:
        logger.warning("installed-pack ledger unreadable at %s", path)
        return []


def _installed_packs(raw: dict[str, Any]) -> list[InstalledPack]:
    out: list[InstalledPack] = []
    for name, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        out.append(
            InstalledPack(
                name=str(name),
                version=str(rec.get("version", "")),
                components=[str(c) for c in rec.get("components", [])],
                connectors=[
                    c for c in rec.get("connectors", []) if isinstance(c, dict)
                ],
                connector_markers=[str(m) for m in rec.get("connector_markers", [])],
                setup_skill=str(rec.get("setup_skill", "")),
                setup_pending=bool(rec.get("setup_pending", False)),
                installed_at=str(rec.get("installed_at", "")),
                bindings=[b for b in rec.get("bindings", []) if isinstance(b, dict)],
                bound={
                    str(k): str(v)
                    for k, v in (rec.get("bound") or {}).items()
                    if isinstance(rec.get("bound"), dict)
                },
                roster=[r for r in rec.get("roster", []) if isinstance(r, dict)],
                pack_owned=[str(p) for p in rec.get("pack_owned", [])],
                component_locks={
                    str(ref): {str(k): str(v) for k, v in lock.items()}
                    for ref, lock in (rec.get("component_locks") or {}).items()
                    if isinstance(lock, dict)
                },
                staged_triggers=[str(t) for t in rec.get("staged_triggers", [])],
            )
        )
    return out


def record_install(pack: InstalledPack, home: Path | None = None) -> None:
    """Upsert one pack's record into the ledger (keyed by pack name), written atomically.

    Re-importing a pack overwrites its record (the same name = the same pack); other packs'
    records are preserved. The ledger is a dict keyed by name so a read is an O(1) lookup and
    a re-import never duplicates a row.
    """
    rec = pack.to_dict()
    rec.pop("name", None)
    with installed_ledger(home) as existing:
        existing[pack.name] = rec


def forget_install(pack_name: str, home: Path | None = None) -> bool:
    """Remove one pack record without disturbing other installs."""
    with installed_ledger(home) as existing:
        if pack_name not in existing:
            return False
        del existing[pack_name]
        return True


class BindingError(Exception):
    """A setup answer that the pack's own declaration does not accept."""


def bind_answer(
    pack_name: str, key: str, value: str, home: Path | None = None
) -> "InstalledPack":
    """Record one setup-interview answer (§3.4/§4.1). Returns the updated record.

    This is what makes "the interview binds a folder" a mechanism rather than a prompt: the
    answer is validated against the binding the PACK declared and persisted in the ledger, so
    the ``unbound`` list shrinks and the "Finish setup" chip can report real progress.

    Fail closed on every disagreement — an unknown pack, an undeclared key, an empty value,
    or (for ``kind: folder``) a path that is not an existing directory. A folder answer is
    resolved and stored absolute so a later reader is not re-resolving it against whatever
    cwd it happens to have.
    """
    packs = {p.name: p for p in load_installed(home)}
    pack = packs.get(pack_name)
    if pack is None:
        raise BindingError(f"pack not installed: {pack_name}")
    declared = next((b for b in pack.bindings if str(b.get("key")) == key), None)
    if declared is None:
        raise BindingError(f"pack {pack_name!r} declares no setup binding {key!r}")
    answer = str(value).strip()
    if not answer:
        raise BindingError(f"binding {key!r} needs a value")
    if str(declared.get("kind")) == "folder":
        resolved = Path(answer).expanduser()
        if not resolved.is_dir():
            raise BindingError(
                f"binding {key!r} needs an existing directory (got {answer!r})"
            )
        answer = str(resolved.resolve())
    pack.bound = {**pack.bound, key: answer}
    record_install(pack, home)
    return pack
