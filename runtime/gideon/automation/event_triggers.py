"""Source-scoped event subscriptions and guarded background action delivery."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
SOURCE_MEMORY = "memory"
SOURCE_INBOX = "inbox"
SOURCE_APP = "app"
EVENT_SOURCES = (SOURCE_MEMORY, SOURCE_INBOX, SOURCE_APP)
MEMORY_UPDATE = "MemoryUpdate"
MEMORY_KEY_PATTERN = "MemoryKeyPattern"
CONTENT_MATCH = "ContentMatch"
INBOX_MESSAGE = "InboxMessage"
INBOX_SENDER = "InboxSender"
INBOX_ADDRESS = "InboxAddress"
APP_EVENT = "AppEvent"
EVENT_PATTERNS = (
    MEMORY_UPDATE,
    MEMORY_KEY_PATTERN,
    CONTENT_MATCH,
    INBOX_MESSAGE,
    INBOX_SENDER,
    INBOX_ADDRESS,
    APP_EVENT,
)
PATTERN_SOURCE: dict[str, str] = {
    MEMORY_UPDATE: SOURCE_MEMORY,
    MEMORY_KEY_PATTERN: SOURCE_MEMORY,
    CONTENT_MATCH: SOURCE_MEMORY,
    INBOX_MESSAGE: SOURCE_INBOX,
    INBOX_SENDER: SOURCE_INBOX,
    INBOX_ADDRESS: SOURCE_INBOX,
    APP_EVENT: SOURCE_APP,
}
_RATE_WINDOW_SECS = 60.0
_RATE_MAX_FIRES = 30
_DEFAULT_DEBOUNCE_SECS = 5.0
CONTENT_MATCH_SCAN_LIMIT = 4096
_CATASTROPHIC_RE = re.compile("\\([^)]*[+*]\\)[+*]|\\((?=[^)]*\\|)[^)]*\\)[+*]")


@dataclass
class EventTrigger:
    id: str
    pattern: str
    source: str = SOURCE_MEMORY
    action_provider: str = "notify"
    action_config: dict = field(default_factory=dict)
    key_glob: str = ""
    content_re: str = ""
    sender_glob: str = ""
    address_glob: str = ""
    event_glob: str = ""
    enabled: bool = True
    state: str = "active"
    park_reason: str = ""
    park_retry_after: float = 0.0
    max_fires: int = 0
    fire_count: int = 0
    debounce_secs: float = _DEFAULT_DEBOUNCE_SECS
    last_fired_at: float = 0.0
    last_status: str = ""
    last_error: str = ""
    name: str = ""

    def to_dict(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(EventTrigger)}

    @classmethod
    def from_dict(cls, d: dict) -> EventTrigger:
        text_defaults = dict(
            id="",
            pattern=MEMORY_UPDATE,
            action_provider="notify",
            key_glob="",
            content_re="",
            sender_glob="",
            address_glob="",
            event_glob="",
            park_reason="",
            last_status="",
            last_error="",
        )
        values: dict = {
            key: str(d.get(key, default)) for key, default in text_defaults.items()
        }
        values["source"] = str(
            d.get("source") or PATTERN_SOURCE.get(values["pattern"], SOURCE_MEMORY)
        )
        values["state"] = str(d.get("state") or "active")
        values["enabled"] = bool(d.get("enabled", True))
        values["action_config"] = dict(d.get("action_config") or {})
        for key in ("max_fires", "fire_count"):
            values[key] = int(d.get(key, 0) or 0)
        numeric_defaults = dict(
            park_retry_after=0.0,
            debounce_secs=_DEFAULT_DEBOUNCE_SECS,
            last_fired_at=0.0,
        )
        for key, default in numeric_defaults.items():
            values[key] = float(d.get(key, default) or 0.0)
        return cls(**values)


def fires_automatically(trigger: EventTrigger) -> bool:
    from gideon.automation.triggers.models import TriggerState

    return bool(trigger.enabled and trigger.state == TriggerState.ACTIVE.value)


def _timestamp(value: Any) -> float:
    from gideon.automation.triggers.service import to_epoch

    return to_epoch(value)


def catastrophic_regex_hint(pattern: str) -> str:
    hazard = _CATASTROPHIC_RE.search(pattern) if pattern else None
    if hazard is None:
        return ""
    return (
        "this pattern nests a quantifier inside a quantified group (e.g. `(a+)+`), which "
        "backtracks exponentially — a 30-char value can take ~40s, on the memory-write path. "
        "Simplify it (`(\\w+)+` almost always means `\\w+`)"
    )


@dataclass(frozen=True)
class EventOccurrence:
    source: str
    event_type: str
    key: str
    value: str
    meta: dict | None = None

    def parameters(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def accepts(self, trigger: EventTrigger) -> bool:
        if not fires_automatically(trigger) or trigger.source != self.source:
            return False
        if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
            return False
        pattern = trigger.pattern
        if pattern in (MEMORY_UPDATE, INBOX_MESSAGE):
            return True
        if pattern == CONTENT_MATCH:
            return self.content_matches(trigger.content_re)
        if pattern == APP_EVENT:
            return not trigger.event_glob or fnmatch.fnmatch(
                self.event_type or "", trigger.event_glob
            )
        if pattern == MEMORY_KEY_PATTERN:
            glob, candidate = trigger.key_glob, self.key or ""
        elif pattern in (INBOX_SENDER, INBOX_ADDRESS):
            name, glob = (
                ("sender", trigger.sender_glob)
                if pattern == INBOX_SENDER
                else ("address", trigger.address_glob)
            )
            candidate = str((self.meta or {}).get(name) or "")
        else:
            return False
        return bool(glob) and fnmatch.fnmatch(candidate, glob)

    def content_matches(self, pattern: str) -> bool:
        if not pattern:
            return False
        bounded = (self.value or "")[:CONTENT_MATCH_SCAN_LIMIT]
        try:
            found = re.search(pattern, bounded)
        except re.error:
            return pattern in bounded
        return found is not None


def matches(
    trigger: EventTrigger,
    *,
    source: str,
    event_type: str,
    key: str,
    value: str,
    meta: dict | None = None,
) -> bool:
    return EventOccurrence(source, event_type, key, value, meta).accepts(trigger)


class EventTriggerStore:
    def __init__(self, path: Path):
        self._path = path

    def load(self) -> list[EventTrigger]:
        try:
            records = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        selected = (
            record
            for record in records
            if isinstance(record, dict) and record.get("id")
        )
        return list(map(EventTrigger.from_dict, selected))

    def save(self, triggers: list[EventTrigger]) -> None:
        records = [row.to_dict() for row in triggers]
        atomic_write(self._path, json.dumps(records, indent=2))

    def upsert(self, t: EventTrigger) -> None:
        self.save([*filter(lambda row: row.id != t.id, self.load()), t])

    def delete(self, trigger_id: str) -> bool:
        original = self.load()
        replacement = list(filter(lambda row: row.id != trigger_id, original))
        removed = len(original) != len(replacement)
        if removed:
            self.save(replacement)
        return removed

    def record_fire(self, trigger_id: str, *, now: float) -> None:
        rows = self.load()
        selected = next((row for row in rows if row.id == trigger_id), None)
        if selected is not None:
            selected.fire_count, selected.last_fired_at = selected.fire_count + 1, now
            exhausted = selected.max_fires and selected.fire_count >= selected.max_fires
            if exhausted:
                selected.enabled = False
        self.save(rows)

    def record_outcome(self, trigger_id: str, *, status: str, error: str = "") -> None:
        rows = self.load()
        selected = next((row for row in rows if row.id == trigger_id), None)
        if selected is None:
            return
        selected.last_status = status
        selected.last_error = error[:200]
        self.save(rows)


class CanonicalEventStore:
    """Compatibility projection over event-kind rows in the trigger document."""

    def __init__(self, base_dir: Path | None = None):
        from gideon.automation.triggers.store import TriggerStore

        self._store = TriggerStore(base_dir=base_dir)

    @staticmethod
    def _view(trigger: Any) -> EventTrigger:
        return EventTriggerEngine._event_view(trigger)

    def load(self) -> list[EventTrigger]:
        events = []
        for row in self._store.load():
            if row.trigger.kind != "event":
                continue
            view = self._view(row.trigger)
            view._trigger = row.trigger
            view._issues = [issue.message for issue in row.errors]
            from gideon.automation.triggers.grants import action_revision

            view._action_revision = action_revision(row.trigger)
            events.append(view)
        return events

    def upsert(self, event: EventTrigger) -> None:
        from gideon.automation.triggers.grants import is_granted, required_provider
        from gideon.automation.triggers.models import Trigger, TriggerState

        previous_row = self._store.get(event.id)
        previous = previous_row.trigger if previous_row is not None else None
        current = Trigger(
            id=event.id,
            name=event.name or event.id,
            kind="event",
            enabled=bool(event.enabled),
            created_by=(previous.created_by if previous is not None else "user"),
            author=(previous.author if previous is not None else ""),
            origin_harness=(previous.origin_harness if previous is not None else ""),
            spec={
                "source": event.source,
                "pattern": event.pattern,
                "key_glob": event.key_glob,
                "content_re": event.content_re,
                "sender_glob": event.sender_glob,
                "address_glob": event.address_glob,
                "event_glob": event.event_glob,
                "max_fires": event.max_fires,
            },
            gates={"debounce_secs": event.debounce_secs, "max_fires": event.max_fires},
            capabilities=dict(previous.capabilities if previous is not None else {}),
            workflow={"inline": {"provider": event.action_provider, "config": dict(event.action_config or {})}},
            run_count=event.fire_count,
            last_fired_at=_utc_iso(event.last_fired_at),
            last_error_summary=event.last_error,
            state=event.state or TriggerState.ACTIVE.value,
        )
        if previous is not None and previous.enabled and required_provider(current) and not is_granted(current):
            current.enabled = False
            current.state = TriggerState.PAUSED.value
        self._store.upsert(current)
        saved = self._store.get(event.id)
        if saved is not None and saved.trigger.enabled and required_provider(saved.trigger) and not is_granted(saved.trigger):
            saved.trigger.enabled = False
            saved.trigger.state = TriggerState.PAUSED.value
            self._store.upsert(saved.trigger)

    def delete(self, trigger_id: str) -> bool:
        row = self._store.get(trigger_id)
        return bool(row is not None and row.trigger.kind == "event" and self._store.delete(trigger_id))

    def record_fire(self, trigger_id: str, *, now: float) -> None:
        row = self._store.get(trigger_id)
        if row is None or row.trigger.kind != "event":
            return
        row.trigger.run_count = int(row.trigger.run_count or 0) + 1
        row.trigger.last_fired_at = _utc_iso(now)
        maximum = int((row.trigger.spec or {}).get("max_fires") or 0)
        if maximum and row.trigger.run_count >= maximum:
            row.trigger.enabled = False
        self._store.upsert(row.trigger)

    def record_outcome(self, trigger_id: str, *, status: str, error: str = "") -> None:
        from gideon.automation.triggers.models import TriggerHealth

        row = self._store.get(trigger_id)
        if row is None or row.trigger.kind != "event":
            return
        row.trigger.health_status = TriggerHealth.OK.value if status == "success" else TriggerHealth.FAILING.value
        row.trigger.last_error_summary = error[:200]
        self._store.upsert(row.trigger)


def _utc_iso(stamp: float) -> str:
    if stamp <= 0:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


@dataclass
class FireOutcome:
    ran: bool
    reason: str = ""
    result: object = None
    screen: object = None

    def to_dict(self) -> dict:
        record = dict(ran=self.ran, reason=self.reason)
        serialize = getattr(self.screen, "to_dict", None)
        if callable(serialize):
            record["screen"] = serialize()
        if self.result is not None:
            record["success"] = bool(getattr(self.result, "success", False))
            names = ("exit_code", "stdout", "stderr", "error", "duration_ms")
            values = ((name, getattr(self.result, name, None)) for name in names)
            record.update(
                (name, value) for name, value in values if value not in (None, "", 0)
            )
        return record


def _truncate_fenced(value: str, limit: int) -> str:
    from gideon.security.security import UNTRUSTED_CLOSE

    fragment = value[:limit]
    if UNTRUSTED_CLOSE not in fragment:
        fragment += "\n" + UNTRUSTED_CLOSE
    return fragment


def _fence_fragment(value: str, limit: int, **provenance) -> str:
    from gideon.security.security import fence_untrusted, is_fenced

    if is_fenced(value):
        return _truncate_fenced(value, limit)
    return fence_untrusted(
        value[:limit], transformation_path=f"truncate:{limit}", **provenance
    )


def _fenced_excerpt(trigger_id: str, key: str, value: str) -> str:
    return _fence_fragment(
        value, 200, source=f"trigger:{trigger_id}", source_type="event", source_id=key
    )


class EventActionAttempt:
    def __init__(
        self, trigger: EventTrigger, occurrence: EventOccurrence, *, test: bool
    ):
        self.trigger, self.event, self.test = trigger, occurrence, test

    def context(self):
        from gideon.integrations.action_providers import ActionContext

        occurrence, trigger = self.event, self.trigger
        fenced = _fence_fragment(
            occurrence.value,
            2000,
            source=f"trigger:{trigger.id}:{occurrence.source}:{occurrence.event_type}",
            source_type=f"event:{occurrence.source}:{occurrence.event_type}",
            source_id=occurrence.key,
        )
        payload: dict[str, Any] = dict(
            source=occurrence.source,
            event_type=occurrence.event_type,
            key=occurrence.key,
            value=fenced,
            trigger_id=trigger.id,
        )
        if occurrence.meta:
            payload["meta"] = dict(occurrence.meta)
        if self.test:
            payload["test"] = True
        excerpt = _fenced_excerpt(trigger.id, occurrence.key, occurrence.value)
        return ActionContext(
            event=f"{occurrence.source}.{occurrence.event_type}",
            context=f"{occurrence.key}: {excerpt}",
            payload=payload,
        )

    def policy(self, context):
        from gideon.security.guardrails.denylist import enforce_action
        from gideon.security.guardrails.policy import unattended_dispatch_key
        from gideon.security.guardrails.rungs import (
            announce_withheld,
            route_provider_action,
        )

        trigger = self.trigger
        identity = unattended_dispatch_key(f"trigger:{trigger.id}")
        decision = enforce_action(
            trigger.action_provider,
            trigger.action_config,
            context,
            session_key=identity,
        )
        if decision.blocked:
            matched = getattr(decision, "matched", "") or ""
            detail = getattr(decision, "reason", "") or "blocked by a guardrail rule"
            reason = f"denylist: {matched} — {detail}" if matched else detail
            return None, FireOutcome(False, reason)
        route = route_provider_action(trigger.action_provider, session_key=identity)
        if route.executes:
            return route, None
        announce_withheld(
            route,
            title=f"{trigger.action_provider} is waiting for you",
            body=f"The {trigger.action_provider!r} action on trigger {trigger.id} did not run: {route.reason}.",
            refs={"trigger": trigger.id, "provider": trigger.action_provider},
            dedup_key=f"autonomy_hold:{route.key}:trigger:{trigger.id}",
        )
        return None, FireOutcome(False, f"held for your approval: {route.reason}")

    async def run(self) -> FireOutcome:
        from gideon.security.guardrails.incident import incident_active

        if incident_active():
            return FireOutcome(
                False, "incident mode is active: unattended fires are suspended"
            )
        from gideon.integrations.action_providers import get_action_provider

        provider = get_action_provider(self.trigger.action_provider)
        if provider is None:
            return FireOutcome(
                False,
                f"action provider {self.trigger.action_provider!r} is not registered",
            )
        from gideon.automation.triggers.screen import screen

        verdict = screen(self.event.value)
        if verdict.blocked:
            detail = f"injection screen blocked the payload: matched the {verdict.matched_group} group"
            if verdict.evaded:
                detail += " (hidden by encoding)"
            return FireOutcome(False, detail, screen=verdict)
        context = self.context()
        route, refusal = self.policy(context)
        if refusal is not None:
            return refusal
        result = await provider.execute(self.trigger.action_config, context)
        if route.records_reversal and getattr(result, "success", False):
            from gideon.security.guardrails.rungs import record_reversal

            record_reversal(
                route,
                result,
                label=self.trigger.action_provider,
                refs={
                    "trigger": self.trigger.id,
                    "provider": self.trigger.action_provider,
                },
            )
        return FireOutcome(True, result=result)


async def execute_event_action(
    t: EventTrigger,
    *,
    source: str,
    event_type: str,
    key: str,
    value: str,
    meta: dict | None = None,
    test: bool = False,
) -> FireOutcome:
    event = EventOccurrence(source, event_type, key, value, meta)
    return await EventActionAttempt(t, event, test=test).run()


_engine: EventTriggerEngine | None = None


def get_engine() -> EventTriggerEngine:
    global _engine
    existing = _engine
    if existing is None:
        existing = _engine = EventTriggerEngine()
    return existing


class EventTriggerEngine:
    def __init__(self, store: Any | None = None):
        self._store = store
        self._fire_times: list[float] = []

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        from gideon.core.config.loader import config_dir
        from gideon.automation.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())
        self._store = store
        return store

    @staticmethod
    def _event_view(trigger: Any) -> EventTrigger:
        spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
        gates = trigger.gates if isinstance(getattr(trigger, "gates", None), dict) else {}
        workflow = trigger.workflow if isinstance(getattr(trigger, "workflow", None), dict) else {}
        action = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else workflow
        return EventTrigger(
            id=trigger.id,
            pattern=str(spec.get("pattern") or MEMORY_UPDATE),
            name=str(getattr(trigger, "name", "") or trigger.id),
            source=str(spec.get("source") or SOURCE_MEMORY),
            action_provider=str(action.get("provider") or "notify"),
            action_config=dict(action.get("config") or {}),
            key_glob=str(spec.get("key_glob") or ""),
            content_re=str(spec.get("content_re") or ""),
            sender_glob=str(spec.get("sender_glob") or ""),
            address_glob=str(spec.get("address_glob") or ""),
            event_glob=str(spec.get("event_glob") or ""),
            enabled=bool(trigger.enabled),
            state=str(getattr(trigger, "state", "active") or "active"),
            max_fires=int(spec.get("max_fires") or 0),
            fire_count=int(getattr(trigger, "run_count", 0) or 0),
            debounce_secs=float(gates.get("debounce_secs", _DEFAULT_DEBOUNCE_SECS) or 0),
            last_fired_at=_timestamp(getattr(trigger, "last_fired_at", "")),
        )

    def _load_events(self) -> list[EventTrigger]:
        store = self._get_store()
        from gideon.automation.triggers.store import TriggerStore

        if isinstance(store, TriggerStore):
            from gideon.automation.triggers.grants import action_revision

            events = []
            for row in store.load():
                if row.trigger.kind != "event" or not row.ok:
                    continue
                event = self._event_view(row.trigger)
                event._action_revision = action_revision(row.trigger)
                events.append(event)
            return events
        return list(store.load())

    def _record_fire(self, trigger_id: str, *, now: float) -> None:
        from gideon.automation.triggers.store import TriggerStore
        from gideon.automation.triggers.service import to_iso

        store = self._get_store()
        if not isinstance(store, TriggerStore):
            store.record_fire(trigger_id, now=now)
            return
        row = store.get(trigger_id)
        if row is None or row.trigger.kind != "event":
            return
        trigger = row.trigger
        trigger.run_count = int(trigger.run_count or 0) + 1
        trigger.last_fired_at = to_iso(now)
        maximum = int((trigger.spec or {}).get("max_fires") or 0)
        if maximum and trigger.run_count >= maximum:
            trigger.enabled = False
        store.upsert(trigger)

    def _record_outcome(self, trigger_id: str, *, status: str, error: str = "") -> None:
        from gideon.automation.triggers.models import TriggerHealth
        from gideon.automation.triggers.store import TriggerStore

        store = self._get_store()
        if not isinstance(store, TriggerStore):
            store.record_outcome(trigger_id, status=status, error=error)
            return
        row = store.get(trigger_id)
        if row is None or row.trigger.kind != "event":
            return
        row.trigger.health_status = (
            TriggerHealth.OK.value if status == "success" else TriggerHealth.FAILING.value
        )
        row.trigger.last_error_summary = error[:200]
        store.upsert(row.trigger)

    def on_event(
        self,
        *,
        source: str,
        event_type: str,
        key: str,
        value: str,
        now: float,
        meta: dict | None = None,
    ) -> None:
        from gideon.automation.triggers.event_fire import RoutedEvent, emit

        emit(RoutedEvent(source, event_type, key, value, now, dict(meta or {})))

    def _rate_ok(self, now: float) -> bool:
        retained = filter(
            lambda stamp: now - stamp < _RATE_WINDOW_SECS, self._fire_times
        )
        self._fire_times = list(retained)
        return len(self._fire_times) < _RATE_MAX_FIRES

    def _schedule_fire(
        self,
        t: EventTrigger,
        *,
        source: str,
        event_type: str,
        key: str,
        value: str,
        now: float,
        meta: dict | None = None,
    ) -> None:
        occurrence = EventOccurrence(source, event_type, key, value, meta)
        try:
            self._record_fire(t.id, now=now)
        except Exception:
            logger.debug("event-trigger record_fire failed", exc_info=True)
        try:
            active_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._spool(t, now=now, **occurrence.parameters())
        else:
            active_loop.create_task(
                self._fire(t, fired_at=now, **occurrence.parameters())
            )

    def _spool(
        self,
        t: EventTrigger,
        *,
        source: str,
        event_type: str,
        key: str,
        value: str,
        now: float,
        meta: dict | None = None,
    ) -> None:
        try:
            from gideon.automation.triggers.dispatch import Envelope, spool_fire

            payload: dict = dict(trigger_id=t.id, key=key, value=value)
            if meta:
                payload["meta"] = dict(meta)
            envelope = Envelope(
                seq=0,
                source=f"event:{t.id}",
                kind=f"{source}.{event_type}",
                payload=payload,
                emitted_at=now,
            )
            spool_fire(envelope)
        except Exception:
            logger.debug("could not spool the event fire for %s", t.id, exc_info=True)

    async def _fire(
        self,
        t: EventTrigger,
        *,
        source: str,
        event_type: str,
        key: str,
        value: str,
        meta: dict | None = None,
        fired_at: float = 0.0,
    ) -> None:
        occurrence = EventOccurrence(source, event_type, key, value, meta)
        try:
            store = self._get_store()
            from gideon.automation.triggers.firepath import FireContext, evaluate
            from gideon.automation.triggers.grants import action_revision, is_granted, required_provider
            from gideon.automation.triggers.screen import requested_capabilities
            from gideon.automation.triggers.store import TriggerStore

            if not isinstance(store, TriggerStore):
                result = await execute_event_action(t, **occurrence.parameters())
            else:
                row = store.get(t.id)
                if row is None or row.trigger.kind != "event" or not row.ok:
                    self._record_outcome(t.id, status="failure", error="current trigger is unavailable")
                    return
                current = row.trigger
                if action_revision(current) != getattr(t, "_action_revision", ""):
                    self._record_outcome(t.id, status="failure", error="trigger action changed before dispatch")
                    return
                provider_name = required_provider(current)
                if provider_name and not is_granted(current):
                    self._record_outcome(t.id, status="failure", error="owner action grant is missing")
                    return
                payload = dict(occurrence.parameters(), trigger_id=t.id)
                payload["value"] = _fence_fragment(
                    occurrence.value,
                    2000,
                    source=f"trigger:{t.id}:{source}.{event_type}",
                    source_type=f"event:{source}.{event_type}",
                    source_id=key,
                )
                context = FireContext(
                    trigger_id=t.id,
                    trigger=current,
                    payload_text=occurrence.value,
                    gates=current.gates or {},
                    capabilities=current.capabilities,
                    requested=requested_capabilities(current),
                    holder=f"event:{t.id}",
                    now=fired_at,
                )
                decision = await evaluate(context)
                if not decision.allowed:
                    self._record_outcome(t.id, status="failure", error=decision.reason)
                    return
                from gideon.automation.triggers.claims import write_claim, release_claim
                from gideon.interfaces.dashboard.handlers.triggers import _dispatch_store_action

                write_claim(decision.claim, base_dir=store.base_dir)
                try:
                    ran, note = await _dispatch_store_action(
                        current, payload, event=f"{source}.{event_type}"
                    )
                finally:
                    release_claim(t.id, base_dir=store.base_dir)
                result = FireOutcome(ran, "" if ran else note)
        except Exception as failure:
            from gideon.integrations.action_providers import provider_failure

            detail = provider_failure(t.action_provider, failure).render()
            logger.warning("event-trigger action failed for %s — %s", t.id, detail)
            self._record_outcome(t.id, status="failure", error=detail)
        else:
            if not result.ran:
                logger.debug("event-trigger %s did not run: %s", t.id, result.reason)
            outcome = result.to_dict()
            succeeded = result.ran and outcome.get("success", True)
            self._record_outcome(
                t.id,
                status="success" if succeeded else "failure",
                error="" if succeeded else str(outcome.get("error") or result.reason or "action failed"),
            )


def emit_event(
    *,
    source: str,
    event_type: str,
    key: str,
    value: str | None,
    now: float,
    meta: dict | None = None,
) -> None:
    try:
        from gideon.automation.triggers.event_fire import RoutedEvent, emit

        emit(RoutedEvent(source, event_type, key, value or "", now, dict(meta or {})))
    except Exception:
        logger.debug("emit_event failed", exc_info=True)
