"""Attributed judge observations read from workflow event journals."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

from gideon.assurance.ledger.kinds import JUDGE_VERDICT
from gideon.assurance.ledger.writer import EVENTS_FILE
from gideon.engine.routing.stats import ref_of

logger = logging.getLogger(__name__)
_RUNS_SUBPATH = ("workflows", "runs")
_QUALITY_FEEDBACK = {"PASS": 1.0, "REJECT": 0.0}
_KNOWN_VERDICTS = frozenset(
    {"PASS", "REJECT", "RETRY", "REPLAN", "ESCALATE", "NEEDS_INPUT"}
)
Cell = tuple[str, str, str]


def _resolve_home(home: Path | None) -> Path | None:
    if home is None:
        try:
            from gideon.core.config.loader import config_dir

            home = config_dir()
        except Exception:
            return None
    return Path(home)


def _events_files(home: Path) -> list[Path]:
    try:
        entries = Path(home).joinpath(*_RUNS_SUBPATH).iterdir()
        return [
            directory / EVENTS_FILE
            for directory in sorted(entries)
            if directory.is_dir()
        ]
    except OSError:
        return []


def _read_records(path: Path) -> Iterator[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in filter(None, map(str.strip, lines)):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


def _cell_of(event: dict[str, Any]) -> Cell | None:
    use_case = str(event.get("use_case") or "")
    query_class = str(event.get("query_class") or "")
    if not (use_case and query_class):
        return None
    ref = str(event.get("ref") or "")
    if not ref:
        provider, model = (str(event.get(key) or "") for key in ("provider", "model"))
        if not (provider and model):
            return None
        ref = ref_of(provider, model)
    return use_case, query_class, ref


class FeedbackCensus:
    def __init__(self):
        self.seen = set()
        self.counts: dict[Cell, int] = {}
        self.totals: dict[Cell, float] = {}
        self.dropped = dict.fromkeys(
            (
                "unattributed",
                "control_flow",
                "unknown_verdict",
                "cannot_judge",
                "duplicate",
            ),
            0,
        )

    def inspect(self, event):
        if event.get("kind") != JUDGE_VERDICT:
            return
        identifier = str(event.get("event_id") or "")
        if identifier and identifier in self.seen:
            reason = "duplicate"
        else:
            cell = _cell_of(event)
            if cell is None:
                reason = "unattributed"
            elif event.get("cannot_judge"):
                reason = "cannot_judge"
            else:
                verdict = str(event.get("verdict") or "").strip().upper()
                if verdict in _QUALITY_FEEDBACK:
                    if identifier:
                        self.seen.add(identifier)
                    self.counts[cell] = self.counts.get(cell, 0) + 1
                    self.totals[cell] = (
                        self.totals.get(cell, 0.0) + _QUALITY_FEEDBACK[verdict]
                    )
                    return
                reason = (
                    "control_flow" if verdict in _KNOWN_VERDICTS else "unknown_verdict"
                )
        self.dropped[reason] += 1

    def result(self):
        if any(self.dropped.values()):
            logger.debug("routing feedback: dropped %s", self.dropped)
        return {
            cell: (round(self.totals[cell] / count, 4), count)
            for cell, count in self.counts.items()
        }


def feedback_index(*, home: Path | None = None) -> dict[Cell, tuple[float, int]]:
    directory = _resolve_home(home)
    if directory is None:
        return {}
    census = FeedbackCensus()
    for path in _events_files(directory):
        for event in _read_records(path):
            census.inspect(event)
    return census.result()


def feedback_for(
    use_case: str, query_class: str, ref: str, *, home: Path | None = None
) -> tuple[float, int]:
    index = feedback_index(home=home)
    return index.get((use_case, query_class, ref), (0.0, 0))
