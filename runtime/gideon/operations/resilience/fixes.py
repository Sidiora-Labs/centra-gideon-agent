"""Confirm-gated auto-fixes for Doctor findings (PLATFORM-RESILIENCE §2).

Every fix is a ``Fix{id, title, impact, dry_preview(), apply()}`` paired with a probe
via its ``fix_id``. **Nothing auto-applies** — the Doctor tab renders the fix with its
impact description and a two-step confirm runs it; every application is SEL-audited.
Fixes touch harness mechanics ONLY (symlinks, caches, orphaned locks/PIDs, rollback
leftovers) — never user content (memory entries, knowledge items, tasks); anything
content-adjacent is flagged, never auto-deleted.

``dry_preview()`` is read-only and returns a human string describing what ``apply()``
would do. ``apply()`` performs the repair and returns a result string. Both are
exception-safe at the registry boundary (:func:`apply_fix`).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fix:
    """A confirm-gated repair for a Doctor finding.

    ``id`` is the stable ``fix_id`` a probe attaches. ``dry_preview`` is read-only;
    ``apply`` mutates (harness mechanics only) and returns a result string.
    """

    id: str
    title: str
    impact: str
    dry_preview: Callable[[], str]
    apply: Callable[[], str]


_FIXES: dict[str, Fix] = {}


def register_fix(fix: Fix) -> None:
    _FIXES[fix.id] = fix


def all_fixes() -> list[Fix]:
    return list(_FIXES.values())


def get_fix(fix_id: str) -> Optional[Fix]:
    return _FIXES.get(fix_id)


def apply_fix(fix_id: str, *, session_key: str = "dashboard") -> dict:
    """Run a registered fix's ``apply()`` under a SEL audit. Returns
    ``{ok, fix_id, result|error}``. Exception-safe: a failing fix reports ``ok:False``,
    never raises to the handler."""
    fix = _FIXES.get(fix_id)
    if fix is None:
        return {"ok": False, "fix_id": fix_id, "error": "unknown fix"}
    from gideon.security.sel import sel

    try:
        result = fix.apply()
        ok = True
        err = ""
    except Exception as exc:
        result = ""
        ok = False
        err = str(exc)
        logger.warning("fix %s failed", fix_id, exc_info=True)
    try:
        sel().log_tool_invocation(
            session_key=session_key,
            agent="gideon",
            source="dashboard",
            tool_name=f"doctor_fix:{fix_id}",
            tool_kind="maintenance",
            outcome="ok" if ok else "error",
            error=err,
            metadata={"fix_id": fix_id, "result": result[:200]},
        )
    except Exception:
        logger.debug("SEL audit for fix %s failed", fix_id, exc_info=True)
    return {
        "ok": ok,
        "fix_id": fix_id,
        **({"result": result} if ok else {"error": err}),
    }


def _dist_paths() -> tuple[Path, Optional[Path]]:
    """(static/dist path, resolved apps/console/dist target-or-None) — mirrors frontend.py's
    resolution without calling it (that function early-returns on a valid copy)."""
    import gideon

    pkg_dir = Path(gideon.__file__).resolve().parent
    tree_dist = pkg_dir / "static" / "dist"
    repo_root = pkg_dir.parent.parent
    built = repo_root / "apps" / "console" / "dist"
    target = built.resolve() if (built / "index.html").is_file() else None
    return tree_dist, target


def _symlink_repair_preview() -> str:
    dist, target = _dist_paths()
    if dist.is_symlink():
        return "static/dist is already a symlink — nothing to repair."
    if not dist.exists():
        return "static/dist is missing." + (
            f" Would create a symlink → {target}."
            if target
            else " No apps/console/dist build found to link."
        )
    if target is None:
        return "static/dist is a directory copy, but no apps/console/dist build was found to link to."
    return (
        f"Would back up the shadowing copy to static/dist.shadow, then symlink "
        f"static/dist → {target} (closes the stale-SPA bug-class)."
    )


def _symlink_repair_apply() -> str:
    dist, target = _dist_paths()
    if dist.is_symlink():
        return "Already a symlink — no change."
    if target is None:
        raise RuntimeError(
            "no apps/console/dist build found to link (build the frontend first)"
        )
    if dist.exists():
        shadow = dist.parent / "dist.shadow"
        if shadow.exists():
            shutil.rmtree(shadow, ignore_errors=True)
        shutil.move(str(dist), str(shadow))
    dist.parent.mkdir(parents=True, exist_ok=True)
    dist.symlink_to(target)
    return f"Repaired: static/dist → {target} (shadow copy backed up)."


def _dead_locks() -> list[Path]:
    from gideon.core.config.loader import config_dir

    locks_dir = config_dir() / "locks"
    if not locks_dir.exists():
        return []
    out = []
    for p in locks_dir.glob("*.lock"):
        try:
            import time as _t

            if (_t.time() - p.stat().st_mtime) > 86400:
                out.append(p)
        except OSError:
            continue
    return out


def _rollback_dirs() -> list[Path]:
    from gideon.extensions.apps.manager import apps_dir

    ad = apps_dir()
    if not ad.exists():
        return []
    return [
        c
        for c in ad.iterdir()
        if c.is_dir() and c.name.startswith(".") and c.name.endswith(".rollback")
    ]


def _orphan_prune_preview() -> str:
    locks = _dead_locks()
    rollbacks = _rollback_dirs()
    parts = []
    if locks:
        parts.append(f"{len(locks)} stale lock file(s) (>24h old)")
    if rollbacks:
        parts.append(f"{len(rollbacks)} interrupted-update rollback dir(s)")
    if not parts:
        return "No orphaned locks or rollback leftovers found."
    return (
        "Would remove: "
        + "; ".join(parts)
        + " (harness mechanics only — no user content)."
    )


def _orphan_prune_apply() -> str:
    removed = 0
    for p in _dead_locks():
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    recovered: list[str] = []
    try:
        from gideon.extensions.apps.app_manager import recover_interrupted_updates

        recovered = recover_interrupted_updates()
    except Exception:
        logger.debug(
            "recover_interrupted_updates failed during orphan prune", exc_info=True
        )
    return f"Removed {removed} stale lock(s); reconciled {len(recovered)} rollback leftover(s)."


def _bindings_that_cannot_run() -> dict[str, list[str]]:
    import asyncio
    import json
    from gideon.extensions.providers.use_cases import active_models_path, load_active_models
    from gideon.operations.resilience.doctor import phantom_bindings
    path = active_models_path()
    raw = json.loads(path.read_text()) if path.exists() else {}
    kept = load_active_models()
    phantom = set(asyncio.run(phantom_bindings()))
    out = {}
    for use_case, refs in (raw if isinstance(raw, dict) else {}).items():
        chain = [refs] if isinstance(refs, str) else refs if isinstance(refs, list) else []
        gone = [str(ref) for ref in chain if ref not in kept.get(use_case, []) or ref in phantom]
        if gone:
            out[use_case] = gone
    return out


def _active_models_prune_preview() -> str:
    from gideon.extensions.providers.use_cases import CHAT_SUBCATEGORIES, load_active_models
    gone = _bindings_that_cannot_run()
    if not gone:
        return "No bindings name a model that is gone."
    active = load_active_models()
    parts = []
    for use_case, refs in gone.items():
        after = ""
        if not [ref for ref in active.get(use_case, []) if ref not in refs]:
            after = " — then uses Chat models" if use_case in CHAT_SUBCATEGORIES else " — then needs a model selected"
        parts.append(f"{use_case}: {', '.join(refs)}{after}")
    return "Would unbind " + "; ".join(parts) + "."


def _active_models_prune_apply() -> str:
    from gideon.extensions.providers.use_cases import load_active_models, save_active_models
    gone = _bindings_that_cannot_run()
    if not gone:
        return "No bindings named a model that is gone; nothing changed."
    active = load_active_models()
    for use_case, refs in gone.items():
        active[use_case] = [ref for ref in active.get(use_case, []) if ref not in refs]
    save_active_models(active)
    return "Unbound " + "; ".join(f"{use_case}: {', '.join(refs)}" for use_case, refs in gone.items()) + "."


def _restore_core_server_preview() -> str:
    from gideon.operations.resilience.core_server import agent_config_path, read_core_server, core_server_detail

    reading = read_core_server(agent_config_path())
    return f"Repair only Gideon's server command and arguments if needed. Keep tools, allowedTools and all other entries. Current: {core_server_detail(reading)}"


def _restore_core_server_apply() -> str:
    import copy
    import json
    from gideon.core.atomic_write import atomic_write
    from gideon.core.config.transactions import _ConfigLock
    from gideon.operations.resilience.core_server import (
        agent_config_path, read_core_server, core_server_detail,
        core_server_command, core_server_name, core_server_args,
    )

    path = agent_config_path()
    if not path.is_file():
        raise RuntimeError("Agent config is missing; nothing was written")
    expected = path.read_bytes()
    with _ConfigLock(path, 5):
        if path.read_bytes() != expected:
            raise RuntimeError("Agent config changed during repair; nothing was written")
        reading = read_core_server(path)
        if reading.set_up:
            return f"Nothing changed: {core_server_detail(reading)}"
        if not reading.needs_setting_up:
            raise RuntimeError(f"{core_server_detail(reading)}; nothing was written")
        command = core_server_command()
        if not command:
            raise RuntimeError("Gideon command is unavailable; nothing was written")
        document = copy.deepcopy(reading.document)
        document.setdefault("mcpServers", {})[core_server_name()] = {
            **(reading.entry or {}), "command": command, "args": core_server_args(),
        }
        if path.read_bytes() != expected:
            raise RuntimeError("Agent config changed during repair; nothing was written")
        atomic_write(path, json.dumps(document, indent=2) + "\n", fsync=True)
    return "Repaired Gideon's server entry; tools and allowedTools unchanged."


def _register_builtin_fixes() -> None:
    register_fix(Fix("tools.restore-core-server", "Restore Gideon server entry", "Changes only server command and arguments; preserves tool permissions.", _restore_core_server_preview, _restore_core_server_apply))
    register_fix(
        Fix(
            id="serving-fs.symlink-repair",
            title="Repair the static/dist symlink",
            impact="Replaces a directory COPY shadowing the runtime symlink with a symlink "
            "to apps/console/dist (backing up the copy). Closes the stale-SPA bug-class.",
            dry_preview=_symlink_repair_preview,
            apply=_symlink_repair_apply,
        )
    )
    register_fix(
        Fix(
            id="serving-fs.orphan-prune",
            title="Prune orphaned locks + rollback leftovers",
            impact="Removes stale lock files (>24h) and reconciles interrupted-update "
            "rollback dirs. Harness mechanics only — never touches user content.",
            dry_preview=_orphan_prune_preview,
            apply=_orphan_prune_apply,
        )
    )
    register_fix(
        Fix(
            id="model-providers.prune-bindings",
            title="Prune bindings to models that are gone",
            impact="Unbinds removed providers and models a responding local provider no longer lists. Failed catalogs preserve bindings.",
            dry_preview=_active_models_prune_preview,
            apply=_active_models_prune_apply,
        )
    )


_register_builtin_fixes()
