"""Public Gideon SDK facade for its supervised Hypermid runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Protocol

from gideon.hypermid.client import HypermidClient
from gideon.hypermid.foundation import Cursor, Id, Scope
from gideon.hypermid.models import EffectStatus, JsonValue, Trace
from gideon.hypermid.modules import ToolModuleSpec
from gideon.hypermid.status import HypermidStatus
from gideon.hypermid.subscriptions import (
    DurableSubscription,
    ResumeStore,
    ScopedEvent,
    SubscriptionClient,
)
from gideon.hypermid.tool_provider import HypermidRoleToolProvider

_MAX_PENDING_DELIVERIES = 1_024


class HypermidSDKError(RuntimeError):
    pass


class RemoteHypermidClient(Protocol):
    async def request(
        self,
        operation: str,
        payload: JsonValue,
        *,
        trace: Trace | None = None,
        deadline_ms: int | None = None,
        effect_kind: str = "query",
        scope: Scope | None = None,
    ) -> JsonValue: ...


class HypermidRuntimeSDK:
    """Scoped facade; Gideon retains model, approval, tool and delivery authority."""

    def __init__(self, lifecycle: Any) -> None:
        adapter = getattr(lifecycle, "adapter", None)
        client = getattr(adapter, "client", None)
        if not isinstance(client, HypermidClient):
            raise HypermidSDKError("runtime has no configured Hypermid client")
        self._lifecycle = lifecycle
        self._client = client
        self._subscription_lock = asyncio.Lock()
        self._subscriptions: dict[Id, DurableSubscription] = {}
        self._deliveries: dict[tuple[Id, Id], ScopedEvent] = {}

    @classmethod
    def from_runtime(cls, runtime: Any) -> HypermidRuntimeSDK:
        lifecycle = getattr(runtime, "hypermid", None)
        if lifecycle is None:
            raise HypermidSDKError("runtime has no Hypermid lifecycle")
        return cls(lifecycle)

    @property
    def scope(self) -> Scope:
        return self._client.scope

    def status(self) -> HypermidStatus:
        return self._lifecycle.adapter.status()

    async def open_tool_module(
        self, session_key: str, spec: ToolModuleSpec
    ) -> HypermidRoleToolProvider:
        if not self.status().available:
            raise HypermidSDKError("Hypermid lifecycle is not authenticated")
        supervisor = getattr(self._lifecycle, "module_supervisor", None)
        if supervisor is None:
            raise HypermidSDKError("Hypermid module supervision is unavailable")
        return await supervisor.open_tool_module(session_key, spec)

    async def close_session(self, session_key: str) -> None:
        supervisor = getattr(self._lifecycle, "module_supervisor", None)
        if supervisor is not None:
            await supervisor.close_session(session_key)

    async def cancel(self, message_id: str) -> None:
        await self._client.cancel(message_id)

    async def effect_status(self, effect_id: str) -> EffectStatus:
        return await self._client.effect_status(effect_id)

    async def subscribe(
        self,
        scope: Scope,
        consumer_id: Id,
        topic_filter: str,
        after_cursor: Cursor | None = None,
        resume_store: ResumeStore | None = None,
    ) -> AsyncIterator[ScopedEvent]:
        self._require_scope(scope)
        consumer = Id(consumer_id)
        async with self._subscription_lock:
            if consumer in self._subscriptions:
                raise HypermidSDKError("consumer already has an active subscription")
            subscription = await SubscriptionClient(self._client).subscribe(
                consumer_id=consumer,
                scope=scope,
                topic_filter=topic_filter,
                resume_store=resume_store,
                after_cursor=after_cursor,
            )
            self._subscriptions[consumer] = subscription
        try:
            async for event in subscription:
                async with self._subscription_lock:
                    pending = sum(
                        1 for key in self._deliveries if key[0] == consumer
                    )
                    if pending >= _MAX_PENDING_DELIVERIES:
                        raise HypermidSDKError(
                            "consumer has too many unacknowledged events"
                        )
                    self._deliveries[(consumer, event.event_id)] = event
                yield event
        finally:
            async with self._subscription_lock:
                if self._subscriptions.get(consumer) is subscription:
                    self._subscriptions.pop(consumer, None)
                for key in [key for key in self._deliveries if key[0] == consumer]:
                    self._deliveries.pop(key, None)
            await subscription.close()

    async def ack(self, consumer_id: Id, event_id: Id) -> None:
        key = (Id(consumer_id), Id(event_id))
        async with self._subscription_lock:
            subscription = self._subscriptions.get(key[0])
            event = self._deliveries.get(key)
        if subscription is None or event is None:
            raise HypermidSDKError("event is not pending for this SDK consumer")
        await subscription.acknowledge(event)
        async with self._subscription_lock:
            if self._deliveries.get(key) is event:
                self._deliveries.pop(key, None)

    async def query_remote(
        self,
        remote: RemoteHypermidClient,
        operation: str,
        payload: JsonValue,
        *,
        trace: Trace | None = None,
        deadline_ms: int | None = None,
    ) -> JsonValue:
        return await remote.request(
            operation,
            payload,
            trace=trace,
            deadline_ms=deadline_ms,
            effect_kind="query",
            scope=self.scope,
        )

    async def effect_remote(
        self,
        remote: RemoteHypermidClient,
        operation: str,
        payload: JsonValue,
        *,
        trace: Trace | None = None,
        deadline_ms: int | None = None,
    ) -> JsonValue:
        return await remote.request(
            operation,
            payload,
            trace=trace,
            deadline_ms=deadline_ms,
            effect_kind="durable",
            scope=self.scope,
        )

    def _require_scope(self, scope: Scope) -> None:
        if scope != self.scope:
            raise HypermidSDKError("SDK scope does not match the authenticated session")


__all__ = [
    "HypermidRuntimeSDK",
    "HypermidSDKError",
    "RemoteHypermidClient",
    "ResumeStore",
    "ScopedEvent",
    "ToolModuleSpec",
]
