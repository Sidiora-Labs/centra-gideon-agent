"""Own provider leases, queued turns, and process retirement for conversations."""

import asyncio
import json
import logging
import os
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gideon import shutdown_event
from gideon.cognition.context_management import cleanup_stale_sessions
from gideon.core.config import AppConfig
from gideon.core.config.loader import default_workspace_dir
from gideon.engine.session_map import SessionMap as SessionMap
from gideon.engine.session_pid import (
    _cleanup_orphaned_mcp_servers,
    _collect_active_pids,
    _kill_confirmed_and_writeback,
    _periodic_pid_sweep,
    _sync_kill_provider,
)
from gideon.engine.session_pid import _track_child_pids as _track_child_pids
from gideon.engine.session_pid import _track_pid as _track_pid
from gideon.engine.session_pid import _track_session_pid as _track_session_pid
from gideon.engine.session_pid import _untrack_child_pids as _untrack_child_pids
from gideon.engine.session_pid import _untrack_pid as _untrack_pid
from gideon.engine.session_pid import _untrack_session_pid as _untrack_session_pid
from gideon.engine.session_pid import (
    cleanup_orphaned_sessions as cleanup_orphaned_sessions,
)
from gideon.integrations.llm.base import CancelOutcome, ModelProvider
from gideon.operations.stats import Stats

logger = logging.getLogger(__name__)
_PROVIDER_RESOLUTION_ERRORS: tuple[type[Exception], ...]
try:
    from gideon.extensions.providers.provider_bridge import (
        ProviderResolutionError as _BridgeResolutionError,
    )
    from gideon.integrations.llm.registry import (
        ProviderResolutionError as _LLMResolutionError,
    )

    _PROVIDER_RESOLUTION_ERRORS = (_BridgeResolutionError, _LLMResolutionError)
except Exception:
    _PROVIDER_RESOLUTION_ERRORS = ()

_MAX_POOL = 10
_MAX_POOL_PROCESSES = 150
_SUBAGENT_PREFIX = "subagent:"
_CHANNEL_PREFIX = "channel:"
_LOOP_WORKER_PREFIX = "dashboard:loop-"
_STATELESS_PREFIXES = ("cron:", _SUBAGENT_PREFIX, _CHANNEL_PREFIX, "inbox:", "side:")
BACKGROUND_KEY = "_bg"
_PERSISTENT_KEYS = frozenset({BACKGROUND_KEY})
_CONTEXT_WARN_PCT = 70.0
_CONTEXT_COMPACT_PCT = 80.0
_CIRCUIT_BREAKER_THRESHOLD = 5
_BG_RECYCLE_PCT = 70.0
_BG_BLIND_RECYCLE_PROMPTS = 40
ProviderFactory = Callable[..., ModelProvider]
StopOutcome = Literal["soft", "hard", "idle"]


def _retains_history(key: str) -> bool:
    return key != BACKGROUND_KEY and not key.startswith(_STATELESS_PREFIXES)


def _process_live(provider: ModelProvider) -> bool:
    probe = getattr(provider, "is_process_alive", None)
    return probe() if probe is not None else provider.is_alive()


async def _fire_session_end(key: str, reason: str, session: "_Session") -> None:
    from gideon.automation.triggers.lifecycle_fire import fire, session_end_payload

    payload = session_end_payload(
        session_key=key,
        reason=reason,
        turns=int(getattr(session, "prompt_count", 0) or 0),
    )
    await fire(payload)


def _resolve_acp_spawn_cwd(cwd: str | None) -> Path:
    from gideon.integrations.acp.errors import AcpWorkspaceUnresolved

    explicit = str(cwd or "").strip()
    if explicit:
        return Path(explicit)
    workspace = str(default_workspace_dir() or "").strip()
    if workspace:
        return Path(workspace)
    logger.warning(
        "Refusing to spawn an ACP CLI: no usable workspace resolved "
        "(GIDEON_WORKSPACE=%r, gateway cwd=%r)",
        os.environ.get("GIDEON_WORKSPACE", ""),
        os.getcwd(),
    )
    raise AcpWorkspaceUnresolved(
        "No usable workspace directory resolved. Running an agent CLI in the gateway's "
        "own working directory is refused. Set GIDEON_WORKSPACE or the workspace "
        "directory in Settings, or give this chat its own working directory. "
        "The configured root is missing or a sensitive location."
    )


@dataclass
class _Session:
    provider: ModelProvider
    last_used: float = field(default_factory=time.monotonic)
    is_new: bool = True
    prompt_count: int = 0
    consecutive_failures: int = 0
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    approval_policy: str = ""
    agent: str = ""
    queue: deque[tuple[str, str, dict]] = field(default_factory=deque)
    steers: deque[str] = field(default_factory=deque)
    steer_drains: bool = False
    prev_turn_cancelled: bool = False
    cancelled: set[str] = field(default_factory=set)

    def touch(self) -> bool:
        initial = self.is_new
        self.is_new = False
        self.last_used = time.monotonic()
        return initial

    def next_message(self) -> tuple[str, str, dict] | None:
        while self.queue:
            message = self.queue.popleft()
            token = message[0]
            if token not in self.cancelled:
                return message
            self.cancelled.remove(token)
        return None

    def cancel_message(self, token: str) -> bool:
        for index, message in enumerate(self.queue):
            if message[0] == token:
                del self.queue[index]
                return True
        if self.semaphore.locked():
            self.cancelled.add(token)
        return False

    def take_steers(self) -> list[str]:
        pending = list(self.steers)
        self.steers.clear()
        return pending


