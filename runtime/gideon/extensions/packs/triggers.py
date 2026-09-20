"""Deploy staged pack triggers into the live automation store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gideon.automation.triggers.store import LoadedTrigger, TriggerStore
from gideon.core.config import loader as config_loader


class PackTriggersError(Exception):
    pass


def deploy_triggers(pack_name: str, home: Path | None = None) -> dict[str, Any]:
    base = home or config_loader.config_dir()
    from gideon.extensions.packs.installed import load_installed

    pack = next((p for p in load_installed(base) if p.name == pack_name), None)
    if pack is None:
        raise PackTriggersError(f"pack not installed: {pack_name}")

    deployed: list[str] = []
    skipped: list[dict[str, Any]] = []
    store = TriggerStore(base)
    staged = base / "packs" / "staged" / pack_name / "triggers"
    for path in sorted(staged.glob("*.json")) if staged.is_dir() else ():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append({"file": path.name, "reason": f"unreadable JSON: {exc}"})
            continue
        if not isinstance(raw, dict):
            skipped.append(
                {"file": path.name, "reason": "trigger must be a JSON object"}
            )
            continue
        loaded = LoadedTrigger.parse(raw)
        if loaded.errors:
            skipped.append(
                {
                    "file": path.name,
                    "id": loaded.trigger.id,
                    "reason": "; ".join(issue.message for issue in loaded.errors),
                }
            )
            continue
        loaded.trigger.enabled = False
        store.upsert(loaded.trigger)
        deployed.append(loaded.trigger.id)
    return {"deployed": deployed, "skipped": skipped}
