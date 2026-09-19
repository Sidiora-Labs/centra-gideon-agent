"""Per-runtime ACP resources with explicit borrowing and shutdown ownership."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from gideon.integrations.acp.session import AcpConnection
    from gideon.integrations.llm.base import ModelProvider

logger = logging.getLogger(__name__)
_SNAPSHOT_TTL_SECS = 600.0
_HEALTH_INTERVAL_SECS = 60.0
_WARM_BACKOFF_BASE_SECS = 60.0
_WARM_BACKOFF_MAX_SECS = 1800.0


@dataclass(slots=True)
class _Slot:
    provider: ModelProvider | None = None
    snapshot: dict = field(default_factory=dict)
    warmed_at: float = 0.0
    warming: asyncio.Lock = field(default_factory=asyncio.Lock)
    fail_count: int = 0
    next_retry_at: float = 0.0
    _shared_conn: AcpConnection | None = None

    def detach(self):
        current, self.provider = self.provider, None
        self.snapshot = {}
        self.warmed_at = 0.0
        return current

    def reset_retry(self) -> None:
        self.fail_count = 0
        self.next_retry_at = 0.0

    def accept(self, provider, snapshot: dict) -> None:
        self.provider = provider
        self.snapshot = snapshot
        self.warmed_at = time.monotonic()
        self.reset_retry()

    def failed(self) -> None:
        self.fail_count += 1
        delay = min(
            _WARM_BACKOFF_BASE_SECS * 2 ** min(self.fail_count - 1, 30),
            _WARM_BACKOFF_MAX_SECS,
        )
        self.next_retry_at = time.monotonic() + delay


class AcpConnectionPool:
    def __init__(
        self,
        *,
        provider_builder: Callable[[str], ModelProvider],
        start_sem: asyncio.Semaphore,
        readiness_check: Callable[[str], Awaitable[bool]] | None = None,
        snapshot_ttl_secs: float = _SNAPSHOT_TTL_SECS,
    ) -> None:
        self._build = provider_builder
        self._start_sem = start_sem
        self._readiness_check = readiness_check
        self._ttl = snapshot_ttl_secs
        self._slots: dict[str, _Slot] = {}
        self._lock = asyncio.Lock()
        self._runtimes: set[str] = set()
        self._health_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self._closed = False

    async def _slot(self, runtime_id: str) -> _Slot:
        async with self._lock:
            self._runtimes.add(runtime_id)
            if runtime_id not in self._slots:
                self._slots[runtime_id] = _Slot()
            return self._slots[runtime_id]

    async def _ready(self, runtime_id: str) -> bool:
        if self._closed:
            return False
        if self._readiness_check is None:
            return True
        try:
            return bool(await self._readiness_check(runtime_id))
        except Exception:
            logger.debug("ACP readiness unavailable for %s", runtime_id, exc_info=True)
            return False

    async def warm(self, runtime_id: str) -> bool:
        if not await self._ready(runtime_id):
            return False
        slot = await self._slot(runtime_id)
        async with slot.warming:
            if self._closed:
                return False
            if slot.provider is not None and _alive(slot.provider):
                return True
            previous = slot.detach()
            if previous is not None:
                await _safe_shutdown(previous)
            candidate = None
            try:
                candidate = self._build(runtime_id)
                async with self._start_sem:
                    if self._closed:
                        return False
                    await candidate.start()
                if self._closed:
                    return False
                snapshot = dict(getattr(candidate, "session_snapshot", {}) or {})
                slot.accept(candidate, snapshot)
                candidate = None
                logger.info(
                    "ACP runtime %s warmed with %d modes",
                    runtime_id,
                    _snapshot_agent_count(slot.snapshot),
                )
                return True
            except Exception:
                slot.failed()
                logger.warning(
                    "ACP runtime %s could not warm", runtime_id, exc_info=True
                )
                return False
            finally:
                if candidate is not None:
                    await _safe_shutdown(candidate)

    async def warm_all(self, runtime_ids: list[str]) -> int:
        outcomes = await asyncio.gather(
            *(self.warm(name) for name in runtime_ids), return_exceptions=True
        )
        return sum(result is True for result in outcomes)

    def snapshot(self, runtime_id: str) -> dict | None:
        slot = self._slots.get(runtime_id)
        if slot is None or not slot.snapshot or not self.is_warmed(runtime_id):
            return None
        if time.monotonic() >= slot.warmed_at + self._ttl:
            return None
        return dict(slot.snapshot)

    def is_warmed(self, runtime_id: str) -> bool:
        slot = self._slots.get(runtime_id)
        return slot is not None and slot.provider is not None and _alive(slot.provider)

    async def open_session(
        self,
        runtime_id: str,
        *,
        cwd,
        command: list[str],
        dialect: str | None,
        session_files_dir=None,
        sandbox_mode: str = "auto",
        extra_env: dict | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
        model: str = "",
        agent_name: str = "",
        mcp_servers: list | None = None,
    ) -> ModelProvider | None:
        if self._closed:
            return None
        try:
            from gideon.integrations.llm.acp_session_provider import (
                open_acp_session_provider,
            )

            connection = await self._shared_connection(
                runtime_id,
                cwd=cwd,
                command=command,
                dialect=dialect,
                sandbox_mode=sandbox_mode,
                extra_env=extra_env,
                session_key=session_key,
                channel_id=channel_id,
            )
            if connection is None or self._closed:
                return None
            return await open_acp_session_provider(
                connection,
                runtime_id=runtime_id,
                cwd=cwd,
                session_files_dir=session_files_dir,
                model=model,
                agent_name=agent_name,
                session_key=session_key,
                mcp_servers=mcp_servers,
            )
        except Exception:
            logger.warning(
                "ACP session could not open on %s", runtime_id, exc_info=True
            )
            return None

    async def _shared_connection(
        self,
        runtime_id: str,
        *,
        cwd,
        command,
        dialect,
        sandbox_mode,
        extra_env,
        session_key,
        channel_id,
    ):
        from gideon.integrations.acp.client import CLIENT_NAME, CLIENT_VERSION
        from gideon.integrations.acp.dialect import get_dialect
        from gideon.integrations.acp.session import AcpConnection

        slot = await self._slot(runtime_id)
        async with slot.warming:
            if self._closed:
                return None
            if slot._shared_conn is not None and _alive(slot._shared_conn):
                return slot._shared_conn
            previous, slot._shared_conn = slot._shared_conn, None
            if previous is not None:
                await _close_connection(previous)
            candidate = None
            protocol = get_dialect(dialect)
            try:
                async with self._start_sem:
                    if self._closed:
                        return None
                    candidate = await AcpConnection.spawn(
                        command=command,
                        work_dir=cwd,
                        dialect=protocol,
                        sandbox_mode=sandbox_mode,
                        extra_env=extra_env,
                        session_key=session_key,
                        channel_id=channel_id,
                    )
                    await candidate.initialize(
                        {
                            "protocolVersion": protocol.protocol_version(),
                            "clientInfo": protocol.client_info(
                                client_name=CLIENT_NAME, client_version=CLIENT_VERSION
                            ),
                        }
                    )
                if self._closed:
                    return None
                slot._shared_conn, candidate = candidate, None
                return slot._shared_conn
            finally:
                if candidate is not None:
                    await _close_connection(candidate)

    async def claim(self, runtime_id: str, *, holder: str = "") -> ModelProvider | None:
        slot = self._slots.get(runtime_id)
        if self._closed or slot is None or slot.provider is None:
            return None
        async with slot.warming:
            if self._closed or slot.provider is None or not _alive(slot.provider):
                return None
            provider = slot.detach()
        if holder:
            self._record_lease(runtime_id, holder)
        self._spawn_bg(self.warm(runtime_id))
        return provider

    @staticmethod
    def _record_lease(runtime_id: str, holder: str) -> None:
        try:
            from gideon.engine.agents.runner_lifecycle import claim_runner

            granted, reason = claim_runner(runtime_id, holder)
            if granted is None:
                logger.debug("ACP lease not recorded for %s: %s", runtime_id, reason)
        except Exception:
            logger.debug("ACP lease recording failed for %s", runtime_id, exc_info=True)

    def start_health_loop(self) -> None:
        if not self._closed and self._health_task is None:
            self._health_task = asyncio.ensure_future(self._health_loop())

    async def _health_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(_HEALTH_INTERVAL_SECS)
                if self._closed:
                    break
                for runtime_id in tuple(self._runtimes):
                    slot = self._slots.get(runtime_id)
                    now = time.monotonic()
                    if slot is not None and self.is_warmed(runtime_id):
                        if now - slot.warmed_at >= self._ttl:
                            await self._refresh(runtime_id)
                    elif slot is None or now >= slot.next_retry_at:
                        await self.warm(runtime_id)
                await self._release_idle_leases()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("ACP pool maintenance stopped", exc_info=True)

    @staticmethod
    async def _release_idle_leases() -> None:
        try:
            from gideon.engine.agents.runner_lifecycle import sweep_idle_leases

            released = await asyncio.get_running_loop().run_in_executor(
                None, sweep_idle_leases
            )
            if released:
                logger.info("Released %d idle ACP leases", len(released))
        except Exception:
            logger.debug("ACP idle lease sweep failed", exc_info=True)

    async def _refresh(self, runtime_id: str) -> None:
        slot = self._slots.get(runtime_id)
        if slot is None:
            return
        async with slot.warming:
            previous = slot.detach()
            if previous is not None:
                await _safe_shutdown(previous)
        await self.warm(runtime_id)

    async def invalidate(self, runtime_id: str) -> None:
        slot = self._slots.get(runtime_id)
        if slot is not None:
            async with slot.warming:
                previous = slot.detach()
                slot.reset_retry()
                if previous is not None:
                    await _safe_shutdown(previous)

    async def shutdown(self) -> None:
        self._closed = True
        tasks = set(self._background)
        if self._health_task is not None:
            tasks.add(self._health_task)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._health_task = None
        for slot in tuple(self._slots.values()):
            async with slot.warming:
                provider = slot.detach()
                connection, slot._shared_conn = slot._shared_conn, None
                if provider is not None:
                    await _safe_shutdown(provider)
                if connection is not None:
                    await _close_connection(connection)

    def _spawn_bg(self, coroutine) -> None:
        def completed(task):
            self._background.discard(task)
            if not task.cancelled() and (failure := task.exception()) is not None:
                logger.debug("ACP pool background work failed: %s", failure)

        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(completed)


_pool: AcpConnectionPool | None = None


def get_acp_pool() -> AcpConnectionPool | None:
    return _pool


def set_acp_pool(pool: AcpConnectionPool | None) -> None:
    global _pool
    _pool = pool


def _ready_acp_runtime_ids() -> list[str]:
    try:
        from gideon.integrations.llm.registry import get_default_registry

        entries = get_default_registry().list_entries()
        return [entry.name for entry in entries if entry.type == "acp_agent"]
    except Exception:
        logger.debug("ACP runtime enumeration failed", exc_info=True)
        return []


def _build_acp_provider(runtime_id: str) -> ModelProvider:
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    options: dict = {**(registry.get_entry(runtime_id).options or {}), "model": "auto"}
    return registry.build(runtime_id, **options)


async def _acp_runtime_ready(runtime_id: str) -> bool:
    try:
        from gideon.engine.agents.registry import get_agent_provider_class
        from gideon.integrations.llm.registry import get_default_registry

        provider = get_agent_provider_class("acp")
        if provider is None:
            return False
        options = get_default_registry().get_entry(runtime_id).options or {}
        return bool((await provider.probe_readiness(dict(options))).ready)
    except Exception:
        logger.debug("ACP runtime %s readiness failed", runtime_id, exc_info=True)
        return False


async def init_acp_pool(start_sem: asyncio.Semaphore) -> AcpConnectionPool:
    previous = get_acp_pool()
    if previous is not None:
        await previous.shutdown()
    pool = AcpConnectionPool(
        provider_builder=_build_acp_provider,
        start_sem=start_sem,
        readiness_check=_acp_runtime_ready,
    )
    set_acp_pool(pool)
    runtimes = _ready_acp_runtime_ids()
    if runtimes:
        pool._spawn_bg(pool.warm_all(runtimes))
    pool.start_health_loop()
    return pool


def _alive(provider: object) -> bool:
    try:
        probe = getattr(provider, "is_process_alive", None)
        if callable(probe):
            return bool(probe())
        fallback = getattr(provider, "is_alive", None)
        return bool(fallback()) if callable(fallback) else True
    except Exception:
        return False


async def _safe_shutdown(provider: ModelProvider) -> None:
    try:
        await provider.shutdown()
    except Exception:
        try:
            from gideon.engine.session_pid import _sync_kill_provider

            _sync_kill_provider(provider)
        except Exception:
            logger.debug("ACP provider shutdown failed", exc_info=True)


async def _close_connection(connection: AcpConnection) -> None:
    try:
        await connection.close()
    except Exception:
        logger.debug("ACP shared connection shutdown failed", exc_info=True)


def _snapshot_agent_count(snapshot: dict) -> int:
    modes = snapshot.get("modes") if isinstance(snapshot, dict) else None
    available = modes.get("availableModes") if isinstance(modes, dict) else None
    return len(available) if isinstance(available, list) else 0