@dataclass(frozen=True)
class _ProcessSnapshot:
    root: int | None
    children: dict[int, int | None]

    @classmethod
    def capture(cls, provider: ModelProvider) -> "_ProcessSnapshot":
        client = getattr(provider, "_client", None)
        root = getattr(client, "_pid", None) if client else None
        for attribute in ("_proc", "_active_proc"):
            if root is not None:
                break
            process = getattr(provider, attribute, None)
            if process is not None and process.returncode is None:
                root = process.pid
        root = root if isinstance(root, int) else None
        known = getattr(client, "_child_pids", None) if client else None
        children = dict(known) if isinstance(known, dict) else {}
        if root:
            from gideon.integrations.acp.client import _get_child_pids, _get_start_time

            for child in _get_child_pids(root):
                if child not in children:
                    children[child] = _get_start_time(child)
        return cls(root, children)

    def terminate_survivors(self, key: str) -> None:
        if not self.root:
            return
        try:
            os.kill(self.root, 0)
            logger.warning("Reset %s: PID %d survived shutdown", key, self.root)
            try:
                os.killpg(os.getpgid(self.root), signal.SIGKILL)
            except OSError:
                os.kill(self.root, signal.SIGKILL)
        except OSError:
            pass
        if self.children:
            from gideon.integrations.acp.client import _kill_escaped_children

            try:
                _kill_escaped_children(self.children)
            except Exception:
                logger.exception("Reset %s: child sweep failed", key)


