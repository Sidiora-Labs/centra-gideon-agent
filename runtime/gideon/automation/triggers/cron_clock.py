"""Step cron schedules in their owner's local wall clock."""

from __future__ import annotations

from datetime import datetime, tzinfo


def _placed(local: datetime, zone: tzinfo, after: float) -> float:
    """Resolve a naive wall time, choosing its first future fold or gap boundary."""
    candidates: list[float] = []
    round_trips: list[datetime] = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        instant = aware.timestamp()
        round_trip = datetime.fromtimestamp(instant, tz=zone).replace(tzinfo=None)
        round_trips.append(round_trip)
        if round_trip == local:
            candidates.append(instant)

    future = [candidate for candidate in candidates if candidate > after]
    if future:
        return min(future)
    if candidates:
        return 0.0

    # A nonexistent wall time maps backward and forward by the size of the gap.
    # Locate the first valid local instant between the requested time and its
    # forward round trip. Zoneinfo transitions are second-granular.
    upper = max(round_trips)
    lower = local
    for _ in range(48):
        if (upper - lower).total_seconds() <= 1e-6:
            break
        middle = lower + (upper - lower) / 2
        if any(
            datetime.fromtimestamp(
                middle.replace(tzinfo=zone, fold=fold).timestamp(), tz=zone
            ).replace(tzinfo=None)
            == middle
            for fold in (0, 1)
        ):
            upper = middle
        else:
            lower = middle
    return upper.replace(tzinfo=zone, fold=0).timestamp()


def next_fire(
    expression: str, *, after: float, zone: tzinfo, last_fire: float = 0.0
) -> float:
    """Return the next cron epoch after ``after`` using naive local cron steps."""
    from croniter import croniter

    cursor = datetime.fromtimestamp(after, tz=zone).replace(tzinfo=None)
    last_local = (
        datetime.fromtimestamp(last_fire, tz=zone).replace(tzinfo=None)
        if last_fire > 0
        else None
    )
    iterator = croniter(expression, cursor)
    for _ in range(500):
        match = iterator.get_next(datetime)
        if (
            last_local is not None
            and match.replace(second=0, microsecond=0)
            == last_local.replace(second=0, microsecond=0)
        ):
            continue
        placed = _placed(match, zone, after)
        if placed > after:
            return float(placed)
    return 0.0
