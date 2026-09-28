"""Plan and safely apply removal of one installed capability pack."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)
_STAGING_FILES = ("roster.json", "config_subset.json")


class PackUninstallError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class KeptComponent:
    ref: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "reason": self.reason}


@dataclass(frozen=True)
class InUse:
    kind: str
    id: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "name": self.name}


@dataclass
class UninstallPlan:
    pack: str
    version: str
    removed: list[str] = field(default_factory=list)
    kept: list[KeptComponent] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    in_use: list[InUse] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)
    applied: bool = False
    confirmation_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "version": self.version,
            "removed": list(self.removed),
            "kept": [item.to_dict() for item in self.kept],
            "missing": list(self.missing),
            "in_use": [item.to_dict() for item in self.in_use],
            "servers": list(self.servers),
            "applied": self.applied,
            "confirmation_token": self.confirmation_token,
        }


def _home() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir()


@contextmanager
def _locked_dependencies(home: Path) -> Iterator[None]:
    """Serialize the plan/apply check with config and trigger deployment writers."""
    from gideon.automation.triggers.store import TriggerStore
    from gideon.core.config.loader import config_path
    from gideon.core.config.transactions import _ConfigLock

    trigger_store = TriggerStore(home)
    with _ConfigLock(config_path(), timeout=5.0):
        with trigger_store._file_lock():
            yield


def _installed_pack(records: dict[str, Any], name: str):
    from gideon.extensions.packs.installed import _installed_packs

    return next((pack for pack in _installed_packs(records) if pack.name == name), None)


def _safe_recorded_path(home: Path, recorded: str) -> Path | None:
    if not recorded or Path(recorded).is_absolute():
        return None
    root = Path(os.path.abspath(home))
    candidate = Path(os.path.normpath(root / recorded))
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    current = candidate
    while current != root:
        if current.is_symlink():
            return None
        current = current.parent
    return candidate


def _landed_id(kind: str, path: Path) -> str:
    if kind == "skill":
        return path.name
    if kind in {"template", "agent"}:
        return path.parent.name
    if kind == "prompt":
        return path.stem if path.suffix == ".yaml" else ""
    if kind == "trigger":
        return path.stem if path.suffix == ".json" else ""
    return ""


def _could_be_imported_as(original: str, landed: str) -> bool:
    if landed == original:
        return True
    prefix = f"{original}-imported-"
    suffix = landed[len(prefix):] if landed.startswith(prefix) else ""
    return bool(suffix) and suffix.isdigit() and int(suffix) > 0


def _in_use(pack: Any, home: Path) -> list[InUse]:
    from gideon.automation.triggers.routing import routed
    from gideon.automation.triggers.store import TriggerStore
    from gideon.core.config.loader import AppConfig
    from gideon.extensions.packs.component_paths import component_path

    source = f"pack:{pack.name}"
    uses = [
        InUse("agent", agent_id, agent_id)
        for agent_id, profile in sorted(AppConfig.load().agents.items())
        if getattr(profile, "source", "") == source
    ]
    # `staged_triggers` records the installed component target name, while the
    # staged definition may carry a distinct live trigger ID. Match the ID that
    # the production deploy path actually submits to TriggerStore.
    trigger_ids = set(pack.staged_triggers)
    for staged_id in pack.staged_triggers:
        staged_path = component_path("trigger", staged_id, home, pack.name)
        if staged_path is None or _safe_recorded_path(
            home, staged_path.relative_to(home).as_posix()
        ) is None:
            continue
        try:
            staged = json.loads(staged_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(staged, dict) and isinstance(staged.get("id"), str):
            trigger_ids.add(staged["id"])
    store = routed(TriggerStore(home))
    uses.extend(
        InUse("automation", trigger.id, trigger.name or trigger.id)
        for trigger in store.list_triggers()
        if trigger.id in trigger_ids
    )
    return uses


def _classify(pack: Any, home: Path) -> tuple[UninstallPlan, dict[str, Path], list[dict[str, str]]]:
    from gideon.extensions.packs.component_paths import component_path
    from gideon.extensions.packs.update import component_digest

    plan = UninstallPlan(pack=pack.name, version=pack.version)
    targets: dict[str, Path] = {}
    state: list[dict[str, str]] = []
    for ref in pack.components:
        kind, separator, original_id = ref.partition(":")
        lock = pack.component_locks.get(ref)
        if not separator or not original_id or not lock or not lock.get("computedHash"):
            plan.kept.append(KeptComponent(ref, "the install contents were not recorded, so it stays"))
            state.append({"ref": ref, "state": "unverifiable"})
            continue
        path = _safe_recorded_path(home, str(lock.get("path", "")))
        if path is None:
            plan.kept.append(KeptComponent(ref, "its recorded location is unsafe or outside the pack install path, so it stays"))
            state.append({"ref": ref, "state": "unsafe"})
            continue
        landed_id = _landed_id(kind, path)
        expected = component_path(kind, landed_id, home, pack.name)
        if (
            not landed_id
            or not _could_be_imported_as(original_id, landed_id)
            or expected is None
            or Path(os.path.normpath(expected)) != path
        ):
            plan.kept.append(KeptComponent(ref, "its recorded location is not where this component installs, so it stays"))
            state.append({"ref": ref, "state": "wrong_location"})
            continue
        if not os.path.lexists(path):
            plan.missing.append(ref)
            state.append({"ref": ref, "state": "missing"})
            continue
        if path.is_symlink():
            plan.kept.append(KeptComponent(ref, "its installed path is a symlink, so it stays"))
            state.append({"ref": ref, "state": "symlink"})
            continue
        digest = component_digest(path)
        state.append({"ref": ref, "state": "present", "digest": digest})
        if digest != lock["computedHash"]:
            plan.kept.append(KeptComponent(ref, "you edited it after it was installed, so it stays"))
            continue
        plan.removed.append(ref)
        targets[ref] = path

    plan.in_use = _in_use(pack, home)
    plan.servers = sorted(
        {str(item.get("server_name")) for item in pack.connectors
         if item.get("mode") == "configure" and item.get("server_name")}
    )
    return plan, targets, state


def _token(records: dict[str, Any], plan: UninstallPlan, state: list[dict[str, str]]) -> str:
    payload = {
        "record": records.get(plan.pack),
        "removed": plan.removed,
        "kept": [item.to_dict() for item in plan.kept],
        "missing": plan.missing,
        "in_use": [item.to_dict() for item in plan.in_use],
        "servers": plan.servers,
        "state": state,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plan_locked(records: dict[str, Any], name: str, home: Path) -> tuple[UninstallPlan, dict[str, Path]]:
    pack = _installed_pack(records, name)
    if pack is None:
        raise PackUninstallError("pack_not_installed", f"pack not installed: {name}", 404)
    plan, targets, state = _classify(pack, home)
    plan.confirmation_token = _token(records, plan, state)
    return plan, targets


def plan_uninstall(name: str) -> UninstallPlan:
    """Return a read-only plan; no component or ledger record is changed."""
    from gideon.extensions.packs.installed import installed_ledger

    home = _home()
    with _locked_dependencies(home):
        with installed_ledger(home) as records:
            plan, _targets = _plan_locked(records, name, home)
            return plan


def _in_use_message(name: str, rows: list[InUse]) -> str:
    agents = [row.name for row in rows if row.kind == "agent"]
    automations = [row.name for row in rows if row.kind == "automation"]
    parts: list[str] = []
    places: list[str] = []
    if agents:
        parts.append(f"agent{'s' if len(agents) != 1 else ''} {', '.join(agents)}")
        places.append("Agents")
    if automations:
        parts.append(f"automation{'s' if len(automations) != 1 else ''} {', '.join(automations)}")
        places.append("Automations")
    return f"{name} is still in use: {' and '.join(parts)}. Remove them in {' and '.join(places)} before uninstalling."


def _prune_empty(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _remove_staging(pack_name: str, home: Path) -> None:
    from gideon.extensions.packs.component_paths import component_path

    if component_path("trigger", "staging-check", home, pack_name) is None:
        return
    stage = home / "packs" / "staged" / pack_name
    if not _safe_recorded_path(home, stage.relative_to(home).as_posix()):
        return
    for filename in _STAGING_FILES:
        candidate = stage / filename
        if not _safe_recorded_path(home, candidate.relative_to(home).as_posix()):
            continue
        if candidate.is_file() and not candidate.is_symlink():
            candidate.unlink()
    _prune_empty(stage / "triggers", stage.parent)
    _prune_empty(stage, stage.parent)


def apply_uninstall(name: str, confirmation_token: str) -> UninstallPlan:
    """Remove only the still-unmodified components covered by the user's current plan."""
    from gideon.extensions.packs.import_ import _audit
    from gideon.extensions.packs.installed import installed_ledger

    home = _home()
    with _locked_dependencies(home):
        with installed_ledger(home) as records:
            plan, targets = _plan_locked(records, name, home)
            if plan.in_use:
                raise PackUninstallError("pack_in_use", _in_use_message(name, plan.in_use), 409)
            if not confirmation_token or confirmation_token != plan.confirmation_token:
                raise PackUninstallError(
                    "confirmation_required",
                    "pack state changed; review the current uninstall plan and confirm again",
                    409,
                )
            from gideon.extensions.packs.update import component_digest

            failed: list[str] = []
            for ref, path in targets.items():
                pack = _installed_pack(records, name)
                lock = pack.component_locks.get(ref) if pack else None
                if path.is_symlink() or not lock or component_digest(path) != lock.get("computedHash"):
                    failed.append(ref)
                    continue
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    kind = ref.partition(":")[0]
                    if kind in {"template", "agent"}:
                        _prune_empty(path.parent, path.parent.parent)
                except OSError as exc:
                    logger.warning("pack uninstall %s could not remove a component: %s", name, type(exc).__name__)
                    failed.append(ref)
            if failed:
                _audit("pack_uninstall", "incomplete", resources=name, error=", ".join(failed))
                raise PackUninstallError(
                    "pack_uninstall_incomplete",
                    f"could not safely remove {', '.join(failed)}; review the plan and try again",
                    500,
                )
            _remove_staging(name, home)
            del records[name]
            plan.applied = True
            _audit("pack_uninstall", "applied", resources=f"{name}@{plan.version} ({len(plan.removed)} removed, {len(plan.kept)} kept)")
            return plan
