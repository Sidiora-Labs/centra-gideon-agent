"""Attempt-level JSONL audit trail for the model-call chokepoint (§2.1).

One line per ATTEMPT (not per request) in ``~/.gideon/model_calls.jsonl`` —
so a request that retried once and then fell back writes three lines sharing one
``audit_id``. This is harness mechanics (a file under the config dir), NOT a
memory entry or knowledge item (§7 memory/knowledge boundary): nothing here
writes to ``memory.db`` / ``knowledge.db``.

The file is append-mostly with a trim at 2× the line cap (the ``notifications.jsonl``
pattern the plan cites): each write appends, and when the file crosses ``2 × cap``
lines it is rewritten to the last ``cap`` lines. Trimming at 2× rather than every
write keeps the hot background path append-only in the common case.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.security.guardrails.failure import FailureMode

logger = logging.getLogger(__name__)

_AUDIT_FILENAME = "model_calls.jsonl"
_LINE_CAP = 5000


def _audit_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / _AUDIT_FILENAME


CALLERS: tuple[str, ...] = (
    "conflict_merge",
    "inbox_triage",
    "nl_to_cron",
    "skill_ladder",
    "triage_gate",
    "triage_propose",
)

UNATTRIBUTED = "(unattributed)"

_CURRENT_CALLER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gideon_current_model_caller", default=""
)


def set_current_caller(caller: str):
    """Bind the subsystem model calls are attributed to. Returns a token; ``reset()`` it after.

    Raises ``ValueError`` on a value outside :data:`CALLERS`: this is a WRITE path, and a
    write path must reject an unknown value loudly rather than storing something the read
    path cannot recognise (``routing.policy.set_mode``'s contract). ``""`` is allowed and
    means "unattributed" — clearing a binding is not a typo.

    Token-scoped rather than cleared to ``""`` so a nested pass restores its parent's
    attribution instead of losing it, the contract ``budgets.set_current_run_key`` uses.
    """
    if caller and caller not in CALLERS:
        raise ValueError(
            f"unknown model-call caller {caller!r} (expected one of {CALLERS})"
        )
    return _CURRENT_CALLER.set(caller or "")


def reset_current_caller(token) -> None:
    """Restore the prior caller. NEVER raises — a failed reset must not break a teardown.

    Catches ``Exception`` deliberately: a reused token raises ``RuntimeError``, and this runs
    in a ``finally`` on the model-call path, where anything escaping would replace a real
    provider error with a bookkeeping one (``budgets.reset_current_run_key``'s finding).
    """
    try:
        _CURRENT_CALLER.reset(token)
    except Exception:  # noqa: BLE001 - see the docstring
        _CURRENT_CALLER.set("")


def current_caller() -> str:
    """The bound caller, or ``""`` when a call is not inside an attributed pass."""
    return _CURRENT_CALLER.get() or ""


@contextlib.contextmanager
def caller_scope(caller: str):
    """Attribute every guarded model call inside this block to ``caller``.

    The seam a subsystem uses. Sync (not async) on purpose: a ContextVar set inside a
    coroutine is visible to everything it awaits, so one ``with`` wraps an ``await`` fine —
    while a task created OUTSIDE the block keeps its own copied context, which is the
    correct answer for a pass that forks work it does not own.
    """
    token = set_current_caller(caller)
    try:
        yield
    finally:
        reset_current_caller(token)


@dataclass
class AttemptRecord:
    """One attempt's audit row. Field order is the on-disk column order."""

    audit_id: str
    ts: float
    use_case: str
    provider: str
    model: str
    attempt: int
    failure_mode: str = FailureMode.NONE.value
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    dollars_est: float = 0.0
    estimated: bool = False
    passed: bool = False
    strategy: str = "direct"
    degraded: bool = False
    query_class: str = ""
    routed: bool = False
    routed_fallback: bool = False
    caller: str = ""
    extra: dict = field(default_factory=dict)

    def to_json_line(self) -> str:
        d = asdict(self)
        extra = d.pop("extra", None) or {}
        d.update(extra)
        return json.dumps(d, separators=(",", ":"), default=str)


def now_ms() -> float:
    """Monotonic-ish millisecond clock for latency spans (wall clock for ``ts``)."""
    return time.monotonic() * 1000.0


def record_attempt(rec: AttemptRecord) -> None:
    """Append one attempt row, trimming the file when it crosses 2× the cap.

    Best-effort and never raises: an audit-write failure must not break a model
    call (the call is the product; the audit is observability). Failures log at
    WARNING so a broken trail is diagnosable rather than silent.
    """
    try:
        if rec.caller and rec.caller not in CALLERS:
            logger.warning(
                "model-call audit: unknown caller %r (expected one of %s) — "
                "recording this attempt unattributed",
                rec.caller,
                CALLERS,
            )
            rec = replace(rec, caller="")
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = rec.to_json_line() + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _maybe_trim(path)
    except Exception:
        logger.warning("model-call audit write failed", exc_info=True)


def _maybe_trim(path: Path) -> None:
    """Rewrite ``path`` to its last ``_LINE_CAP`` lines once it exceeds 2× the cap."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) <= 2 * _LINE_CAP:
        return
    kept = lines[-_LINE_CAP:]
    atomic_write(path, "".join(kept))


def read_recent(limit: int = 1000) -> list[dict]:
    """Return up to ``limit`` most-recent attempt rows (oldest→newest), parsed.

    Powers the derived health view (§2.5). Malformed lines are skipped, not
    fatal — a partially-written tail must not blank the whole panel.
    """
    path = _audit_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    rows: list[dict] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows
