"""Spend metering + budget ceilings for unattended work (AUTONOMY-GUARDRAILS §1.1).

The §2 model-call chokepoint is where metering becomes possible — every attempt
carries a token count and a dollar estimate, so the ``SpendMeter`` can fold them
into per-scope counters. A ceiling that bites pauses the run into needs-input
rather than silently overspending.

Two scopes matter for a personal gateway:

* ``run`` — one unattended run (a goal-loop cycle, a cron fire, a subagent). The
  counter is in-memory, keyed by a caller-supplied run key, and reset when the run
  ends. It stops a single runaway from burning a whole day's budget in one go.
* ``day`` — all unattended spend for a calendar day, persisted to
  ``~/.gideon/spend.json`` (atomic_write, pruned >30 days) so it survives a
  restart. It is the real cost guardrail.

Dollar estimates reuse ``pricing.estimate_cost`` (provider-reported cost preferred
by the caller; this is the heuristic fallback, flagged ``estimated``). Budgets
compare against the conservative (higher) estimate — a token ceiling and a dollar
ceiling both apply, either can bite.

This is harness mechanics: ``spend.json`` is a file under the config dir, NOT a
memory entry or knowledge item (§7 boundary).
"""

from __future__ import annotations

import contextvars
import itertools
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from gideon.core import spend_day
from gideon.core.atomic_write import atomic_write
from gideon.security.guardrails.failure import (
    NO_ROOM,
    SPENT,
    UNMEASURED,
    UNPRICED,
    BudgetExceededError,
)

logger = logging.getLogger(__name__)

_SPEND_FILENAME = "spend.json"
_PRUNE_DAYS = 30


@dataclass(frozen=True)
class Budget:
    """A spend ceiling. Zero in any dimension means UNLIMITED for that dimension."""

    max_tokens: int = 0
    max_dollars: float = 0.0

    @property
    def is_unlimited(self) -> bool:
        return self.max_tokens <= 0 and self.max_dollars <= 0.0


class BudgetVerdict(str, Enum):
    """The meter's verdict after folding a charge into a scope's running total."""

    OK = "ok"
    WARN = "warn"
    EXCEEDED = "exceeded"


_WARN_FRACTION = 0.8


def _today_key() -> str:
    """The calendar-day key for the day-scope counter (local date, ISO)."""
    return spend_day.today()


@dataclass
class _ScopeTotal:
    tokens: int = 0
    dollars: float = 0.0
    #: Calls charged with no price: their dollars are unknown, so ``dollars`` leaves them out.
    unpriced: int = 0


def unpriced_clause(count: int) -> str:
    """What a dollar total set against a ceiling leaves out, as a person reads it, or ``""``."""
    if count <= 0:
        return ""
    calls = "1 call" if count == 1 else f"{count} calls"
    return f"not counting {calls} that had no price"


#: How long an answer a call is taken to give before any call to its model has finished today:
#: the default answer allowance before a model has supplied measured usage.
ANSWER_TOKENS_BEFORE_FIRST_CALL = 4096


def prompt_tokens(chars: int) -> int:
    """A request of *chars* characters, in tokens, for what a call sets aside before it starts.

    Over-estimated rather than under (``token_estimate.CONSERVATIVE_CHARS_PER_TOKEN``): a ceiling
    that sets aside too little is one calls running together can pass.
    """
    from gideon.core.token_estimate import CONSERVATIVE_CHARS_PER_TOKEN

    return math.ceil(max(0, int(chars or 0)) / CONSERVATIVE_CHARS_PER_TOKEN)


