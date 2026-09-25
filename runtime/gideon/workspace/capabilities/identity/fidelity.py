"""Source-linked literal fidelity checks with explicit observation provenance."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from gideon.assurance.eval.scenario import Assertion, AssertionType
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.twin import TwinStore


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must contain 1..{limit} characters")


class FidelityStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, body TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, fingerprint TEXT, body TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS case_requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL);")

    def _sources(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            raise ValueError("source_ids must contain 1..10 unique local document identifiers")
        twin = TwinStore(self.path.parent / "twin.sqlite3").snapshot()
        available = {doc["id"]: doc for doc in twin["documents"] if doc["enabled"] and not doc["private"]}
        if any(identifier not in available for identifier in ids):
            raise ValueError("Sources must be enabled, non-private local identity documents")
        sources = [{key: available[identifier][key] for key in ("id", "title", "text")} for identifier in ids]
        if len(json.dumps(sources).encode()) > 16000:
            raise ValueError("Selected sources exceed the evaluation context limit")
        return twin["revision"], sources

    @staticmethod
    def _get(db, table, identifier):
        row = db.execute(f"SELECT body FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Fidelity record not found")
        return json.loads(row[0])

    def save_case(self, *, prompt, source_ids, rules, category="behavioral", id=None, expected_revision=0, request_id=None):
        _text(prompt, "prompt", 4000)
        self._sources(source_ids)
        if category not in ("behavioral", "values", "boundary", "conversation"):
            raise ValueError("Unknown evaluation category")
        if not isinstance(rules, list) or not 1 <= len(rules) <= 20:
            raise ValueError("rules must contain 1..20 explicit literal expectations")
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {"type", "value", "case_sensitive"}:
                raise ValueError("Unknown rule fields")
            if rule.get("type") not in ("equals", "contains", "not_contains"):
                raise ValueError("Only equals, contains and not_contains rules are supported")
            _text(rule.get("value"), "rule value", 1000)
            if type(rule.get("case_sensitive", False)) is not bool:
                raise ValueError("case_sensitive must be boolean")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        if id is None:
            _text(request_id, "request_id", 128)
        fingerprint = json.dumps([prompt, source_ids, rules, category], sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if id is None:
                prior = db.execute("SELECT fingerprint,body FROM case_requests WHERE id=?", (request_id,)).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise ConflictError("request_id already names a different case")
                    return json.loads(prior[1])
            old = self._get(db, "cases", id) if id else None
            if (old["revision"] if old else 0) != expected_revision:
                raise ConflictError("Case changed; reload before saving")
            case = {"id": id or uuid4().hex, "revision": expected_revision + 1, "prompt": prompt,
                    "source_ids": source_ids, "rules": rules, "category": category}
            db.execute("INSERT OR REPLACE INTO cases VALUES (?,?)", (case["id"], json.dumps(case)))
            if id is None:
                db.execute("INSERT INTO case_requests VALUES (?,?,?)", (request_id, fingerprint, json.dumps(case)))
            return case

    def get_case(self, id):
        with sqlite3.connect(self.path) as db:
            return self._get(db, "cases", id)

    def list_cases(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM cases ORDER BY id")]

    def get_run(self, id):
        with sqlite3.connect(self.path) as db:
            return self._get(db, "runs", id)

    def list_runs(self):
        with sqlite3.connect(self.path) as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM runs")]
            return sorted(rows, key=lambda row: (row["created_at"], row["id"]))

    def _begin(self, case_id, request_id, origin, answer=None):
        _text(request_id, "request_id", 128)
        fingerprint = json.dumps([case_id, origin, answer])
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT fingerprint,body FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError("request_id already names a different evaluation")
                existing = json.loads(prior[1])
                if existing["status"] == "running":
                    raise ConflictError("Evaluation is already running; inspect its recorded state")
                return existing
            case = self._get(db, "cases", case_id)
            revision, sources = self._sources(case["source_ids"])
            run = {"id": uuid4().hex, "request_id": request_id, "case_id": case_id, "case_revision": case["revision"],
                   "case_snapshot": case, "source_snapshot": sources, "twin_revision": revision,
                   "origin": origin, "status": "running", "answer": None, "results": [], "passed": None,
                   "provider": None, "model": None, "created_at": datetime.now(timezone.utc).isoformat()}
            if answer is not None:
                self._score(run, answer)
            db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run["id"], request_id, fingerprint, json.dumps(run)))
            return run

    def begin_run(self, *, case_id, request_id):
        return self._begin(case_id, request_id, "provider")

    def record_observation(self, *, case_id, answer, request_id):
        _text(answer, "answer", 100000)
        return self._begin(case_id, request_id, "supplied_observation", answer)

    @staticmethod
    def _score(run, answer):
        _text(answer, "answer", 100000)
        results = [{"rule": rule, "passed": Assertion(type=AssertionType(rule["type"]), value=rule["value"],
                    case_sensitive=rule.get("case_sensitive", False)).check(answer)} for rule in run["case_snapshot"]["rules"]]
        run.update(answer=answer, results=results, passed=all(row["passed"] for row in results), status="completed")

    def complete_run(self, id, answer, provider=None, model=None):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._get(db, "runs", id)
            if run["status"] != "running":
                raise ConflictError("Evaluation already settled")
            self._score(run, answer)
            run.update(provider=provider, model=model)
            db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run), id))
            return run

    def fail_run(self, id, error):
        _text(error, "error", 1000)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._get(db, "runs", id)
            if run["status"] != "running":
                raise ConflictError("Evaluation already settled")
            run.update(status="unavailable", error=error)
            db.execute("UPDATE runs SET body=? WHERE id=?", (json.dumps(run), id))
            return run
