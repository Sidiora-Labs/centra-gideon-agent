from __future__ import annotations

from datetime import datetime, timezone

from gideon.automation.triggers.arm import next_fire
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.service import next_after_completion

UTC = timezone.utc


def _epoch(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def _clock(expr: str, *, skip_dates: list[str] | None = None) -> Trigger:
    return Trigger(
        id="wall-clock",
        name="Wall clock",
        kind="clock",
        enabled=True,
        spec={
            "kind": "cron",
            "expr": expr,
            "timezone": "America/New_York",
            "strict": True,
            "skip_dates": skip_dates or [],
        },
        workflow={"provider": "run-prompt", "config": {}},
        capabilities={"providers": ["run-prompt"]},
    )


def test_cron_wall_clock_gap_and_fold_vectors():
    # After the spring transition, 09:00 remains 09:00 in the owner's zone.
    after_spring_transition = _clock("0 9 * * *")
    assert next_fire(after_spring_transition, now=_epoch(2026, 3, 8, 8)) == _epoch(
        2026, 3, 8, 13
    )

    # A scheduled 02:30 does not exist on this date; it advances to the 03:00 gap edge.
    spring_gap = _clock("30 2 * * *")
    assert next_fire(spring_gap, now=_epoch(2026, 3, 8, 6, 59)) == _epoch(2026, 3, 8, 7)

    # The repeated 01:30 resolves to its first instant. Re-arming after that fire,
    # even while the local clock is in the second 01:00 hour, skips the duplicate.
    fall_fold = _clock("30 1 * * *")
    first_fold = _epoch(2026, 11, 1, 5, 30)
    assert next_fire(fall_fold, now=_epoch(2026, 11, 1, 4)) == first_fold
    fall_fold.last_fired_at = datetime.fromtimestamp(first_fold, tz=UTC).isoformat()
    assert next_after_completion(
        fall_fold,
        completed_at=_epoch(2026, 11, 1, 6, 5),
        now=_epoch(2026, 11, 1, 6, 5),
    ) == _epoch(2026, 11, 2, 6, 30)

    # Owner-local skip dates and the completion re-arm path remain in force.
    skipped_transition = _clock("0 9 * * *", skip_dates=["2026-03-08"])
    assert next_after_completion(
        skipped_transition,
        completed_at=_epoch(2026, 3, 8, 8),
        now=_epoch(2026, 3, 8, 8),
    ) == _epoch(2026, 3, 9, 13)
