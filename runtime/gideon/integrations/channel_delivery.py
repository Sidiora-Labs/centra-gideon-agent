"""Outbound channel contract and the process-wide provider directory."""

from __future__ import annotations

from dataclasses import dataclass, is_dataclass, replace
import logging
from copy import copy
from threading import RLock
from typing import Any, Callable, Protocol, runtime_checkable

from gideon.integrations.outbound_queue import QueuedDelivery
from gideon.security.security import redact_for_display, redact_values_for_display

logger = logging.getLogger(__name__)


@runtime_checkable
class ChannelDelivery(Protocol):
    """Outbound delivery a channel provides to the gateway. All text is PLAIN
    markdown — the implementation renders it to the channel's format."""

    async def open_dm(self, user_id: str) -> str:
        """Open (or resolve) a DM channel with a user; return its channel id."""
        ...

    async def deliver_text(
        self,
        channel: str,
        text: str,
        thread_ts: str = "",
        *,
        unfurl_links: "bool | None" = None,
        unfurl_media: "bool | None" = None,
        reply_broadcast: "bool | None" = None,
    ) -> str:
        """Post plain-markdown text to a channel/thread; return the message ts. The
        optional link-preview / broadcast hints are generic messaging concepts a
        channel applies if it supports them (ignored otherwise)."""
        ...

    async def deliver_rich(
        self,
        channel: str,
        payload: "object",
        fallback_text: str,
        *,
        thread_ts: str = "",
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        reply_broadcast: bool = False,
    ) -> str:
        """Deliver a caller-supplied structured/rich payload (e.g. Block Kit) with a
        plain-text fallback; return the message ts. The payload is opaque to core —
        callers pass channel-shaped structures through; a channel that can't render
        rich content falls back to ``fallback_text``."""
        ...

    async def deliver_cron_result(
        self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a cron job result with the channel's ack affordance; return the
        parent message ts (for threading follow-ups)."""
        ...

    async def deliver_notification(
        self, channel: str, title: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a titled notification (heartbeat/subagent) to a channel/thread."""
        ...

    async def deliver_chat_mirror(
        self, channel: str, text: str, thread_ts: str = ""
    ) -> None:
        """Mirror a dashboard chat reply to a linked channel thread, rendering any
        trailing ``[OPTIONS: …]`` block as the channel's interactive affordance."""
        ...

    async def deliver_subagent_reply(
        self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0
    ) -> None:
        """Deliver a subagent's synthesized reply to a channel/thread, with the
        channel's timing affordance (a footer showing how long the run took)."""
        ...

    async def resolve_user_name(self, user_id: str) -> str:
        """Human-readable display name for a channel user id (best-effort; returns
        the id or empty on failure). Used by inbox sender-name resolution."""
        ...

    async def resolve_user_profile(self, user_id: str) -> "dict":
        """Full profile dict for a channel user (name/real_name/title/etc.); ``{}`` on
        failure. Shape is provider-defined — callers read known keys defensively."""
        ...

    async def channel_info(self, channel_id: str) -> "dict":
        """Metadata for a channel (e.g. ``{"name": ..., "is_im": ...}``); ``{}`` on
        failure. Provider-agnostic shape — callers read known keys defensively."""
        ...

    def list_reply_channels(self) -> "list[dict]":
        """Channels this delivery can post replies into, as ``{"id", "name"}`` dicts
        (the channel app's own config decides — tracked/active channels). Used by the
        dashboard's channel picker for link/handoff. May be empty."""
        ...

    def is_tracked_channel(self, channel_id: str) -> bool:
        """Whether *channel_id* is in this channel's outbound allowlist (the app's
        own tracked-channel config). Core consults this for targeted sends."""
        ...

    def build_thread_link(self, channel: str, ts: str) -> str:
        """Deep link to a message/thread on this channel provider (e.g. a
        jump-to-source URL for notifications). Returns "" when the provider has
        no linkable surface. Core never constructs vendor URLs itself — the
        provider owns its own link format."""
        ...

    async def upload_attachment(
        self,
        channel: str,
        file_path: str,
        *,
        filename: str = "",
        thread_ts: str = "",
        title: str = "",
        initial_comment: str = "",
    ) -> str:
        """Upload a file to a channel/thread; return the delivered message ts (or "")."""
        ...

    async def start_stream(
        self, channel: str, thread_ts: str = "", initial_text: str = ""
    ) -> str:
        """Begin a live-updating stream message (for tool/progress animation); return
        its ts, or "" if the channel has no streaming affordance."""
        ...

    async def append_stream_task(
        self,
        channel: str,
        stream_ts: str,
        task_id: str,
        title: str,
        status: str,
    ) -> None:
        """Append/update a progress item on an in-flight stream started by
        start_stream. ``status`` is a generic progress state ("in_progress" /
        "complete"). Channels without task-animation may no-op."""
        ...

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        """Finalize a stream started by start_stream."""
        ...

    async def request_approval(
        self,
        event: "object",
        *,
        source: str,
        parent_session_key: str = "",
        sessions: "object | None" = None,
        on_prompted: "Callable[[object], None] | None" = None,
    ) -> "bool | None":
        """Prompt the owner to approve a tool call on this channel.

        Returns ``True`` (approved) / ``False`` (rejected), or ``None`` if the
        channel can't prompt (no owner/channel) so the gateway falls back to the
        dashboard. Implementations own the channel-specific approval UI + the wait
        for the owner's response, and should coordinate with the dashboard via the
        ``on_prompted`` hook (invoked with the pending record) when provided by the
        caller. ``sessions`` is the live ConversationDirectory for cross-surface reconcile.

        **The approval brief (additive meta).** ``event.tool_meta`` carries the core-
        composed brief under
        :data:`~gideon.security.approval_brief.APPROVAL_BRIEF_META_KEY`, so a channel can
        tell the owner what the call would TOUCH, not just what it is called::

            {"tool": str,              # tool identity, same value as event.title
             "risk": str,              # EFFECTIVE per-invocation risk (not the
                                       #   DECLARED event.risk_level)
             "blastRadius": {"writes": bool, "network": bool,
                             "shell": bool, "readOnly": bool},   # optional
             "blastRadiusLine": str}                             # optional

        Reading it is OPTIONAL and purely additive: the method's arguments are
        unchanged, no existing field or ``tool_meta`` key is replaced, and a channel
        that ignores the key prompts exactly as it did before. Two rules for a
        renderer:

        * ``blastRadius``/``blastRadiusLine`` are ABSENT when nothing could be
          established — show no blast-radius line at all, rather than "nothing
          established", which reads as "nothing happens";
        * every facet is a POSITIVE claim, so render only the ``True`` ones. Never
          enumerate all four with on/off states: a ``False`` means "not established",
          and painting it as "no network" turns absence of evidence into an all-clear.

        The brief is a compact summary for a surface with no room — the dashboard
        remains the rich approval surface, and rendering logic stays in the channel's
        own bundle."""
        ...


_REGISTRY: dict[str, ChannelDelivery] = {}
_QUEUES: dict[str, QueuedDelivery] = {}
_REGISTRY_LOCK = RLock()
_PROVIDER_SUFFIXES = ("_runtime", "_channel", "_delivery")


_TEXT_ARGUMENTS: dict[str, dict[str, int | None]] = {
    "deliver_text": {"text": 1},
    "deliver_rich": {"fallback_text": 2},
    "deliver_cron_result": {"job_name": 1, "text": 3},
    "deliver_notification": {"title": 1, "text": 2},
    "deliver_chat_mirror": {"text": 1},
    "deliver_subagent_reply": {"text": 1},
    "upload_attachment": {"title": None, "initial_comment": None},
    "start_stream": {"initial_text": 2},
    "append_stream_task": {"title": 3},
    "send": {},
}

_RICH_TEXT_FIELDS = frozenset(
    {
        "text",
        "title",
        "description",
        "caption",
        "fallback",
        "fallback_text",
        "initial_comment",
        "alt_text",
        "label",
        "header",
        "footer",
        "markdown",
        "content",
        "body",
        "summary",
    }
)
_OPAQUE_TEXT_FIELDS = frozenset(
    {
        "id",
        "request_id",
        "tool_call_id",
        "user_id",
        "channel",
        "channel_id",
        "thread_ts",
        "stream_ts",
        "task_id",
        "action_id",
        "callback_data",
        "value",
        "path",
        "file_path",
        "filename",
    }
)


def _mask_rich_value(value: Any) -> Any:
    """Mask visible rich-payload text while preserving opaque routing and button values."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _OPAQUE_TEXT_FIELDS or key.endswith("_id"):
                out[key] = item
            elif isinstance(item, str) and key in _RICH_TEXT_FIELDS:
                out[key] = redact_for_display(item)
            else:
                out[key] = _mask_rich_value(item)
        return out
    if isinstance(value, list):
        return [_mask_rich_value(item) for item in value]
    return value


def _mask_approval(event: Any) -> Any:
    """Copy an approval brief and mask its display text without changing response IDs."""
    if isinstance(event, dict):
        masked = dict(event)
        for field in ("title", "text", "tool_purpose"):
            if isinstance(masked.get(field), str):
                masked[field] = redact_for_display(masked[field])
        for field in ("tool_input", "tool_input_obj", "tool_meta"):
            if field in masked:
                masked[field] = redact_values_for_display(masked[field])
        return masked

    changes: dict[str, Any] = {}
    for field in ("title", "text", "tool_purpose"):
        value = getattr(event, field, None)
        if isinstance(value, str):
            changes[field] = redact_for_display(value)
    for field in ("tool_input", "tool_input_obj", "tool_meta"):
        if hasattr(event, field):
            changes[field] = redact_values_for_display(getattr(event, field))
    if not changes:
        return event
    if is_dataclass(event):
        return replace(event, **changes)
    masked = copy(event)
    for field, value in changes.items():
        try:
            setattr(masked, field, value)
        except (AttributeError, TypeError):
            raise TypeError("approval event cannot be safely masked")
    return masked


def _mask_outbound_message(message: Any) -> Any:
    """Mask canonical transport text without changing its routing identity."""
    if isinstance(message, dict):
        masked = dict(message)
        if isinstance(masked.get("text"), str):
            masked["text"] = redact_for_display(masked["text"])
        if "metadata" in masked:
            masked["metadata"] = _mask_rich_value(masked["metadata"])
        return masked
    changes: dict[str, Any] = {}
    text = getattr(message, "text", None)
    if isinstance(text, str):
        changes["text"] = redact_for_display(text)
    if hasattr(message, "metadata"):
        changes["metadata"] = _mask_rich_value(getattr(message, "metadata"))
    if not changes:
        raise TypeError("channel send message has no maskable text field")
    if is_dataclass(message):
        return replace(message, **changes)
    masked = copy(message)
    for field, value in changes.items():
        try:
            setattr(masked, field, value)
        except (AttributeError, TypeError):
            raise TypeError("channel send message cannot be safely masked")
    return masked


def _mask_call(name: str, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if name == "send":
        positional = list(args)
        keyword = dict(kwargs)
        if positional:
            positional[0] = _mask_outbound_message(positional[0])
        elif "message" in keyword:
            keyword["message"] = _mask_outbound_message(keyword["message"])
        else:
            raise TypeError("channel send message is required")
        return method(*positional, **keyword)
    if name == "request_approval":
        if args:
            args = (_mask_approval(args[0]), *args[1:])
        elif "event" in kwargs:
            kwargs = {**kwargs, "event": _mask_approval(kwargs["event"])}
        return method(*args, **kwargs)

    positional = list(args)
    keyword = dict(kwargs)
    if name == "deliver_rich":
        if len(positional) > 1:
            positional[1] = _mask_rich_value(positional[1])
        elif "payload" in keyword:
            keyword["payload"] = _mask_rich_value(keyword["payload"])

    for field, position in _TEXT_ARGUMENTS.get(name, {}).items():
        if position is not None and len(positional) > position:
            positional[position] = redact_for_display(positional[position]) if isinstance(positional[position], str) else positional[position]
        elif field in keyword and isinstance(keyword[field], str):
            keyword[field] = redact_for_display(keyword[field])
    return method(*positional, **keyword)


class MaskedDelivery:
    """A registration-boundary view that masks customer text before any channel call."""

    def __init__(self, inner: ChannelDelivery) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.inner, name)
        if name not in _TEXT_ARGUMENTS and name != "request_approval":
            return attribute

        def masked(*args: Any, **kwargs: Any) -> Any:
            return _mask_call(name, attribute, *args, **kwargs)

        return masked


def provider_of(delivery: Any) -> str:
    """Derive a compatibility namespace when the transport supplies no key."""
    implementation = type(delivery)
    namespace = (getattr(implementation, "__module__", "") or "").partition(".")[0]
    suffix = next((part for part in _PROVIDER_SUFFIXES if namespace.endswith(part)), "")
    stem = namespace.removesuffix(suffix) if suffix else namespace
    return stem or implementation.__name__.lower()


def register(delivery: ChannelDelivery | None, provider: str = "") -> str:
    """Publish one provider change, or clear all providers during shutdown."""
    global _REGISTRY, _QUEUES
    if isinstance(delivery, MaskedDelivery):
        delivery = delivery.inner
    key = provider or (provider_of(delivery) if delivery is not None else "")
    with _REGISTRY_LOCK:
        updated = dict(_REGISTRY)
        queues = dict(_QUEUES)
        retired_queues = list(queues.values()) if not key and delivery is None else []
        previous = updated.get(key)
        previous_queue = queues.get(key)
        if delivery is not None:
            updated[key] = delivery
            if previous is not delivery:
                queues[key] = QueuedDelivery(key, delivery)
        elif key:
            updated.pop(key, None)
            queues.pop(key, None)
        else:
            updated.clear()
            queues.clear()
        _REGISTRY = updated
        _QUEUES = queues
        if previous_queue is not None and previous is not delivery:
            previous_queue.retire()
        for queue in retired_queues:
            queue.retire()
    if delivery is not None and previous is not None and previous is not delivery:
        logger.debug("channel delivery re-registered for %s", key)
    return key


def delivery_for(provider: str) -> ChannelDelivery | None:
    """Resolve an origin provider; an absent origin never selects another channel."""
    snapshot = _QUEUES
    return snapshot.get(provider) if provider else None


def raw_delivery_for(provider: str) -> ChannelDelivery | None:
    return _REGISTRY.get(provider) if provider else None


def provider_for_delivery(delivery: Any) -> str:
    """Return the registered provider owning a resolved delivery handle."""
    return next(
        (provider for provider, candidate in _QUEUES.items() if candidate is delivery),
        "",
    )


@dataclass(frozen=True)
class OwnerReachResult:
    delivered: bool
    provider: str = ""
    reason: str = ""
    connected_channels: int = 0
    attempted_channels: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


async def reach_owner(
    send: Callable[[str, ChannelDelivery, str], Any],
    *,
    only: tuple[str, ...] = (),
    inbox_fallback: Callable[[str], Any] | None = None,
) -> OwnerReachResult:
    """Try connected channels in stable order using each channel's own owner id.

    Inbox fallback runs only when at least one channel is connected and every
    eligible channel failed. A deployment with no connected channels stays quiet.
    """
    from gideon.core.config.credentials import owner_id_for
    from gideon.integrations.channel_transports import get_transport

    snapshot = _QUEUES
    candidates = [name for name in sorted(snapshot) if not only or name in only]
    connected = 0
    attempted: list[str] = []
    reasons: list[str] = []
    for provider in candidates:
        transport = get_transport(provider)
        if transport is not None and not transport.connected:
            continue
        connected += 1
        owner_id = owner_id_for(provider)
        if not owner_id:
            reasons.append(f"{provider}: owner not configured")
            continue
        delivery = snapshot[provider]
        attempted.append(provider)
        try:
            destination = await delivery.open_dm(owner_id)
            if not destination:
                reasons.append(f"{provider}: owner destination unavailable")
                continue
            if await send(provider, delivery, destination):
                return OwnerReachResult(
                    True, provider, "delivered", connected, tuple(attempted),
                    tuple(reasons),
                )
            reasons.append(f"{provider}: delivery refused")
        except Exception:
            logger.info("owner delivery failed for provider=%s", provider)
            reasons.append(f"{provider}: delivery failed")
    if connected == 0:
        return OwnerReachResult(False, reason="no connected channels", connected_channels=0)
    reason = "; ".join(reasons) or "no owner channel could deliver"
    if inbox_fallback is not None:
        try:
            await inbox_fallback(reason)
            return OwnerReachResult(
                False, reason="inbox fallback", connected_channels=connected,
                attempted_channels=tuple(attempted), failures=tuple(reasons),
            )
        except Exception:
            logger.info("owner delivery inbox fallback failed")
            reason = "channel delivery and inbox fallback failed"
    return OwnerReachResult(
        False, reason=reason, connected_channels=connected,
        attempted_channels=tuple(attempted), failures=tuple(reasons),
    )


def owner_reachable() -> ChannelDelivery | None:
    """Return a connected channel with its own owner configured, in stable order."""
    from gideon.core.config.credentials import owner_id_for
    from gideon.integrations.channel_transports import get_transport

    snapshot = _QUEUES
    for provider in sorted(snapshot):
        transport = get_transport(provider)
        if (transport is None or transport.connected) and owner_id_for(provider):
            return snapshot[provider]
    return None


def registered_providers() -> list[str]:
    """Return a stable inventory of the currently connected providers."""
    return sorted(_REGISTRY)


def approval_channel() -> str:
    """Configured explicit approval channel, or the empty owner-default choice."""
    from gideon.core.config.loader import AppConfig

    return str(getattr(AppConfig.load().agent, "approval_channel", "") or "").strip()


def approval_delivery(origin: str = "") -> tuple[str, ChannelDelivery] | None:
    """Resolve approval delivery: captured origin first, then the explicit choice.

    An unavailable explicit choice is terminal. It must never send an approval prompt
    through a different channel that the owner did not select.
    """
    from gideon.core.config.credentials import owner_id_for
    from gideon.integrations.channel_transports import get_transport

    snapshot = _QUEUES

    def usable(provider: str) -> ChannelDelivery | None:
        delivery = snapshot.get(provider)
        transport = get_transport(provider)
        if (
            delivery is None
            or not owner_id_for(provider)
            or (transport is not None and not transport.connected)
        ):
            return None
        return delivery

    if origin and (delivery := usable(origin)) is not None:
        return origin, delivery
    explicit = approval_channel()
    if explicit:
        delivery = usable(explicit)
        return (explicit, delivery) if delivery is not None else None
    for provider in sorted(snapshot):
        if (delivery := usable(provider)) is not None:
            return provider, delivery
    return None
