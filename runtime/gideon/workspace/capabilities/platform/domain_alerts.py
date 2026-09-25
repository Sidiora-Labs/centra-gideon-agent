"""Conservative alerts projected from canonical personal-domain evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.integrations.inbox import (
    InboxStore,
    emit_attention_item,
    resolve_attention_items,
)
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore

DOMAINS = (
    ("goals", "capabilities/identity/goals.sqlite3", "#/capabilities/identity"),
    ("wellbeing", "capabilities/wellbeing.sqlite3", "#/capabilities/wellbeing"),
)
DETECTORS = (
    "overdue_goal",
    "recording_gap",
    "unanswered_thread",
    "task_quality",
    "learning_health",
    "recorded_crash",
)


def source(home: Path, relative: str) -> Path:
    path = home / relative
    if path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
        raise ValueError("Domain source escapes Gideon home")
    return path


def readiness(home: Path) -> list[dict[str, str]]:
    rows = []
    for name, relative, route in DOMAINS:
        state = "unconfigured"
        try:
            path = source(home, relative)
            if path.exists():
                import sqlite3

                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    required = "goals" if name == "goals" else "revisions"
                    healthy = (
                        connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                    )
                    state = "ready" if required in tables and healthy else "unavailable"
                finally:
                    connection.close()
        except Exception:
            state = "unavailable"
        rows.append(
            {
                "domain": name,
                "state": state,
                "setup_route": route,
                "network": "not_applicable",
            }
        )
    return rows


def _additional_conditions(home: Path, now: datetime) -> list[dict]:
    from gideon.automation.triggers.store import TriggerStore
    from gideon.operations.resilience.doctor import DoctorContext, all_probes
    from gideon.workspace.capabilities.communications.evidence import report
    from gideon.workspace.capabilities.communications.store import PeopleStore
    from gideon.workspace.capabilities.platform import cadence

    result = []

    def add(detector, identifier, evidence, title, route):
        result.append(
            {
                "detector": detector,
                "source_id": identifier,
                "evidence": evidence,
                "title": title,
                "body": (
                    "Review the recorded source evidence. This observation does not infer "
                    "an outcome beyond that evidence."
                ),
                "route": route,
            }
        )

    if source(home, "capabilities/communications/people.sqlite3").exists():
        for row in report(PeopleStore(home / "capabilities/communications"), now=now)[
            "threads"
        ]:
            if row["state"] == "unanswered":
                evidence = {
                    key: row[key]
                    for key in ("source", "source_account_id", "person_id", "thread_id")
                }
                evidence.update(
                    message_id=row["latest"]["external_id"],
                    observed_at=row["latest"]["occurred_at"],
                )
                add(
                    "unanswered_thread",
                    row["person_id"],
                    evidence,
                    "A recorded conversation is unanswered",
                    "#/capabilities/communications",
                )
    if source(home, "capabilities/platform/cadence.json").exists():
        for entry in TriggerStore(home).list_triggers(include_broken=False):
            row = cadence.projection(entry, base_dir=home, now=now.timestamp())
            if row["reason"] == "low_execution_success":
                add(
                    "task_quality",
                    entry.id,
                    {
                        "run_ids": [item["run_id"] for item in row["evidence"]],
                        "failures": row["samples"] - row["successes"],
                        "samples": row["samples"],
                    },
                    "A task class has repeated recorded failures",
                    "#/capabilities/platform",
                )
    source(home, "learning.db")
    source(home, "crashes")
    selected = [
        probe
        for probe in all_probes()
        if probe.id in ("memory-pipeline.freshness", "crashes.recent")
    ]

    async def probes():
        return [
            (probe.id, await probe.run(DoctorContext(home=home))) for probe in selected
        ]

    with ThreadPoolExecutor(max_workers=1) as pool:
        outcomes = pool.submit(lambda: asyncio.run(probes())).result(timeout=15)
    for identifier, outcome in outcomes:
        if outcome.ok:
            continue
        if identifier == "memory-pipeline.freshness" and outcome.evidence.get(
            "staging_log"
        ):
            keys = ("passes", "errors", "all_ok_streak", "produced", "staging_backlog")
            add(
                "learning_health",
                identifier,
                {key: outcome.evidence[key] for key in keys},
                "Recorded learning pipeline needs attention",
                "#/doctor",
            )
        elif identifier == "crashes.recent":
            for row in outcome.evidence.get("crashes", []):
                if (
                    row.get("ts") is not None
                    and now.timestamp() - 7 * 86400 <= row["ts"] <= now.timestamp()
                ):
                    add(
                        "recorded_crash",
                        row["file"],
                        {"file": row["file"], "ts": row["ts"], "kind": row["kind"]},
                        "A runtime crash was recorded",
                        "#/doctor",
                    )
    return result


def conditions(home: Path, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = []
    goals_path = source(home, DOMAINS[0][1])
    if goals_path.exists():
        for goal in GoalStore(goals_path).list_goals():
            if (
                goal["status"] == "active"
                and goal["target_date"]
                and goal["target_date"] < now.date().isoformat()
            ):
                rows.append(
                    {
                        "detector": "overdue_goal",
                        "source_id": goal["id"],
                        "evidence": {
                            "revision": goal["revision"],
                            "target_date": goal["target_date"],
                        },
                        "title": "A goal target date has passed",
                        "body": "Review the active goal and its target date. This does not infer completion.",
                        "route": DOMAINS[0][2],
                    }
                )
    if source(home, DOMAINS[1][1]).exists():
        store = InterventionStore(home)
        for plan in store.list_plans():
            summary = store.summary(plan["id"], days=7, as_of=now.isoformat())
            missing = [
                row["date"]
                for row in summary["days"]
                if row["scheduled"] and row["status"] is None
            ]
            if len(missing) >= 3:
                rows.append(
                    {
                        "detector": "recording_gap",
                        "source_id": plan["id"],
                        "evidence": {
                            "revision": plan["revision"],
                            "unrecorded_dates": missing,
                        },
                        "title": "Scheduled activity has unrecorded days",
                        "body": "Review the activity record. Unrecorded days do not mean the activity was skipped.",
                        "route": DOMAINS[1][2],
                    }
                )
    rows.extend(_additional_conditions(home, now))
    for row in rows:
        row["fingerprint"] = hashlib.sha256(
            json.dumps(row, sort_keys=True).encode()
        ).hexdigest()
    return rows


def scan(state, store: InboxStore | None = None, now: datetime | None = None) -> dict:
    home = config_dir().resolve(strict=True)
    source(home, "inbox.json")
    store = store or InboxStore()
    if not store.items:
        store.load()
    rows = conditions(home, now)
    current = {row["fingerprint"] for row in rows}
    existing = {item.refs.get("domain_fingerprint") for item in store.items.values()}
    for item in list(store.items.values()):
        fingerprint = item.refs.get("domain_fingerprint")
        if fingerprint and fingerprint not in current:
            resolve_attention_items(
                state, {"domain_fingerprint": fingerprint}, store=store
            )
    for row in rows:
        if row["fingerprint"] not in existing:
            emit_attention_item(
                state,
                source="personal",
                kind="domain_alert",
                title=row["title"],
                body=row["body"],
                item_kind="system",
                store=store,
                dedup_key=row["fingerprint"],
                refs={
                    "domain_fingerprint": row["fingerprint"],
                    "domain_source": row["source_id"],
                    "evidence": row["evidence"],
                    "url": row["route"],
                },
            )
    return {"conditions": len(rows), "readiness": readiness(home)}


def inventory() -> dict:
    return {
        "version": 1,
        "domains": readiness(config_dir()),
        "detectors": list(DETECTORS),
        "other_detectors": "existing_source_contracts_only",
    }
