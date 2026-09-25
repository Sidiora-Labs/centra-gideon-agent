"""Opt-in interval adaptation from typed execution outcomes, never raw error text."""

import hashlib
import json
import re
import time
from pathlib import Path

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.store import TriggerStore
from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir


def _root(base_dir=None):
    return Path(base_dir) if base_dir is not None else config_dir()


def _path(base_dir=None):
    return _root(base_dir) / "capabilities/platform/cadence.json"


def document(base_dir=None):
    try:
        value = json.loads(_path(base_dir).read_text())
    except FileNotFoundError:
        return {"version": 1, "revision": 0, "policies": {}}
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("policies"), dict)
        or type(value.get("revision")) is not int
    ):
        raise ValueError("Cadence policy is unreadable")
    return value


def signature(trigger):
    return hashlib.sha256(
        json.dumps(trigger.workflow, sort_keys=True).encode()
    ).hexdigest()


def eligible(trigger):
    return (
        trigger.kind == "clock"
        and trigger.spec.get("kind") == "interval"
        and float(trigger.spec.get("interval_secs", 0)) > 0
    )


def mutate(identifier, body, base_dir=None):
    if (
        not isinstance(body, dict)
        or set(body) != {"revision", "enabled", "task_class"}
        or type(body["enabled"]) is not bool
    ):
        raise ValueError("Revision, enabled flag and task class required")
    data = document(base_dir)
    if type(body["revision"]) is not int or body["revision"] != data["revision"]:
        raise ValueError("Cadence policy changed; reload before saving")
    loaded = TriggerStore(_root(base_dir)).get(identifier)
    if loaded is None or not loaded.ok or not eligible(loaded.trigger):
        raise ValueError("An existing valid native interval trigger is required")
    if body["enabled"]:
        if not isinstance(body["task_class"], str) or not re.fullmatch(
            "[a-z][a-z0-9_-]{0,63}", body["task_class"]
        ):
            raise ValueError("Task class must be a stable lowercase identifier")
        data["policies"][identifier] = {
            "task_class": body["task_class"],
            "binding_signature": signature(loaded.trigger),
        }
    else:
        data["policies"].pop(identifier, None)
    data["revision"] += 1
    path = _path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data))
    return view(base_dir=base_dir)


def outcome(row):
    if (
        row.get("trigger") in {"ok", "ran", "ran_late"}
        and row.get("status") == "success"
    ):
        return True
    if row.get("trigger") == "failed" and row.get("status") in {"failure", "error"}:
        return False
    return None


def projection(trigger, *, now=None, base_dir=None):
    now = time.time() if now is None else now
    base = float(trigger.spec.get("interval_secs", 0)) if eligible(trigger) else 0
    result = {
        "trigger_id": trigger.id,
        "name": trigger.name,
        "base_interval": base,
        "effective_interval": base,
        "multiplier": 1,
        "task_class": None,
        "samples": 0,
        "successes": 0,
        "excluded": 0,
        "confidence": "insufficient",
        "reason": "not_opted_in",
        "evidence": [],
    }
    try:
        policies = document(base_dir)["policies"]
        policy = policies.get(trigger.id)
        if not base or not policy:
            return result
        result["task_class"] = policy["task_class"]
        if policy["binding_signature"] != signature(trigger):
            return {**result, "reason": "workflow_binding_changed"}
        journal = ExecutionJournal(_root(base_dir))
        samples = []
        for item in TriggerStore(_root(base_dir)).list_triggers(include_broken=False):
            peer = policies.get(item.id)
            if (
                not peer
                or peer["task_class"] != policy["task_class"]
                or peer["binding_signature"] != signature(item)
                or not eligible(item)
            ):
                continue
            rows, _ = journal.list_for_job_sync(item.id, limit=100)
            for row in rows:
                verdict = outcome(row)
                if verdict is None:
                    result["excluded"] += 1
                elif row.get("finished_at", 0) > 0 and row["finished_at"] <= now:
                    samples.append(
                        {
                            "run_id": row["run_id"],
                            "trigger_id": item.id,
                            "finished_at": row["finished_at"],
                            "success": verdict,
                        }
                    )
        samples = sorted(
            samples, key=lambda row: (row["finished_at"], row["run_id"]), reverse=True
        )[:20]
        result.update(
            samples=len(samples),
            successes=sum(row["success"] for row in samples),
            evidence=samples,
            reason="insufficient_samples",
        )
        if len(samples) < 5:
            return result
        result["confidence"] = "observed"
        if now - samples[0]["finished_at"] >= 86400:
            result["reason"] = "idle_grace_probe"
        elif all(row["success"] for row in samples[:3]):
            result["reason"] = "recent_recovery"
        elif result["successes"] / len(samples) < 0.5:
            effective = max(base, min(base * 4, 86400))
            result.update(
                reason="low_execution_success",
                effective_interval=effective,
                multiplier=effective / base,
            )
        else:
            result["reason"] = "execution_success_sufficient"
        return result
    except (ValueError, OSError, TypeError, KeyError):
        return {**result, "reason": "evidence_unavailable"}


def effective_interval(trigger, *, now, base_dir=None):
    return projection(trigger, now=now, base_dir=base_dir)["effective_interval"]


def view(*, now=None, base_dir=None):
    data = document(base_dir)
    return {
        "version": 1,
        "revision": data["revision"],
        "triggers": [
            projection(trigger, now=now, base_dir=base_dir)
            for trigger in TriggerStore(_root(base_dir)).list_triggers(
                include_broken=False
            )
            if eligible(trigger)
        ],
    }
