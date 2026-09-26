"""Converge app-declared contributions into the existing script-hook store."""

from __future__ import annotations

from gideon.engine.hooks import ScriptHookStore, get_global_hook_store
from gideon.extensions.apps.app_manager import _manifest_of
from gideon.extensions.apps.manager import _read_installed, apps_dir

APP_HOOK_PREFIX = "app:"


def desired_app_hooks() -> dict[str, dict]:
    desired: dict[str, dict] = {}
    root = apps_dir()
    if not root.is_dir():
        return desired
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_installed(entry.name)
        if meta is None or not meta.enabled:
            continue
        manifest = _manifest_of(meta.name)
        if manifest is None or manifest.validate():
            continue
        for hook in manifest.extra.get("hooks", []):
            hook_id = f"{APP_HOOK_PREFIX}{meta.name}:{hook['name']}"
            desired[hook_id] = {
                "id": hook_id,
                "name": f"{manifest.displayName}: {hook['name']}",
                "event": hook["event"],
                "matcher": str(hook.get("matcher", "")),
                "provider": hook["provider"],
                "provider_config": dict(hook.get("providerConfig", {})),
                "timeout": int(hook.get("timeout", 30)),
                "enabled": True,
            }
    return desired


def reconcile_app_hooks(store: object | None = None) -> None:
    """Apply enabled app hooks and prune removed ones without touching user hooks."""
    target = store if isinstance(store, ScriptHookStore) else get_global_hook_store() or ScriptHookStore()
    desired = desired_app_hooks()
    for existing in target.list_all():
        if existing.id.startswith(APP_HOOK_PREFIX) and existing.id not in desired:
            target.delete(existing.id)
    for hook_id, payload in desired.items():
        existing = target.get(hook_id)
        if existing is None:
            target.create(payload)
        elif any(getattr(existing, key) != value for key, value in payload.items() if key != "id"):
            target.update(hook_id, payload)
