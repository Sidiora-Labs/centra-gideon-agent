"""Ambient presentation reads existing projections without inventing activity."""

import json
from datetime import datetime, timezone

from gideon.core.config.loader import config_dir

from .graph import revision
from .store import Conflict


class AmbientDisplay:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS ambient_preferences(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT)"
            )
            db.execute(
                "INSERT OR IGNORE INTO ambient_preferences VALUES(1,?)",
                (
                    json.dumps(
                        {
                            "revision": 1,
                            "show_clock": True,
                            "font_scale": 1,
                            "idle_seconds": 30,
                        }
                    ),
                ),
            )

    def preferences(self):
        with self.store.connection() as db:
            return json.loads(
                db.execute(
                    "SELECT body FROM ambient_preferences WHERE id=1"
                ).fetchone()[0]
            )

    def save(self, body):
        if not isinstance(body, dict) or set(body) != {
            "revision",
            "show_clock",
            "font_scale",
            "idle_seconds",
        }:
            raise ValueError(
                "ambient requires revision, show_clock, font_scale, idle_seconds"
            )
        expected = revision(body["revision"])
        if (
            type(body["show_clock"]) is not bool
            or type(body["font_scale"]) is not int
            or not 1 <= body["font_scale"] <= 3
            or type(body["idle_seconds"]) is not int
            or not 10 <= body["idle_seconds"] <= 300
        ):
            raise ValueError("ambient presentation values are invalid")
        with self.store.connection() as db:
            current = json.loads(
                db.execute(
                    "SELECT body FROM ambient_preferences WHERE id=1"
                ).fetchone()[0]
            )
            if current["revision"] != expected:
                raise Conflict("ambient preferences changed; reload before saving")
            saved = {**body, "revision": expected + 1}
            db.execute(
                "UPDATE ambient_preferences SET body=? WHERE id=1", (json.dumps(saved),)
            )
            return saved

    async def snapshot(self):
        home = self.store.path.parent.parent
        if config_dir().resolve() != home.resolve():
            raise Conflict(
                "ambient projection scope differs from registered runtime home"
            )
        from gideon.automation.triggers.store import TriggerStore
        from gideon.cognition.proactive.surface import build_digest_view
        from gideon.engine.tasks.native import NativeTaskProvider
        from gideon.interfaces.dashboard.handlers.proactive import (
            _install_state,
            _latest_digest,
        )
        from gideon.interfaces.dashboard.views_store import list_views

        cards = {}
        try:
            rows = TriggerStore(home).load()
            broken = [row for row in rows if not row.ok]
            cards["schedule"] = {
                "state": "error" if broken else "ready",
                "rows": [
                    {
                        "id": row.trigger.id,
                        "title": row.trigger.name,
                        "enabled": row.trigger.enabled,
                        "next_fire_at": row.trigger.next_fire_at,
                    }
                    for row in rows[:20]
                ],
                "error": "Some schedule records are unreadable." if broken else "",
            }
        except Exception as exc:
            cards["schedule"] = {"state": "error", "rows": [], "error": str(exc)}
        try:
            tasks, total = (
                await NativeTaskProvider().list_tasks(limit=20)
                if (home / "tasks").is_dir()
                else ([], 0)
            )
            cards["work"] = {
                "state": "ready",
                "rows": [
                    {"id": task.id, "title": task.title, "status": task.status.value}
                    for task in tasks
                ],
                "total": total,
                "error": "",
            }
        except Exception as exc:
            cards["work"] = {"state": "error", "rows": [], "error": str(exc)}
        try:
            views = list_views()
            cards["tiles"] = {
                "state": "ready",
                "rows": [
                    tile
                    for view in views
                    if view["id"] == "overview"
                    for tile in view["tiles"]
                ],
                "error": "",
            }
        except Exception as exc:
            cards["tiles"] = {"state": "error", "rows": [], "error": str(exc)}
        try:
            state = _install_state()
            run, output, events = (
                _latest_digest()
                if state["installed"] and state["enabled"]
                else (None, None, [])
            )
            cards["digest"] = build_digest_view(
                enabled=state["enabled"],
                installed=state["installed"],
                run=run,
                output=output,
                events=events,
            )
        except Exception as exc:
            cards["digest"] = {"state": "error", "error": str(exc)}
        from gideon.workspace.capabilities.identity.goals import GoalStore
        from gideon.workspace.capabilities.identity.progress import ProgressStore
        from gideon.workspace.capabilities.wellbeing.store import MeasurementStore

        try:
            goals_path = home / "capabilities/identity/goals.sqlite3"
            goals = GoalStore(goals_path) if goals_path.exists() else None
            cards["goals"] = {
                "state": "ready",
                "rows": [
                    {"id": row["id"], "title": row["title"], "status": row["status"]}
                    for row in (goals.list_goals()[:20] if goals else [])
                ],
                "error": "",
            }
            cards["calendar"] = {
                "state": "ready",
                "rows": [
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "status": row["status"],
                        "start_at": row["start_at"],
                        "end_at": row["end_at"],
                    }
                    for row in (goals.list_sessions()[:20] if goals else [])
                ],
                "error": "",
            }
            progress_path = home / "capabilities/identity/progress.sqlite3"
            progress = (
                ProgressStore(progress_path).sheet()
                if progress_path.exists()
                else {
                    "age": None,
                    "as_of": None,
                    "planned_completed_minutes": None,
                    "tasks_done": None,
                    "missing_task_ids": [],
                    "invalid_task_ids": [],
                }
            )
            cards["progress"] = {
                "state": "ready" if progress_path.exists() else "unavailable",
                "age": progress["age"],
                "as_of": progress["as_of"],
                "planned_completed_minutes": progress["planned_completed_minutes"],
                "tasks_done": progress["tasks_done"],
                "missing_task_ids": progress["missing_task_ids"],
                "invalid_task_ids": progress["invalid_task_ids"],
                "error": (
                    ""
                    if progress_path.exists()
                    else "Progress profile is not configured; age and progress totals are unknown."
                ),
            }
        except Exception as exc:
            for name in ("goals", "calendar", "progress"):
                cards[name] = {"state": "error", "rows": [], "error": str(exc)}
        try:
            records = (
                MeasurementStore(home).list(limit=10)
                if (home / "capabilities/wellbeing.sqlite3").exists()
                else []
            )
            cards["health"] = {
                "state": "ready",
                "rows": [
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        "observed_at": row["observed_at"],
                        "unit": row["unit"],
                        "values": row["values"],
                    }
                    for row in records
                ],
                "error": "",
            }
        except Exception as exc:
            cards["health"] = {"state": "error", "rows": [], "error": str(exc)}
        cards["external_calendar"] = {
            "state": "unavailable",
            "error": "External calendar events are not connected to this display; planned sessions appear separately.",
        }
        cards["mortality"] = {
            "state": "unavailable",
            "error": "Mortality and life expectancy estimates are unavailable.",
        }
        return {
            "preferences": self.preferences(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "cards": cards,
        }
