"""Frozen workflow ledger cassettes and side-effect-free divergence inspection."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.automation.workflows import journal, store
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader


def _root() -> Path:
    root = config_loader.config_dir() / "experiments" / "replays"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


_PLAIN = {"kind", "node_id", "instance_path", "epoch", "effect_status", "state", "status", "provider", "tool", "lane"}
_IGNORE = {"at", "ts", "timestamp", "elapsed_ms", "elapsed_seconds", "wall_time", "event_id"}


def _event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value if key in _PLAIN else _digest(value)
        for key, value in sorted(row.items())
        if key not in _IGNORE
    }


def _steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay recorded step transitions into an inspectable trajectory."""
    ordered: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        path = str(event.get("instance_path") or "")
        if not path:
            continue
        key = (path, str(event.get("epoch") or 0))
        step = ordered.setdefault(key, {
            "instance_path": path, "epoch": event.get("epoch", 0),
            "node_id": event.get("node_id", ""), "state": "pending",
            "attempts": 0, "events": [],
        })
        kind = str(event.get("kind") or "")
        step["events"].append(event)
        if kind == "step_attempt":
            step["attempts"] += 1
        elif kind == "step_started":
            step["state"] = "running"
        elif kind == "step_completed":
            step["state"] = event.get("state") or "done"
        elif kind == "step_failed":
            step["state"] = "failed"
        elif kind == "step_cached":
            step["state"] = "cached"
        elif kind == "step_skipped":
            step["state"] = "skipped"
        elif kind == "step_escalated":
            step["state"] = "escalated"
    return list(ordered.values())


def _record(run_id: str) -> dict[str, Any]:
    run = store.get(run_id)
    if run is None:
        raise KeyError(run_id)
    events = journal.journal_records(run_id)
    events.sort(key=lambda row: (int(row.get("seq") or 0), str(row.get("kind") or "")))
    frozen = [_event(row) for row in events]
    return {
        "run_id": run_id,
        "workflow_name": run.workflow_name,
        "status": run.status.value,
        "spec_sha256": _digest(store.read_spec(run_id)),
        "events": frozen,
        "steps": _steps(frozen),
    }


def capture(run_id: str) -> dict[str, Any]:
    body = _record(run_id)
    replay_id = secrets.token_hex(12)
    artifact = {"id": replay_id, "created_at": datetime.now(timezone.utc).isoformat(), **body}
    path = _root() / f"{replay_id}.json"
    atomic_write(path, json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2), mode=0o600)
    return {key: value for key, value in artifact.items() if key != "events"} | {"event_count": len(body["events"])}


def read(replay_id: str) -> dict[str, Any]:
    if len(replay_id) != 24 or any(c not in "0123456789abcdef" for c in replay_id):
        raise KeyError(replay_id)
    try:
        return json.loads((_root() / f"{replay_id}.json").read_text())
    except (OSError, ValueError) as exc:
        raise KeyError(replay_id) from exc


def compare(replay_id: str, candidate_run_id: str = "") -> dict[str, Any]:
    baseline = read(replay_id)
    candidate = _record(candidate_run_id or baseline["run_id"])
    divergence: list[dict[str, Any]] = []
    for field in ("workflow_name", "status", "spec_sha256"):
        if baseline.get(field) != candidate.get(field):
            divergence.append({"path": field, "recorded": baseline.get(field), "candidate": candidate.get(field)})
    first = baseline.get("events") or []
    second = candidate["events"]
    before_steps = _steps(first)
    after_steps = _steps(second)
    for index in range(max(len(before_steps), len(after_steps))):
        old = before_steps[index] if index < len(before_steps) else {}
        new = after_steps[index] if index < len(after_steps) else {}
        for key in ("instance_path", "epoch", "node_id", "state", "attempts"):
            if old.get(key) != new.get(key):
                divergence.append({"path": f"steps[{index}].{key}", "recorded": old.get(key), "candidate": new.get(key)})
    for index in range(max(len(first), len(second))):
        old = first[index] if index < len(first) else {}
        new = second[index] if index < len(second) else {}
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                divergence.append({"path": f"events[{index}].{key}", "recorded": old.get(key), "candidate": new.get(key)})
    return {
        "replay_id": replay_id,
        "recorded_run_id": baseline["run_id"],
        "candidate_run_id": candidate["run_id"],
        "recorded_events": len(first),
        "candidate_events": len(second),
        "recorded_steps": before_steps,
        "candidate_steps": after_steps,
        "matches": not divergence,
        "divergence": divergence[:200],
        "truncated": len(divergence) > 200,
        "side_effects_executed": False,
    }
