"""Human progress projection from existing source records, without invented scores."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from gideon.core.record_ids import record_path, UnsafeRecordId
from gideon.engine.tasks.models import Task
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.store import ConflictError, StoryStore


class ProgressStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.home = self.path.parent.parent.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL);")

    def profile(self):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT body FROM profile WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"revision": 0, "birth_date": None, "timezone": "UTC", "tracked_task_ids": []}

    def configure(self, *, birth_date, timezone, tracked_task_ids, expected_revision, request_id):
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError("timezone must be an IANA timezone") from None
        if birth_date is not None:
            if not isinstance(birth_date, str) or date.fromisoformat(birth_date).isoformat() != birth_date:
                raise ValueError("birth_date must be YYYY-MM-DD or null")
            if date.fromisoformat(birth_date) > datetime.now(ZoneInfo(timezone)).date():
                raise ValueError("birth_date cannot be in the future")
        if not isinstance(tracked_task_ids, list) or len(tracked_task_ids) > 100 or any(not isinstance(id, str) for id in tracked_task_ids) or len(set(tracked_task_ids)) != len(tracked_task_ids):
            raise ValueError("tracked_task_ids must be at most 100 unique native task identifiers")
        for id in tracked_task_ids:
            try:
                record_path(self.home / "tasks", id, kind="task_id")
            except UnsafeRecordId:
                raise ValueError("Invalid local task identifier") from None
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("request_id is required")
        fingerprint = json.dumps([birth_date, timezone, tracked_task_ids, expected_revision])
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT fingerprint,body FROM requests WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError("request_id already names a different progress profile change")
                return json.loads(prior[1])
            old = db.execute("SELECT body FROM profile WHERE id=1").fetchone()
            revision = json.loads(old[0])["revision"] if old else 0
            if revision != expected_revision:
                raise ConflictError("Progress profile changed; reload before saving")
            result = dict(revision=revision + 1, birth_date=birth_date, timezone=timezone, tracked_task_ids=tracked_task_ids)
            db.execute("INSERT OR REPLACE INTO profile VALUES(1,?)", (json.dumps(result),))
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, json.dumps(result)))
            return result

    def sheet(self, as_of=None):
        profile = self.profile()
        zone = ZoneInfo(profile["timezone"])
        day = date.fromisoformat(as_of) if as_of is not None else datetime.now(zone).date()
        if as_of is not None and day.isoformat() != as_of:
            raise ValueError("as_of must be YYYY-MM-DD")
        birth = date.fromisoformat(profile["birth_date"]) if profile["birth_date"] else None
        age = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day)) if birth and day >= birth else None
        goals_path = self.path.parent / "goals.sqlite3"
        goals = GoalStore(goals_path) if goals_path.exists() else None
        goal_rows = goals.list_goals() if goals else []
        sessions = [row for row in goals.list_sessions() if row["status"] == "completed" and datetime.fromisoformat(row["end_at"]).astimezone(zone).date() <= day] if goals else []
        completed = [{"id": row["id"], "goal_id": row["goal_id"], "title": row["title"], "local_date": datetime.fromisoformat(row["end_at"]).astimezone(zone).date().isoformat(),
                      "planned_minutes": (datetime.fromisoformat(row["end_at"]) - datetime.fromisoformat(row["start_at"])).total_seconds() / 60} for row in sessions]
        tasks, missing, invalid = [], [], []
        for id in profile["tracked_task_ids"]:
            try:
                path = record_path(self.home / "tasks", id, kind="task_id")
            except UnsafeRecordId:
                invalid.append(id)
                continue
            if not path.is_file():
                missing.append(id)
                continue
            try:
                raw = json.loads(path.read_text())
                task = Task.from_dict(raw)
                if task.id != id:
                    raise ValueError("Source identity mismatch")
                tasks.append({"id": task.id, "title": task.title, "status": task.status.value})
            except (TypeError, ValueError, KeyError):
                invalid.append(id)
        story_path = self.path.parent / "stories.sqlite3"
        return {"profile": profile, "as_of": day.isoformat(), "age": age,
                "goals": {status: sum(row["status"] == status for row in goal_rows) for status in ("active", "completed", "archived")},
                "sessions_completed": completed, "planned_completed_minutes": sum(row["planned_minutes"] for row in completed),
                "authored_story_count": len(StoryStore(story_path).list()) if story_path.exists() else 0,
                "tasks": tasks, "tasks_done": sum(row["status"] == "done" for row in tasks), "missing_task_ids": missing, "invalid_task_ids": invalid,
                "unknown_metrics": ["health", "skill_level", "measured_effort"], "source_policy": "Current source state; as_of bounds session dates and age, not historical task or goal status"}
