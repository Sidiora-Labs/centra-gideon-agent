"""The per-loop FILE store — everything under ``config_dir()/loop/<id>/``.

PP-16 seam 4b: ``loop/store.py`` used to be TWO stores sharing one module — the SQLite
``loops`` row and this per-loop file dir (ledger events, worker findings, guidance,
questions, nudges, verdicts, plan session, stop sentinel). The 2026-08-27 measurement
put the file half at ~48 functions with 19 consuming modules, 7 of them touching no row
at all — so the split is along the real seam: this module owns the FILES, ``store.py``
owns the ROW, and the redacted views (which compose both) stay with the row.

Two deliberate couplings, inherited from the measurement and kept visible:

* :func:`reap_orphan_dirs` uses the ROW store's ``list_all`` as its GC oracle (a file
  dir with no backing row is an orphan). The import is function-local — the only
  files→row edge, and it points that way because the row is authoritative.
* ``loops.db`` lives under the same root this module owns (``_loops_root``), because
  the root IS the durability artifact (durability/inventory.py, snapshot, merge) —
  splitting the code must not move a byte on disk.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gideon.assurance.ledger import EVENTS_FILE, JUDGE_VERDICT, STEP_COMPLETED
from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.security.security import redact_credentials, redact_exfiltration_urls


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


if TYPE_CHECKING:
    from gideon.automation.loop.loop import LoopStatus

logger = logging.getLogger(__name__)

_LOOP_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STOP_SENTINEL = "STOP"


def _loops_root() -> Path:
    return config_dir() / "loop"


def valid_loop_id(loop_id: str) -> bool:
    return bool(_LOOP_ID_RE.match(loop_id or ""))


def loop_dir(loop_id: str) -> Path | None:
    """The loop's file dir (created), or None if the id is invalid. Re-resolves
    under the root + confirms containment so a crafted id can't escape."""
    if not valid_loop_id(loop_id):
        return None
    root = _loops_root().resolve()
    d = (root / loop_id).resolve()
    if not d.is_relative_to(root):
        return None
    d.mkdir(parents=True, exist_ok=True)
    (d / "findings").mkdir(exist_ok=True)
    return d


def safe_loop_dir(loop_id: str) -> Path | None:
    """Read-only variant — never creates."""
    if not valid_loop_id(loop_id):
        return None
    root = _loops_root().resolve()
    d = (root / loop_id).resolve()
    if not d.is_relative_to(root) or not d.exists():
        return None
    return d


def _ledger_filename(node_path: str) -> str:
    import hashlib

    return hashlib.sha256(node_path.encode("utf-8")).hexdigest()[:16] + ".json"


def append_jsonl(loop_id: str, filename: str, record: dict[str, Any]) -> None:
    """Append to the loop's journal or event log. Plain append: append-only by contract."""
    d = loop_dir(loop_id)
    if d is None:
        return
    path = d / filename
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(loop_id: str, filename: str) -> list[dict[str, Any]]:
    """Read an append-only log, skipping a half-written final line (expected after a crash)."""
    d = safe_loop_dir(loop_id)
    path = d / filename if d else None
    if not path or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            logger.debug("loop %s: skipping corrupt line in %s", loop_id, filename)
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def write_output(loop_id: str, node_path: str, output: Any) -> str:
    """Persist an inline ledger output under `loop/<id>/outputs/`. Returns the loop-relative ref."""
    d = loop_dir(loop_id)
    if d is None:
        return ""
    rel = f"outputs/{_ledger_filename(node_path)}"
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps(
            {"node_path": node_path, "output": output}, indent=2, ensure_ascii=False
        ),
    )
    return rel


def write_artifact(loop_id: str, node_path: str, output: Any) -> str:
    """Persist an OFFLOADED ledger output under `loop/<id>/artifacts/` (the spill path)."""
    d = loop_dir(loop_id)
    if d is None:
        return ""
    rel = f"artifacts/{_ledger_filename(node_path)}"
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps(
            {"node_path": node_path, "output": output}, indent=2, ensure_ascii=False
        ),
    )
    return rel


def _redact_str(s: str) -> str:
    cleaned, _ = redact_credentials(s)
    cleaned, _ = redact_exfiltration_urls(cleaned)
    return cleaned


