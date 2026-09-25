"""Transactional story revisions and source-bound player sessions."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from gideon.core.config.loader import config_dir

from .graph import identifier, revision, validate_graph


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


class ExperienceStore:
    def __init__(self, home: Path | None = None):
        self.path = (home or config_dir()) / "capabilities" / "experience.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS versions(id TEXT, revision INTEGER, body TEXT NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(scope TEXT, id TEXT, input TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(scope,id));
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _story(self, db, key, version=None):
        identifier(key)
        if version is None:
            row = db.execute(
                "SELECT revision FROM stories WHERE id=? AND deleted=0", (key,)
            ).fetchone()
            if not row:
                raise NotFound("story not found")
            version = row[0]
        row = db.execute(
            "SELECT body FROM versions WHERE id=? AND revision=?", (key, version)
        ).fetchone()
        if not row:
            raise NotFound("story revision not found")
        return json.loads(row[0])

    def stories(self):
        with self.connection() as db:
            return [
                self._story(db, row[0])
                for row in db.execute(
                    "SELECT id FROM stories WHERE deleted=0 ORDER BY rowid DESC LIMIT 200"
                )
            ]

    def story(self, key):
        with self.connection() as db:
            return self._story(db, key)

    def save(self, body, key=None):
        graph = validate_graph(body)
        with self.connection() as db:
            if key is None:
                key, version = uuid4().hex, 1
                db.execute(
                    "INSERT INTO stories(id,revision) VALUES(?,?)", (key, version)
                )
            else:
                previous = self._story(db, key)
                if revision(body.get("revision")) != previous["revision"]:
                    raise Conflict("story revision changed")
                version = previous["revision"] + 1
                db.execute("UPDATE stories SET revision=? WHERE id=?", (version, key))
            story = {"id": key, **graph, "revision": version}
            db.execute(
                "INSERT INTO versions VALUES(?,?,?)", (key, version, json.dumps(story))
            )
            return story

    def delete(self, key, expected):
        with self.connection() as db:
            if self._story(db, key)["revision"] != revision(expected):
                raise Conflict("story revision changed")
            db.execute("UPDATE stories SET deleted=1 WHERE id=?", (key,))

    def _session(self, db, key):
        row = db.execute(
            "SELECT body FROM sessions WHERE id=?", (identifier(key),)
        ).fetchone()
        if not row:
            raise NotFound("session not found")
        return json.loads(row[0])

    def _view(self, db, session):
        story = self._story(db, session["story_id"], session["story_revision"])
        node = next(n for n in story["nodes"] if n["id"] == session["current_node"])
        return {"session": session, "story": story, "node": node}

    def session(self, key):
        with self.connection() as db:
            return self._view(db, self._session(db, key))

    def sessions(self, story_id=None):
        with self.connection() as db:
            if story_id is not None:
                identifier(story_id)
            rows = db.execute(
                "SELECT body FROM sessions WHERE (? IS NULL OR json_extract(body,'$.story_id')=?) ORDER BY rowid DESC LIMIT 200",
                (story_id, story_id),
            )
            return [json.loads(r[0]) for r in rows]

    def _replay(self, db, scope, body):
        request_id = identifier(body.get("request_id"))
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        row = db.execute(
            "SELECT input,result FROM requests WHERE scope=? AND id=?",
            (scope, request_id),
        ).fetchone()
        if row and row[0] != encoded:
            raise Conflict("request identifier was already used with different input")
        return encoded, json.loads(row[1]) if row else None

    def _remember(self, db, scope, body, encoded, result):
        db.execute(
            "INSERT INTO requests VALUES(?,?,?,?)",
            (scope, body["request_id"], encoded, json.dumps(result)),
        )
        return result

    def start(self, body):
        if not isinstance(body, dict) or set(body) != {
            "story_id",
            "story_revision",
            "request_id",
        }:
            raise ValueError("start requires story_id, story_revision and request_id")
        with self.connection() as db:
            encoded, replay = self._replay(db, "start", body)
            if replay is not None:
                return replay
            story = self._story(db, body["story_id"])
            if story["revision"] != revision(body["story_revision"]):
                raise Conflict("story revision changed")
            session = {
                "id": uuid4().hex,
                "story_id": story["id"],
                "story_revision": story["revision"],
                "current_node": story["start_node"],
                "history": [],
                "revision": 1,
            }
            db.execute(
                "INSERT INTO sessions VALUES(?,?)", (session["id"], json.dumps(session))
            )
            return self._remember(db, "start", body, encoded, self._view(db, session))

    def choose(self, key, body):
        if not isinstance(body, dict) or set(body) != {
            "choice_id",
            "revision",
            "request_id",
        }:
            raise ValueError("choice requires choice_id, revision and request_id")
        identifier(body["choice_id"])
        with self.connection() as db:
            session = self._session(db, key)
            encoded, replay = self._replay(db, key, body)
            if replay is not None:
                return replay
            if session["revision"] != revision(body["revision"]):
                raise Conflict("session revision changed")
            node = self._view(db, session)["node"]
            choice = next(
                (c for c in node["choices"] if c["id"] == body["choice_id"]), None
            )
            if choice is None:
                raise ValueError("choice is unavailable at the current node")
            if len(session["history"]) >= 1000:
                raise Conflict("session reached the 1000 choice limit")
            session["revision"] += 1
            session["history"].append(
                {
                    "request_id": body["request_id"],
                    "choice_id": choice["id"],
                    "from_node": node["id"],
                    "to_node": choice["target"],
                    "revision": session["revision"],
                }
            )
            session["current_node"] = choice["target"]
            db.execute(
                "UPDATE sessions SET body=? WHERE id=?", (json.dumps(session), key)
            )
            return self._remember(db, key, body, encoded, self._view(db, session))
