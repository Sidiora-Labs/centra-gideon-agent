"""Background work owned by the onboarding import surface."""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from typing import Any

from gideon.cognition.onboarding_import.model import ImportItem, ImportReport, ScanResult, WriteResult

logger = logging.getLogger(__name__)
_JOIN_SECONDS = 5.0


class ImportJob:
    def __init__(self, job_id: str, fingerprints: list[str], accepted: Mapping[str, str], *,
                 scan: Callable[..., list[ScanResult]] | None = None,
                 persist: Callable[[dict[str, Any]], None] | None = None,
                 execute: Callable[["ImportJob"], ImportReport] | None = None) -> None:
        self.id = job_id
        self.fingerprints = list(dict.fromkeys(fingerprints))
        self.accepted = dict(accepted)
        self.status = "running"
        self.phase = "scanning"
        self.total = len(self.fingerprints)
        self.done = 0
        self.counts = {"imported": 0, "existing": 0, "conflict": 0, "rejected": 0}
        self.results: list[dict[str, Any]] = []
        self.current = ""
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.error = ""
        self.report: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scan = scan
        self._persist = persist
        self._execute = execute

    @property
    def running(self) -> bool:
        return self.status == "running"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"setup-import-{self.id}", daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(_JOIN_SECONDS)

    def _save(self) -> None:
        if self._persist is None:
            return
        with self._lock:
            state = self.to_dict()
            state.update(fingerprints=self.fingerprints, accepted=self.accepted,
                         results=list(self.results))
        self._persist(state)

    def _stop_before(self, item: ImportItem) -> bool:
        if self._stop.is_set():
            return True
        with self._lock:
            self.current = item.title or item.key
        return False

    def _landed(self, _item: ImportItem, result: WriteResult) -> None:
        with self._lock:
            self.done += 1
            self.counts[result.outcome.value] += 1
            self.results.append(result.to_dict())
        self._save()

    def _run(self) -> None:
        from gideon.cognition.onboarding_import import run_import, scan_all
        from gideon.cognition.onboarding_import.floors import safe_text

        try:
            if self._execute is None:
                scans = (self._scan or scan_all)(look=True)
                self.phase = "importing"
                self._save()
                report = run_import(scans, fingerprints=self.fingerprints, accepted=self.accepted,
                                    on_result=self._landed, stop_before=self._stop_before)
            else:
                self.phase = "scanning"
                self._save()
                report = self._execute(self)
            with self._lock:
                self.report = report.to_dict()
                self.status = "stopped" if report.not_reached else "done"
        except Exception as exc:
            logger.warning("onboarding import: job failed", exc_info=True)
            message, _ = safe_text(str(exc) or type(exc).__name__)
            with self._lock:
                self.status = "failed"
                self.error = message[:200]
                landed = {row.get("fingerprint") for row in self.results}
                self.report = {"counts": dict(self.counts), "results": list(self.results),
                               "secrets_skipped": 0, "redactions": 0, "notes": [],
                               "unselected": [], "missing": [],
                               "not_reached": [fp for fp in self.fingerprints if fp not in landed]}
        finally:
            with self._lock:
                self.phase = "finished"
                self.current = ""
                self.finished_at = time.time()
            try:
                self._save()
            except Exception:
                logger.warning("onboarding import: final job state save failed", exc_info=True)

    def to_dict(self, *, include_report: bool = True) -> dict[str, Any]:
        with self._lock:
            payload = {"id": self.id, "status": self.status, "phase": self.phase,
                       "stopping": self._stop.is_set() and self.running,
                       "total": self.total, "done": self.done, "counts": dict(self.counts),
                       "current": self.current, "started_at": self.started_at,
                       "finished_at": self.finished_at, "error": self.error}
            if include_report:
                payload["report"] = self.report
            return payload


class ImportActivity:
    """One scan reader and one item-atomic import per local owner."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: ImportJob | None = None
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._go = threading.Event()
        self._go.set()
        self._pausers = 0
        self._read = 0
        self._of = 0
        self._ids = itertools.count(1)

    @contextmanager
    def scanning(self):
        with self._lock:
            self._pausers += 1
            self._go.clear()
        try:
            yield
        finally:
            with self._lock:
                self._pausers -= 1
                if self._pausers == 0:
                    self._go.set()

    def _wait_for_scan(self) -> None:
        while not self._go.wait(0.1):
            if self._reader_stop.is_set():
                return

    def read_behind(self, scans: list[ScanResult]) -> None:
        total = sum(len(scan.unread) for scan in scans)
        with self._lock:
            if total == 0 or (self._reader is not None and self._reader.is_alive()):
                return
            if self._job is not None and self._job.running:
                return
            self._read, self._of = 0, total
            self._reader_stop = threading.Event()
            stop = self._reader_stop
            self._reader = threading.Thread(target=self._read_all, args=(scans, stop),
                                            name="onboarding-import-reading", daemon=True)
            self._reader.start()

    def _read_all(self, scans: list[ScanResult], stop: threading.Event) -> None:
        from gideon.cognition.onboarding_import.engine import read_unread

        def tick() -> None:
            with self._lock:
                self._read += 1

        try:
            read_unread(scans, stop=stop.is_set, on_read=tick, wait=self._wait_for_scan)
        except Exception:
            logger.warning("onboarding import: background transcript scan failed", exc_info=True)

    def start_import(self, fingerprints: list[str], accepted: Mapping[str, str], *,
                     scan: Callable[..., list[ScanResult]] | None = None,
                     persist: Callable[[dict[str, Any]], None] | None = None) -> tuple[ImportJob, bool]:
        with self._lock:
            if self._job is not None and self._job.running:
                return self._job, False
            self._reader_stop.set()
            job = ImportJob(f"import-{next(self._ids)}", fingerprints, accepted,
                            scan=scan, persist=persist)
            self._job = job
        job.start()
        return job, True

    @property
    def job(self) -> ImportJob | None:
        with self._lock:
            return self._job

    def status(self) -> dict[str, Any]:
        with self._lock:
            reader = self._reader
            read, of = self._read, self._of
            job = self._job
        return {"reading": {"running": reader is not None and reader.is_alive(),
                             "read": read, "of": of},
                "job": job.to_dict(include_report=False) if job is not None else None}

    def shutdown(self) -> None:
        self._reader_stop.set()
        reader = self._reader
        if reader is not None:
            reader.join(_JOIN_SECONDS)
        job = self.job
        if job is not None:
            job.stop(wait=True)