@dataclass(frozen=True)
class CallCost:
    """A call about to be made, as the ceilings weigh it before it starts
    (:meth:`SpendMeter.admit`).

    ``ref`` is the ``provider:model`` it goes to, the key the meter keeps what calls to it have
    used and cost today under. ``prompt_tokens`` is the size of its request (:func:`prompt_tokens`).
    ``rate`` is what the model costs, USD per 1,000,000 prompt and answer tokens, or ``None`` when
    nothing prices it; ``(0.0, 0.0)`` is a model known to cost nothing.
    """

    ref: str
    prompt_tokens: int = 0
    rate: tuple[float, float] | None = None
    known_dollars: float | None = None
    unit_only: bool = False
    measured: bool = True
    unit: str = ""
    unpriced_for: str = ""

    @property
    def free(self) -> bool:
        if self.unit_only:
            return self.known_dollars == 0.0
        return self.rate is not None and self.rate[0] <= 0.0 and self.rate[1] <= 0.0


@dataclass(frozen=True)
class Hold:
    """What one admitted call has set aside against the ceilings, until it settles or fails.

    ``answer_tokens`` is the answer it was taken to give, which is what it is taken to have given
    when its provider reports no usage."""

    id: int
    ref: str
    run_key: str
    tokens: int
    dollars: float
    answer_tokens: int
    unit_only: bool = False


@dataclass(frozen=True)
class Waiting:
    """The call must wait: the calls running now hold room it may need, or a call to its model
    whose cost nothing knows yet is running. ``refusal`` is what it gets if it stops waiting.
    """

    refusal: BudgetExceededError


@dataclass
class _Seen:
    """The most one call to a model has used and cost today, of the calls that settled."""

    tokens: int = 0
    answer_tokens: int = 0
    dollars: float = 0.0


#: A cent's millionth: two amounts closer than this are one amount (charges round to 6 places).
_EPSILON = 1e-9


@dataclass(frozen=True)
class _Room:
    """One ceiling, weighed for one call: what is spent against it, what the calls running now
    have set aside, and what this call may use."""

    scope: str
    dimension: str
    limit: float
    spent: float
    held: float
    needed: float
    unpriced: int

    @property
    def slack(self) -> float:
        return self.limit - self.spent - self.held - self.needed

    def fits(self) -> bool:
        return self.slack >= -_EPSILON

    def fits_alone(self) -> bool:
        return self.limit - self.spent - self.needed >= -_EPSILON

    def refusal(self, why: str, ref: str) -> BudgetExceededError:
        return BudgetExceededError(
            self.scope,
            self.dimension,
            self.limit,
            self.spent,
            unpriced=self.unpriced,
            why=why,
            needed=self.needed,
            held=self.held,
            ref=ref,
        )


