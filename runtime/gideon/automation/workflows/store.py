"""Workflow row persistence and run-owned documents."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gideon.automation.workflows.models import NodeInstance, RunStatus, WorkflowRun
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)


def config_dir() -> Path:
    return config_loader.config_dir()


def workflows_dir() -> Path:
    return config_dir() / "workflows"


def runs_root() -> Path:
    return workflows_dir() / "runs"


def run_dir(run_id: str) -> Path:
    return runs_root() / run_id


def _db_path() -> Path:
    return workflows_dir() / "runs.db"


def new_run_id() -> str:
    return secrets.token_hex(4)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_COLUMNS = (
    "id",
    "workflow_name",
    "status",
    "spec_version",
    "inputs",
    "intent",
    "origin",
    "parent_run_id",
    "root_run_id",
    "spawned_by_node_id",
    "branch_key",
    "forked_from",
    "project_id",
    "mode",
    "budget",
    "pinned",
    "created_at",
    "started_at",
    "completed_at",
    "elapsed_seconds",
    "total_tokens",
    "agent_count",
    "error_message",
    "attention",
    "policy_overrides",
    "owner_username",
    "origin_harness",
    "extra",
)

_JSON_COLUMNS = frozenset(
    {
        "inputs",
        "origin",
        "forked_from",
        "budget",
        "attention",
        "policy_overrides",
        "extra",
    }
)

_RUN_SCHEMA = "CREATE TABLE IF NOT EXISTS runs (\n            id TEXT PRIMARY KEY,\n            workflow_name TEXT NOT NULL,\n            status TEXT NOT NULL DEFAULT 'draft',\n            spec_version INTEGER NOT NULL DEFAULT 1,\n            inputs TEXT NOT NULL DEFAULT '{}',\n            intent TEXT NOT NULL DEFAULT '',\n            origin TEXT NOT NULL DEFAULT '{}',\n            parent_run_id TEXT,\n            root_run_id TEXT NOT NULL DEFAULT '',\n            spawned_by_node_id TEXT,\n            branch_key TEXT,\n            forked_from TEXT,\n            project_id TEXT NOT NULL DEFAULT '',\n            mode TEXT NOT NULL DEFAULT 'background',\n            budget TEXT NOT NULL DEFAULT '{}',\n            pinned INTEGER NOT NULL DEFAULT 0,\n            created_at TEXT NOT NULL DEFAULT '',\n            started_at TEXT,\n            completed_at TEXT,\n            elapsed_seconds REAL NOT NULL DEFAULT 0,\n            total_tokens INTEGER NOT NULL DEFAULT 0,\n            agent_count INTEGER NOT NULL DEFAULT 0,\n            error_message TEXT NOT NULL DEFAULT '',\n            attention TEXT,\n            policy_overrides TEXT NOT NULL DEFAULT '{}',\n            owner_username TEXT NOT NULL DEFAULT '',\n            origin_harness TEXT NOT NULL DEFAULT '',\n            extra TEXT NOT NULL DEFAULT '{}'\n        )"


def _ensure_columns(conn: sqlite3.Connection, cols: dict[str, str]) -> None:
    try:
        found = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    except sqlite3.DatabaseError:
        return
    pending = (
        (name, declaration) for name, declaration in cols.items() if name not in found
    )
    for name, declaration in pending:
        try:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
        except sqlite3.DatabaseError:
            logger.debug("could not add column %s", name, exc_info=True)


def _connect() -> sqlite3.Connection:
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(str(_db_path()), timeout=5.0)
    database.row_factory = sqlite3.Row
    try:
        for pragma in ("journal_mode=WAL", "busy_timeout=5000"):
            database.execute(f"PRAGMA {pragma}")
    except sqlite3.DatabaseError:
        logger.debug("could not set WAL/busy_timeout pragmas", exc_info=True)
    database.execute(_RUN_SCHEMA)
    _ensure_columns(
        database,
        {
            "policy_overrides": "TEXT NOT NULL DEFAULT '{}'",
            "owner_username": "TEXT NOT NULL DEFAULT ''",
            "origin_harness": "TEXT NOT NULL DEFAULT ''",
        },
    )
    for name, columns in (
        ("idx_runs_root_status", "root_run_id, status"),
        ("idx_runs_name_created", "workflow_name, created_at"),
        ("idx_runs_status", "status"),
    ):
        database.execute(f"CREATE INDEX IF NOT EXISTS {name} ON runs({columns})")
    database.commit()
    return database


@contextmanager
def _connection(*, write: bool = False):
    database = _connect()
    try:
        yield database
        if write:
            database.commit()
    finally:
        database.close()


class RunRowCodec:
    @staticmethod
    def decode_cell(row: sqlite3.Row, name: str) -> Any:
        value = row[name]
        if name not in _JSON_COLUMNS:
            return bool(value) if name == "pinned" else value
        fallback = None if name in ("forked_from", "attention") else {}
        if value in (None, ""):
            return fallback
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            logger.warning("run %s: corrupt %s cell", row["id"], name)
            return fallback

    @classmethod
    def decode(cls, row: sqlite3.Row) -> WorkflowRun:
        document = {name: cls.decode_cell(row, name) for name in _COLUMNS}
        extension = document.pop("extra") or {}
        result = WorkflowRun.from_dict(document)
        result.extra = extension if isinstance(extension, dict) else {}
        return result

    @staticmethod
    def encode(run: WorkflowRun) -> dict[str, Any]:
        document = run.to_dict()

        def cell(name: str) -> Any:
            if name == "extra":
                return json.dumps(run.extra or {})
            value = document.get(name)
            if name == "pinned":
                return int(bool(value))
            if name in _JSON_COLUMNS and value is not None:
                return json.dumps(value)
            return value

        return {name: cell(name) for name in _COLUMNS}


def _row_to_run(row: sqlite3.Row) -> WorkflowRun:
    return RunRowCodec.decode(row)


def _run_to_params(run: WorkflowRun) -> dict[str, Any]:
    return RunRowCodec.encode(run)


class RunTable:
    @staticmethod
    def insert(database: sqlite3.Connection, run: WorkflowRun) -> None:
        fields = ", ".join(_COLUMNS)
        values = ", ".join(":" + name for name in _COLUMNS)
        database.execute(
            f"INSERT INTO runs ({fields}) VALUES ({values})", _run_to_params(run)
        )

    @staticmethod
    def stamp(run: WorkflowRun) -> None:
        for name, factory in (
            ("id", new_run_id),
            ("root_run_id", lambda: run.id),
            ("created_at", _now),
        ):
            if not getattr(run, name):
                setattr(run, name, factory())
        if not run.owner_username:
            from gideon.cognition.identity import current_username

            run.owner_username = current_username()
        if not run.origin_harness:
            from gideon.operations.durability.shards import machine_id

            run.origin_harness = machine_id(config_dir())

    @staticmethod
    def rows(clause: str = "", values=(), *, tail: str = "") -> list[WorkflowRun]:
        with _connection() as database:
            records = database.execute(
                "SELECT * FROM runs" + clause + tail, values
            ).fetchall()
        return list(map(_row_to_run, records))

    @staticmethod
    def change(statement: str, values) -> int:
        with _connection(write=True) as database:
            affected = database.execute(statement, values).rowcount
        return affected


def create(run: WorkflowRun) -> WorkflowRun:
    RunTable.stamp(run)
    with _connection(write=True) as database:
        RunTable.insert(database, run)
    run_dir(run.id).mkdir(parents=True, exist_ok=True)
    return run


def get(run_id: str) -> WorkflowRun | None:
    with _connection() as database:
        record = database.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return None if record is None else _row_to_run(record)


def save(run: WorkflowRun) -> WorkflowRun:
    with _connection(write=True) as database:
        changes = ", ".join(name + " = :" + name for name in _COLUMNS if name != "id")
        written = database.execute(
            f"UPDATE runs SET {changes} WHERE id = :id", _run_to_params(run)
        )
        if not written.rowcount:
            RunTable.insert(database, run)
    return run


def list_runs(
    *,
    workflow_name: str = "",
    status: str | RunStatus = "",
    root_run_id: str = "",
    project_id: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[WorkflowRun], int]:
    criteria = [
        (name, value)
        for name, value in (
            ("workflow_name", workflow_name),
            ("project_id", project_id),
            ("status", status),
            ("root_run_id", root_run_id),
        )
        if value
    ]
    bindings = [
        (
            (value.value if isinstance(value, RunStatus) else str(value))
            if name == "status"
            else value
        )
        for name, value in criteria
    ]
    clause = (
        " WHERE " + " AND ".join(name + " = ?" for name, _ in criteria)
        if criteria
        else ""
    )
    with _connection() as database:
        count = database.execute(
            "SELECT COUNT(*) FROM runs" + clause, bindings
        ).fetchone()[0]
        selected = database.execute(
            "SELECT * FROM runs"
            + clause
            + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*bindings, max(1, limit), max(0, offset)],
        ).fetchall()
    return list(map(_row_to_run, selected)), int(count)


def active_runs() -> list[WorkflowRun]:
    return RunTable.rows(
        " WHERE status IN (?, ?, ?)",
        tuple(
            state.value
            for state in (
                RunStatus.RUNNING,
                RunStatus.PAUSED,
                RunStatus.NEEDS_INPUT,
            )
        ),
    )


def delete(run_id: str) -> bool:
    return RunTable.change("DELETE FROM runs WHERE id = ?", (run_id,)) > 0


def count_for_def(workflow_name: str) -> int:
    with _connection() as database:
        (value,) = database.execute(
            "SELECT COUNT(*) FROM runs WHERE workflow_name = ?", (workflow_name,)
        ).fetchone()
    return int(value)


def set_policy_overrides(run_id: str, overrides: dict[str, Any]) -> WorkflowRun | None:
    from gideon.automation.workflows.supervisor_policy import OVERRIDABLE_POLICY_KEYS

    rejected = set(overrides).difference(OVERRIDABLE_POLICY_KEYS)
    if rejected:
        raise ValueError(
            f"unknown policy override key(s) {sorted(rejected)} — "
            f"the overridable set is {sorted(OVERRIDABLE_POLICY_KEYS)}"
        )
    with _connection(write=True) as database:
        changed = database.execute(
            "UPDATE runs SET policy_overrides = ? WHERE id = ?",
            (json.dumps(dict(overrides)), run_id),
        ).rowcount
    return get(run_id) if changed else None


class RunDocuments:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.root = run_dir(run_id)

    def write(self, relative: str, payload: Any) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, json.dumps(payload, indent=2, ensure_ascii=False))
        return target

    def read(self, relative: str, default=None) -> Any:
        target = self.root / relative
        if target.is_file():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning(
                    "run %s: unreadable %s", self.run_id, relative, exc_info=True
                )
        return default

    def output_ref(self, folder: str, node_path: str) -> str:
        return folder + "/" + _output_filename(node_path)

    def write_body(self, folder: str, node_path: str, output: Any) -> str:
        reference = self.output_ref(folder, node_path)
        self.write(reference, {"node_path": node_path, "output": output})
        return reference

    def body_location(self, node_path: str) -> Path | None:
        return next(
            (
                path
                for folder in ("outputs", "artifacts")
                if (path := self.root / self.output_ref(folder, node_path)).is_file()
            ),
            None,
        )

    def read_body(self, target: Path, *, label: str, subject: str) -> Any:
        if not target.is_file():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8")).get("output")
        except (OSError, ValueError):
            logger.warning(
                "run %s: unreadable %s %s", self.run_id, label, subject, exc_info=True
            )
            return None

    def artifact(self, reference: str) -> Any:
        if not reference:
            return None
        root = self.root.resolve()
        artifact_root = (root / "artifacts").resolve()
        try:
            target = (root / reference).resolve()
            target.relative_to(artifact_root)
        except (ValueError, OSError):
            return None
        return self.read_body(target, label="artifact", subject=reference)

    def archive(self, node_path: str, version: int) -> str:
        source = self.body_location(node_path)
        if source is None:
            return ""
        reference = self.output_ref(f"outputs/attic/v{version:03d}", node_path)
        destination = self.root / reference
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.replace(destination)
        except OSError:
            logger.warning(
                "run %s: could not archive output for %s", self.run_id, node_path
            )
            return ""
        return reference

    def append(self, filename: str, record: dict[str, Any]) -> None:
        target = self.root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def records(self, filename: str) -> list[dict[str, Any]]:
        target = self.root / filename
        if not target.is_file():
            return []
        entries = []
        lines = map(
            str.strip, target.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        for line in filter(None, lines):
            try:
                entry = json.loads(line)
            except ValueError:
                logger.debug(
                    "run %s: skipping corrupt line in %s", self.run_id, filename
                )
            else:
                if isinstance(entry, dict):
                    entries.append(entry)
        return entries


def write_spec(run_id: str, spec: dict[str, Any]) -> Path:
    return RunDocuments(run_id).write("spec.json", spec)


def read_spec(run_id: str) -> dict[str, Any] | None:
    return RunDocuments(run_id).read("spec.json")


def write_spec_history(run_id: str, version: int, record: dict[str, Any]) -> Path:
    return RunDocuments(run_id).write(f"spec_history/v{version:03d}.json", record)


def write_state(run_id: str, instances: dict[str, NodeInstance]) -> Path:
    records = dict((path, instance.to_dict()) for path, instance in instances.items())
    return RunDocuments(run_id).write("state.json", {"instances": records})


def read_state(run_id: str) -> dict[str, NodeInstance]:
    missing = object()
    document = RunDocuments(run_id).read("state.json", missing)
    if document is missing:
        return {}

    def restore(path, record):
        instance = NodeInstance.from_dict(record)
        instance.path = instance.path or str(path)
        return str(path), instance

    return dict(
        restore(path, record)
        for path, record in (document.get("instances") or {}).items()
    )


def _output_filename(node_path: str) -> str:
    return hashlib.sha256(node_path.encode("utf-8")).hexdigest()[:16] + ".json"


def write_output(run_id: str, node_path: str, output: Any) -> str:
    return RunDocuments(run_id).write_body("outputs", node_path, output)


def write_artifact(run_id: str, node_path: str, output: Any) -> str:
    return RunDocuments(run_id).write_body("artifacts", node_path, output)


def read_artifact(run_id: str, ref: str) -> Any:
    return RunDocuments(run_id).artifact(ref)


def archive_output(run_id: str, node_path: str, version: int) -> str:
    return RunDocuments(run_id).archive(node_path, version)


def read_output(run_id: str, node_path: str) -> Any:
    documents = RunDocuments(run_id)
    target = documents.body_location(node_path)
    return (
        None
        if target is None
        else documents.read_body(target, label="output for", subject=node_path)
    )


def append_jsonl(run_id: str, filename: str, record: dict[str, Any]) -> None:
    RunDocuments(run_id).append(filename, record)


def read_jsonl(run_id: str, filename: str) -> list[dict[str, Any]]:
    return RunDocuments(run_id).records(filename)


def request_cancel(run_id: str) -> None:
    marker = run_dir(run_id) / "CANCEL"
    marker.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(marker, _now())


def cancel_requested(run_id: str) -> bool:
    return (run_dir(run_id) / "CANCEL").is_file()


def clear_cancel(run_id: str) -> None:
    (run_dir(run_id) / "CANCEL").unlink(missing_ok=True)
