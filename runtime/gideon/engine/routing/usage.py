"""Usage totals, retained daily history and operator-facing spend projections."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable

from gideon.core import spend_day
from gideon.core.atomic_write import atomic_write
from gideon.engine.routing.rates import resolve_effective_price
from gideon.engine.routing.stats import ref_of

logger = logging.getLogger(__name__)
_USAGE_FILE = "usage_stats.json"
USAGE_VERSION = 1
PURPOSES = ("interactive", "background", "loop", "eval", "app")
PURPOSE_BY_SOURCE = {
    "chat": "interactive",
    "cli": "interactive",
    "channel": "interactive",
    "loop": "loop",
    "cron": "background",
    "subagent": "background",
    "background": "background",
    "system": "background",
    "eval": "eval",
}
APP_PURPOSE = "app"
WINDOW_DAYS = {"day": 1, "week": 7, "month": 30}
GROUPS = ("model", "provider", "purpose")
UNWRITTEN_PURPOSES = frozenset({"eval"})


def _usage_path(home: Path) -> Path:
    return Path(home).joinpath(_USAGE_FILE)


def empty_fold() -> dict[str, Any]:
    return {
        "version": USAGE_VERSION,
        **{
            name: {}
            for name in ("days", "app_sources", "unmapped", "uncounted", "sources")
        },
    }


def load_usage(home: Path) -> dict[str, Any]:
    try:
        document = json.loads(_usage_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        document = None
    if isinstance(document, dict):
        for key, default in empty_fold().items():
            document.setdefault(key, default)
        return document
    return empty_fold()


def save_usage(home: Path, fold: dict[str, Any]) -> None:
    encoded = json.dumps(fold, indent=2, sort_keys=True)
    atomic_write(_usage_path(home), encoded + "\n")


def _empty_cell() -> dict[str, Any]:
    return dict(
        calls=0,
        tokens_in=0,
        tokens_out=0,
        dollars_est=0.0,
        estimated_dollars=0.0,
        estimated_calls=0,
        unpriced_calls=0,
        local_calls=0,
    )


def purpose_for_source(source: str) -> tuple[str, str]:
    name = str(source or "").strip()
    purpose = PURPOSE_BY_SOURCE.get(name)
    return (purpose, "") if purpose is not None else (APP_PURPOSE, name or "(unnamed)")


def reachable_purposes() -> tuple[str, ...]:
    available = set(PURPOSE_BY_SOURCE.values()).difference(UNWRITTEN_PURPOSES)
    available.add(APP_PURPOSE)
    return tuple(filter(available.__contains__, PURPOSES))


@dataclass
class RatePresence:
    home: Path | None
    cache: dict = field(default_factory=dict)

    def __call__(self, provider, model):
        key = provider, model
        if key not in self.cache:
            try:
                rate = resolve_effective_price(provider, model, home=self.home)
            except Exception:
                logger.debug(
                    "rate lookup failed for %s:%s", provider, model, exc_info=True
                )
                rate = None
            self.cache[key] = (
                rate is None or not rate.priced,
                bool(rate is not None and rate.source == "local"),
            )
        return self.cache[key]


def _rate_lookup(home: Path | None) -> Callable[[str, str], tuple[bool, bool]]:
    return RatePresence(home)


def _count(bucket: dict[str, Any], key: str) -> None:
    if key:
        bucket[key] = 1 + int(bucket.get(key, 0))


def _day_from_iso(ts: Any, zone: tzinfo | None = None) -> str:
    return spend_day.day_of(ts, zone)


def _day_from_epoch(ts: Any, zone: tzinfo | None = None) -> str:
    return spend_day.day_of_epoch(ts, zone)


@dataclass
class TurnAccumulator:
    fold: dict
    look: Callable
    zone: tzinfo | None = None

    def accept(self, row):
        date = _day_from_iso(row.get("ts"), self.zone)
        provider, model = (str(row.get(key, "") or "") for key in ("provider", "model"))
        if not date or not (provider or model):
            _count(
                self.fold.setdefault("unmapped", {}),
                "row:no_date" if not date else "row:no_ref",
            )
            return False
        purpose, application = purpose_for_source(row.get("source", ""))
        if application:
            _count(self.fold.setdefault("app_sources", {}), application)
        absent, local = self.look(provider, model)
        if isinstance(row.get("local"), bool):
            local = row["local"]
        elif row.get("price_source"):
            local = row["price_source"] == "local"
        declaration = row.get("priced")
        unpriced = not bool(declaration) if declaration is not None else absent
        dollars = float(row.get("cost_usd", 0.0) or 0.0)
        estimated = bool(row.get("estimated", True))
        models = self.fold.setdefault("days", {}).setdefault(date, {})
        cell = models.setdefault(ref_of(provider, model), {}).setdefault(
            purpose, _empty_cell()
        )
        cell["calls"] = int(cell["calls"]) + 1
        for target, source in (
            ("tokens_in", "input_tokens"),
            ("tokens_out", "output_tokens"),
        ):
            cell[target] = int(cell[target]) + max(0, int(row.get(source, 0) or 0))
        cell["dollars_est"] = round(float(cell["dollars_est"]) + dollars, 6)
        if estimated:
            cell["estimated_dollars"] = round(
                float(cell["estimated_dollars"]) + dollars, 6
            )
            cell["estimated_calls"] = int(cell["estimated_calls"]) + 1
        for key, enabled in (("unpriced_calls", unpriced), ("local_calls", local)):
            if enabled:
                cell[key] = int(cell[key]) + 1
        return True


def fold_turn_row(
    fold: dict[str, Any],
    row: dict[str, Any],
    *,
    look: Callable[[str, str], tuple[bool, bool]] | None = None,
    zone: tzinfo | None = None,
) -> bool:
    return TurnAccumulator(fold, look or _rate_lookup(None), zone).accept(row)


def audit_census(
    rows: list[dict[str, Any]],
    *,
    ledgered_audit_ids: set[str] | frozenset[str] = frozenset(),
    zone: tzinfo | None = None,
) -> dict[str, Any]:
    zone = spend_day.zone() if zone is None else zone
    calls = 0
    dollars_est = 0.0
    by_use_case: dict[str, int] = {}
    days: dict[str, int] = {}
    for row in rows:
        audit_id = str(row.get("audit_id", "") or "").strip()
        if audit_id and audit_id in ledgered_audit_ids:
            continue
        calls += 1
        dollars_est = round(dollars_est + float(row.get("dollars_est", 0.0) or 0.0), 6)
        _count(by_use_case, str(row.get("use_case", "") or "(blank)"))
        _count(days, _day_from_epoch(row.get("ts"), zone))
    return dict(
        calls=calls,
        dollars_est=dollars_est,
        by_use_case=by_use_case,
        days=days,
    )


def _iter_json_lines(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in filter(None, map(str.strip, lines)):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _default_paths(
    audit_path: Path | None, ledger_path: Path | None
) -> tuple[Path, Path]:
    if audit_path is None:
        from gideon.security.guardrails.audit import _audit_path

        audit_path = _audit_path()
    if ledger_path is None:
        from gideon.operations.usage_ledger import _path

        ledger_path = _path()
    return Path(audit_path), Path(ledger_path)


def _dedup_ledger(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out = []
    for row in rows:
        key = next(
            (
                (field, str(row[field]))
                for field in ("audit_id", "import_record_id")
                if row.get(field)
            ),
            None,
        )
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        out.append(row)
    return out


def fold_files(
    *,
    home: Path | None = None,
    audit_path: Path | None = None,
    ledger_path: Path | None = None,
    zone: tzinfo | None = None,
) -> dict[str, Any]:
    zone = spend_day.zone() if zone is None else zone
    audit, ledger = _default_paths(audit_path, ledger_path)
    fold = empty_fold()
    accumulator = TurnAccumulator(fold, _rate_lookup(home), zone)
    ledger_rows = _dedup_ledger(_iter_json_lines(ledger))
    turns = sum(accumulator.accept(row) for row in ledger_rows)
    ledgered_audit_ids = frozenset(
        value
        for row in ledger_rows
        for value in [
            str(row.get("audit_id", "") or "").strip(),
            *[
                str(item).strip()
                for item in (row.get("audit_ids") or [])
                if isinstance(item, str)
            ],
        ]
        if value
    )
    fold.update(
        uncounted=audit_census(
            _iter_json_lines(audit), ledgered_audit_ids=ledgered_audit_ids, zone=zone
        ),
        sources={"usage_ledger": turns},
        calendar_timezone=str(zone),
    )
    return fold


def rebuild(
    home: Path, *, audit_path: Path | None = None, ledger_path: Path | None = None
) -> dict[str, Any]:
    result = fold_files(home=home, audit_path=audit_path, ledger_path=ledger_path)
    save_usage(home, result)
    return result


class RetainedDays:
    @staticmethod
    def choose(prior, fresh):
        if not isinstance(prior, dict):
            return fresh
        if not isinstance(fresh, dict):
            return prior
        return (
            fresh if int(fresh.get("calls", 0)) >= int(prior.get("calls", 0)) else prior
        )

    @classmethod
    def models(cls, prior, fresh):
        result = {}
        for ref in set(prior) | set(fresh):
            old, new = prior.get(ref) or {}, fresh.get(ref) or {}
            result[ref] = {
                purpose: cls.choose(old.get(purpose), new.get(purpose))
                for purpose in set(old) | set(new)
            }
        return result

    @classmethod
    def merge(cls, prior, fresh):
        result = {}
        for date in set(prior) | set(fresh):
            old, new = prior.get(date) or {}, fresh.get(date) or {}
            if isinstance(old, dict) and isinstance(new, dict):
                result[date] = cls.models(old, new)
            else:
                result[date] = new if isinstance(new, dict) else old
        return result


def _merge_days(prior: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    return RetainedDays.merge(prior, fresh)


def _unallocated(prior: dict, reconstructable: dict) -> dict:
    """Subtract only rows whose original instants are still available, in the old calendar.

    Aggregate-only residuals have no instant to redate. Keep them visible without
    adding them to a dated window or counting reconstructed rows twice.
    """
    out = _agg()
    for day, models in (prior.get("days") or {}).items():
        for ref, purposes in models.items():
            for purpose, cell in purposes.items():
                raw = (
                    ((reconstructable.get("days") or {}).get(day) or {}).get(ref) or {}
                ).get(purpose) or {}
                residual = {
                    key: max(
                        0, float(cell.get(key, 0) or 0) - float(raw.get(key, 0) or 0)
                    )
                    for key in out
                }
                _add(out, residual)
    return out


def refresh(
    home: Path, *, audit_path: Path | None = None, ledger_path: Path | None = None
) -> dict[str, Any]:
    prior = load_usage(home)
    zone = spend_day.zone()
    result = fold_files(
        home=home, audit_path=audit_path, ledger_path=ledger_path, zone=zone
    )
    # Before calendar metadata existed this native fold used UTC dates.
    old_zone = str(prior.get("calendar_timezone") or "UTC")
    archives = list(prior.get("calendar_archives") or [])
    unallocated = _agg()
    _add(unallocated, prior.get("unallocated_total") or {})
    if prior.get("days") and old_zone != str(zone):
        from gideon.core.timezones import zone_or_raise

        old_rows = fold_files(
            home=home,
            audit_path=audit_path,
            ledger_path=ledger_path,
            zone=zone_or_raise(old_zone),
        )
        _add(unallocated, _unallocated(prior, old_rows))
        archives.append(
            {
                "timezone": old_zone,
                "days": prior["days"],
                "unallocated_total": prior.get("unallocated_total") or {},
            }
        )
    else:
        result["days"] = _merge_days(prior.get("days") or {}, result.get("days") or {})
    result["calendar_archives"] = archives
    result["unallocated_total"] = unallocated
    save_usage(home, result)
    return result


def _today_utc() -> str:
    return spend_day.today()


def window_dates(window: str, *, today: str = "") -> list[str]:
    count = WINDOW_DAYS.get(window, WINDOW_DAYS["day"])
    try:
        return spend_day.days_ending(today or spend_day.today(), count)
    except ValueError:
        return spend_day.days_ending(spend_day.today(), count)


def _agg() -> dict[str, Any]:
    cell = _empty_cell()
    del cell["estimated_calls"]
    return cell


def _add(into: dict[str, Any], cell: dict[str, Any]) -> None:
    for key in ("calls", "tokens_in", "tokens_out"):
        into[key] += int(cell.get(key, 0) or 0)
    for key in ("dollars_est", "estimated_dollars"):
        into[key] = round(into[key] + float(cell.get(key, 0.0) or 0.0), 6)
    for key in ("unpriced_calls", "local_calls"):
        into[key] += int(cell.get(key, 0) or 0)


def _share(agg: dict[str, Any]) -> float:
    dollars = float(agg.get("dollars_est", 0.0) or 0.0)
    if dollars <= 0:
        return 0.0
    return round(min(1.0, float(agg.get("estimated_dollars", 0.0) or 0.0) / dollars), 4)


def _finish(agg: dict[str, Any], key: str = "") -> dict[str, Any]:
    result = {
        **agg,
        "tokens": agg["tokens_in"] + agg["tokens_out"],
        "estimated_share": _share(agg),
        "priced": agg["unpriced_calls"] == 0,
    }
    if key:
        result["key"] = key
    return result


def _uncounted_in_window(fold: dict[str, Any], dates: list[str]) -> dict[str, Any]:
    census = fold.get("uncounted") or {}
    days = census.get("days") or {}
    return dict(
        calls=sum(int(days.get(date, 0) or 0) for date in dates),
        total_calls=int(census.get("calls", 0) or 0),
        total_dollars_est=float(census.get("dollars_est", 0.0) or 0.0),
        by_use_case=dict(census.get("by_use_case") or {}),
    )


def _day_cells(fold, date):
    day = (fold.get("days") or {}).get(date) or {}
    for ref, purposes in day.items():
        if isinstance(purposes, dict):
            for purpose, cell in purposes.items():
                if isinstance(cell, dict):
                    yield ref, purpose, cell


@dataclass
class UsageWindow:
    fold: dict
    dates: list
    total: dict = field(default_factory=_agg)
    models: dict = field(default_factory=dict)
    purposes: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    series: list = field(default_factory=list)

    def collect(self, group):
        for date in self.dates:
            daily = _agg()
            for ref, purpose, cell in _day_cells(self.fold, date):
                key = (
                    str(purpose)
                    if group == "purpose"
                    else str(ref).split(":", 1)[0] if group == "provider" else str(ref)
                )
                _add(self.groups.setdefault(key, _agg()), cell)
                _add(daily, cell)
                _add(self.total, cell)
                _add(self.models.setdefault(str(ref), _agg()), cell)
                _add(self.purposes.setdefault(str(purpose), _agg()), cell)
            self.series.append(
                dict(
                    date=date,
                    calls=daily["calls"],
                    dollars_est=daily["dollars_est"],
                    tokens=daily["tokens_in"] + daily["tokens_out"],
                )
            )
        return self


def query(
    fold: dict[str, Any], *, window: str = "day", group: str = "model", today: str = ""
) -> dict[str, Any]:
    window = window if window in WINDOW_DAYS else "day"
    group = group if group in GROUPS else "model"
    view = UsageWindow(fold, window_dates(window, today=today)).collect(group)
    rows = [_finish(aggregate, key) for key, aggregate in view.groups.items()]
    rows.sort(key=lambda row: (-float(row["dollars_est"]), str(row["key"])))
    total = _finish(view.total)
    return dict(
        window=window,
        group=group,
        dates=view.dates,
        rows=rows,
        total=total,
        series=view.series,
        estimated_share=total["estimated_share"],
        unmapped=dict(fold.get("unmapped") or {}),
        app_sources=dict(fold.get("app_sources") or {}),
        uncounted=_uncounted_in_window(fold, view.dates),
        reachable_purposes=list(reachable_purposes()),
        calendar_timezone=fold.get("calendar_timezone") or str(spend_day.zone()),
        legacy_total=_finish(fold.get("unallocated_total") or _agg()),
    )


def _usd(value: float) -> str:
    precision = 2 if value >= 1 else 4
    return f"${value:.{precision}f}"


def month_dates(month: str, fold: dict[str, Any]) -> list[str]:
    prefix = str(month or "")[:7]
    return sorted(
        date for date in (fold.get("days") or {}) if str(date).startswith(prefix)
    )


@dataclass(frozen=True)
class MonthlyRecap:
    label: str
    view: UsageWindow

    def lines(self):
        total = self.view.total
        uncounted = _uncounted_in_window(self.view.fold, self.view.dates)
        if total["calls"] == 0:
            yield f"{self.label}: no model turns recorded."
            if uncounted["calls"]:
                yield _uncounted_sentence(uncounted)
            return
        yield f"{self.label}: ~{_usd(total['dollars_est'])} across {total['calls']} turns."
        yield f"{round(100 * total['local_calls'] / total['calls'])}% of those turns ran locally at $0."

        def rank(pair):
            return (-float(pair[1]["dollars_est"]), pair[0])

        if self.view.purposes:
            parts = (
                f"{name} ~{_usd(aggregate['dollars_est'])}"
                for name, aggregate in sorted(self.view.purposes.items(), key=rank)
            )
            yield "By purpose: " + ", ".join(parts) + "."
        if self.view.models:
            ref, aggregate = min(self.view.models.items(), key=rank)
            yield f"Biggest line item: {ref} (~{_usd(aggregate['dollars_est'])})."
        yield "Every dollar here is an estimate, not a provider-reported charge."
        count = total["unpriced_calls"]
        if count:
            yield f"{count} {'turn' if count == 1 else 'turns'} ran on a model with no price row and counted as $0, so the total is a floor."
        if uncounted["calls"]:
            yield _uncounted_sentence(uncounted)


def usage_recap(
    month: str, *, fold: dict[str, Any] | None = None, home: Path | None = None
) -> str:
    if fold is None:
        fold = load_usage(Path(home) if home is not None else Path("."))
    try:
        label = datetime.strptime(str(month)[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        label = str(month)
    view = UsageWindow(fold, month_dates(month, fold)).collect("model")
    return " ".join(MonthlyRecap(label, view).lines())


def _uncounted_sentence(uncounted: dict[str, Any]) -> str:
    count = int(uncounted.get("calls", 0) or 0)
    noun = "call" if count == 1 else "calls"
    return f"Separately, {count} unattended model {noun} were recorded this month but are not included above — they cannot be merged with turns without double-counting loops."
