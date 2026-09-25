"""Timed local exercises with persisted server observations and versioned scoring."""

import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .store import MeasurementError, MeasurementStore, text


def now():
    return datetime.now(timezone.utc)


def challenge(kind, index, issued):
    if kind == "arithmetic":
        left, right = secrets.randbelow(100), secrets.randbelow(100)
        operator = "+" if secrets.randbelow(2) else "-"
        stimulus = dict(left=left, right=right, operator=operator)
        expected = str(left + right if operator == "+" else left - right)
    else:
        colors = ("red", "blue", "green", "yellow")
        stimulus = dict(word=secrets.choice(colors), color=secrets.choice(colors))
        expected = stimulus["color"]
    return dict(
        index=index,
        stimulus=stimulus,
        presented_at=issued.isoformat(),
        expected=expected,
    )


def public(row, at=None):
    result = json.loads(json.dumps(row))
    at = at or now()
    if result["status"] == "active" and at >= datetime.fromisoformat(
        result["deadline"]
    ):
        result["status"] = "expired"
    for trial in result["trials"]:
        trial.pop("expected", None)
    if result["status"] != "active":
        result["current_trial"] = None
    elif result["current_trial"]:
        result["current_trial"].pop("expected", None)
    trials = result["trials"]
    correct = sum(trial["correct"] for trial in trials)
    elapsed = sum(trial["elapsed_ms"] for trial in trials)
    result["score"] = dict(
        answered=len(trials),
        correct=correct,
        accuracy=correct / len(trials) if trials else None,
        mean_elapsed_ms=elapsed / len(trials) if trials else None,
        total_elapsed_ms=elapsed,
    )
    return result


class CognitiveStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS cognitive_sessions(id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision))"
            )

    def _get(self, db, identity):
        row = db.execute(
            "SELECT data FROM cognitive_sessions WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if row is None:
            raise MeasurementError("Exercise session not found", 404, "not_found")
        return json.loads(row[0])

    def _write(self, operation, payload, identity=None):
        fields = (
            {"request_id", "kind", "planned_trials", "time_limit_seconds"}
            if operation == "start"
            else (
                {"request_id", "revision", "answer"}
                if operation == "answer"
                else {"request_id", "revision"}
            )
        )
        if not isinstance(payload, dict) or set(payload) != fields:
            raise MeasurementError(
                "Exercise operation requires its exact declared fields"
            )
        request_id = text(payload["request_id"], "request_id", 128)
        if operation == "start":
            if payload["kind"] not in ("arithmetic", "color_word"):
                raise MeasurementError("Exercise kind must be arithmetic or color_word")
            for field, maximum in [("planned_trials", 20), ("time_limit_seconds", 600)]:
                if (
                    type(payload[field]) is not int
                    or not 1 <= payload[field] <= maximum
                ):
                    raise MeasurementError(
                        f"{field} must be an integer in 1..{maximum}"
                    )
        else:
            if type(payload["revision"]) is not int:
                raise MeasurementError("Exercise revision must be an integer")
            if operation == "answer":
                text(payload["answer"], "answer", 128)
        fingerprint = json.dumps(
            ["cognitive", operation, identity, payload], sort_keys=True
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used", 409, "conflict")
                return json.loads(prior[1])
            observed = now()
            if operation == "start":
                row = {
                    key: payload[key]
                    for key in ("kind", "planned_trials", "time_limit_seconds")
                }
                row.update(
                    id=str(uuid4()),
                    revision=1,
                    source="local_exercise",
                    rules_version=1,
                    started_at=observed.isoformat(),
                    deadline=(
                        observed + timedelta(seconds=row["time_limit_seconds"])
                    ).isoformat(),
                    status="active",
                    trials=[],
                    current_trial=challenge(row["kind"], 1, observed),
                )
            else:
                row = self._get(db, identity)
                if payload["revision"] != row["revision"]:
                    raise MeasurementError("Exercise changed; reload", 409, "conflict")
                if row["status"] != "active":
                    raise MeasurementError(
                        "Exercise is already terminal", 409, "conflict"
                    )
                row["revision"] += 1
                if observed >= datetime.fromisoformat(row["deadline"]):
                    row["status"] = "expired"
                    row["current_trial"] = None
                elif operation == "cancel":
                    row["status"] = "cancelled"
                    row["current_trial"] = None
                else:
                    trial = row["current_trial"]
                    elapsed = (
                        observed - datetime.fromisoformat(trial["presented_at"])
                    ).total_seconds() * 1000
                    if elapsed < 0:
                        raise MeasurementError(
                            "Server clock moved backwards; retry after clock recovery",
                            409,
                            "conflict",
                        )
                    trial.update(
                        answer=payload["answer"],
                        answered_at=observed.isoformat(),
                        correct=payload["answer"].strip().lower() == trial["expected"],
                        elapsed_ms=elapsed,
                    )
                    row["trials"].append(trial)
                    if len(row["trials"]) == row["planned_trials"]:
                        row["status"], row["current_trial"] = "completed", None
                    else:
                        row["current_trial"] = challenge(
                            row["kind"], len(row["trials"]) + 1, observed
                        )
            db.execute(
                "INSERT INTO cognitive_sessions VALUES(?,?,?)",
                (row["id"], row["revision"], json.dumps(row)),
            )
            result = public(row, observed)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(result)),
            )
            return result

    def start(self, payload):
        return self._write("start", payload)

    def answer(self, identity, payload):
        return self._write("answer", payload, identity)

    def cancel(self, identity, payload):
        return self._write("cancel", payload, identity)

    def get(self, identity):
        with self.connection() as db:
            return public(self._get(db, identity))

    def list_sessions(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise MeasurementError("limit must be 1..500")
        with self.connection() as db:
            rows = db.execute(
                "SELECT r.data FROM cognitive_sessions r WHERE revision=(SELECT MAX(s.revision) FROM cognitive_sessions s WHERE s.id=r.id) ORDER BY json_extract(data,'$.started_at') DESC,id LIMIT ?",
                (limit,),
            )
            at = now()
            return [public(json.loads(row[0]), at) for row in rows]