class SpendMeter:
    """Folds per-attempt spend into run- and day-scope counters, verdicts them against a
    :class:`Budget`, and admits a call before it is made (:meth:`admit`).

    Thread-safety: the day-scope counter is persisted and may be touched from the
    gateway loop + a subagent thread, so mutations take a lock. Run-scope counters,
    what the calls running now have set aside and what calls to each model have cost
    today are in-memory.
    """

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._lock = threading.Lock()
        self._run_totals: dict[str, _ScopeTotal] = {}
        self._holds: dict[int, Hold] = {}
        self._hold_ids = itertools.count(1)
        self._seen: dict[str, _Seen] = {}
        self._seen_day = ""

    # ── Paths / persistence ─────────────────────────────────────────────

    def _spend_path(self) -> Path:
        base = self._config_dir
        if base is None:
            from gideon.core.config.loader import resolve_config_dir

            base = resolve_config_dir()
        return base / _SPEND_FILENAME

    def _load_day(self) -> dict:
        path = self._spend_path()
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_day(self, data: dict, today: str) -> None:
        # Prune days older than the retention window before writing.
        try:
            cutoff = (
                datetime.strptime(today, spend_day.DAY_FORMAT).toordinal() - _PRUNE_DAYS
            )

            def _keep(day_key: str) -> bool:
                ordinal = _ordinal_of(day_key)
                # Unparseable keys are kept (never silently dropped by the pruner).
                return ordinal is None or ordinal >= cutoff

            pruned = {day: v for day, v in data.items() if _keep(day)}
        except Exception:
            pruned = data
        try:
            atomic_write(self._spend_path(), json.dumps(pruned, separators=(",", ":")))
        except Exception:
            logger.warning("spend.json write failed", exc_info=True)

    # ── Recording spend ─────────────────────────────────────────────────

    def charge(
        self,
        tokens: int,
        dollars: float,
        *,
        run_key: str | None = None,
        unpriced: int = 0,
    ) -> None:
        """Record ``tokens`` + ``dollars`` of spend against the day scope (always)
        and a run scope (when ``run_key`` is given). Best-effort; never raises.

        ``unpriced`` is how many of the calls this charge covers nothing priced
        (``routing.rates.CallPrice``): one call a guard charges, or the attempts a whole batch
        made. Their dollars are unknown, so ``dollars`` holds only what the priced ones cost, and
        they are counted as calls the figure leaves out rather than as free spend, even when they
        reported no tokens. Their tokens count as any call's do."""
        today = _today_key()
        with self._lock:
            self._charge_locked(tokens, dollars, run_key, unpriced, today)

    def _charge_locked(
        self,
        tokens: int,
        dollars: float,
        run_key: str | None,
        unpriced: int,
        today: str,
    ) -> None:
        tokens = max(0, int(tokens or 0))
        dollars = max(0.0, float(dollars or 0.0))
        unpriced = max(0, int(unpriced or 0))
        if tokens == 0 and dollars == 0.0 and not unpriced:
            return
        # Day scope (persisted).
        data = self._load_day()
        existing = data.get(today)
        prev = existing if isinstance(existing, dict) else {}
        data[today] = {
            "tokens": int(prev.get("tokens", 0)) + tokens,
            "dollars": round(float(prev.get("dollars", 0.0)) + dollars, 6),
            "unpriced": int(prev.get("unpriced", 0) or 0) + unpriced,
        }
        self._save_day(data, today)
        # Run scope (in-memory).
        if run_key:
            rt = self._run_totals.setdefault(run_key, _ScopeTotal())
            rt.tokens += tokens
            rt.dollars += dollars
            rt.unpriced += unpriced

    # ── Admitting a call before it is made ───────────────────────────────

    def admit(
        self,
        cost: CallCost,
        *,
        day: Budget,
        run: Budget | None = None,
        run_key: str = "",
    ) -> "Hold | Waiting | BudgetExceededError | None":
        """Weigh a call about to be made against the day's ceilings and its run's (*run*, the
        ceiling of the run *run_key* names).

        Answers what it set aside (a :class:`Hold`, or ``None`` when no ceiling applies to it),
        :class:`Waiting` when the calls running now hold room it may need, or the refusal:
        a ceiling already reached (``spent``), one it cannot fit in even alone (``no_room``), or
        a dollar ceiling set against a model nothing prices (``unpriced``). A dollar ceiling does
        not weigh a call to a model known to cost nothing at all.
        """
        run = run if (run is not None and run_key) else Budget()
        today = _today_key()
        with self._lock:
            seen = None if cost.unit_only else self._seen_today(today).get(cost.ref)
            answer = (
                seen.answer_tokens
                if seen is not None
                else ANSWER_TOKENS_BEFORE_FIRST_CALL
            )
            tokens = max(0, int(cost.prompt_tokens)) + answer
            dollars: float | None = None
            if cost.rate is not None:
                dollars = (
                    cost.prompt_tokens * cost.rate[0] + answer * cost.rate[1]
                ) / 1_000_000
            if seen is not None:
                tokens = max(tokens, seen.tokens)
                if dollars is not None:
                    dollars = max(dollars, seen.dollars)
            if cost.unit_only:
                answer = tokens = 0
                dollars = cost.known_dollars
                if dollars is not None and (not math.isfinite(dollars) or dollars < 0):
                    raise ValueError("Unit cost must be finite and non-negative")
            dollars = None if dollars is None else round(dollars, 6)

            rooms: list[_Room] = []
            day_total = self._day_total_locked(today)
            scopes = [("day", day, day_total, list(self._holds.values()))]
            if run_key:
                run_total = self._run_totals.get(run_key, _ScopeTotal())
                in_run = [h for h in self._holds.values() if h.run_key == run_key]
                scopes.append(("run", run, run_total, in_run))
            for scope, budget, total, holds in scopes:
                if budget.max_tokens > 0 and not cost.unit_only:
                    rooms.append(
                        _Room(
                            scope,
                            "tokens",
                            float(budget.max_tokens),
                            float(total.tokens),
                            float(sum(h.tokens for h in holds)),
                            float(tokens),
                            0,
                        )
                    )
                if budget.max_dollars > 0.0 and not cost.free:
                    if dollars is None:
                        return BudgetExceededError(
                            scope,
                            "dollars",
                            float(budget.max_dollars),
                            float(total.dollars),
                            unpriced=total.unpriced,
                            why=UNPRICED if cost.measured else UNMEASURED,
                            ref=cost.ref,
                            unit=cost.unit,
                            unpriced_for=cost.unpriced_for,
                        )
                    rooms.append(
                        _Room(
                            scope,
                            "dollars",
                            float(budget.max_dollars),
                            float(total.dollars),
                            round(sum(h.dollars for h in holds), 6),
                            dollars,
                            total.unpriced,
                        )
                    )
            if not rooms:
                return None
            for room in rooms:
                if room.spent >= room.limit - _EPSILON:
                    return room.refusal(SPENT, cost.ref)
            tightest = min(rooms, key=lambda room: room.slack)
            first_call = seen is None and not cost.unit_only
            if first_call and any(h.ref == cost.ref for h in self._holds.values()):
                # Nothing knows yet what a call to this model costs: learn it from the one that is
                # running before a second one runs beside it.
                return Waiting(tightest.refusal(NO_ROOM, cost.ref))
            held = dollars if (dollars is not None and not cost.free) else 0.0
            if all(room.fits() for room in rooms):
                return self._hold_locked(
                    cost.ref, run_key, tokens, held, answer, cost.unit_only
                )
            if first_call:
                # What it may cost is a guess until one call has settled: it runs when nothing
                # else holds room, rather than never while the ceiling is not reached.
                if all(room.held <= 0.0 for room in rooms):
                    return self._hold_locked(
                        cost.ref, run_key, tokens, held, answer, cost.unit_only
                    )
                return Waiting(tightest.refusal(NO_ROOM, cost.ref))
            for room in rooms:
                if not room.fits_alone():
                    return room.refusal(NO_ROOM, cost.ref)
            return Waiting(tightest.refusal(NO_ROOM, cost.ref))

    def _hold_locked(
        self,
        ref: str,
        run_key: str,
        tokens: int,
        dollars: float,
        answer_tokens: int,
        unit_only: bool = False,
    ) -> Hold:
        hold = Hold(
            id=next(self._hold_ids),
            ref=ref,
            run_key=run_key,
            tokens=int(tokens),
            dollars=float(dollars),
            answer_tokens=int(answer_tokens),
            unit_only=unit_only,
        )
        self._holds[hold.id] = hold
        return hold

    def settle(
        self,
        hold: Hold | None,
        *,
        ref: str,
        tokens: int,
        answer_tokens: int,
        dollars: float,
        priced: bool,
        run_key: str | None = None,
        usage_reported: bool = False,
        unit_only: bool = False,
    ) -> None:
        """Replace what a call set aside with what it cost: charge the day, and the run
        *run_key* names, and keep what it used as the most a call to *ref* has today when it is.

        A call whose provider reported no usage at all is charged what it set aside, the most it
        was taken to use: counting it as nothing would be a call the ceiling never saw.
        Never raises."""
        tokens = max(0, int(tokens or 0))
        dollars = max(0.0, float(dollars or 0.0))
        today = _today_key()
        with self._lock:
            held = self._holds.pop(hold.id, None) if hold is not None else None
            if hold is not None and held is None:
                return
            if (
                held is not None
                and tokens == 0
                and dollars == 0.0
                and not usage_reported
            ):
                tokens, answer_tokens, dollars = (
                    held.tokens,
                    held.answer_tokens,
                    held.dollars,
                )
                priced = priced or held.dollars > 0.0
            if (tokens or dollars) and not (
                unit_only or (held is not None and held.unit_only)
            ):
                seen = self._seen_today(today).setdefault(ref, _Seen())
                seen.tokens = max(seen.tokens, tokens)
                seen.answer_tokens = max(
                    seen.answer_tokens, max(0, int(answer_tokens or 0))
                )
                if priced:
                    seen.dollars = max(seen.dollars, dollars)
            try:
                self._charge_locked(tokens, dollars, run_key, 0 if priced else 1, today)
            except Exception:  # noqa: BLE001 - settling never breaks the call it closes
                logger.warning("spend charge failed", exc_info=True)

    def release(self, hold: Hold | None) -> None:
        """Give back what a call set aside, when it failed or was stopped and charged nothing."""
        if hold is None:
            return
        with self._lock:
            self._holds.pop(hold.id, None)

    def held(self) -> tuple[int, float]:
        """The tokens and dollars the calls running now have set aside."""
        with self._lock:
            holds = list(self._holds.values())
        return sum(h.tokens for h in holds), round(sum(h.dollars for h in holds), 6)

    def _seen_today(self, day: str) -> dict[str, _Seen]:
        """What calls to each model have cost today; a new day starts with nothing known."""
        if self._seen_day != day:
            self._seen_day = day
            self._seen = {}
        return self._seen

    def charge_run(self, run_key: str, tokens: int, dollars: float) -> None:
        """Record spend against ``run_key``'s run scope ONLY — spend the day scope has already
        counted (a guarded call charged it where it was made), folded into a run it also
        belongs to. :meth:`charge` there would count those dollars against the day twice.
        Best-effort; never raises."""
        tokens = max(0, int(tokens or 0))
        dollars = max(0.0, float(dollars or 0.0))
        if not run_key or (tokens == 0 and dollars == 0.0):
            return
        with self._lock:
            rt = self._run_totals.setdefault(run_key, _ScopeTotal())
            rt.tokens += tokens
            rt.dollars += dollars

    def end_run(self, run_key: str) -> None:
        """Drop a run's in-memory counter when the run completes."""
        with self._lock:
            self._run_totals.pop(run_key, None)

    # ── Verdicts ─────────────────────────────────────────────────────────

    def day_totals(self) -> _ScopeTotal:
        today = _today_key()
        with self._lock:
            return self._day_total_locked(today)

    def _day_total_locked(self, day: str) -> _ScopeTotal:
        row = self._load_day().get(day, {})
        if not isinstance(row, dict):
            row = {}
        return _ScopeTotal(
            tokens=int(row.get("tokens", 0)),
            dollars=float(row.get("dollars", 0.0)),
            unpriced=int(row.get("unpriced", 0) or 0),
        )

    def run_totals(self, run_key: str) -> _ScopeTotal:
        with self._lock:
            rt = self._run_totals.get(run_key)
            if rt is None:
                return _ScopeTotal()
            return _ScopeTotal(
                tokens=rt.tokens, dollars=rt.dollars, unpriced=rt.unpriced
            )

    def check_day(self, budget: Budget) -> tuple[BudgetVerdict, str]:
        """Verdict the CURRENT day total against ``budget`` (before a new charge).

        Returns (verdict, reason). A caller that gets ``EXCEEDED`` must refuse the
        next unattended LLM call / skip the fire; ``WARN`` is surfaced, not blocking.
        """
        return self._verdict(self.day_totals(), budget, scope="day")

    def check_run(self, run_key: str, budget: Budget) -> tuple[BudgetVerdict, str]:
        """Verdict a run's accumulated total against ``budget``."""
        return self._verdict(self.run_totals(run_key), budget, scope="run")

    def check_day_before_work(self, budget: Budget) -> tuple[BudgetVerdict, str]:
        """The verdict a seam that STARTS unattended work asks (a trigger's fire, a subagent's
        spawn, an app's worker, a proposal's execution, a browse step): the day's TOKEN ceiling.

        Only the token ceiling stops work before it starts, because it counts every call. A
        dollar ceiling limits the calls that cost money, each refused where it is made
        (:meth:`admit`), so it never stops work up front: the work may run on a model that costs
        nothing, or call no model at all.
        """
        return self.check_day(Budget(max_tokens=budget.max_tokens))

    def check_run_before_work(
        self, run_key: str, budget: Budget
    ) -> tuple[BudgetVerdict, str]:
        """:meth:`check_day_before_work` for a run's ceiling: its token dimension only."""
        return self.check_run(run_key, Budget(max_tokens=budget.max_tokens))

    @staticmethod
    def _verdict(
        total: _ScopeTotal, budget: Budget, *, scope: str
    ) -> tuple[BudgetVerdict, str]:
        if budget.is_unlimited:
            return BudgetVerdict.OK, ""
        verdict = BudgetVerdict.OK
        reason = ""
        if budget.max_tokens > 0:
            if total.tokens >= budget.max_tokens:
                return (
                    BudgetVerdict.EXCEEDED,
                    f"{scope} token budget exceeded ({total.tokens}/{budget.max_tokens})",
                )
            if total.tokens >= budget.max_tokens * _WARN_FRACTION:
                verdict, reason = (
                    BudgetVerdict.WARN,
                    f"{scope} token budget at {total.tokens}/{budget.max_tokens}",
                )
        if budget.max_dollars > 0.0:
            # The dollar total leaves out every call that had no price, and says so: a ceiling
            # that holds such calls holds spend it could not count.
            left_out = unpriced_clause(total.unpriced)
            left_out = f", {left_out}" if left_out else ""
            if total.dollars >= budget.max_dollars:
                return (
                    BudgetVerdict.EXCEEDED,
                    f"{scope} dollar budget exceeded "
                    f"(${total.dollars:.4g}/${budget.max_dollars:.4g}{left_out})",
                )
            if total.dollars >= budget.max_dollars * _WARN_FRACTION:
                verdict, reason = (
                    BudgetVerdict.WARN,
                    f"{scope} dollar budget at "
                    f"${total.dollars:.4g}/${budget.max_dollars:.4g}{left_out}",
                )
        return verdict, reason


