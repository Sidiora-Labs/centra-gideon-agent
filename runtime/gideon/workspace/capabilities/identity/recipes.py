"""Versioned bounded recipes delegating to existing guarded read operations."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from gideon.workspace.capabilities.identity.store import ConflictError

READ_TOOLS = frozenset({"identity_story_list", "identity_story_get", "identity_story_chain", "identity_story_history",
    "identity_goals_list_goals", "identity_goals_get_goal", "identity_goals_list_sessions", "identity_goals_get_session", "identity_goals_calendar", "identity_progress_sheet", "identity_bundle_inventory"})


def _text(value, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Expected a bounded nonempty string")


def _walk(value, previous, outputs=None, depth=0):
    if depth > 8:
        raise ValueError("Recipe arguments are too deeply nested")
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("A binding must contain only a string $ref")
            source, separator, pointer = value["$ref"].partition("#")
            if not separator or source not in previous or (pointer and not pointer.startswith("/")):
                raise ValueError("Bindings may reference only earlier step outputs")
            if outputs is None:
                return value
            result = outputs[source]
            for part in pointer.split("/")[1:] if pointer else []:
                key = part.replace("~1", "/").replace("~0", "~")
                if isinstance(result, list) and key.isdigit():
                    result = result[int(key)]
                elif isinstance(result, dict):
                    result = result[key]
                else:
                    raise ValueError("Binding pointer does not address an output")
            return result
        return {key: _walk(item, previous, outputs, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, previous, outputs, depth + 1) for item in value]
    return value


class RecipeStore:
    def __init__(self, path: Path, *, allowed_tools=None, guarded=False):
        self.guarded = guarded
        self.allowed_tools = READ_TOOLS if allowed_tools is None else frozenset(allowed_tools)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,body TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS versions(id TEXT,revision INTEGER,body TEXT NOT NULL,PRIMARY KEY(id,revision));"
                             "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,body TEXT NOT NULL);")

    @staticmethod
    def _get(db, table, id):
        row = db.execute(f"SELECT body FROM {table} WHERE id=?", (id,)).fetchone()
        if not row:
            raise KeyError("Recipe record not found")
        return json.loads(row[0])

    def get(self, id):
        with sqlite3.connect(self.path) as db:
            return self._get(db, "recipes", id)

    def list(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM recipes ORDER BY id")]

    def history(self, id):
        with sqlite3.connect(self.path) as db:
            self._get(db, "recipes", id)
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM versions WHERE id=? ORDER BY revision", (id,))]

    @staticmethod
    def _replay(db, request_id, fingerprint):
        _text(request_id, 128)
        row = db.execute("SELECT fingerprint,body FROM requests WHERE id=?", (request_id,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise ConflictError("request_id already names another recipe operation")
            return json.loads(row[1])
        return None

    def save(self, *, title, steps, request_id, enabled=True, id=None, expected_revision=0):
        _text(title)
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected enabled boolean and nonnegative revision")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 5:
            raise ValueError("Recipes require one to five read steps")
        if len(json.dumps(steps, allow_nan=False)) > 16000:
            raise ValueError("Recipe arguments exceed their size limit")
        previous = set()
        for step in steps:
            if not isinstance(step, dict) or set(step) != {"id", "tool", "arguments"} or not isinstance(step["arguments"], dict):
                raise ValueError("Each step requires id, tool and argument object")
            if not isinstance(step["id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", step["id"]) or step["id"] in previous:
                raise ValueError("Step identifiers must be unique names")
            if step["tool"] not in self.allowed_tools:
                raise ValueError("Recipe tool is not an admitted read operation")
            _walk(step["arguments"], previous)
            previous.add(step["id"])
        fingerprint = json.dumps(["save", title, steps, enabled, id, expected_revision], sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, request_id, fingerprint)
            if replay:
                return replay
            old = self._get(db, "recipes", id) if id else None
            if (old["revision"] if old else 0) != expected_revision:
                raise ConflictError("Recipe changed; reload before saving")
            result = dict(id=id or uuid4().hex, title=title, steps=steps, enabled=enabled, revision=expected_revision + 1)
            db.execute("INSERT OR REPLACE INTO recipes VALUES(?,?)", (result["id"], json.dumps(result)))
            db.execute("INSERT INTO versions VALUES(?,?,?)", (result["id"], result["revision"], json.dumps(result)))
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, json.dumps(result)))
            return result

    def restore(self, *, id, revision, expected_revision, request_id):
        old = next((row for row in self.history(id) if row["revision"] == revision), None)
        if old is None:
            raise KeyError("Recipe revision not found")
        return self.save(id=id, title=old["title"], steps=old["steps"], enabled=old["enabled"], expected_revision=expected_revision, request_id=request_id)

    def begin(self, *, recipe_id, revision, request_id):
        if type(revision) is not int or revision < 1:
            raise ValueError("A current recipe revision is required")
        fingerprint = json.dumps(["begin", recipe_id, revision])
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, request_id, fingerprint)
            if replay:
                run = self._get(db, "runs", replay["id"])
                if run.get("session_key") and not self.guarded:
                    raise ValueError("Session-bound runs require the guarded recipe adapter")
                return run
            recipe = self._get(db, "recipes", recipe_id)
            if not recipe["enabled"] or recipe["revision"] != revision:
                raise ConflictError("Recipe is disabled or its revision changed")
            run = dict(id=uuid4().hex, recipe_id=recipe_id, recipe_revision=revision, recipe_snapshot=recipe, next_index=0, steps=[], status="ready", created_at=datetime.now(timezone.utc).isoformat())
            db.execute("INSERT INTO runs VALUES(?,?)", (run["id"], json.dumps(run)))
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_id, fingerprint, json.dumps({"id": run["id"]})))
            return run

    def get_run(self, id):
        with sqlite3.connect(self.path) as db:
            run = self._get(db, "runs", id)
            if run.get("session_key") and not self.guarded:
                raise ValueError("Session-bound runs require the guarded recipe adapter")
            return run

    def list_runs(self):
        with sqlite3.connect(self.path) as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM runs ORDER BY id")]
            return rows if self.guarded else [row for row in rows if not row.get("session_key")]

    def cancel(self, run_id):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._get(db, "runs", run_id)
            if run.get("session_key") and not self.guarded:
                raise ValueError("Session-bound runs require the guarded recipe adapter")
            if run["status"] in ("ready", "running"):
                run["status"] = "cancelled"
                db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run), run_id))
            return run

    async def advance(self, run_id, expected_index, provider):
        if type(expected_index) is not int or expected_index < 0:
            raise ValueError("expected_index must be a nonnegative integer")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._get(db, "runs", run_id)
            if run["status"] != "ready" or expected_index < run["next_index"]:
                return run
            if expected_index != run["next_index"]:
                raise ConflictError("Run step changed; reload before advancing")
            if run.get("session_key") and not self.guarded:
                raise ValueError("Session-bound runs require the guarded recipe adapter")
            if run["recipe_snapshot"]["steps"][run["next_index"]]["tool"] not in self.allowed_tools:
                raise ValueError("Recipe operation is not admitted by this execution adapter")
            current = self._get(db, "recipes", run["recipe_id"])
            if not current["enabled"] or current["revision"] != run["recipe_revision"]:
                run["status"] = "revoked"
                db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run), run_id))
                return run
            run["status"] = "running"
            db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run), run_id))
        step = run["recipe_snapshot"]["steps"][run["next_index"]]
        try:
            outputs = {row["id"]: row["output"] for row in run["steps"]}
            arguments = _walk(step["arguments"], set(outputs), outputs)
            result = await provider.invoke(step["tool"], arguments)
            if not result.success:
                raise ValueError(result.error or "Read operation failed")
            if len(result.output.encode()) > 64000:
                raise ValueError("Read output exceeds recipe limit")
            outcome = {"id": step["id"], "tool": step["tool"], "status": "completed", "output": json.loads(result.output)}
        except Exception as error:
            outcome = {"id": step["id"], "tool": step["tool"], "status": "failed", "error": str(error)[:500]}
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current_run = self._get(db, "runs", run_id)
            current_run["steps"].append(outcome)
            current_run["next_index"] += 1
            if current_run["status"] != "cancelled":
                current_run["status"] = "failed" if outcome["status"] == "failed" else "completed" if current_run["next_index"] == len(run["recipe_snapshot"]["steps"]) else "ready"
            db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(current_run), run_id))
            return current_run
