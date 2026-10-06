"""One owner for what an installed app contributes to this Gideon process.

Lifecycle transitions and gateway startup use the same load/unload operations. The owner
registers every app's in-process code before starting any of their supervised processes,
and records anything that cannot be removed so the app page can explain why a restart is
needed.
"""

from __future__ import annotations

import logging
import hashlib
import asyncio
import importlib
import importlib.util
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.extensions.apps.manifest import AppManifest

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_restart: dict[str, list[str]] = {}
_PROCESS_SAMPLE_GAP_SECS = 0.2


def installed() -> list[tuple[AppManifest, bool]]:
    """Read installed manifests and enabled state in the order lifecycle loads them."""
    from gideon.extensions.apps.manager import list_apps
    from gideon.extensions.apps.manifest import AppManifest

    out: list[tuple[AppManifest, bool]] = []
    for record in list_apps():
        name = str(record.get("name") or "")
        if not name:
            continue
        try:
            manifest = AppManifest.from_dict(record.get("manifest") or {})
        except Exception:
            logger.warning("app %s: manifest cannot be read; it was not loaded", name)
            continue
        out.append((manifest, bool(record.get("enabled", False))))
    return out


def start_installed(*, gateway: bool = True) -> list[str]:
    """Load enabled apps as lifecycle enable does; defer all process starts until code loads."""
    ready: list[AppManifest] = []
    for manifest, enabled in installed():
        if not enabled:
            record(manifest)
            continue
        compatibility = manifest.core_compatibility()
        if not compatibility.admits:
            _refuse(manifest, compatibility.reason)
            continue
        if compatibility.reason:
            logger.warning("app %s: %s", manifest.name, compatibility.reason)
        ready.append(manifest)
    if gateway:
        load(*ready)
    else:
        for manifest in ready:
            _load_in_process(manifest)
    return [manifest.name for manifest in ready]


def load(*manifests: AppManifest) -> None:
    """Load registrations for all manifests, then launch their processes."""
    loaded: list[AppManifest] = []
    try:
        for manifest in manifests:
            _load_in_process(manifest)
            loaded.append(manifest)
        for manifest in manifests:
            _release_processes(manifest.name)
            _register_mcp(manifest)
            _start_backend(manifest)
            _start_workers(manifest)
        from gideon.integrations.channel_transports import request_reconcile

        request_reconcile()
    except Exception:
        for manifest in reversed(loaded or list(manifests)):
            try:
                unload(manifest.name, manifest)
            except Exception:
                logger.exception("app %s: cleanup after load failure failed", manifest.name)
        raise


def reload(name: str, manifest: AppManifest) -> list[str]:
    residue = unload(name, _manifest_from_disk(name))
    meta = _read_installed(name)
    if meta is not None and meta.enabled:
        load(manifest)
    return residue


def record(manifest: AppManifest) -> None:
    """Keep disabled app providers visible without importing or starting app code."""
    if manifest.all_providers():
        _provider_registry().register(manifest, enabled=False)


def _load_in_process(manifest: AppManifest) -> None:
    from gideon.extensions.apps.app_manager import (
        _register_proposal_kinds,
        _seed_app_prompts,
        _seed_app_skills,
        _origin_of,
    )

    if manifest.all_providers():
        registry = _provider_registry()
        registry.register(manifest, enabled=False)
        primary = registry.get(manifest.name)
        if primary is not None:
            from gideon.extensions.providers.loader import _load_ext_module

            for provider in primary.chain():
                module_path = provider.provider_config.implementation.rpartition(":")[0]
                if not module_path:
                    continue
                try:
                    _discard_bundle_bytecode(manifest.name, module_path)
                    _load_ext_module(provider, module_path)
                except Exception as exc:
                    provider.error = str(exc)
                    logger.exception("app %s provider module failed to load", manifest.name)
            registry.enable(manifest.name)
    _seed_app_prompts(manifest, manifest.name)
    _seed_app_skills(manifest, manifest.name, origin=_origin_of(manifest.name))
    _register_proposal_kinds(manifest, manifest.name)


