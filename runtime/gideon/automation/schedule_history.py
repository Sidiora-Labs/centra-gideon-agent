"""Execution journals for automation details and the recent activity index."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_SUMMARY_CAP = 200
_TRACE_CAP = 50_000
_MAX_RECORDS_PER_JOB = 100
_MAX_INDEX_RECORDS = 2_000
_MAX_SUPPRESSED_PER_JOB = _MAX_RECORDS_PER_JOB // 4
_MAX_INDEX_PER_JOB = _MAX_RECORDS_PER_JOB
_HISTORY_DIRNAME = "cron-history"
_INDEX_NAME = "_index.jsonl"
_LOCK_NAME = ".history.lock"
_LIST_FIELDS = (
    "run_id",
    "job_id",
    "trigger",
    "started_at",
    "finished_at",
    "duration_ms",
    "status",
    "summary",
    "error",
)


@dataclass
class ExecutionRecord:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    job_id: str = ""
    trigger: str = "scheduled"
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: int = 0
    status: str = "success"
    summary: str = ""
    trace: str = ""
    error: str = ""

    def to_dict(self, *, include_trace: bool = True) -> dict[str, Any]:
        fields = (*_LIST_FIELDS, "trace") if include_trace else _LIST_FIELDS
        return {name: getattr(self, name) for name in fields}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionRecord:
        record = cls()
        for name in (*_LIST_FIELDS, "trace"):
            fallback = getattr(record, name)
            value = d.get(name, fallback)
            if name == "duration_ms":
                converted = int(value or 0)
            elif name in {"started_at", "finished_at"}:
                converted = float(value or 0.0)
            else:
                converted = str(value)
            if name == "run_id" and not converted:
                converted = fallback
            setattr(record, name, converted)
        return record


def _redact_stored(text: str | None) -> str:
    if not text:
        return ""
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        without_urls = redact_exfiltration_urls(str(text))[0]
        return redact_credentials(without_urls)[0]
    except Exception:
        return "[redaction failed; text withheld]"


def _inert(record: dict[str, Any]) -> bool:
    from gideon.automation.triggers.models import INERT_OUTCOMES

    return str(record.get("status") or "") in INERT_OUTCOMES


def _retain_job(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suppressed_budget = min(sum(map(_inert, records)), _MAX_SUPPRESSED_PER_JOB)
    budgets = {True: suppressed_budget, False: _MAX_RECORDS_PER_JOB - suppressed_budget}
    retained = []
    for record in reversed(records):
        category = _inert(record)
        if budgets[category] > 0:
            retained.append(record)
            budgets[category] -= 1
    retained.reverse()
    return retained


def _retain_index(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usage: Counter[str] = Counter()
    retained = []
    for record in reversed(records):
        owner = str(record.get("job_id") or "")
        if usage[owner] >= _MAX_INDEX_PER_JOB:
            continue
        retained.append(record)
        usage[owner] += 1
        if len(retained) == _MAX_INDEX_RECORDS:
            break
    retained.reverse()
    return retained


class ExecutionJournal:
    def __init__(self, base_dir: Path) -> None:
        self._dir = Path(base_dir).joinpath(_HISTORY_DIRNAME)
        self._index = self._dir.joinpath(_INDEX_NAME)

    def _job_path(self, job_id: str) -> Path:
        resolved = self._dir.joinpath(f"{job_id}.jsonl").resolve()
        if resolved.parent == self._dir.resolve():
            return resolved
        raise ValueError(f"unsafe job_id for history path: {job_id!r}")

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._dir.joinpath(_LOCK_NAME).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _records(path: Path) -> list[dict[str, Any]]:
        loaded = []
        try:
            with path.open(encoding="utf-8") as source:
                for line in source:
                    if line.strip():
                        try:
                            loaded.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except FileNotFoundError:
            return []
        except OSError:
            logger.debug("Could not read execution journal %s", path, exc_info=True)
            return []
        return loaded

    @staticmethod
    def _rewrite(path: Path, records: list[dict[str, Any]]) -> None:
        atomic_write(
            path,
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n" for record in records
            ),
            mode=0o600,
        )

    @staticmethod
    def _append_record(path: Path, record: dict[str, Any]) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            try:
                os.fchmod(descriptor, 0o600)
            except OSError:
                pass
            encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise OSError("Execution journal append made no progress")
                remaining = remaining[written:]
        finally:
            os.close(descriptor)

    def _record_execution(self, run: ExecutionRecord) -> None:
        for field_name, limit in (
            ("summary", _SUMMARY_CAP),
            ("trace", _TRACE_CAP),
            ("error", None),
        ):
            value = _redact_stored(getattr(run, field_name))
            setattr(run, field_name, value if limit is None else value[:limit])
        location = self._job_path(run.job_id)
        with self._exclusive():
            self._append_record(location, run.to_dict())
            self._append_record(self._index, run.to_dict(include_trace=False))
            self._compact_job(run.job_id)
            self._compact_index()

    async def append(self, run: ExecutionRecord) -> None:
        await asyncio.to_thread(self._record_execution, run)

    @staticmethod
    def _page(
        records: list[dict[str, Any]], offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        return list(reversed(records))[offset : offset + limit], len(records)

    def _job_page(
        self, job_id: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        page, total = self._page(self._records(self._job_path(job_id)), offset, limit)
        return [
            {key: value for key, value in record.items() if key != "trace"}
            for record in page
        ], total

    async def list_for_job(
        self, job_id: str, offset: int = 0, limit: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        return await asyncio.to_thread(self._job_page, job_id, offset, limit)

    def _window_count(self, job_id: str, since: float, manual: bool) -> int:
        counted = 0
        for record in self._records(self._job_path(job_id)):
            try:
                timestamp = float(record.get("started_at") or 0.0)
            except (TypeError, ValueError):
                continue
            if timestamp < since or _inert(record):
                continue
            if manual or str(record.get("trigger") or "") != "manual":
                counted += 1
        return counted

    async def count_since(
        self, job_id: str, since: float, *, manual: bool = False
    ) -> int:
        return await asyncio.to_thread(self._window_count, job_id, since, manual)

    def _index_page(
        self, offset: int, limit: int, job_id: str | None
    ) -> tuple[list[dict[str, Any]], int]:
        entries = self._records(self._index)
        if job_id:
            entries = [entry for entry in entries if entry.get("job_id") == job_id]
        return self._page(entries, offset, limit)

    async def list_all(
        self, offset: int = 0, limit: int = 20, job_id: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return await asyncio.to_thread(self._index_page, offset, limit, job_id)

    def _find(self, job_id: str, run_id: str) -> dict[str, Any] | None:
        return next(
            (
                entry
                for entry in self._records(self._job_path(job_id))
                if entry.get("run_id") == run_id
            ),
            None,
        )

    async def get_run(self, job_id: str, run_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._find, job_id, run_id)

    def _compact_job(self, job_id: str) -> None:
        location = self._job_path(job_id)
        entries = self._records(location)
        if len(entries) > _MAX_RECORDS_PER_JOB:
            self._rewrite(location, _retain_job(entries))

    def _compact_index(self) -> None:
        entries = self._records(self._index)
        if len(entries) > _MAX_INDEX_RECORDS:
            self._rewrite(self._index, _retain_index(entries))

    def _compact_history(self) -> None:
        if not self._dir.exists():
            return
        with self._exclusive():
            jobs = (
                path.stem
                for path in self._dir.glob("*.jsonl")
                if path.name != _INDEX_NAME
            )
            for job_id in jobs:
                self._compact_job(job_id)
            self._compact_index()

    async def rotate_all(self) -> None:
        await asyncio.to_thread(self._compact_history)

    def _remove_job(self, job_id: str) -> None:
        with self._exclusive():
            location = self._job_path(job_id)
            try:
                location.unlink(missing_ok=True)
            except OSError:
                logger.debug(
                    "Could not remove execution journal %s", location, exc_info=True
                )
            remaining = [
                entry
                for entry in self._records(self._index)
                if entry.get("job_id") != job_id
            ]
            self._rewrite(self._index, remaining)

    async def delete_for_job(self, job_id: str) -> None:
        await asyncio.to_thread(self._remove_job, job_id)
