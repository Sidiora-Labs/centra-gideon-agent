from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventReentry:
    envelope: Any
    now: float

    def prepare(self) -> tuple[Callable[..., Any], dict[str, Any]] | None:
        from gideon.automation.event_triggers import (
            SOURCE_MEMORY,
            emit_event,
            get_engine,
        )

        kind = str(getattr(self.envelope, "kind", "") or "")
        source, _, event_type = kind.partition(".")
        if not event_type:
            source, event_type = SOURCE_MEMORY, kind
        if not event_type:
            return None
        payload = getattr(self.envelope, "payload", None) or {}
        meta = payload.get("meta")
        arguments = {
            "source": source,
            "event_type": event_type,
            "key": str(payload.get("key", "") or ""),
            "value": str(payload.get("value", "") or ""),
            "now": self.now,
            "meta": dict(meta) if isinstance(meta, dict) else None,
        }
        get_engine()
        return emit_event, arguments

    def run(self) -> tuple[str, str]:
        from gideon.automation.triggers.dispatch import (
            Handling,
            classify_handler_outcome,
        )

        try:
            prepared = self.prepare()
        except Exception as exc:
            return (
                classify_handler_outcome(exc),
                f"re-entry could not be prepared: {exc!r}",
            )
        if prepared is None:
            return (
                Handling.PERMANENT.value,
                "envelope carries no event kind; nothing can route it",
            )
        emit, arguments = prepared
        try:
            emit(**arguments)
        except Exception:
            logger.warning(
                "spooled fire raised after the side-effect boundary", exc_info=True
            )
            return (
                Handling.DELIVERED.value,
                "re-entry raised after the side-effect boundary; counted delivered because a retry could double-fire an action that already ran",
            )
        return Handling.DELIVERED.value, ""


@dataclass
class SpoolReplay:
    now: float
    reenter: Callable[..., tuple[str, str]]
    seen: dict[str, float] = field(default_factory=dict)
    handled: int = 0
    holding: str = ""

    def settle(self, envelope: Any, held_id: str, held_retries: int) -> bool:
        from gideon.automation.triggers.dispatch import (
            DEDUP_WINDOW_SECS,
            DrainAction,
            drain_decision,
            is_duplicate,
            write_spool_hold,
        )

        identity = str(getattr(envelope, "event_id", "") or "")
        retries = held_retries if identity and identity == held_id else 0
        emitted = float(getattr(envelope, "emitted_at", 0.0) or 0.0) or self.now
        detail = ""
        if is_duplicate(envelope, self.seen, emitted, DEDUP_WINDOW_SECS):
            action, why = (
                DrainAction.SKIP_DUPLICATE.value,
                "identical payload already re-entered",
            )
        else:
            handling, detail = self.reenter(envelope, now=self.now)
            action, why = drain_decision(handling=handling, held_retries=retries)
        if action == DrainAction.CONSUME.value:
            if why or detail:
                logger.warning(
                    "spooled fire %s consumed undelivered: %s %s", identity, why, detail
                )
            self.seen[envelope.payload_hash] = emitted
        elif action == DrainAction.SKIP_DUPLICATE.value:
            logger.info("spooled fire %s skipped: %s", identity, why)
        elif action == DrainAction.GIVE_UP.value:
            logger.warning(
                "spooled fire %s GIVEN UP after %d attempts: %s %s",
                identity,
                retries + 1,
                why,
                detail,
            )
        elif action == DrainAction.HOLD.value:
            if write_spool_hold(event_id=identity, held_retries=retries + 1):
                logger.info("spooled fire %s held: %s %s", identity, why, detail)
                self.holding = identity
                return False
            logger.warning(
                "spooled fire %s could not persist its retry budget; acking rather than retrying unbounded: %s",
                identity,
                detail,
            )
        else:
            raise AssertionError(
                f"no branch for DrainAction {action!r} — a new member must declare what the drain does with it rather than fall through to another member's handling"
            )
        self.handled += 1
        return True

    def run(self) -> int:
        from gideon.automation.triggers import service
        from gideon.automation.triggers.dispatch import (
            clear_spool,
            clear_spool_hold,
            read_spool_hold,
        )

        try:
            envelopes, bad = service.drain_spooled_fires()
        except Exception:
            logger.warning(
                "spool drain failed; leaving the spool for the next tick", exc_info=True
            )
            return 0
        if bad:
            logger.warning("spool drain skipped %d unparseable line(s)", bad)
        if not envelopes:
            clear_spool_hold()
            return 0
        self.now = self.now or time.time()
        held_id, held_retries = read_spool_hold()
        for envelope in envelopes:
            if not self.settle(envelope, held_id, held_retries):
                break
        if not self.holding:
            clear_spool_hold()
        if self.handled:
            try:
                clear_spool(handled=self.handled)
            except Exception:
                logger.warning(
                    "spool ack failed; %d fire(s) may re-run next tick",
                    self.handled,
                    exc_info=True,
                )
        return self.handled