def _redact_value(val: Any) -> Any:
    if isinstance(val, str):
        return _redact_str(val)
    if isinstance(val, list):
        return [_redact_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    return val


def redact_finding(finding: dict) -> dict:
    """Redact credentials + exfiltration URLs from a worker-authored finding.
    Tolerates a non-dict (returns {}) so one malformed file can't poison a list."""
    if not isinstance(finding, dict):
        return {}
    return {k: _redact_value(v) for k, v in finding.items()}


def write_status(loop_id: str, status: LoopStatus, **extra: Any) -> None:
    """Mirror status to status.json — the cycle gate the worker reads each turn.
    Atomic with the DB write's intent (the SQLite row is authoritative)."""
    d = loop_dir(loop_id)
    if d is None:
        return
    payload = {"status": status.value, "ts": time.time(), **extra}
    atomic_write(d / "status.json", json.dumps(payload, indent=2))


def write_brief(loop_id: str, text: str) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "brief.md").write_text(text)


def write_guidance(loop_id: str, text: str) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "guidance.txt").write_text(text)


def read_guidance(loop_id: str) -> str:
    d = safe_loop_dir(loop_id)
    f = d / "guidance.txt" if d else None
    return f.read_text() if f and f.exists() else ""


def clear_guidance(loop_id: str) -> bool:
    d = safe_loop_dir(loop_id)
    f = d / "guidance.txt" if d else None
    if f and f.exists():
        f.unlink()
        return True
    return False


def valid_task_guidance_id(task_id: str) -> bool:
    return bool(_TASK_ID_RE.match(task_id or ""))


def _task_guidance_name(task_id: str) -> str | None:
    return f"guidance_{task_id}.txt" if valid_task_guidance_id(task_id) else None


def write_task_guidance(loop_id: str, task_id: str, text: str) -> None:
    name = _task_guidance_name(task_id)
    d = loop_dir(loop_id)
    if d is not None and name:
        (d / name).write_text(text)


def read_task_guidance(loop_id: str, task_id: str) -> str:
    name = _task_guidance_name(task_id)
    d = safe_loop_dir(loop_id)
    f = d / name if (d and name) else None
    return f.read_text() if f and f.exists() else ""


def clear_task_guidance(loop_id: str, task_id: str) -> None:
    name = _task_guidance_name(task_id)
    d = safe_loop_dir(loop_id)
    f = d / name if (d and name) else None
    if f and f.exists():
        f.unlink()


def stop_sentinel_path(loop_id: str) -> Path | None:
    d = loop_dir(loop_id)
    return (d / STOP_SENTINEL) if d is not None else None


def write_stop_sentinel(loop_id: str) -> None:
    p = stop_sentinel_path(loop_id)
    if p is not None:
        p.write_text("stop")