def unload(name: str, manifest: AppManifest | None, *, forget: bool = False) -> list[str]:
    """Stop app processes and registrations, evict its bundle modules, and report residue."""
    from gideon.extensions.apps.app_manager import (
        _deregister_mcp,
        _deregister_proposal_kinds,
        _remove_app_prompts,
        _remove_app_skills,
        _stop_backend,
        _stop_worker,
    )
    from gideon.extensions.apps.native_contract import namespaced_module_name
    from gideon.extensions.providers.availability import get_availability_board

    _hold_processes(name)
    _stop_backend(name)
    _stop_worker(name)
    _deregister_mcp(name)
    registry = _provider_registry()
    if forget:
        registry.deregister(name)
    else:
        registry.disable(name)
    from gideon.integrations.channel_transports import (
        request_reconcile,
        settle_from_thread,
    )

    request_reconcile()
    settle_from_thread()
    if manifest is not None:
        _remove_app_prompts(manifest, name)
        _remove_app_skills(manifest, name)
        _deregister_proposal_kinds(manifest, name)
    get_availability_board().forget(name)
    try:
        from gideon.extensions.providers.connection import get_connection_board

        get_connection_board().forget(name)
    except ImportError:
        pass
    from gideon.extensions.apps.code_provenance import release

    release(name)
    root = _app_root(name)
    residue = _evict_modules(name)
    if root is not None:
        residue.extend(_thread_task_residue(root, name))
    residue.extend(_processes_left(name))
    if residue:
        note_restart(name, residue)
    return residue


def restart_reason(name: str) -> str:
    with _lock:
        return "; ".join(_restart.get(name, []))


def ui_revision(name: str) -> str:
    """Return a content revision for the installed UI bundle, without exposing paths."""
    from gideon.extensions.apps.manager import app_dir

    root = (app_dir(name) / "ui").resolve()
    digest = hashlib.sha256()
    if not root.is_dir():
        return ""
    for path in sorted(root.rglob("*")):
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(131072), b""):
                digest.update(chunk)
    return digest.hexdigest()[:20]


def note_restart(name: str, reasons: list[str]) -> None:
    with _lock:
        known = _restart.setdefault(name, [])
        for reason in reasons:
            if reason not in known:
                known.append(reason)


def _evict_modules(name: str) -> list[str]:
    prefix = f"_gideon_app_{name.replace('-', '_')}__"
    root = _app_root(name)
    removed: list[str] = []
    for module_name, module in tuple(sys.modules.items()):
        path = str(getattr(module, "__file__", "") or "")
        owned = module_name.startswith(prefix)
        if root is not None and path:
            try:
                owned = owned or Path(path).resolve().is_relative_to(root)
            except OSError:
                pass
        if not owned:
            continue
        from gideon.extensions.providers.media_scanners import unregister_module_scanners
        unregister_module_scanners(module_name)
        from gideon.integrations.llm.registry import unregister_app_module_types
        unregister_app_module_types(name, module_name, module)
        sys.modules.pop(module_name, None)
        if path.endswith((".so", ".pyd", ".dll", ".dylib")):
            removed.append(f"a compiled extension from the previous version remains loaded ({Path(path).name})")
    if root is not None:
        sys.path_importer_cache.pop(str(root), None)
    importlib.invalidate_caches()
    return removed


def _processes_left(name: str) -> list[str]:
    """Find app-owned descendants that survive this lifecycle transition."""
    try:
        root = _app_root(name)
        if root is None:
            return []
        first = _matching_processes(root)
        if not first:
            return []
        time.sleep(_PROCESS_SAMPLE_GAP_SECS)
        second = _matching_processes(root)
        alive = first & second
        if not alive:
            return []
        return [f"a process from the previous version is still running (pid {pid})" for pid in sorted(alive)]
    except Exception:
        logger.debug("app %s: residual process check failed", name, exc_info=True)
        return []


