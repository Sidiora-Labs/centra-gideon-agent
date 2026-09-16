"""Durable per-model estimates derived from classified call attempts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_STATS_FILE = "routing_stats.json"
STATS_VERSION = 1
_ALPHA = 0.2
_W_SUCCESS = 0.60
_W_FEEDBACK = 0.40


def ref_of(provider: str, model: str) -> str:
    return ":".join(map(str, (provider, model)))


def _ema(old: float, new: float, alpha: float = _ALPHA) -> float:
    return (1.0 - alpha) * old + alpha * new


def _score(success_rate: float, feedback: float, feedback_n: int) -> float:
    value = (
        success_rate
        if feedback_n <= 0
        else _W_SUCCESS * success_rate + _W_FEEDBACK * feedback
    )
    return round(value, 4)


def _stats_path(home: Path) -> Path:
    return Path(home).joinpath(_STATS_FILE)


def _empty():
    return {"version": STATS_VERSION, "use_cases": {}}


def load_stats(home: Path) -> dict[str, Any]:
    try:
        document = json.loads(_stats_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        document = None
    if not isinstance(document, dict):
        return _empty()
    for key, default in _empty().items():
        document.setdefault(key, default)
    return document


def save_stats(home: Path, stats: dict[str, Any]) -> None:
    encoded = json.dumps(stats, indent=2, sort_keys=True)
    atomic_write(_stats_path(home), encoded + "\n")


def _coordinates(rec):
    return tuple(str(rec.get(key, "") or "") for key in ("use_case", "query_class"))


@dataclass
class AttemptEstimate:
    row: dict

    def accept(self, rec, now):
        observations = (
            ("success_rate", 1.0 if rec.get("passed") else 0.0, 4),
            ("avg_ms", float(rec.get("latency_ms", 0.0) or 0.0), 1),
            ("avg_cost_usd", float(rec.get("dollars_est", 0.0) or 0.0), 6),
        )
        self.row["n"] = int(self.row.get("n", 0)) + 1
        for field, value, precision in observations:
            self.row[field] = (
                value
                if self.row["n"] == 1
                else round(_ema(float(self.row[field]), value), precision)
            )
        self.row["score"] = _score(
            float(self.row["success_rate"]),
            float(self.row.get("feedback", 0.0)),
            int(self.row.get("feedback_n", 0)),
        )
        self.row["updated_at"] = now
        return self.row


@dataclass
class RoutingFold:
    document: dict

    def apply(self, rec, now):
        use_case, query_class = _coordinates(rec)
        if not (use_case and query_class):
            return False
        ref = ref_of(str(rec.get("provider", "")), str(rec.get("model", "")))
        if ref == ":":
            return False
        classes = self.document.setdefault("use_cases", {}).setdefault(use_case, {})
        models = classes.setdefault(query_class, {})
        row = models.get(ref)
        if row is None:
            row = {"n": 0, "feedback": 0.0, "feedback_n": 0}
        models[ref] = AttemptEstimate(row).accept(rec, now)
        return True


def fold_record(
    stats: dict[str, Any], rec: dict[str, Any], *, now: str = ""
) -> dict[str, Any]:
    RoutingFold(stats).apply(rec, now)
    return stats


def record_routing_stats(rec: dict[str, Any], *, home: Path, now: str = "") -> None:
    try:
        document = load_stats(home)
        document["version"] = STATS_VERSION
        fold_record(document, rec, now=now)
        save_stats(home, document)
    except Exception:
        logger.warning("routing stats fold failed", exc_info=True)
    else:
        _check_for_gap(document, rec, home=home)


def _check_for_gap(stats: dict[str, Any], rec: dict[str, Any], *, home: Path) -> None:
    coordinates = _coordinates(rec)
    if all(coordinates):
        try:
            from gideon.engine.routing.gap import detect_gap

            detect_gap(stats, *coordinates, home=home)
        except Exception:
            logger.warning("routing proposal check failed", exc_info=True)


def _audit_records(path):
    try:
        body = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for line in filter(None, map(str.strip, body.splitlines())):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def rebuild(home: Path, audit_path: Path | None = None) -> int:
    if audit_path is None:
        from gideon.security.guardrails.audit import _audit_path

        audit_path = _audit_path()
    fold = RoutingFold(_empty())
    count = sum(
        fold.apply(rec, str(rec.get("ts", ""))) for rec in _audit_records(audit_path)
    )
    save_stats(home, fold.document)
    return count
