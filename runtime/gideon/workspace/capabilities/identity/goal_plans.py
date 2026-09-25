"""Human goal hierarchy and observed metrics over canonical planning/source records."""

import json
import math
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from gideon.core.record_ids import UnsafeRecordId, record_path
from gideon.engine.tasks.models import Task
from gideon.workspace.capabilities.identity.goals import GoalStore, _instant, _text
from gideon.workspace.capabilities.identity.store import ConflictError


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError("Metric must be finite and within one trillion units")


class GoalPlanStore:
    def __init__(self, path: Path):
        self.goals = GoalStore(path)
        self.path = self.goals.path
        self.home = self.path.parent.parent.parent
        with sqlite3.connect(self.path) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS goal_plans(id TEXT PRIMARY KEY,body TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS goal_checkins(id TEXT PRIMARY KEY,goal_id TEXT,observed_at TEXT,body TEXT NOT NULL,UNIQUE(goal_id,observed_at));"
                "CREATE TABLE IF NOT EXISTS plan_requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL);"
            )

    @staticmethod
    def _default(goal_id):
        return dict(
            goal_id=goal_id,
            revision=0,
            parent_id=None,
            horizon="long_term",
            milestones=[],
            links=[],
            unit="units",
            target_value=None,
        )

    @staticmethod
    def _plan(db, goal_id):
        row = db.execute(
            "SELECT body FROM goal_plans WHERE id=?", (goal_id,)
        ).fetchone()
        return json.loads(row[0]) if row else GoalPlanStore._default(goal_id)

    def _source(self, link):
        kind, identifier = link["kind"], link["id"]
        try:
            if kind == "session":
                row = self.goals.get_session(identifier)
                return {
                    **link,
                    "title": row["title"],
                    "status": row["status"],
                    "availability": "available",
                }
            if kind == "task":
                path = record_path(self.home / "tasks", identifier, kind="task_id")
                if not path.is_file():
                    raise KeyError(identifier)
                task = Task.from_dict(json.loads(path.read_text()))
                if task.id != identifier:
                    raise ValueError("Task source identity mismatch")
                return {
                    **link,
                    "title": task.title,
                    "status": task.status.value,
                    "availability": "available",
                }
            database = self.home / "loop/loops.db"
            if not database.resolve().is_relative_to(self.home.resolve()):
                raise ValueError("Loop source resolves outside runtime home")
            if not database.exists():
                raise KeyError(identifier)
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute(
                    "SELECT name,status,kind FROM loops WHERE id=?", (identifier,)
                ).fetchone()
            if row is None:
                raise KeyError(identifier)
            return {
                **link,
                "title": row[0],
                "status": row[1],
                "loop_kind": row[2],
                "availability": "available",
            }
        except KeyError:
            return {**link, "title": None, "status": None, "availability": "missing"}
        except (
            UnsafeRecordId,
            ValueError,
            TypeError,
            AttributeError,
            sqlite3.Error,
            OSError,
        ):
            return {
                **link,
                "title": None,
                "status": None,
                "availability": "unavailable",
            }

    @staticmethod
    def _replay(db, request_id, fingerprint):
        _text(request_id, "request_id", 128)
        row = db.execute(
            "SELECT fingerprint,body FROM plan_requests WHERE id=?", (request_id,)
        ).fetchone()
        if row:
            if row[0] != fingerprint:
                raise ConflictError(
                    "request_id already names another goal planning change"
                )
            return json.loads(row[1])
        return None

    def configure(
        self,
        *,
        goal_id,
        parent_id,
        horizon,
        milestones,
        links,
        unit,
        target_value,
        expected_revision,
        request_id,
    ):
        _text(unit, "unit", 40)
        if target_value is not None:
            _number(target_value)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected nonnegative plan revision")
        if horizon not in ("short_term", "long_term", "lifetime"):
            raise ValueError("Invalid goal horizon")
        if not isinstance(milestones, list) or len(milestones) > 100:
            raise ValueError("At most 100 milestones are allowed")
        names = set()
        for item in milestones:
            if not isinstance(item, dict) or set(item) != {
                "id",
                "title",
                "done",
                "target_date",
            }:
                raise ValueError("Milestones require id,title,done,target_date")
            _text(item["id"], "milestone id", 80)
            _text(item["title"], "milestone title", 200)
            if item["id"] in names or type(item["done"]) is not bool:
                raise ValueError(
                    "Milestone identifiers must be unique and done must be boolean"
                )
            names.add(item["id"])
            if (
                item["target_date"] is not None
                and date.fromisoformat(item["target_date"]).isoformat()
                != item["target_date"]
            ):
                raise ValueError("Milestone target date must be YYYY-MM-DD")
        if not isinstance(links, list) or len(links) > 100:
            raise ValueError("At most 100 local source links are allowed")
        seen = set()
        for link in links:
            if (
                not isinstance(link, dict)
                or set(link) != {"kind", "id"}
                or link["kind"] not in ("task", "loop", "session")
            ):
                raise ValueError("Links require local task,loop,session kind and id")
            _text(link["id"], "source id", 200)
            if link["kind"] == "loop" and not re.fullmatch(r"[a-f0-9]{8}", link["id"]):
                raise ValueError("Invalid local loop identifier")
            if (link["kind"], link["id"]) in seen:
                raise ValueError("Source links must be unique available local records")
            seen.add((link["kind"], link["id"]))
        fields = dict(
            goal_id=goal_id,
            parent_id=parent_id,
            horizon=horizon,
            milestones=milestones,
            links=links,
            unit=unit,
            target_value=target_value,
        )
        fingerprint = json.dumps(
            ["configure", fields, expected_revision], sort_keys=True
        )
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, request_id, fingerprint)
            if replay:
                return replay
            if any(self._source(link)["availability"] != "available" for link in links):
                raise ValueError("Source links must be available local records")
            GoalStore._get(db, "goals", goal_id)
            old = self._plan(db, goal_id)
            if old["revision"] != expected_revision:
                raise ConflictError("Goal plan changed; reload before saving")
            if (
                old["unit"] != unit
                and db.execute(
                    "SELECT 1 FROM goal_checkins WHERE goal_id=? LIMIT 1", (goal_id,)
                ).fetchone()
            ):
                raise ConflictError(
                    "Metric unit cannot change after observations exist"
                )
            ancestor, visited = parent_id, {goal_id}
            while ancestor is not None:
                if ancestor in visited:
                    raise ValueError("Goal hierarchy cannot contain a cycle")
                GoalStore._get(db, "goals", ancestor)
                visited.add(ancestor)
                ancestor = self._plan(db, ancestor)["parent_id"]
            result = {**fields, "revision": expected_revision + 1}
            db.execute(
                "INSERT OR REPLACE INTO goal_plans VALUES(?,?)",
                (goal_id, json.dumps(result)),
            )
            db.execute(
                "INSERT INTO plan_requests VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(result)),
            )
            return result

    def checkin(self, *, goal_id, value, observed_at, notes, request_id):
        _number(value)
        _text(notes, "notes", 5000, True)
        observed_at = _instant(observed_at)
        fingerprint = json.dumps(["checkin", goal_id, value, observed_at, notes])
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, request_id, fingerprint)
            if replay:
                return replay
            GoalStore._get(db, "goals", goal_id)
            if db.execute(
                "SELECT 1 FROM goal_checkins WHERE goal_id=? AND observed_at=?",
                (goal_id, observed_at),
            ).fetchone():
                raise ConflictError(
                    "A metric observation already exists at this instant"
                )
            result = dict(
                id=uuid4().hex,
                goal_id=goal_id,
                value=value,
                observed_at=observed_at,
                notes=notes,
                unit=self._plan(db, goal_id)["unit"],
                source="human_reported",
            )
            db.execute(
                "INSERT INTO goal_checkins VALUES(?,?,?,?)",
                (result["id"], goal_id, observed_at, json.dumps(result)),
            )
            db.execute(
                "INSERT INTO plan_requests VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(result)),
            )
            return result

    def get(self, goal_id):
        with sqlite3.connect(self.path) as db:
            goal = GoalStore._get(db, "goals", goal_id)
            plan = self._plan(db, goal_id)
            children = [
                row["goal_id"]
                for raw in db.execute("SELECT body FROM goal_plans")
                if (row := json.loads(raw[0]))["parent_id"] == goal_id
            ]
            checkins = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM goal_checkins WHERE goal_id=? ORDER BY observed_at,id",
                    (goal_id,),
                )
            ]
        velocity = None
        if len(checkins) >= 2:
            first, last = checkins[0], checkins[-1]
            days = (
                datetime.fromisoformat(last["observed_at"])
                - datetime.fromisoformat(first["observed_at"])
            ).total_seconds() / 86400
            velocity = {
                "value_per_day": (last["value"] - first["value"]) / days,
                "unit": plan["unit"],
                "from": first["observed_at"],
                "to": last["observed_at"],
            }
        return {
            "goal": goal,
            "plan": plan,
            "children": sorted(children),
            "checkins": checkins,
            "velocity": velocity,
            "linked_sources": [self._source(link) for link in plan["links"]],
            "milestones_complete_ratio": (
                sum(item["done"] for item in plan["milestones"])
                / len(plan["milestones"])
                if plan["milestones"]
                else None
            ),
        }

    def list(self):
        return [self.get(goal["id"]) for goal in self.goals.list_goals()]