def _ordinal_of(day_key: str) -> int | None:
    try:
        return datetime.strptime(day_key, "%Y-%m-%d").toordinal()
    except (ValueError, TypeError):
        return None


_METER: SpendMeter | None = None


_CURRENT_RUN_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gideon_current_run_key", default=""
)


def set_current_run_key(run_key: str):
    """Bind the run scope model spend accrues to. Returns the token; reset() it when the run ends.

    Scoped with a token rather than cleared to "", so nested runs (a trigger fire that spawns a
    subagent) restore the parent's scope instead of losing it — the same contract
    `mcp_core.set_current_session_key` uses.
    """
    return _CURRENT_RUN_KEY.set(run_key or "")


def reset_current_run_key(token) -> None:
    """Restore the prior run scope. NEVER raises — a failed reset must not break a run's teardown.

    Catches `Exception`, not a tuple, and that is deliberate: my first version listed
    `(ValueError, LookupError)` and a reused token raises **RuntimeError** ("Token has already been
    used"), which a test caught immediately. This runs in a `finally` on the fire path, so anything
    escaping here would replace a real provider error with a bookkeeping one — the caller would see
    the wrong exception for the wrong reason. Falling back to `set("")` leaves the scope unbound,
    which is the same state a call outside any run has.
    """
    try:
        _CURRENT_RUN_KEY.reset(token)
    except Exception:  # noqa: BLE001 - see the docstring
        _CURRENT_RUN_KEY.set("")


