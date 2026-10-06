from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gideon.automation.triggers.wakeup import RESUME_TARGET_KEY


def action_in(workflow: Any) -> Mapping[str, Any]:
    block = workflow if isinstance(workflow, Mapping) else {}
    nested = block.get("inline")
    return nested if isinstance(nested, Mapping) else block


def edited_action(stored: Any, edit: Mapping[str, Any]) -> dict[str, Any]:
    before = action_in(stored)
    was = str(before.get("provider") or "").strip()
    named = str(edit.get("provider") or "").strip()
    changed = bool(named and named != was)
    old = before.get("config")
    config = {} if changed or not isinstance(old, Mapping) else dict(old)
    if "config" in edit:
        sent = edit["config"]
        if sent is None:
            config = {}
        elif not isinstance(sent, Mapping):
            raise ValueError("action.config must be an object")
        else:
            for key, value in sent.items():
                if value is None:
                    config.pop(key, None)
                else:
                    config[key] = value
    return {"provider": named or was, "config": config}


def edited_workflow(stored: Any, edit: Any) -> Any:
    if not isinstance(edit, Mapping) or RESUME_TARGET_KEY in edit:
        return edit
    nested = edit.get("inline")
    sent = nested if isinstance(nested, Mapping) else edit
    if "provider" not in sent and "config" not in sent:
        return edit
    block = dict(stored) if isinstance(stored, Mapping) else {}
    action = edited_action(block, sent)
    if isinstance(block.get("inline"), Mapping):
        return {**block, **{k: v for k, v in edit.items() if k not in {"inline", "provider", "config"}}, "inline": action}
    if "provider" in block or "config" in block:
        return {**block, **{k: v for k, v in edit.items() if k != "inline"}, **action}
    return {**edit, "inline": action} if sent is nested else action
