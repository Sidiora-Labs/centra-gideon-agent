"""Scoped durable-event integration over the authenticated Hypermid client."""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import EventSubscription, HypermidClient, HypermidProtocolError
from .foundation import Cursor, Digest, Id, Scope, Trace
from .models import Envelope, JsonValue, Principal, SubscriptionSnapshot


def _canonical_payload(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ScopedEvent:
    event_id: Id
    topic: str
    producer: Principal
    scope: Scope
    at_ms: int
    schema_name: str
    schema_version: int
    payload_digest: Digest
    trace: Trace | None
    cursor: Cursor
    payload: object
    delivery_count: int = 1

    @classmethod
    def from_wire(cls, value: object) -> ScopedEvent:
        if not isinstance(value, Mapping):
            raise HypermidProtocolError("event record must be an object")
        required = {
            "event_id",
            "topic",
            "producer",
            "scope",
            "at_ms",
            "schema_name",
            "schema_version",
            "payload_digest",
            "cursor",
            "payload",
        }
        optional = {"trace", "delivery_count", "subscription_id"}
        if not required.issubset(value) or set(value) - required - optional:
            raise HypermidProtocolError("event record fields do not match hypermid.v1")
        topic = value["topic"]
        schema_name = value["schema_name"]
        at_ms = value["at_ms"]
        schema_version = value["schema_version"]
        delivery_count = value.get("delivery_count", 1)
        if not isinstance(topic, str) or not topic or len(topic) > 256:
            raise HypermidProtocolError("event topic is invalid")
        if (
            not isinstance(schema_name, str)
            or not schema_name
            or len(schema_name) > 160
        ):
            raise HypermidProtocolError("event schema name is invalid")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in (at_ms, schema_version)
        ):
            raise HypermidProtocolError("event numeric metadata is invalid")
        if (
            isinstance(delivery_count, bool)
            or not isinstance(delivery_count, int)
            or delivery_count < 1
        ):
            raise HypermidProtocolError("event delivery count is invalid")
        digest = Digest(value["payload_digest"])
        if Digest.sha256(_canonical_payload(value["payload"])) != digest:
            raise HypermidProtocolError(
                "event payload digest does not match its payload"
            )
        trace_value = value.get("trace")
        return cls(
            event_id=Id(value["event_id"]),
            topic=topic,
            producer=Principal.from_wire(value["producer"]),
            scope=Scope.from_wire(value["scope"]),
            at_ms=at_ms,
            schema_name=schema_name,
            schema_version=schema_version,
            payload_digest=digest,
            trace=Trace.from_wire(trace_value) if trace_value is not None else None,
            cursor=Cursor.from_wire(value["cursor"]),
            payload=value["payload"],
            delivery_count=delivery_count,
        )

    def to_wire(self) -> dict[str, object]:
        value: dict[str, object] = {
            "event_id": str(self.event_id),
            "topic": self.topic,
            "producer": self.producer.to_wire(),
            "scope": self.scope.to_wire(),
            "at_ms": self.at_ms,
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "payload_digest": str(self.payload_digest),
            "cursor": self.cursor.to_wire(),
            "payload": self.payload,
            "delivery_count": self.delivery_count,
        }
        if self.trace is not None:
            value["trace"] = self.trace.to_wire()
        return value


@dataclass(frozen=True, slots=True)
class ResumePoint:
    consumer_id: Id
    scope: Scope
    topic_filter: str
    cursor: Cursor


