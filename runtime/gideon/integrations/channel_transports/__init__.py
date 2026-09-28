"""Live transport catalog and serialized owner of inbound receiver lifecycles."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any

from gideon.integrations.outbound_queue import QueuedDelivery

if TYPE_CHECKING:
    from gideon.integrations.channel_transports.base import ChannelTransportProvider

logger = logging.getLogger(__name__)

_transports: "dict[str, ChannelTransportProvider]" = {}
_senders: dict[str, QueuedDelivery] = {}
_catalog_lock = RLock()

START_TIMEOUT_SECS = 60.0
STOP_TIMEOUT_SECS = 10.0
HEALTH_TIMEOUT_SECS = 5.0
SETTLE_TIMEOUT_SECS = 30.0
WEBUI_TRANSPORT = "webui"
STARTING = "starting"


@dataclass
class _Receiver:
    transport: "ChannelTransportProvider"
    start: asyncio.Task[str]
    binding: "_Binding"

    @property
    def starting(self) -> bool:
        return not self.start.done()

    @property
    def failure(self) -> str:
        if not self.start.done() or self.start.cancelled():
            return ""
        try:
            return self.start.result()
        except Exception as exc:
            return _describe(exc)


@dataclass
class _Binding:
    services: Any
    loop: asyncio.AbstractEventLoop
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: asyncio.Task[None] | None = None
    again: bool = False


_binding: _Binding | None = None
_receivers: dict[str, _Receiver] = {}


def register_transport(provider: "ChannelTransportProvider") -> None:
    with _catalog_lock:
        previous = _transports.get(provider.name)
        if previous is not provider:
            retired = _senders.get(provider.name)
            if retired is not None:
                retired.retire()
            _senders[provider.name] = QueuedDelivery(provider.name, provider)
        _transports.update({provider.name: provider})
    request_reconcile()


def unregister_transport(name: str) -> None:
    with _catalog_lock:
        if name in _transports:
            del _transports[name]
        retired = _senders.pop(name, None)
        if retired is not None:
            retired.retire()
    request_reconcile()


def get_transport(name: str) -> "ChannelTransportProvider | None":
    with _catalog_lock:
        return _transports.get(name)


def queued_transport(name: str) -> QueuedDelivery | None:
    with _catalog_lock:
        return _senders.get(name)


def list_transports() -> list[str]:
    with _catalog_lock:
        return [*_transports]


def register_default_transports() -> None:
    from gideon.integrations.channel_transports.webui import create_provider

    register_transport(create_provider())


async def bind_inbound(services: Any) -> None:
    """Bind gateway services and start a non-blocking first reconciliation."""
    global _binding
    loop = asyncio.get_running_loop()
    current = _binding
    if current is not None and current.loop is loop and current.services is services:
        current.services = services
    else:
        _binding = _Binding(services=services, loop=loop)
    await reconcile_inbound()


async def unbind_inbound() -> None:
    """Stop all receivers, clear their delivery registrations, and forget the gateway."""
    global _binding
    binding, _binding = _binding, None
    if binding is None:
        return
    async with binding.lock:
        for name in list(_receivers):
            await _retire(name)
    from gideon.integrations.channel_delivery import register

    with _catalog_lock:
        names = tuple(_transports)
    for name in names:
        register(None, provider=name)


def request_reconcile() -> None:
    """Schedule one serialized pass, including when called from a worker thread."""
    binding = _binding
    if binding is None or binding.loop.is_closed():
        return
    try:
        on_loop = asyncio.get_running_loop() is binding.loop
    except RuntimeError:
        on_loop = False
    if on_loop:
        _schedule(binding)
        return
    try:
        binding.loop.call_soon_threadsafe(_schedule, binding)
    except RuntimeError:
        return


def _schedule(binding: _Binding) -> None:
    if _binding is not binding:
        return
    binding.again = True
    if binding.pending is None or binding.pending.done():
        binding.pending = binding.loop.create_task(
            _drain(binding), name="channel-receivers"
        )


async def _drain(binding: _Binding) -> None:
    while binding.again and _binding is binding:
        binding.again = False
        await reconcile_inbound()


async def settled() -> None:
    """Wait until all queued reconciliation passes have completed."""
    binding = _binding
    while binding is not None and binding.pending is not None and not binding.pending.done():
        await asyncio.wait({binding.pending})
        binding = _binding


def settle_from_thread(timeout: float = SETTLE_TIMEOUT_SECS) -> None:
    """Wait for a queued stop/reconcile from a non-loop lifecycle thread."""
    binding = _binding
    if binding is None or binding.loop.is_closed():
        return
    try:
        if asyncio.get_running_loop() is binding.loop:
            return
    except RuntimeError:
        pass
    try:
        asyncio.run_coroutine_threadsafe(settled(), binding.loop).result(timeout)
    except Exception:
        logger.warning("channel receiver reconciliation did not settle", exc_info=True)


async def reconcile_inbound() -> None:
    """Keep exactly the current configured receiver for each registered channel."""
    binding = _binding
    if binding is None:
        return
    async with binding.lock:
        if _binding is not binding:
            return
        with _catalog_lock:
            transports = dict(_transports)
        wanted: dict[str, "ChannelTransportProvider"] = {}
        for name, transport in transports.items():
            if name == WEBUI_TRANSPORT:
                continue
            configured = await _configured(transport)
            current = _receivers.get(name)
            if configured or (
                configured is None and current is not None and current.transport is transport
            ):
                wanted[name] = transport

        blocked: set[str] = set()
        for name, receiver in list(_receivers.items()):
            if receiver.binding is not binding or wanted.get(name) is not receiver.transport:
                if not await _retire(name):
                    blocked.add(name)
        for name, transport in wanted.items():
            if name not in _receivers and name not in blocked:
                _receivers[name] = _Receiver(
                    transport,
                    binding.loop.create_task(
                        _start(transport, binding.services),
                        name=f"channel-receiver:{name}",
                    ),
                    binding,
                )


async def _configured(transport: "ChannelTransportProvider") -> bool | None:
    # Catalog membership means this provider is enabled. Several adapters report
    # offline until start_inbound initiates pairing or polling.
    return True


async def _start(transport: "ChannelTransportProvider", services: Any) -> str:
    loop = asyncio.get_running_loop()
    began = loop.time()
    try:
        await asyncio.wait_for(
            transport.start_inbound(services), timeout=START_TIMEOUT_SECS
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        timed_out = isinstance(exc, TimeoutError) and loop.time() - began >= START_TIMEOUT_SECS
        reason = (
            f"it did not finish starting within {START_TIMEOUT_SECS:g} seconds"
            if timed_out
            else _describe(exc)
        )
        logger.warning("channel %s receiver did not start: %s", transport.name, reason)
        await _stop(transport)
        reason = reason.rstrip(". ")
        return (
            f"{transport.display_name} is not receiving messages — its receiver did not start: "
            f"{reason}. Fix its settings, or turn it off and on, to try again."
        )
    logger.info("channel %s receiver started", transport.name)
    return ""


async def _retire(name: str) -> bool:
    receiver = _receivers.get(name)
    if receiver is None:
        return True
    if receiver.starting:
        receiver.start.cancel()
        done, pending = await asyncio.wait({receiver.start}, timeout=STOP_TIMEOUT_SECS)
        if pending:
            receiver.start.add_done_callback(lambda _task: request_reconcile())
            logger.warning("channel %s receiver start did not cancel", name)
            return False
    _receivers.pop(name, None)
    if receiver.failure:
        return True
    await _stop(receiver.transport)
    return True


async def _stop(transport: "ChannelTransportProvider") -> None:
    try:
        await asyncio.wait_for(
            transport.stop_inbound(), timeout=STOP_TIMEOUT_SECS
        )
    except Exception:
        logger.warning("channel %s receiver stop failed", transport.name, exc_info=True)
    from gideon.integrations.channel_delivery import register

    register(None, provider=transport.name)


_URL_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]*://[^/\s'\"<>]+)[^\s'\"<>]*", re.IGNORECASE
)


def _safe_origin(match: re.Match[str]) -> str:
    scheme, separator, authority = match.group(1).partition("://")
    authority = authority.rsplit("@", 1)[-1]
    return f"{scheme}{separator}{authority}"


def _describe(exc: BaseException) -> str:
    from gideon.security.security import redact_for_display

    text = " ".join(str(exc).split())
    text = redact_for_display(_URL_RE.sub(_safe_origin, text))
    detail = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return detail if len(detail) <= 240 else detail[:239] + "…"


def _safe_detail(detail: Any) -> str:
    from gideon.security.security import redact_for_display

    text = " ".join(str(detail or "").split())
    text = redact_for_display(_URL_RE.sub(_safe_origin, text))
    return text if len(text) <= 240 else text[:239] + "…"


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_detail(value)
    if isinstance(value, dict):
        return {key: _safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_safe_value(item) for item in value)
    return value


async def channel_health(transport: "ChannelTransportProvider") -> dict[str, Any]:
    receiver = _receivers.get(transport.name)
    if receiver is not None and receiver.transport is transport:
        if receiver.starting:
            return {
                "state": STARTING,
                "detail": f"{transport.display_name} is starting to receive messages.",
            }
        if receiver.failure:
            return {"state": "error", "detail": receiver.failure}
    try:
        status = await asyncio.wait_for(
            transport.health(), timeout=HEALTH_TIMEOUT_SECS
        )
    except Exception as exc:
        return {"state": "error", "detail": _describe(exc)}
    return _safe_value(status)
