"""Abstract base for channel transport providers.

A *channel transport* carries messages between Gideon and an external
communication system (the dashboard Web UI, Slack, and future Telegram/Discord).
Each transport owns one external system: it connects, sends outbound messages,
and reports its own health. Inbound delivery is transport-specific — see the
note on the inbound seam below.

**Inbound:** transports do NOT own an inbound dispatch loop here. For a channel
app like Slack, the
Socket-Mode receiver lives in ``gideon.engine.gateway`` and routes inbound
messages straight to chat sessions; for the Web UI, the dashboard chat runner is
the canonical consumer. The ``ChannelManager`` (comms) is a management +
visibility surface over the registered transports, not a second inbound router.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class OutboundMessage:
    """A message to send to an external channel."""

    channel_id: str
    text: str
    thread_id: str = ""
    sender: str = "gideon"
    metadata: dict[str, Any] | None = None


@dataclass
class ChannelMessage:
    """The symmetric INBOUND shape (#40) — the dual of :class:`OutboundMessage`.

    A transport that owns an inbound source normalizes its native payload to this,
    so the platform sees one canonical inbound message regardless of channel."""

    channel_id: str
    text: str
    sender: str = ""
    thread_id: str = ""
    message_id: str = ""
    ts: float = 0.0
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelCapabilities:
    """What an adapter can do (#40) — machine-readable so the platform can route /
    feature-gate. Defaults are conservative (text-out only, no inbound)."""

    inbound: bool = False
    threads: bool = False
    attachments: bool = False
    reactions: bool = False
    edits: bool = False
    rich_text: bool = False
    typing_indicator: bool = False
    max_text_len: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            member.name: deepcopy(getattr(self, member.name)) for member in fields(self)
        }


class ChannelTransportProvider(ABC):
    """Provider interface for an external comms transport.

    The platform calls :meth:`send` to deliver outbound messages and
    :meth:`health` to surface connection state on the Channels page. Concrete
    transports declare their own credentials/lifecycle (e.g. Slack tokens from
    the ``slack-channel`` extension instance config).
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def connect(self) -> bool:
        """Initialize the connection to the external system. Returns success."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> bool:
        """Send a message to the external channel. Returns success."""
        ...

    @property
    def connected(self) -> bool:
        return False

    def capabilities(self) -> ChannelCapabilities:
        """What this adapter can do (#40). Default: text-out only, no inbound.
        Adapters override to declare threads/attachments/reactions/edits/etc."""
        return ChannelCapabilities()

    def receive(self) -> AsyncIterator[ChannelMessage]:
        """Optional inbound seam (#40): yield normalized :class:`ChannelMessage`s.

        Default raises — most transports keep their existing inbound path (Slack's
        socket receiver, the WebUI chat runner). A new pull-based adapter overrides
        this to emit the canonical inbound shape."""
        raise NotImplementedError(f"{self.name} has no inbound receive loop")

    async def start_inbound(self, services: "Any") -> None:
        """Start this transport's inbound receiver, driving the platform runtime.

        Called once by the gateway at boot AFTER core services are up, passing a
        :class:`~gideon.engine.gateway_services.GatewayServices` handle (sessions,
        cron, channel history, dashboard state, config, owner). A transport that
        owns a push receiver (Slack Socket-Mode) connects here and routes inbound
        messages to chat sessions via ``services``. Default: no inbound (the Web UI
        drives its own inbound through the dashboard chat runner)."""
        return None

    async def stop_inbound(self) -> None:
        """Gracefully stop the inbound receiver started by :meth:`start_inbound`."""
        return None

    async def health(self) -> dict[str, Any]:
        """Default operational status for adapters with no richer readiness probe."""
        status = (
            ("ready", "connected") if self.connected else ("offline", "not connected")
        )
        return dict(zip(("state", "detail"), status))

    async def test(self) -> dict[str, Any]:
        """Map the readiness probe into the management surface's test result."""
        status = await self.health()
        result = {"ok": status.get("state") == "ready"}
        result["detail"] = status.get("detail", "")
        return result

    def info(self) -> dict[str, Any]:
        descriptor = {
            name: getattr(self, name) for name in ("name", "display_name", "connected")
        }
        descriptor["capabilities"] = self.capabilities().to_dict()
        return descriptor
