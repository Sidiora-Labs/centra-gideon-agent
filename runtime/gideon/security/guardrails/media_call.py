"""Canonical unit pricing, admission and accounting for media provider calls."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class MediaCall:
    provider: str
    model: str
    unit: str
    quantity: float | None
    size: str = ""
    quality: str = ""

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.model}"


def _work_of(session_key: str) -> str:
    from gideon.security.session_credentials import current_work

    work = current_work()
    return session_key or (work.session_key if work else "")


def is_unattended(session_key: str = "") -> bool:
    from gideon.security.guardrails.budgets import current_run_key
    from gideon.security.guardrails.policy import is_unattended_session

    key = _work_of(session_key)
    return bool(current_run_key()) or (bool(key) and is_unattended_session(key))


async def metered_media_call(
    call: MediaCall,
    run: Callable[[], Awaitable[T]],
    *,
    session_key: str = "",
    unattended: bool | None = None,
    billed: Callable[[T], float | None] | None = None,
) -> T:
    """Reserve requested unit costs before unattended work and record actual known quantities.

    A failed remote operation can still have been billed. Its hold is released and its Usage
    row remains unpriced, rather than asserting that a missing result cost nothing.
    """
    from gideon.engine.routing.rates import price_units, unit_rate_for
    from gideon.security.guardrails.budgets import (
        CallCost,
        budget_from_config,
        current_run_key,
        get_meter,
        run_budget_from_config,
    )
    from gideon.security.guardrails.model_call import admit_call

    caps = is_unattended(session_key) if unattended is None else unattended
    asked = price_units(
        call.provider,
        call.model,
        call.unit,
        call.quantity,
        size=call.size,
        quality=call.quality,
    )
    rate = unit_rate_for(call.provider, call.model, call.unit)
    has_price = (
        rate is not None
        and rate.unit_price(size=call.size, quality=call.quality) is not None
    )
    uncovered = ""
    if rate is not None and not has_price:
        uncovered = " ".join(
            p
            for p in (
                call.size or rate.default_size,
                call.quality or rate.default_quality,
                call.unit,
            )
            if p
        )
    meter = get_meter()
    hold = None
    if caps:
        hold = await admit_call(
            meter,
            CallCost(
                ref=call.ref,
                known_dollars=asked.cost_usd if asked.priced else None,
                unit_only=True,
                unit=call.unit,
                measured=asked.priced or call.quantity is not None or not has_price,
                unpriced_for=uncovered,
            ),
            budget_from_config(),
            run_budget_from_config(),
        )
    started = time.monotonic()
    try:
        result = await run()
        # A returned quantity is evidence; a requested amount is not a measured result.
        quantity = billed(result) if billed is not None else call.quantity
        if quantity is not None and quantity <= 0:
            quantity = None
        price = price_units(
            call.provider,
            call.model,
            call.unit,
            quantity,
            size=call.size,
            quality=call.quality,
        )
    except BaseException:
        # Only a declared zero rate establishes that a failed call was free.
        quantity = None
        price = price_units(
            call.provider,
            call.model,
            call.unit,
            None,
            size=call.size,
            quality=call.quality,
        )
        _settle(meter, hold, call, price, caps)
        _record(call, quantity, price, caps, _work_of(session_key), started, "absent")
        raise
    _settle(meter, hold, call, price, caps)
    _record(
        call,
        quantity,
        price,
        caps,
        _work_of(session_key),
        started,
        "reported" if quantity is not None else "absent",
    )
    return result


def _settle(meter, hold, call, price, caps):
    from gideon.security.guardrails.budgets import current_run_key

    if caps:
        meter.settle(
            hold,
            ref=call.ref,
            tokens=0,
            answer_tokens=0,
            dollars=price.cost_usd or 0.0,
            priced=price.priced,
            run_key=current_run_key() or None,
            usage_reported=True,
            unit_only=True,
        )
    else:
        meter.release(hold)


def _record(call, quantity, price, caps, session_key, started, status):
    from gideon.operations.usage_ledger import record_units

    try:
        record_units(
            source="background" if caps else "chat",
            session_key=session_key,
            provider=call.provider,
            model=call.model,
            unit=call.unit,
            quantity=quantity,
            cost_usd=price.cost_usd,
            priced=price.priced,
            estimated=price.estimated,
            price_source=price.source,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_status=status,
        )
    except Exception:
        logger.debug("Media usage row could not be recorded", exc_info=True)