def clear_stop_sentinel(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    p = d / STOP_SENTINEL if d else None
    if p and p.exists():
        p.unlink()


def _read_raw_finding_files(loop_id: str) -> list[dict]:
    """The worker's per-cycle deliverable files, in ledger order (cycle_* by index, then task_*
    by mtime), redacted and with `task_id` resolved from the filename. INGEST-ONLY — the reader
    the public projections serve is the ledger, populated once by :func:`record_cycle_findings`;
    this raw scan is how a not-yet-ledgered file gets there. `_source_file` keys the idempotency.
    """
    d = safe_loop_dir(loop_id)
    if d is None:
        return []
    fdir = d / "findings"
    if not fdir.exists():
        return []

    def _cycle_idx(p: Path) -> tuple[int, str]:
        m = re.search(r"(\d+)", p.stem)
        return (int(m.group(1)) if m else 0, p.name)

    cycle_files = sorted(fdir.glob("cycle_*.json"), key=_cycle_idx)
    task_files = sorted(fdir.glob("task_*.json"), key=lambda p: p.stat().st_mtime)
    out: list[dict] = []
    for f in [*cycle_files, *task_files]:
        try:
            parsed = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(parsed, dict):
            continue
        finding = redact_finding(parsed)
        raw_tid = finding.get("task_id")
        if isinstance(raw_tid, str):
            finding["task_id"] = raw_tid.strip() or None
        elif raw_tid is not None:
            finding["task_id"] = None
        if not finding.get("task_id"):
            name = f.name
            if name.startswith("task_") and "_" in name[5:]:
                tid = name[5:].rsplit("_", 1)[0]
                if tid:
                    finding["task_id"] = tid
        finding["_source_file"] = f.name
        out.append(finding)
    return out


def record_cycle_findings(loop_id: str) -> int:
    """Ingest any worker finding files not yet on the ledger, as `step_started`/`step_completed`.

    The ONE write of a cycle's finding into the durable store (PP-5): the worker authors the file,
    this turns it into ledger events, and every reader projects from the ledger. Idempotent — keyed
    by the source filename, so the watchdog calling it each poll (and across restarts) never
    double-emits. Returns how many new findings were ledgered.
    """
    from gideon.automation.loop import store
    from gideon.automation.loop.journal import LoopJournal

    raw = _read_raw_finding_files(loop_id)
    if not raw:
        return 0
    already = {
        str(e.get("source_file") or "")
        for e in read_jsonl(loop_id, EVENTS_FILE)
        if e.get("kind") == STEP_COMPLETED
    }
    loop = store.get(loop_id)
    stages = loop.plan if loop else []

    def _normalize_stage_label(label: str) -> str:
        label = re.sub(
            r"^\s*(?:stage\s*)?\d+\s*/\s*\d+\s*[.–—:)\-]?\s*", "", label, flags=re.I
        )
        label = re.sub(r"^\s*(?:stage\s*)?\d+\s*[.–—:)\-]\s*", "", label, flags=re.I)
        return label.strip().casefold()

    def _canonical_stage(raw_label: str) -> str | None:
        for stage in stages:
            key = str(stage.get("stage") or stage.get("title") or "")
            if raw_label in (
                str(stage.get("stage") or ""),
                str(stage.get("title") or ""),
            ):
                return key
        normalized = _normalize_stage_label(raw_label)
        for stage in stages:
            key = str(stage.get("stage") or stage.get("title") or "")
            if normalized in {
                _normalize_stage_label(str(stage.get("stage") or "")),
                _normalize_stage_label(str(stage.get("title") or "")),
            } - {""}:
                return key
        return None

    journal = LoopJournal.open(loop_id)
    filed = 0
    for finding in raw:
        src = str(finding.get("_source_file") or "")
        if src and src in already:
            continue
        raw_stage = finding.get("stage")
        if isinstance(raw_stage, str):
            canonical_stage = _canonical_stage(raw_stage)
            if canonical_stage is not None:
                finding["stage_label"] = raw_stage
                finding["stage"] = canonical_stage
        cycle_val = finding.get("cycle")
        try:
            cycle = int(cycle_val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            cycle = len(already) + filed + 1
        journal.cycle(cycle, finding)
        filed += 1
    return filed


def record_breaker_trip(loop_id: str, cycle: int, reason: str) -> None:
    """A stall → a `breaker_trip` ledger event (PP-5)."""
    from gideon.automation.loop.journal import LoopJournal

    LoopJournal.open(loop_id).breaker_trip(cycle, reason)


def record_watcher_reaped(loop_id: str, *, cycles: int, reason: str) -> None:
    """A reap → a `watcher_reaped` ledger event (PP-5): a running watcher cut off early."""
    from gideon.automation.loop.journal import LoopJournal

    LoopJournal.open(loop_id).watcher_reaped(cycles=cycles, reason=reason)


def get_findings(loop_id: str) -> list[dict]:
    """A loop's findings, PROJECTED off the ledger (PP-5) — the `step_completed` events'
    carried finding payloads, in emit order. The old findings/ file glob is gone as a store; the
    worker's files are an ingest source, not the reader's second store."""
    out: list[dict] = []
    for rec in read_jsonl(loop_id, EVENTS_FILE):
        if rec.get("kind") != STEP_COMPLETED:
            continue
        finding = rec.get("finding")
        if isinstance(finding, dict):
            out.append({k: v for k, v in finding.items() if k != "_source_file"})
    return out


def cycles_completed(loop_id: str) -> int:
    """How many cycles this loop has completed — the ledger's `step_completed` count (PP-5).

    Replaces the retired `loops.total_cycles` column (PP-16 seam 4a): the column was a stored
    copy of exactly this number, so every reader now asks the projection. Delegates to
    :func:`gideon.automation.loop.journal.cycles_completed`, which routes through the ledger's own
    aggregate — the same indirection `record_cycle_findings`/`record_breaker_trip` use, and for
    the same reason (one vocabulary, one place it is defined).

    Callers that already hold :func:`get_findings` should use ``len(findings)`` instead of paying
    a second scan: the two are equal by construction and a rail pins that.
    """
    from gideon.automation.loop.journal import cycles_completed as _count

    return _count(loop_id)


def task_finding_count(loop_id: str, task_id: str) -> int:
    if not valid_task_guidance_id(task_id):
        return 0
    return sum(
        1
        for rec in read_jsonl(loop_id, EVENTS_FILE)
        if rec.get("kind") == STEP_COMPLETED
        and str(rec.get("task_id") or "") == task_id
    )


def write_question(loop_id: str, question: str, **extra: Any) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "questions.json").write_text(
            json.dumps({"question": question, "ts": time.time(), **extra}, indent=2)
        )


def pending_question(loop_id: str) -> dict | None:
    d = safe_loop_dir(loop_id)
    f = d / "questions.json" if d else None
    if not f or not f.exists():
        return None
    try:
        q = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(q, dict):
        return None
    for k in ("question", "why"):
        if isinstance(q.get(k), str):
            q[k] = _redact_str(q[k])
    return q


def clear_question(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    f = d / "questions.json" if d else None
    if f and f.exists():
        f.unlink()


def append_nudge(loop_id: str, text: str, sent_at_cycle: int) -> None:
    d = loop_dir(loop_id)
    if d is None:
        return
    p = d / "nudges.json"
    try:
        log = json.loads(p.read_text()) if p.exists() else []
        if not isinstance(log, list):
            log = []
    except (json.JSONDecodeError, OSError):
        log = []
    log.append(
        {
            "text": text,
            "sent_at": time.time(),
            "sent_at_cycle": sent_at_cycle,
            "applied_cycle": None,
        }
    )
    atomic_write(p, json.dumps(log, indent=2))


def mark_nudges_applied(loop_id: str, cycle: int) -> None:
    d = safe_loop_dir(loop_id)
    p = d / "nudges.json" if d else None
    if not p or not p.exists():
        return
    try:
        log = json.loads(p.read_text())
        if not isinstance(log, list):
            return
    except (json.JSONDecodeError, OSError):
        return
    changed = False
    for n in log:
        if (
            isinstance(n, dict)
            and n.get("applied_cycle") is None
            and int(n.get("sent_at_cycle", 0)) < cycle
        ):
            n["applied_cycle"] = cycle
            changed = True
    if changed:
        atomic_write(p, json.dumps(log, indent=2))


def get_nudges(loop_id: str) -> list[dict]:
    d = safe_loop_dir(loop_id)
    p = d / "nudges.json" if d else None
    if not p or not p.exists():
        return []
    try:
        log = json.loads(p.read_text())
        return [_redact_value(n) for n in log] if isinstance(log, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def write_verdict(loop_id: str, cycle: int, verdict: dict) -> None:
    from gideon.automation.loop.journal import LoopJournal

    LoopJournal.open(loop_id).verdict({"cycle": cycle, **verdict})


def get_verdicts(loop_id: str) -> list[dict]:
    """Projected off the ledger — the `judge_verdict` events' payloads (PP-5), in emit order."""
    from gideon.automation.loop.journal import strip_meta

    return [
        strip_meta(rec)
        for rec in read_jsonl(loop_id, EVENTS_FILE)
        if rec.get("kind") == JUDGE_VERDICT
    ]


_PLAN_SESSION_FILE = "plan_session.json"


def read_plan_session(loop_id: str):
    from gideon.cognition.planning.session import PlanSession

    d = safe_loop_dir(loop_id)
    f = d / _PLAN_SESSION_FILE if d else None
    if not f or not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return PlanSession.from_dict(data)


def write_plan_session(session) -> None:
    d = loop_dir(session.project_id)
    if d is not None:
        atomic_write(d / _PLAN_SESSION_FILE, json.dumps(session.to_dict(), indent=2))


def clear_plan_session(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    f = d / _PLAN_SESSION_FILE if d else None
    if f and f.exists():
        f.unlink()


def reap_orphan_dirs() -> int:
    """Delete per-loop file dirs under the loops root that have NO backing DB row
    (an interrupted delete, a dev DB reset, a failed draft insert that still made the
    dir). Only ``valid_loop_id``-shaped entries are touched, so ``loops.db`` + any
    sidecar is never at risk. Per-entry guarded: one bad entry (permission error,
    broken symlink) is logged + skipped rather than aborting the whole sweep + leaking
    the rest. Runs once at boot."""
    root = _loops_root()
    if not root.is_dir():
        return 0
    from gideon.automation.loop import store as _row_store

    try:
        ids = {r.id for r in _row_store.list_all()}
    except Exception:
        return 0
    reaped = 0
    import shutil

    for child in root.iterdir():
        try:
            if not child.is_dir() or not valid_loop_id(child.name) or child.name in ids:
                continue
            shutil.rmtree(child, ignore_errors=True)
            reaped += 1
            logger.info("loop: reaped orphan dir %s (no DB row)", child.name)
        except Exception:
            logger.debug("loop: failed to reap orphan dir %s", child, exc_info=True)
    return reaped
