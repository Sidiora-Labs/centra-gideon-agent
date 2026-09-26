"""Bounded campaigns backed by real workflow runs and their durable ledgers."""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from gideon.automation.workflows import journal, service, store
from gideon.automation.workflows.models import OriginKind
from gideon.core.config import loader as config_loader


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    return config_loader.config_dir() / "experiments" / "campaigns.sqlite3"


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL,
            workflow_name TEXT NOT NULL, metric TEXT NOT NULL,
            direction TEXT NOT NULL, max_parallel INTEGER NOT NULL,
            max_tokens INTEGER NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attempts (
            campaign_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            inputs TEXT NOT NULL, state TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '', score REAL,
            valid INTEGER, observation TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id, ordinal),
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );
    """)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def create(spec: dict[str, Any]) -> dict[str, Any]:
    title = str(spec.get("title") or "").strip()
    objective = str(spec.get("objective") or "").strip()
    workflow = str(spec.get("workflow_name") or "").strip()
    metric = str(spec.get("metric") or "").strip()
    variants = spec.get("variants")
    direction = str(spec.get("direction") or "maximize")
    parallel = spec.get("max_parallel", 1)
    tokens = spec.get("max_tokens", 0)
    if not all((title, objective, workflow, metric)):
        raise ValueError("title, objective, workflow_name and metric are required")
    if len(title) > 120 or len(objective) > 1000 or len(metric) > 120:
        raise ValueError("campaign text exceeds its limit")
    if direction not in ("maximize", "minimize"):
        raise ValueError("direction must be maximize or minimize")
    if not isinstance(variants, list) or not 1 <= len(variants) <= 20 or not all(isinstance(v, dict) for v in variants):
        raise ValueError("variants must contain 1 to 20 workflow input objects")
    if not isinstance(parallel, int) or isinstance(parallel, bool) or not 1 <= parallel <= 4:
        raise ValueError("max_parallel must be between 1 and 4")
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        raise ValueError("max_tokens must be a nonnegative integer")
    for variant in variants:
        if len(json.dumps(variant, ensure_ascii=False)) > 16000:
            raise ValueError("a variant exceeds 16000 characters")
    campaign_id = secrets.token_hex(12)
    now = _now()
    with _db() as db:
        db.execute(
            "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, title, objective, workflow, metric, direction, parallel, tokens, "active", now),
        )
        db.executemany(
            "INSERT INTO attempts (campaign_id, ordinal, inputs, state, updated_at) VALUES (?, ?, ?, 'queued', ?)",
            [(campaign_id, i, json.dumps(v, sort_keys=True), now) for i, v in enumerate(variants)],
        )
    return detail(campaign_id) or {}


def _reconcile(db: sqlite3.Connection, campaign_id: str) -> None:
    for row in db.execute("SELECT ordinal, run_id FROM attempts WHERE campaign_id=? AND run_id!=''", (campaign_id,)):
        run = store.get(row["run_id"])
        state = run.status.value if run is not None else "run_missing"
        db.execute(
            "UPDATE attempts SET state=?, updated_at=? WHERE campaign_id=? AND ordinal=? AND state!=?",
            (state, _now(), campaign_id, row["ordinal"], state),
        )


def _view(db: sqlite3.Connection, campaign_id: str) -> dict[str, Any] | None:
    campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if campaign is None:
        return None
    _reconcile(db, campaign_id)
    attempts = []
    total_tokens = 0
    tokens_recorded = True
    for row in db.execute("SELECT * FROM attempts WHERE campaign_id=? ORDER BY ordinal", (campaign_id,)):
        attempt = dict(row)
        attempt["inputs"] = json.loads(attempt["inputs"])
        attempt["valid"] = None if attempt["valid"] is None else bool(attempt["valid"])
        run = store.get(attempt["run_id"]) if attempt["run_id"] else None
        if run is not None:
            totals = journal.run_totals(run.id)
            attempt["tokens"] = totals.get("tokens")
            attempt["cost_usd"] = totals.get("cost_usd") if totals.get("priced") else None
            if totals.get("tokens") is None:
                tokens_recorded = False
            else:
                total_tokens += int(totals["tokens"])
            attempt["run_error"] = run.error_message
        attempts.append(attempt)
    valid = [a for a in attempts if a["valid"] is True and a["score"] is not None and a["state"] == "complete"]
    best = None
    if valid:
        best = min(valid, key=lambda a: a["score"] if campaign["direction"] == "minimize" else -a["score"])
    result = dict(campaign)
    result.update(attempts=attempts, total_tokens=total_tokens if tokens_recorded else None, best_attempt=best["ordinal"] if best else None)
    return result


def detail(campaign_id: str) -> dict[str, Any] | None:
    with _db() as db:
        return _view(db, campaign_id)


def listing() -> list[dict[str, Any]]:
    with _db() as db:
        ids = [row[0] for row in db.execute("SELECT id FROM campaigns ORDER BY created_at DESC LIMIT 100")]
        return [view for campaign_id in ids if (view := _view(db, campaign_id))]


async def advance(campaign_id: str, *, supervisor: Any, session_key: str = "") -> dict[str, Any]:
    if supervisor is None:
        raise RuntimeError("workflow supervisor is unavailable")
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        view = _view(db, campaign_id)
        if view is None:
            raise KeyError(campaign_id)
        if view["status"] != "active":
            return view
        busy = sum(a["state"] not in ("queued", "complete", "failed", "cancelled", "run_missing", "launch_failed", "needs_input", "escalated") for a in view["attempts"])
        remaining = max(0, view["max_parallel"] - busy)
        if view["max_tokens"] and (view["total_tokens"] is None or view["total_tokens"] >= view["max_tokens"]):
            remaining = 0
        claims = []
        for a in (a for a in view["attempts"] if a["state"] == "queued"):
            if len(claims) >= remaining:
                break
            changed = db.execute("UPDATE attempts SET state='launching', updated_at=? WHERE campaign_id=? AND ordinal=? AND state='queued'", (_now(), campaign_id, a["ordinal"]))
            if changed.rowcount:
                claims.append(a)
    for attempt in claims:
        try:
            result = await service.start_run(
                name=view["workflow_name"], inputs=attempt["inputs"], mode="background",
                supervisor=supervisor, origin_kind=OriginKind.API, session_key=session_key,
            )
            run_id = str(result.get("run_id") or "")
            state = "running" if result.get("ok") else "launch_failed"
            if run_id and not result.get("ok"):
                state = "launch_failed"
        except Exception:
            run_id, state = "", "launch_failed"
        with _db() as db:
            db.execute("UPDATE attempts SET state=?, run_id=?, updated_at=? WHERE campaign_id=? AND ordinal=? AND state='launching'", (state, run_id, _now(), campaign_id, attempt["ordinal"]))
    return detail(campaign_id) or {}


def observe(campaign_id: str, ordinal: int, *, score: float, valid: bool, observation: str) -> dict[str, Any]:
    if not math.isfinite(score):
        raise ValueError("score must be finite")
    if not observation.strip() or len(observation) > 4000:
        raise ValueError("observation must be 1 to 4000 characters")
    with _db() as db:
        view = _view(db, campaign_id)
        if view is None:
            raise KeyError(campaign_id)
        target = next((a for a in view["attempts"] if a["ordinal"] == ordinal), None)
        if target is None or target["state"] != "complete":
            raise ValueError("only completed workflow attempts can receive an observation")
        db.execute("UPDATE attempts SET score=?, valid=?, observation=?, updated_at=? WHERE campaign_id=? AND ordinal=?", (score, int(valid), observation.strip(), _now(), campaign_id, ordinal))
    return detail(campaign_id) or {}


def stop(campaign_id: str) -> dict[str, Any]:
    with _db() as db:
        if not db.execute("SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)).fetchone():
            raise KeyError(campaign_id)
        db.execute("UPDATE campaigns SET status='stopped' WHERE id=?", (campaign_id,))
    return detail(campaign_id) or {}