def current_run_key() -> str:
    """The bound run scope, or "" when a call is not inside a tracked run."""
    return _CURRENT_RUN_KEY.get() or ""


_CURRENT_RUN_BUDGET: contextvars.ContextVar[Budget] = contextvars.ContextVar(
    "gideon_current_run_budget", default=Budget()
)


def set_current_run_budget(budget: Budget):
    """Bind a ceiling for the ambient run. Returns the token; reset() it when the run ends.

    Token-scoped like `set_current_run_key` so a nested run restores the parent's ceiling
    rather than losing it.
    """
    return _CURRENT_RUN_BUDGET.set(budget if isinstance(budget, Budget) else Budget())


def reset_current_run_budget(token) -> None:
    """Restore the prior run ceiling. NEVER raises — see `reset_current_run_key`."""
    try:
        _CURRENT_RUN_BUDGET.reset(token)
    except Exception:  # noqa: BLE001 - a failed reset must not break a run's teardown
        _CURRENT_RUN_BUDGET.set(Budget())


def current_run_budget() -> Budget:
    """The ambient run ceiling, or an unlimited Budget when no run bound one."""
    got = _CURRENT_RUN_BUDGET.get()
    return got if isinstance(got, Budget) else Budget()


def get_meter() -> SpendMeter:
    """The shared spend meter for this gateway (lazy-created)."""
    global _METER
    if _METER is None:
        _METER = SpendMeter()
    return _METER