def _matching_processes(root: Path) -> set[int]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return set()
    result: set[int] = set()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cwd = (entry / "cwd").resolve()
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, PermissionError):
            continue
        if (cwd.is_relative_to(root) if cwd.exists() else False) or str(root) in cmdline:
            result.add(int(entry.name))
    return result


def _app_root(name: str) -> Path | None:
    try:
        from gideon.extensions.apps.manager import app_dir

        return app_dir(name).resolve()
    except (OSError, ValueError):
        return None


def _discard_bundle_bytecode(name: str, module_path: str) -> None:
    """Do not let a same-second, same-size pyc mask freshly swapped app source."""
    if "." in module_path:
        return
    root = _app_root(name)
    source = root / f"{module_path}.py" if root is not None else None
    if source is None or not source.is_file():
        return
    try:
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.unlink(missing_ok=True)
    except (NotImplementedError, OSError, ValueError):
        logger.debug("app %s: stale bytecode cache could not be removed", name, exc_info=True)


def _thread_task_residue(root: Path, name: str) -> list[str]:
    reasons: list[str] = []
    prefix = f"_gideon_app_{name.replace('-', '_')}__"
    for thread in threading.enumerate():
        target = getattr(thread, "_target", None)
        module = str(getattr(target, "__module__", "") or "")
        if module.startswith(prefix):
            reasons.append(f"app code still owns a live thread ({thread.name})")
    try:
        from asyncio.tasks import _all_tasks

        for task in tuple(_all_tasks):
            coroutine = task.get_coro()
            frame = getattr(coroutine, "cr_frame", None)
            globals_ = getattr(frame, "f_globals", {}) if frame else {}
            filename = Path(str(globals_.get("__file__", ""))).resolve()
            if filename.is_relative_to(root):
                reasons.append("app code still owns a pending asyncio task")
    except Exception:
        logger.debug("app task residue check failed", exc_info=True)
    return reasons


def _refuse(manifest: AppManifest, reason: str) -> None:
    record(manifest)
    primary = _provider_registry().get(manifest.name)
    for provider in primary.chain() if primary else []:
        provider.error = reason
    try:
        from gideon.extensions.apps.backend_runtime import get_backend_supervisor
        from gideon.extensions.apps.worker_runtime import get_worker_supervisor

        get_backend_supervisor().hold(manifest.name)
        get_worker_supervisor().hold(manifest.name)
    except Exception:
        logger.debug("app %s: process hold failed", manifest.name, exc_info=True)


def _register_mcp(manifest: AppManifest) -> None:
    if manifest.mcpServers:
        from gideon.extensions.apps.mcp_bridge import register_app_mcp_servers

        register_app_mcp_servers(manifest)


def _start_backend(manifest: AppManifest) -> None:
    from gideon.extensions.apps.backend_runtime import get_backend_supervisor

    get_backend_supervisor().start(manifest)


def _start_workers(manifest: AppManifest) -> None:
    from gideon.extensions.apps.worker_runtime import get_worker_supervisor

    get_worker_supervisor().start(manifest)


def _provider_registry():
    from gideon.extensions.providers.registry import get_provider_registry

    return get_provider_registry()


def _hold_processes(name: str) -> None:
    from gideon.extensions.apps.backend_runtime import get_backend_supervisor
    from gideon.extensions.apps.worker_runtime import get_worker_supervisor

    get_backend_supervisor().hold(name)
    get_worker_supervisor().hold(name)


def _release_processes(name: str) -> None:
    from gideon.extensions.apps.backend_runtime import get_backend_supervisor
    from gideon.extensions.apps.worker_runtime import get_worker_supervisor

    get_backend_supervisor().release(name)
    get_worker_supervisor().release(name)


def _manifest_from_disk(name: str):
    from gideon.extensions.apps.app_manager import _manifest_of

    return _manifest_of(name)


def _read_installed(name: str):
    from gideon.extensions.apps.manager import _read_installed

    return _read_installed(name)