class ResumeStore:
    """Owner-private atomic cursor storage for explicit daemon resubscription."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ResumePoint | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HypermidProtocolError(
                "subscription resume state is unreadable"
            ) from exc
        if not isinstance(raw, dict) or set(raw) != {
            "consumer_id",
            "scope",
            "topic_filter",
            "cursor",
        }:
            raise HypermidProtocolError("subscription resume state is invalid")
        return ResumePoint(
            consumer_id=Id(raw["consumer_id"]),
            scope=Scope.from_wire(raw["scope"]),
            topic_filter=str(raw["topic_filter"]),
            cursor=Cursor.from_wire(raw["cursor"]),
        )

    def save(self, point: ResumePoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        encoded = json.dumps(
            {
                "consumer_id": str(point.consumer_id),
                "scope": point.scope.to_wire(),
                "topic_filter": point.topic_filter,
                "cursor": point.cursor.to_wire(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class DurableSubscription(AsyncIterator[ScopedEvent]):
    def __init__(
        self,
        client: HypermidClient,
        stream: EventSubscription,
        point: ResumePoint,
        resume_store: ResumeStore | None,
        replay: tuple[ScopedEvent, ...] = (),
    ) -> None:
        self.client = client
        self.stream = stream
        self.point = point
        self.resume_store = resume_store
        self._replay = deque(replay)
        self._last_seen = point.cursor

    def __aiter__(self) -> DurableSubscription:
        return self

    async def __anext__(self) -> ScopedEvent:
        if self._replay:
            event = self._replay.popleft()
            self._validate_event(event)
            return event
        envelope = await self.stream.__anext__()
        event = self._event_from_envelope(envelope)
        self._validate_event(event)
        return event

    def _validate_event(self, event: ScopedEvent) -> None:
        if event.scope != self.point.scope:
            raise HypermidProtocolError(
                "daemon delivered an event outside the subscription scope"
            )
        if (
            event.cursor.epoch != self._last_seen.epoch
            or event.cursor.sequence <= self._last_seen.sequence
        ):
            raise HypermidProtocolError(
                "daemon delivered a replayed, reordered, or foreign-epoch event"
            )
        self._last_seen = event.cursor

    async def acknowledge(self, event: ScopedEvent) -> None:
        if event.scope != self.point.scope:
            raise HypermidProtocolError(
                "cannot acknowledge an event from another scope"
            )
        await self.client.request(
            "events.ack",
            {
                "consumer_id": str(self.point.consumer_id),
                "event_id": str(event.event_id),
            },
            scope=self.point.scope,
        )
        self.point = ResumePoint(
            self.point.consumer_id,
            self.point.scope,
            self.point.topic_filter,
            event.cursor,
        )
        if self.resume_store is not None:
            self.resume_store.save(self.point)

    async def close(self) -> None:
        try:
            await self.client.request(
                "events.unsubscribe",
                {"consumer_id": str(self.point.consumer_id)},
                scope=self.point.scope,
            )
        finally:
            await self.stream.close()

    @staticmethod
    def _event_from_envelope(envelope: Envelope) -> ScopedEvent:
        if envelope.kind != "event" or not isinstance(envelope.payload, Mapping):
            raise HypermidProtocolError("subscription yielded a non-event envelope")
        payload: object = envelope.payload.get("event", envelope.payload)
        if isinstance(payload, Mapping) and "delivery_count" not in payload:
            delivery_count = envelope.payload.get("delivery_count")
            if delivery_count is not None:
                payload = {**payload, "delivery_count": delivery_count}
        return ScopedEvent.from_wire(payload)


class SubscriptionClient:
    def __init__(self, client: HypermidClient) -> None:
        self.client = client

    async def publish(
        self,
        *,
        event_id: Id,
        topic: str,
        scope: Scope,
        at_ms: int,
        schema_name: str,
        schema_version: int,
        payload: object,
        trace: Trace | None = None,
    ) -> ScopedEvent:
        if scope != self.client.scope:
            raise HypermidProtocolError(
                "event scope does not match the authenticated session"
            )
        draft: dict[str, Any] = {
            "event_id": str(event_id),
            "topic": topic,
            "scope": scope.to_wire(),
            "at_ms": at_ms,
            "schema_name": schema_name,
            "schema_version": schema_version,
            "payload": payload,
        }
        if trace is not None:
            draft["trace"] = trace.to_wire()
        result = await self.client.request(
            "events.publish", draft, trace=trace, effect_kind="idempotent", scope=scope
        )
        return ScopedEvent.from_wire(result)

    async def subscribe(
        self,
        *,
        consumer_id: Id,
        scope: Scope,
        topic_filter: str,
        resume_store: ResumeStore | None = None,
        after_cursor: Cursor | None = None,
    ) -> DurableSubscription:
        if scope != self.client.scope:
            raise HypermidProtocolError(
                "subscription scope does not match the authenticated session"
            )
        stored = resume_store.load() if resume_store is not None else None
        if stored is not None and (
            stored.consumer_id != consumer_id
            or stored.scope != scope
            or stored.topic_filter != topic_filter
        ):
            raise HypermidProtocolError(
                "stored subscription identity does not match this request"
            )
        if (
            stored is not None
            and after_cursor is not None
            and stored.cursor != after_cursor
        ):
            raise HypermidProtocolError(
                "explicit cursor conflicts with durable resume state"
            )
        after = stored.cursor if stored is not None else after_cursor
        request: dict[str, JsonValue] = {
            "consumer_id": str(consumer_id),
            "scope": scope.to_wire(),
            "topic_filter": topic_filter,
        }
        if after is not None:
            request["after"] = after.to_wire()
        provisional = SubscriptionSnapshot(
            subscription_id=consumer_id,
            cursor=after or Cursor(1, 0),
        )
        stream = EventSubscription(self.client, provisional)
        if str(consumer_id) in self.client._subscriptions:
            raise HypermidProtocolError("consumer already has an active subscription")
        self.client._subscriptions[str(consumer_id)] = stream
        try:
            result = await self.client.request(
                "events.subscribe",
                request,
                scope=scope,
            )
        except BaseException:
            await stream.close()
            raise
        if not isinstance(result, Mapping):
            await stream.close()
            raise HypermidProtocolError(
                "daemon returned an invalid subscription snapshot"
            )
        try:
            snapshot_cursor = Cursor.from_wire(
                result.get("cursor", result.get("snapshot_cursor"))
            )
            subscription_id = Id(result.get("subscription_id", consumer_id))
            if subscription_id != consumer_id:
                raise HypermidProtocolError(
                    "daemon changed the durable consumer identity"
                )
            replay_value = result.get("replay", [])
            if not isinstance(replay_value, list):
                raise HypermidProtocolError("subscription replay must be an array")
            replay = tuple(ScopedEvent.from_wire(value) for value in replay_value)
        except BaseException:
            await stream.close()
            raise
        stream.snapshot = SubscriptionSnapshot(
            subscription_id=subscription_id, cursor=snapshot_cursor
        )
        initial_cursor = after or Cursor(snapshot_cursor.epoch, 0)
        point = stored or ResumePoint(consumer_id, scope, topic_filter, initial_cursor)
        return DurableSubscription(self.client, stream, point, resume_store, replay)


__all__ = [
    "DurableSubscription",
    "ResumePoint",
    "ResumeStore",
    "ScopedEvent",
    "SubscriptionClient",
]