def reset_meter() -> None:
    """Drop the process-global meter — invoked by an autouse test fixture so a
    test's spend/run state never leaks into the next (the SEL/breaker discipline)."""
    global _METER
    _METER = None


def safety_budget_for_inbound() -> Budget:
    """The run ceiling for one inbound-access turn (EXTERNAL-ACCESS §9.5).

    The HEADLESS profile's budget, which ``safety_profile_for`` fills from the operator's
    configured ``max_tokens_per_day`` / ``max_dollars_per_day`` when the profile declares
    none of its own. So an inbound turn is capped by the same numbers the operator already
    set for unattended work — no new knob, and no unlimited-by-omission hole.

    Lives here, next to the ContextVar it feeds, rather than in ``policy``: this is the
    budget-binding seam's helper, and importing it from ``policy`` would point the
    dependency the wrong way (``policy`` already imports ``budgets``).

    Fail-SAFE, not fail-open: an unreadable config leaves ``safety_profile_for``
    returning the base profile, whose budget is unlimited — the same ceiling every
    non-inbound turn has today, so a config fault can never make an inbound turn
    *cheaper to abuse* than the interactive path it sits beside.
    """
    from gideon.security.guardrails.policy import HEADLESS, safety_profile_for

    return safety_profile_for(HEADLESS).budget


def budget_from_config() -> Budget:
    """Build the day-scope :class:`Budget` from the loaded GuardrailsConfig.

    Fail-open to unlimited on any config read failure — a broken config must not
    wedge every unattended run (the ceiling is a guardrail, not a gate; the scan +
    breaker remain the hard controls).
    """
    try:
        from gideon.core.config.loader import AppConfig

        b = AppConfig.load().guardrails.budgets
        return Budget(
            max_tokens=b.max_tokens_per_day, max_dollars=b.max_dollars_per_day
        )
    except Exception:
        logger.debug("budget_from_config: falling back to unlimited", exc_info=True)
        return Budget()


def run_budget_from_config() -> Budget:
    """Build the run-scope :class:`Budget` (tokens only) from GuardrailsConfig."""
    try:
        from gideon.core.config.loader import AppConfig

        b = AppConfig.load().guardrails.budgets
        return Budget(max_tokens=b.max_tokens_per_run)
    except Exception:
        logger.debug("run_budget_from_config: falling back to unlimited", exc_info=True)
        return Budget()
