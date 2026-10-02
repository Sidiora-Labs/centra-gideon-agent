"""Gideon's authority-preserving Hypermid host adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from gideon.cognition.context_engine import (
    AssembledContext,
    ContextEngine,
    DefaultContextEngine,
)

from .client import HypermidClient
from .models import JsonValue, Trace
from .status import HypermidStatus, HypermidStatusStore

if TYPE_CHECKING:
    from gideon.cognition.context import PromptAssembler

log = logging.getLogger(__name__)

_MODES = frozenset({"off", "pass_through", "shadow", "primary"})


def mode_value(value: object) -> str:
    result = str(getattr(value, "value", value))
    if result not in _MODES:
        raise ValueError(f"unsupported Hypermid mode {result!r}")
    return result


class HypermidAdapter:
    """Authenticated daemon access behind Gideon's existing context contract.

    Off, pass-through and shadow modes always return the built-in Gideon assembly
    unchanged. The adapter owns no provider, tool, approval, transcript or result
    delivery path.
    """

    name = "hypermid"

    def __init__(
        self,
        client: HypermidClient | None,
        *,
        mode: object = "off",
        status: HypermidStatusStore | None = None,
        delegate: ContextEngine | None = None,
    ) -> None:
        self.client = client
        self._mode = mode_value(mode)
        self._status = status or HypermidStatusStore(self._mode)
        self._delegate = delegate or DefaultContextEngine()
        self._started = False

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: object) -> HypermidStatus:
        self._mode = mode_value(mode)
        return self._status.mode_changed(self._mode)

    def apply_writer_snapshot(self, snapshot: object) -> HypermidStatus:
        return self._status.authority(self._mode, snapshot)

    @property
    def delegate(self) -> ContextEngine:
        return self._delegate

    def set_delegate(self, delegate: ContextEngine) -> None:
        self._delegate = delegate

    @property
    def owns_compaction(self) -> bool:
        snapshot = self._status.snapshot()
        return (
            self._mode == "primary"
            and snapshot.writer == "hypermid"
            and snapshot.lease_state == "held"
        )

    async def start(self) -> HypermidStatus:
        if self._mode == "off":
            self._started = True
            return self._status.disabled(self._mode)
        self._status.starting(self._mode)
        client = self.client
        if client is None:
            self._started = False
            return self._status.unavailable(
                self._mode,
                "Hypermid connection is not configured",
                code="NOT_CONFIGURED",
            )
        try:
            session = await client.connect()
            description = await client.negotiate(("server.describe", "passthrough"))
            if description.daemon_instance_id != client.daemon_instance_id:
                raise ValueError("daemon description identity does not match handshake")
            status = self._status.ready(
                self._mode,
                session,
                description,
                daemon_instance_id=client.daemon_instance_id,
            )
        except Exception as error:
            self._started = False
            try:
                await client.close()
            except Exception:
                log.debug("Hypermid client cleanup failed", exc_info=True)
            log.warning("Hypermid is unavailable; Gideon remains authoritative: %s", error)
            return self._status.unavailable(self._mode, error)
        self._started = True
        return status

    async def stop(self) -> None:
        self._status.draining()
        client = self.client
        if client is not None:
            try:
                await client.close()
            except Exception:
                log.warning("Hypermid client close failed", exc_info=True)
        self._started = False
        self._status.stopped()

    def status(self) -> HypermidStatus:
        return self._status.snapshot()

    def mark_unavailable(
        self, error: BaseException | str, *, code: str | None = None
    ) -> HypermidStatus:
        self._started = False
        return self._status.unavailable(self._mode, error, code=code)

    async def passthrough(
        self, payload: JsonValue, *, trace: Trace | None = None
    ) -> JsonValue:
        """Round-trip an immutable value without granting daemon write authority."""
        if self._mode == "off":
            return payload
        client = self.client
        if client is None or not self._started or not client.connected:
            return payload
        try:
            result = await client.passthrough(payload, trace=trace)
        except Exception as error:
            self._started = False
            self._status.unavailable(self._mode, error)
            log.warning("Hypermid pass-through failed; retaining Gideon value: %s", error)
            return payload
        return result

    def ingest(self, session_key: str, role: str, content: str) -> None:
        self._delegate.ingest(session_key, role, content)

    def assemble(
        self,
        builder: PromptAssembler,
        text: str,
        *,
        is_new_session: bool,
        **kwargs: Any,
    ) -> AssembledContext:
        return self._delegate.assemble(
            builder,
            text,
            is_new_session=is_new_session,
            **kwargs,
        )

    def after_turn(self, session_key: str) -> None:
        self._delegate.after_turn(session_key)


__all__ = ["HypermidAdapter", "mode_value"]