class ConversationDirectory:
    """Coordinate independently serialized turns with shared runner capacity."""

    _POOL_HEALTH_INTERVAL = 30
    _agent_model_cache: dict[str, str] = {}

    def __init__(self, cfg: AppConfig, provider_factory: ProviderFactory | None = None):
        self._cfg = cfg
        self._provider_factory = provider_factory
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()
        self._start_sem = asyncio.Semaphore(4)
        self._cleanup_task: asyncio.Task | None = None
        self._compacting: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()
        self._on_compacted: Callable[[str, float], Awaitable[None]] | None = None
        self._on_session_expire: Callable[[str], Awaitable[object]] | None = None
        self._stop_children: Callable[[str], Awaitable[int]] | None = None
        self._session_map = SessionMap()
        self._active_dashboard_sessions: set[str] | None = None
        self._pool_started = False
        self._pool_size = self._bounded_pool_size(cfg.session.pool_size)
        self._pool_agent = cfg.session.pool_agent or cfg.default_agent
        self._pool_ttl_secs = max(0, cfg.session.pool_ttl_secs)
        self._pool_cwd = default_workspace_dir()
        self._warm_pool: asyncio.Queue[tuple[ModelProvider, float]] = asyncio.Queue()
        self._pool_fill_lock = asyncio.Lock()
        self._pool_health_task: asyncio.Task | None = None
        self._pool_sweep_pids: set[int] = set()

    @staticmethod
    def _bounded_pool_size(requested: int) -> int:
        if requested > _MAX_POOL:
            logger.warning(
                "pool_size %d exceeds max %d, clamping", requested, _MAX_POOL
            )
        return max(0, min(requested, _MAX_POOL))

    def _launch(self, operation: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task: asyncio.Task[Any] = asyncio.create_task(operation)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _take_pool_entries(self) -> list[tuple[ModelProvider, float]]:
        entries = []
        while True:
            try:
                entries.append(self._warm_pool.get_nowait())
            except asyncio.QueueEmpty:
                return entries

    @staticmethod
    async def _close_quietly(
        provider: ModelProvider, *, kill_on_cancel: bool = False
    ) -> None:
        try:
            await provider.shutdown()
        except Exception:
            logger.debug("Provider shutdown failed", exc_info=True)
        except BaseException:
            if kill_on_cancel:
                _sync_kill_provider(provider)
            raise

    def _remember_provider(self, key: str, provider: ModelProvider) -> None:
        from gideon.engine.agents.provider import AgentProvider

        if _retains_history(key) and isinstance(provider, AgentProvider):
            sid = provider.session_id
            if sid:
                directory = (
                    str(provider._work_dir) if hasattr(provider, "_work_dir") else ""
                )
                self._session_map.set(key, sid, cwd=directory)

    def has_session(self, key: str) -> bool:
        return self._sessions.get(key) is not None

    def get_provider(self, key: str) -> ModelProvider | None:
        entry = self._sessions.get(key)
        return None if entry is None else entry.provider

    def mark_new(self, key: str) -> None:
        if key in self._sessions:
            self._sessions[key].is_new = True

    def get_pid(self, key: str) -> int | None:
        provider = self.get_provider(key)
        client = getattr(provider, "client", None)
        return getattr(client, "_pid", None)

    async def reload_provider_factory(self) -> None:
        replacement = AppConfig.load()
        async with self._pool_fill_lock:
            async with self._lock:
                self._cfg = replacement
                self._provider_factory = replacement.create_provider_factory()
                self._pool_size = self._bounded_pool_size(replacement.session.pool_size)
                self._pool_agent = (
                    replacement.session.pool_agent or replacement.default_agent
                )
                self._pool_cwd = default_workspace_dir()
                old_pool = self._take_pool_entries()
                for provider, _ in old_pool:
                    await self._close_quietly(provider)
                old_sessions = list(self._sessions.values())
                self._sessions.clear()
        for entry in old_sessions:
            await self._close_quietly(entry.provider)
        self._pool_started = False
        health = self._pool_health_task
        self._pool_health_task = None
        if health is not None and not health.done():
            health.cancel()
        await self.start_pool(blocking=False)

    async def start_pool(self, *, blocking: bool = True) -> None:
        if self._pool_started or self._provider_factory is None:
            return
        self._session_map.prune()
        self._pool_started = True
        if blocking:
            await self._ensure_background()
            if self._pool_size:
                self._launch(self._fill_warm_pool())
                self._pool_health_task = self._launch(self._pool_health_loop())
        else:
            self._launch(self._prime_capacity())

    async def _prime_capacity(self) -> None:
        await self._ensure_background()
        await self._fill_warm_pool()
        if self._pool_size:
            self._pool_health_task = self._launch(self._pool_health_loop())

    async def _ensure_background(self) -> None:
        async with self._lock:
            if BACKGROUND_KEY in self._sessions:
                return
        if self._provider_factory is None:
            return
        try:
            provider = self._provider_factory(
                BACKGROUND_KEY,
                agent="gideon-lite",
                model_axis="background",
            )
            async with self._start_sem:
                await provider.start()
        except _PROVIDER_RESOLUTION_ERRORS as error:
            logger.info("Background session deferred: %s", error)
            return
        except Exception:
            logger.warning("Background session startup failed", exc_info=True)
            return
        async with self._lock:
            if BACKGROUND_KEY not in self._sessions:
                self._sessions[BACKGROUND_KEY] = _Session(provider, is_new=False)
                return
            await provider.shutdown()

    async def _fill_warm_pool(self) -> None:
        if self._pool_size <= 0 or self._provider_factory is None:
            return
        from gideon.integrations.acp.transport import _get_child_pids

        async with self._pool_fill_lock:
            while self._warm_pool.qsize() < self._pool_size:
                roots = self._pool_pids()
                population = len(roots) + sum(
                    len(_get_child_pids(pid)) for pid in roots
                )
                if population >= _MAX_POOL_PROCESSES:
                    logger.warning("Warm pool process limit reached: %d", population)
                    return
                candidate = None
                try:
                    candidate = self._provider_factory(
                        "",
                        agent=self._pool_agent or None,
                        cwd=self._pool_cwd or None,
                    )
                    async with self._start_sem:
                        await candidate.start()
                    self._warm_pool.put_nowait((candidate, time.monotonic()))
                    candidate = None
                except Exception:
                    logger.warning("Warm pool startup failed", exc_info=True)
                    return
                finally:
                    if candidate is not None:
                        await self._close_quietly(candidate, kill_on_cancel=True)

    def _claim_from_pool(self, agent: str | None) -> tuple[ModelProvider, float] | None:
        if (agent or self._pool_agent or "") != (self._pool_agent or ""):
            return None
        try:
            return self._warm_pool.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _specialize_acp(
        self,
        provider: ModelProvider,
        agent: str | None,
        model: str | None,
        options: dict[str, Any],
    ) -> None:
        from gideon.engine.agents.provider import AgentProvider

        if not isinstance(provider, AgentProvider):
            return
        if agent:
            await provider.set_agent(agent)
        if model:
            await provider.set_model(model)
        unattended = getattr(provider, "set_unattended", None)
        if unattended is not None:
            unattended(bool(options.get("unattended")))
        for option, method in (
            ("acp_mode", "set_mode"),
            ("reasoning_effort_override", "set_reasoning_effort"),
        ):
            value = str(options.get(option) or "")
            apply = getattr(provider, method, None)
            if value and apply is not None:
                await apply(value)

    async def _claim_acp_pool(
        self,
        key: str,
        channel_id: str | None,
        agent: str | None,
        model: str | None,
        extra_factory_kwargs: dict[str, Any],
    ) -> ModelProvider | None:
        runtime = str(extra_factory_kwargs.get("provider_kind") or "")
        if not runtime.startswith("acp:"):
            return None
        try:
            from gideon.integrations.acp.connection_pool import get_acp_pool

            pool = get_acp_pool()
            provider = (
                await pool.claim(runtime, holder=key) if pool is not None else None
            )
        except Exception:
            logger.debug("ACP pool claim failed for %s", key, exc_info=True)
            return None
        if provider is None:
            return None
        try:
            from gideon.engine.agents.provider import AgentProvider

            if isinstance(provider, AgentProvider):
                provider.set_session_key(key, channel_id)
            persona = str(extra_factory_kwargs.get("agent") or agent or "")
            await self._specialize_acp(provider, persona, model, extra_factory_kwargs)
            return provider
        except (Exception, asyncio.CancelledError):
            _sync_kill_provider(provider)
            raise

    async def _open_acp_concurrent(
        self,
        key: str,
        channel_id: str | None,
        agent: str | None,
        model: str | None,
        cwd: str | None,
        extra_factory_kwargs: dict[str, Any],
    ) -> ModelProvider | None:
        runtime = str(extra_factory_kwargs.get("provider_kind") or "")
        if not runtime.startswith("acp:"):
            return None
        from gideon.integrations.acp.errors import AcpWorkspaceUnresolved

        try:
            from gideon.integrations.acp.connection_pool import get_acp_pool
            from gideon.integrations.llm.acp_provider_runtime import (
                options_sandbox_mode,
            )
            from gideon.integrations.llm.acp_session_provider import (
                concurrent_sessions_enabled,
            )
            from gideon.integrations.llm.registry import get_default_registry

            options = dict(get_default_registry().get_entry(runtime).options or {})
            dialect = str(options["dialect"]) if options.get("dialect") else None
            if not concurrent_sessions_enabled(dialect):
                return None
            pool = get_acp_pool()
            command = options.get("command")
            if pool is None or not isinstance(command, list) or not command:
                return None
            files = options.get("session_files_dir")
            environment = options.get("env")
            provider = await pool.open_session(
                runtime,
                cwd=_resolve_acp_spawn_cwd(cwd),
                command=list(map(str, command)),
                dialect=dialect,
                session_files_dir=Path(str(files)) if files else None,
                sandbox_mode=options_sandbox_mode(options),
                extra_env=environment if isinstance(environment, dict) else None,
                session_key=key,
                channel_id=channel_id,
                model=model or "",
                agent_name=agent or "",
            )
            if provider is not None:
                await self._specialize_acp(provider, agent, model, extra_factory_kwargs)
            return provider
        except AcpWorkspaceUnresolved:
            raise
        except Exception:
            logger.debug(
                "ACP concurrent open_session failed for %s", key, exc_info=True
            )
            return None

    def _expired_pool_entry(self, born: float) -> bool:
        return self._pool_ttl_secs > 0 and time.monotonic() - born > self._pool_ttl_secs

    async def _drain_and_claim(self, agent: str | None) -> ModelProvider | None:
        rejected = False
        while (entry := self._claim_from_pool(agent)) is not None:
            provider, born = entry
            probe = getattr(provider, "is_process_alive", None)
            if not self._expired_pool_entry(born) and probe is not None and probe():
                return provider
            rejected = True
            await self._close_quietly(provider, kill_on_cancel=True)
        if rejected:
            self._schedule_replenish()
        return None

    def _schedule_replenish(self) -> None:
        if self._pool_size:
            self._launch(self._fill_warm_pool())

    def _pool_pids(self) -> set[int]:
        queued = self._take_pool_entries()
        result = set(self._pool_sweep_pids)
        for provider, born in queued:
            self._warm_pool.put_nowait((provider, born))
            pid = getattr(getattr(provider, "client", None), "_pid", None)
            if isinstance(pid, int):
                result.add(pid)
        return result

    async def _inspect_capacity(self) -> None:
        rejected = []
        try:
            for provider, born in self._take_pool_entries():
                pid = getattr(getattr(provider, "client", None), "_pid", None)
                if isinstance(pid, int):
                    self._pool_sweep_pids.add(pid)
                try:
                    probe = getattr(provider, "is_process_alive", None)
                    keep = (
                        not self._expired_pool_entry(born)
                        and probe is not None
                        and probe()
                    )
                except Exception:
                    keep = False
                if keep:
                    self._warm_pool.put_nowait((provider, born))
                else:
                    rejected.append(provider)
            for provider in rejected:
                try:
                    await self._close_quietly(provider, kill_on_cancel=True)
                except asyncio.CancelledError:
                    continue
        finally:
            self._pool_sweep_pids.clear()
        if rejected:
            self._schedule_replenish()

    async def _pool_health_loop(self) -> None:
        while True:
            await asyncio.sleep(self._POOL_HEALTH_INTERVAL)
            if not self._pool_size or self._warm_pool.empty():
                continue
            try:
                await self._inspect_capacity()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Warm pool health check failed")

    def context_info(self) -> list[dict[str, object]]:
        from gideon.engine.agents.provider import AgentProvider

        report: list[dict[str, object]] = []
        for key, entry in self._sessions.items():
            provider = entry.provider
            usage = provider.context_usage_pct()
            model, agent = "unknown", ""
            if isinstance(provider, AgentProvider):
                model, agent = provider.agent_model or "auto", provider.agent_name or ""
                if model == "auto" and agent and agent != "gideon":
                    model = self._resolve_agent_model(agent)
                model = model or "auto"
            if key == BACKGROUND_KEY:
                name = "Background (titles, cron, heartbeat)"
            elif key.startswith("dashboard:"):
                name = f"Chat ({key.split(':', 1)[1]})"
            else:
                name = key
            report.append(
                dict(
                    key=key,
                    name=name,
                    model=model,
                    agent=agent,
                    context_pct=None if usage is None else round(usage, 1),
                    prompts=entry.prompt_count,
                )
            )
        return report

    @staticmethod
    def _resolve_agent_model(agent: str) -> str:
        cache = ConversationDirectory._agent_model_cache
        if agent in cache:
            return cache[agent]
        model = "auto"
        try:
            from gideon.engine.agent import AGENTS_DIR

            for path in AGENTS_DIR.glob("*.json"):
                try:
                    definition = json.loads(path.read_text())
                except (ValueError, OSError):
                    continue
                if definition.get("name") == agent or path.stem == agent:
                    model = definition.get("model", "auto")
                    break
        except Exception:
            pass
        cache[agent] = model
        return model

    async def recycle_background(self) -> None:
        entry = self._sessions.get(BACKGROUND_KEY)
        if entry is None:
            return
        usage = entry.provider.context_usage_pct()
        exhausted = (
            entry.prompt_count >= _BG_BLIND_RECYCLE_PROMPTS
            if usage is None
            else usage >= _BG_RECYCLE_PCT
        )
        if exhausted:
            async with self._lock:
                retired = self._sessions.pop(BACKGROUND_KEY, None)
            if retired is not None:
                await retired.provider.shutdown()
            await self._ensure_background()

    async def _acquire_runner(
        self,
        factory: ProviderFactory,
        key: str,
        agent: str | None,
        channel_id: str | None,
        model: str | None,
        cwd: str | None,
        extra_env: dict[str, str] | None,
        options: dict[str, Any],
    ) -> ModelProvider:
        from gideon.engine.agents.provider import AgentProvider
        from gideon.engine.agents.runners import (
            guard_unattended_spawn,
            runtime_id_for_agent,
        )
        from gideon.security.guardrails.policy import is_unattended_session

        runtime = str(options.get("provider_kind") or "")
        runtime = runtime if runtime.startswith("acp") else runtime_id_for_agent(agent)
        guard_unattended_spawn(
            runtime,
            unattended=(bool(options.get("unattended")) or is_unattended_session(key)),
        )
        retain = _retains_history(key)
        resume = self._session_map.get(key) if retain else None
        eligible = retain and not resume
        same_directory = not cwd or cwd == self._pool_cwd
        if eligible and self._pool_size and same_directory and not extra_env:
            warm = await self._drain_and_claim(agent)
            if warm is not None:
                try:
                    if isinstance(warm, AgentProvider):
                        warm.set_session_key(key, channel_id)
                        default = (
                            self._resolve_agent_model(self._pool_agent)
                            if self._pool_agent
                            else None
                        )
                        if model and default and model != default:
                            await warm.set_model(model)
                    self._schedule_replenish()
                    return warm
                except (Exception, asyncio.CancelledError):
                    _sync_kill_provider(warm)
                    raise
        if eligible:
            shared = await self._open_acp_concurrent(
                key, channel_id, agent, model, cwd, options
            )
            if shared is None:
                shared = await self._claim_acp_pool(
                    key, channel_id, agent, model, options
                )
            if shared is not None:
                return shared
        directory = cwd
        if not directory and resume:
            remembered = self._session_map.get_cwd(key)
            if remembered and Path(remembered).is_dir():
                directory = remembered
        provider = factory(
            key,
            agent=agent,
            channel_id=channel_id,
            model_override=model,
            cwd=directory,
            extra_env=extra_env,
            **options,
        )
        if resume and isinstance(provider, AgentProvider):
            provider.set_resume(resume)
        try:
            async with self._start_sem:
                await provider.start()
        except (Exception, asyncio.CancelledError):
            _sync_kill_provider(provider)
            raise
        return provider

    async def get_or_create(
        self,
        key: str,
        agent: str | None = None,
        channel_id: str | None = None,
        approval_policy: str = "",
        model: str | None = None,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        **extra_factory_kwargs: Any,
    ) -> tuple[ModelProvider, bool, bool]:
        """Acquire a turn lease; its owner must call ``release`` when the turn ends."""
        if key == BACKGROUND_KEY:
            extra_factory_kwargs.setdefault("model_axis", "background")
        reuse = None
        stale = None
        try:
            async with self._lock:
                entry = self._sessions.get(key) if key not in self._compacting else None
                if entry is not None:
                    if _process_live(entry.provider):
                        reuse = entry, entry.touch()
                    else:
                        stale = self._sessions.pop(key).provider
                factory = self._provider_factory
                if reuse is None and factory is None:
                    raise RuntimeError("No provider factory configured")
        finally:
            if stale is not None:
                await self._close_quietly(stale)
        if reuse is not None:
            entry, initial = reuse
            await entry.semaphore.acquire()
            return entry.provider, initial, False
        if factory is None:
            raise RuntimeError("No provider factory configured")
        provider = await self._acquire_runner(
            factory, key, agent, channel_id, model, cwd, extra_env, extra_factory_kwargs
        )
        from gideon.engine.agents.provider import AgentProvider

        try:
            resumed = provider.resumed if isinstance(provider, AgentProvider) else False
            async with self._lock:
                winner = (
                    self._sessions.get(key) if key not in self._compacting else None
                )
                if winner is None:
                    entry = _Session(
                        provider,
                        is_new=False,
                        approval_policy=approval_policy,
                        agent=agent or "",
                    )
                    self._sessions[key] = entry
                    self._remember_provider(key, provider)
                    if self._cleanup_task is None or self._cleanup_task.done():
                        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
                    await entry.semaphore.acquire()
                    Stats().inc_session_created()
                    return provider, True, resumed
                winner.last_used = time.monotonic()
                if approval_policy:
                    winner.approval_policy = approval_policy
                if agent:
                    winner.agent = agent
            # A losing start owns no directory entry. Retire it without locking other keys.
            await provider.shutdown()
        except BaseException:
            _sync_kill_provider(provider)
            raise
        await winner.semaphore.acquire()
        return winner.provider, False, False

    async def reset(self, key: str) -> None:
        async with self._lock:
            entry = self._sessions.pop(key, None)
        if entry is None:
            return
        process = _ProcessSnapshot.capture(entry.provider)
        await entry.provider.shutdown()
        process.terminate_survivors(key)

    def check_context_usage(self, key: str, provider: ModelProvider) -> float | None:
        usage = provider.context_usage_pct()
        entry = self._sessions.get(key)
        if entry is not None:
            entry.prompt_count += 1
        if usage is not None:
            if usage >= self._cfg.session.autocompact_pct:
                self._trigger_compaction(key, f"context at {usage:.0f}%", usage)
            else:
                log = logger.warning if usage >= _CONTEXT_WARN_PCT else logger.info
                log("Session %s context at %.0f%%", key, usage)
        return usage

    def set_compact_callback(
        self, callback: Callable[[str, float], Awaitable[None]] | None
    ) -> None:
        if callback is not None and self._on_compacted is not None:
            logger.warning(
                "Compact callback already registered; replacing existing handler"
            )
        self._on_compacted = callback

    def set_session_expire_callback(
        self, callback: Callable[[str], Awaitable[object]] | None
    ) -> None:
        self._on_session_expire = callback

    def _trigger_compaction(self, key: str, reason: str, pct: float) -> None:
        if key in self._compacting:
            return
        self._compacting.add(key)
        logger.info("Recycling %s: %s", key, reason)
        self._launch(self._compact_session(key, pct))

    async def _compact_session(self, key: str, pct: float) -> None:
        try:
            async with self._lock:
                entry = self._sessions.pop(key, None)
            if entry is None:
                return
            self._session_map.delete(key)
            await entry.provider.shutdown()
            if self._on_compacted is not None:
                try:
                    await self._on_compacted(key, pct)
                except Exception:
                    logger.exception("Compact callback failed for %s", key)
        except Exception:
            logger.exception("Session recycle failed for %s", key)
        finally:
            self._compacting.discard(key)

    async def remove(self, key: str) -> None:
        await self._end_entry(key, permanent=False)

    async def destroy(self, key: str) -> None:
        await self._end_entry(key, permanent=True)

    async def _end_entry(self, key: str, *, permanent: bool) -> None:
        async with self._lock:
            entry = self._sessions.pop(key, None)
        if permanent:
            try:
                if entry is not None:
                    await entry.provider.shutdown()
            finally:
                self._session_map.delete(key)
                if entry is not None:
                    await _fire_session_end(key, "destroyed", entry)
        elif entry is not None:
            await entry.provider.shutdown()
            await _fire_session_end(key, "removed", entry)

    async def close_all(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
        running = tuple(self._background_tasks)
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._background_tasks.clear()
        providers = [provider for provider, _ in self._take_pool_entries()]
        async with self._lock:
            retired = dict(self._sessions)
            for key, entry in retired.items():
                self._remember_provider(key, entry.provider)
            self._sessions.clear()
        providers[:0] = [entry.provider for entry in retired.values()]
        if providers:
            closing = asyncio.gather(
                *(self._close_quietly(p) for p in providers), return_exceptions=True
            )
            try:
                await asyncio.wait_for(closing, timeout=5.0)
            except asyncio.TimeoutError:
                for provider in providers:
                    _sync_kill_provider(provider)
        for key, entry in retired.items():
            await _fire_session_end(key, "shutdown", entry)

    def record_success(self, key: str) -> None:
        entry = self._sessions.get(key)
        if entry is not None:
            entry.consecutive_failures = 0

    async def record_failure(self, key: str) -> bool:
        entry = self._sessions.get(key)
        if entry is None:
            return False
        entry.consecutive_failures += 1
        if entry.consecutive_failures < _CIRCUIT_BREAKER_THRESHOLD:
            return False
        await self.reset(key)
        return True

    def release(self, key: str, *, cleanup: bool = False) -> None:
        entry = self._sessions.get(key)
        if entry is None:
            return
        if cleanup and key.startswith(_SUBAGENT_PREFIX):
            try:
                sid = entry.provider.session_id
                if sid:
                    asyncio.ensure_future(self._safe_cleanup(entry.provider, sid))
            except Exception:
                logger.debug("Session cleanup unavailable", exc_info=True)
        entry.semaphore.release()

    async def _safe_cleanup(self, provider: ModelProvider, session_id: str) -> None:
        try:
            await provider.cleanup_session(session_id)
        except Exception:
            logger.warning(
                "Session file cleanup failed for %s", session_id, exc_info=True
            )

    def enqueue(
        self, key: str, msg_ts: str, text: str, *, force: bool = False, **kwargs: Any
    ) -> bool:
        entry = self._sessions.get(key)
        if entry is None or not (force or entry.semaphore.locked()):
            return False
        entry.queue.append((msg_ts, text, kwargs))
        return True

    def dequeue(self, key: str) -> tuple[str, str, dict] | None:
        entry = self._sessions.get(key)
        return entry.next_message() if entry is not None else None

    def add_steer(self, key: str, text: str) -> bool:
        entry = self._sessions.get(key)
        text = text.strip()
        if (
            not text
            or entry is None
            or not entry.steer_drains
            or not entry.semaphore.locked()
        ):
            return False
        entry.steers.append(text)
        return True

    def set_steer_drains(self, key: str, value: bool) -> list[str]:
        entry = self._sessions.get(key)
        if entry is None:
            return []
        entry.steer_drains = bool(value)
        return [] if value else [text for text in entry.take_steers() if text]

    def drain_steers(self, key: str) -> list[str]:
        entry = self._sessions.get(key)
        return entry.take_steers() if entry is not None else []

    def cancel_queued(self, key: str, msg_ts: str) -> bool:
        entry = self._sessions.get(key)
        return entry.cancel_message(msg_ts) if entry is not None else False

    def is_cancelled(self, key: str, msg_ts: str) -> bool:
        entry = self._sessions.get(key)
        if entry is None or msg_ts not in entry.cancelled:
            return False
        entry.cancelled.remove(msg_ts)
        return True

    def clear_queue(self, key: str) -> None:
        entry = self._sessions.get(key)
        if entry is not None:
            entry.queue.clear()
            entry.cancelled.clear()

    async def is_provider_alive(self, key: str) -> bool | None:
        async with self._lock:
            provider = self.get_provider(key)
        return None if provider is None else _process_live(provider)

    def get_approval_policy(self, key: str) -> str:
        entry = self._sessions.get(key)
        return entry.approval_policy if entry is not None else ""

    def get_agent(self, key: str) -> str:
        entry = self._sessions.get(key)
        return entry.agent if entry is not None else ""

    def set_approval_policy(self, key: str, policy: str) -> None:
        entry = self._sessions.get(key)
        if entry is None:
            return
        previous, entry.approval_policy = entry.approval_policy, policy
        apply = getattr(entry.provider, "set_approval_policy", None)
        if callable(apply):
            apply(policy)
        if previous != policy:
            from gideon.security.sel import sel

            sel().log_tool_invocation(
                session_key=key,
                source="session",
                tool_name="set_approval_policy",
                outcome=policy or "default",
                metadata={"old_policy": previous, "new_policy": policy},
            )

    def set_task_mode(self, key: str, mode: str) -> None:
        provider = self.get_provider(key)
        apply = getattr(provider, "set_task_mode", None)
        if callable(apply):
            apply(mode or "agent")

    def set_channel_link(
        self, key: str, thread_ts: str, channel_id: str | None
    ) -> None:
        self._session_map.set_channel_link(key, thread_ts, channel_id)

    def get_channel_link(self, key: str) -> tuple[str | None, str | None]:
        return self._session_map.get_channel_link(key)

    def get_session_for_thread(self, thread_ts: str) -> str | None:
        return self._session_map.get_session_for_thread(thread_ts)

    async def set_channel(self, key: str, channel_id: str) -> None:
        thread, _ = self.get_channel_link(key)
        self.set_channel_link(key, thread or "", channel_id)

    def get_channel(self, key: str) -> str | None:
        return self.get_channel_link(key)[1]

    def find_key_by_sid(self, sid: str) -> str | None:
        return self._session_map.find_key_by_sid(sid)

    def delete_session_map_entry(self, key: str) -> None:
        self._session_map.delete(key)

    async def set_thread(self, key: str, thread_ts: str) -> None:
        self.set_channel_link(key, thread_ts, self.get_channel_link(key)[1])

    def get_thread(self, key: str) -> str | None:
        return self.get_channel_link(key)[0]

    async def cancel_current(
        self, key: str, *, wait_ack_timeout: float = 0.0
    ) -> CancelOutcome:
        provider = self.get_provider(key)
        return (
            await provider.cancel(wait_ack_timeout=wait_ack_timeout)
            if provider is not None
            else "no_turn"
        )

    def register_child_stopper(self, stopper: Callable[[str], Awaitable[int]]) -> None:
        self._stop_children = stopper

    async def _stop_spawned_children(self, key: str) -> int:
        if self._stop_children is None:
            return 0
        try:
            count = await self._stop_children(key)
        except Exception:
            logger.warning(
                "Stopping spawned children failed for %s", key, exc_info=True
            )
            return 0
        if count:
            note = getattr(self.get_provider(key), "note_subagents_stopped", None)
            if note is not None:
                try:
                    note(count)
                except Exception:
                    logger.debug("Recording child stop count failed", exc_info=True)
        return count

    async def stop_turn(
        self,
        key: str,
        *,
        force: bool = False,
        preserve_queue: bool = False,
        on_soft: Callable[[], Awaitable[None]] | None = None,
        on_hard: Callable[[], Awaitable[None]] | None = None,
    ) -> StopOutcome:
        entry = self._sessions.get(key)
        if entry is None:
            return "idle"
        if not preserve_queue:
            self.clear_queue(key)
        await self._stop_spawned_children(key)
        outcome = (
            "forced"
            if force
            else await entry.provider.cancel(
                wait_ack_timeout=self._cfg.agent.soft_stop_budget_secs,
            )
        )
        if outcome == "no_turn":
            return "idle"
        result: Literal["soft", "hard"]
        if outcome == "acked":
            entry.prev_turn_cancelled = True
            result, callback = "soft", on_soft
        else:
            await self.reset(key)
            self._launch(self._eager_respawn(key))
            result, callback = "hard", on_hard
        if callback is not None:
            try:
                await callback()
            except Exception:
                logger.warning("on_%s hook failed for %s", result, key, exc_info=True)
        return result

    async def _eager_respawn(self, key: str) -> None:
        try:
            await self.get_or_create(key)
            self.release(key)
        except Exception:
            logger.warning("Eager respawn failed for %s", key, exc_info=True)

    @property
    def count(self) -> int:
        return len(self._sessions)

    async def drain_all_providers(self) -> list:
        async with self._lock:
            detached = [entry.provider for entry in self._sessions.values()]
            self._sessions.clear()
        return detached

    async def drain_warm_pool(self) -> list:
        return [provider for provider, _ in self._take_pool_entries()]

    async def _sweep_process_records(self) -> None:
        active, reliable = _collect_active_pids(self._sessions)
        active.update(self._pool_pids())
        if not reliable:
            return
        owner = os.getpid()
        dead, candidates = await asyncio.to_thread(_periodic_pid_sweep, owner, active)
        confirmed = []
        if candidates:
            current, reliable = _collect_active_pids(self._sessions)
            current.update(self._pool_pids())
            if reliable:
                confirmed = [pid for pid in candidates if pid not in current]
        if confirmed or dead:
            await asyncio.to_thread(
                _kill_confirmed_and_writeback, owner, confirmed, dead
            )

    async def _cleanup_loop(self) -> None:
        configured = self._cfg.session.timeout_secs
        timeout = max(60, configured) if configured > 0 else configured
        cadence = max(timeout // 6, 60) if timeout > 0 else 300
        if timeout != configured:
            logger.warning(
                "session.timeout_secs=%d is below minimum 60; clamping to 60",
                configured,
            )
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=cadence)
            except asyncio.TimeoutError:
                if timeout > 0:
                    await self._expire_idle(timeout)
                try:
                    _cleanup_orphaned_mcp_servers()
                except Exception:
                    pass
                try:
                    await self._sweep_process_records()
                except Exception:
                    logger.debug("Orphan PID sweep failed", exc_info=True)
                try:
                    cleanup_stale_sessions()
                except Exception:
                    logger.debug("Stale workspace cleanup failed", exc_info=True)
            else:
                return

    def set_active_dashboard_sessions(self, session_keys: set[str]) -> None:
        self._active_dashboard_sessions = set(session_keys)

    async def _expire_idle(self, timeout_secs: int) -> None:
        deadline = time.monotonic() - timeout_secs
        async with self._lock:
            expired = []
            for key, entry in self._sessions.items():
                if key in _PERSISTENT_KEYS or key.startswith(
                    (_CHANNEL_PREFIX, _LOOP_WORKER_PREFIX)
                ):
                    continue
                orphan = (
                    key.startswith("dashboard:")
                    and self._active_dashboard_sessions is not None
                    and key not in self._active_dashboard_sessions
                )
                if orphan or entry.last_used < deadline:
                    expired.append((key, orphan))
        for key, orphan in expired:
            Stats().inc_session_cleaned()
            if not orphan and self._on_session_expire is not None:
                try:
                    await self._on_session_expire(key)
                except Exception:
                    logger.warning(
                        "on_session_expire failed for %s", key, exc_info=True
                    )
            await self.reset(key)
