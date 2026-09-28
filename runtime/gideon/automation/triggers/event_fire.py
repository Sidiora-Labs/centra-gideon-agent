"""Canonical event-trigger routing and fire admission."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
_active_router: EventRouter | None = None
_router_lock = threading.RLock()


@dataclass(frozen=True)
class RoutedEvent:
    source: str
    event_type: str
    key: str
    value: str
    now: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventRouter:
    store: Any
    dispatch: Any
    loop: asyncio.AbstractEventLoop
    enabled: bool = True
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    fire_times: list[float] = field(default_factory=list)
    stopped: bool = False
    delivery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def accepts(self, event: Any, trigger: Any) -> bool:
        if trigger.kind != "event" or not trigger.enabled or trigger.state != "active":
            return False
        from gideon.automation.event_triggers import EventOccurrence, EventTriggerEngine

        return EventOccurrence(
            event.source, event.event_type, event.key, event.value, event.meta
        ).accepts(EventTriggerEngine._event_view(trigger))

    def matching(self, event: Any) -> list[Any]:
        from gideon.automation.triggers.provider import armable

        return [trigger for trigger in armable(self.store) if self.accepts(event, trigger)]

    def emit(self, event: Any) -> None:
        if not self.enabled:
            return
        if self.stopped:
            self.spool(event)
            return
        try:
            active = asyncio.get_running_loop()
        except RuntimeError:
            if not self._handoff(event):
                self.spool(event)
            return
        if active is self.loop:
            task = active.create_task(self.route(event))
            self.tasks.add(task)
            task.add_done_callback(self._settled)
        elif self.loop.is_running():
            if not self._handoff(event):
                self.spool(event)
        else:
            self.spool(event)

    def _handoff(self, event: Any) -> bool:
        if not self.loop.is_running():
            return False
        try:
            self.loop.call_soon_threadsafe(self._spawn, event)
        except RuntimeError:
            logger.warning(
                "event %s.%s arrived while the gateway loop was closing",
                event.source,
                event.event_type,
            )
            return False
        return True

    def _spawn(self, event: Any) -> None:
        if not self.enabled or self.stopped:
            self.spool(event)
            return
        task = self.loop.create_task(self.route(event))
        self.tasks.add(task)
        task.add_done_callback(self._settled)

    async def route(self, event: Any) -> int:
        if not self.enabled or self.stopped:
            return 0
        async with self.delivery_lock:
            return await self._route(event)

    async def _route(self, event: Any) -> int:
        from gideon.automation.triggers.service import admit_fire, record_suppression

        fired = 0
        for trigger in self.matching(event):
            now = time.time()
            if not self._rate_ok(now):
                await record_suppression(
                    trigger,
                    outcome="skipped_gate",
                    reason="event source rate cap reached (30 fires per minute)",
                    now=now,
                    base_dir=self.store.base_dir,
                    event=event,
                )
                break
            context, decision = await admit_fire(
                self.store,
                trigger,
                now=now,
                payload_text=event.value,
                holder=f"event:{trigger.id}",
            )
            if not decision.allowed:
                await record_suppression(
                    trigger,
                    outcome=decision.outcome,
                    reason=decision.reason,
                    now=now,
                    base_dir=self.store.base_dir,
                    event=event,
                )
                continue
            from gideon.automation.triggers import claims
            from gideon.automation.triggers.service import to_iso

            claims.write_claim(decision.claim, base_dir=self.store.base_dir)
            current = self.store.get(trigger.id)
            if current is None or not current.ok or current.trigger.kind != "event":
                claims.release_claim(trigger.id, base_dir=self.store.base_dir)
                continue
            live = current.trigger
            live.run_count = int(live.run_count or 0) + 1
            live.last_fired_at = to_iso(now)
            maximum = _max_fires(live)
            if maximum and live.run_count >= maximum:
                live.enabled = False
            self.store.upsert(live)
            payload = _payload(event, live)
            try:
                await self.dispatch(live, payload, event="trigger.event")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("event trigger %s dispatch failed", live.id)
            finally:
                claims.release_claim(live.id, base_dir=self.store.base_dir)
            fired += 1
            self._record_rate(now)
        return fired

    def spool(self, event: Any) -> bool:
        if not self.matching(event):
            return False
        from gideon.automation.triggers.dispatch import Envelope, spool_fire

        return spool_fire(
            Envelope(
                seq=0,
                source="event:canonical",
                kind=f"{event.source}.{event.event_type}",
                payload={
                    "key": str(event.key),
                    "value": str(event.value),
                    "meta": dict(event.meta or {}),
                },
                emitted_at=float(event.now or time.time()),
            )
        )

    def _rate_ok(self, now: float) -> bool:
        self.fire_times = [stamp for stamp in self.fire_times if now - stamp < 60.0]
        return len(self.fire_times) < 30

    def _record_rate(self, now: float) -> None:
        self.fire_times.append(now)

    def _settled(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None:
            logger.warning(
                "event trigger dispatch failed",
                exc_info=(type(failure), failure, failure.__traceback__),
            )

    async def stop(self) -> None:
        self.stopped = True
        await self.settle()

    async def settle(self) -> None:
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


def _max_fires(trigger: Any) -> int:
    try:
        return max(0, int((trigger.spec or {}).get("max_fires", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _payload(event: Any, trigger: Any) -> dict[str, Any]:
    from gideon.automation.event_triggers import _fence_fragment

    source = f"{event.source}.{event.event_type}"
    fenced = _fence_fragment(
        str(event.value or ""),
        2000,
        source=f"trigger:{trigger.id}:{source}",
        source_type=f"event:{source}",
        source_id=str(event.key or ""),
    )
    return {
        "trigger_id": trigger.id,
        "source": event.source,
        "event_type": event.event_type,
        "key": event.key,
        "value": fenced,
        "meta": dict(event.meta or {}),
        "event": source,
    }


def attach(
    store: Any, dispatch: Any, loop: asyncio.AbstractEventLoop, *, enabled: bool = True
) -> EventRouter:
    global _active_router
    router = EventRouter(store, dispatch, loop, enabled=enabled)
    with _router_lock:
        previous = _active_router
        if previous is not None and not previous.stopped:
            raise RuntimeError("an event router is already attached")
        _active_router = router
    return router


def current() -> EventRouter | None:
    with _router_lock:
        return _active_router


async def detach(router: EventRouter | None) -> None:
    global _active_router
    if router is None:
        return
    with _router_lock:
        if _active_router is router:
            _active_router = None
    await router.stop()


def emit(event: Any) -> None:
    router = current()
    if router is not None:
        router.emit(event)
        return
    try:
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir

        spool_matching(TriggerStore(base_dir=config_dir()), event)
    except Exception:
        logger.debug("event without a router could not be spooled", exc_info=True)


def spool_matching(store: Any, event: Any) -> bool:
    """Durably retain only an event for which a current canonical row subscribes."""
    from gideon.automation.event_triggers import EventOccurrence, EventTriggerEngine
    from gideon.automation.triggers.provider import armable
    from gideon.automation.triggers.dispatch import Envelope, spool_fire

    if not any(
        trigger.kind == "event"
        and EventOccurrence(
            event.source, event.event_type, event.key, event.value, event.meta
        ).accepts(EventTriggerEngine._event_view(trigger))
        for trigger in armable(store)
    ):
        return False
    return spool_fire(
        Envelope(
            seq=0,
            source="event:canonical",
            kind=f"{event.source}.{event.event_type}",
            payload={
                "key": str(event.key),
                "value": str(event.value),
                "meta": dict(event.meta or {}),
            },
            emitted_at=float(event.now or time.time()),
        )
    )
