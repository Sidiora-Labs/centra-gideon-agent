"""Per-turn accounting journal, event projection and bounded query views."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_CAP = 50_000
_GROUP_KEYS = ("model", "source", "agent", "provider", "day")
_SESSION_KEY_SEP = "-"
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


@dataclass
class TurnUsage:
    ts: str
    session_key: str
    source: str
    agent: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = True
    duration_ms: int = 0
    context_pct: float | None = None


def _path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath("usage", "turns.jsonl")


@dataclass(frozen=True)
class UsageJournal:
    path: Path

    def append(self, usage):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(usage), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        _maybe_trim(self.path)

    def rows(self):
        if not self.path.is_file():
            return []
        result = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    result.append(row)
        except OSError:
            logger.debug("usage ledger read failed", exc_info=True)
        return result

    def trim(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2 * _CAP:
                retained = lines[-_CAP:]
                atomic_write(self.path, "\n".join(retained) + "\n")
        except OSError:
            logger.debug("usage ledger trim failed", exc_info=True)


def record_turn(u: TurnUsage) -> None:
    try:
        UsageJournal(_path()).append(u)
    except Exception:
        logger.debug("usage ledger append failed", exc_info=True)


def _context_pct(event: object) -> float | None:
    """The turn's context occupancy, or None when nothing measured it.

    Never 0.0 for "unmeasured": a turn nobody measured must not read as an empty
    context window, the same honesty rule ``priced`` keeps for cost.
    """
    raw = getattr(event, "context_usage_pct", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


@dataclass(frozen=True)
class EventAccounting:
    event: object
    model: str
    estimate: bool

    def values(self):
        from gideon.operations.pricing import estimate_cost, has_pricing

        counts = {key: int(getattr(self.event, key, 0) or 0) for key in _TOKEN_FIELDS}
        cost = float(getattr(self.event, "cost_usd", 0.0) or 0.0)
        if not cost and self.model and self.estimate:
            cost = estimate_cost(self.model, **counts)
        return counts, cost, has_pricing

    def record(self, source, session_key, agent, provider):
        counts, cost, has_pricing = self.values()
        return TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            source=source,
            agent=agent,
            provider=provider,
            model=self.model,
            **counts,
            cost_usd=cost,
            priced=bool(cost) or has_pricing(self.model),
            duration_ms=int(getattr(self.event, "duration_ms", 0) or 0),
            context_pct=_context_pct(self.event),
        )


def record_from_event(
    event: object,
    *,
    source: str,
    session_key: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
    estimate_if_missing: bool = True,
) -> None:
    accounting = EventAccounting(event, model, estimate_if_missing)
    record_turn(accounting.record(source, session_key, agent, provider))


def _maybe_trim(p: Path) -> None:
    UsageJournal(p).trim()


def _iter_rows() -> list[dict]:
    return UsageJournal(_path()).rows()


def _day_of(ts: str) -> str:
    return ts[:10]


def _in_window(ts: str, since: str, until: str) -> bool:
    return not ((since and ts < since) or (until and ts >= until))


def _blank_agg() -> dict:
    return {
        **dict.fromkeys(_TOKEN_FIELDS, 0),
        "cost_usd": 0.0,
        "turns": 0,
        "priced": True,
    }


def _fold(agg: dict, row: dict) -> None:
    for key in _TOKEN_FIELDS:
        agg[key] += int(row.get(key, 0) or 0)
    agg["cost_usd"] += float(row.get("cost_usd", 0.0) or 0.0)
    agg["turns"] += 1
    if not row.get("priced", True):
        agg["priced"] = False


def _session_matches(key: str, session_key: str, session_prefix: str) -> bool:
    exact = not session_key or key == session_key
    nested = (
        not session_prefix
        or key == session_prefix
        or key.startswith(session_prefix + _SESSION_KEY_SEP)
    )
    return exact and nested


@dataclass(frozen=True)
class TurnSelection:
    since: str = ""
    until: str = ""
    session_key: str = ""
    session_prefix: str = ""

    def accepts(self, row):
        return _in_window(
            str(row.get("ts", "")), self.since, self.until
        ) and _session_matches(
            str(row.get("session_key", "")), self.session_key, self.session_prefix
        )

    def rows(self):
        return filter(self.accepts, _iter_rows())


def _row_selected(
    row: dict, since: str, until: str, session_key: str, session_prefix: str = ""
) -> bool:
    return TurnSelection(since, until, session_key, session_prefix).accepts(row)


def rollup(
    *,
    since: str = "",
    until: str = "",
    group_by: str = "model",
    session_key: str = "",
    session_prefix: str = "",
) -> list[dict]:
    if group_by not in _GROUP_KEYS:
        raise ValueError(f"group_by must be one of {_GROUP_KEYS}, got {group_by!r}")
    groups = {}
    selected = TurnSelection(since, until, session_key, session_prefix)
    for row in selected.rows():
        key = (
            _day_of(str(row.get("ts", "")))
            if group_by == "day"
            else str(row.get(group_by, ""))
        )
        _fold(groups.setdefault(key, _blank_agg()), row)
    keys = sorted(groups, key=lambda key: (-groups[key]["cost_usd"], str(key)))
    return [{group_by: key, **groups[key]} for key in keys]


def totals(
    *, since: str = "", until: str = "", session_key: str = "", session_prefix: str = ""
) -> dict:
    aggregate = _blank_agg()
    for row in TurnSelection(since, until, session_key, session_prefix).rows():
        _fold(aggregate, row)
    return aggregate
