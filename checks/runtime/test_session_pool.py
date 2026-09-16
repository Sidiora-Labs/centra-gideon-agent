"""Tests for warm session pool (session.pool_size / session.pool_agent)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_cfg(
    pool_size: int = 2, pool_agent: str = "gideon", pool_ttl_secs: int = 1800
) -> MagicMock:
    cfg = MagicMock()
    cfg.session.pool_size = pool_size
    cfg.session.pool_agent = pool_agent
    cfg.session.pool_ttl_secs = pool_ttl_secs
    cfg.session.timeout_secs = 3600
    cfg.default_agent = ""
    return cfg


def _make_provider() -> MagicMock:
    p = MagicMock()
    p.start = AsyncMock()
    p.shutdown = AsyncMock()
    p.is_process_alive = MagicMock(return_value=True)
    p.exit_code = None
    return p


def _make_manager(
    pool_size: int = 2, pool_agent: str = "gideon", pool_ttl_secs: int = 1800
):
    from gideon.engine.session import ConversationDirectory

    cfg = _make_cfg(pool_size, pool_agent, pool_ttl_secs)
    factory = MagicMock(side_effect=lambda *a, **kw: _make_provider())
    with patch(
        "gideon.engine.session.default_workspace_dir",
        return_value="/home/user/.gideon/workspace",
    ):
        mgr = ConversationDirectory(cfg, provider_factory=factory)
    return mgr, factory


class TestFillWarmPool:
    @pytest.mark.asyncio
    async def test_fills_to_pool_size(self):
        mgr, factory = _make_manager(pool_size=3)
        await mgr._fill_warm_pool()

        assert mgr._warm_pool.qsize() == 3
        assert factory.call_count == 3
        for _ in range(3):
            p, spawn_time = mgr._warm_pool.get_nowait()
            p.start.assert_awaited_once()
            assert spawn_time > 0

    @pytest.mark.asyncio
    async def test_noop_when_pool_size_zero(self):
        mgr, factory = _make_manager(pool_size=0)
        await mgr._fill_warm_pool()

        assert mgr._warm_pool.qsize() == 0
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_stops_on_spawn_failure(self):
        mgr, factory = _make_manager(pool_size=3)
        call_count = 0
        failed_providers: list = []

        def _factory(*a, **kw):
            nonlocal call_count
            call_count += 1
            p = _make_provider()
            if call_count == 2:
                p.start = AsyncMock(side_effect=RuntimeError("spawn failed"))
                failed_providers.append(p)
            return p

        factory.side_effect = _factory
        await mgr._fill_warm_pool()

        assert mgr._warm_pool.qsize() == 1
        assert len(failed_providers) == 1
        failed_providers[0].shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_cleans_up_via_finally(self):
        mgr, factory = _make_manager(pool_size=1)
        provider = _make_provider()
        provider.start = AsyncMock(side_effect=asyncio.CancelledError)
        provider.shutdown = AsyncMock(side_effect=asyncio.CancelledError)
        factory.side_effect = lambda *a, **kw: provider

        with patch("gideon.engine.session._sync_kill_provider") as mock_kill:
            with pytest.raises(asyncio.CancelledError):
                await mgr._fill_warm_pool()
            mock_kill.assert_called_once_with(provider)
        assert mgr._warm_pool.qsize() == 0


class TestLivenessDrainLoop:
    @pytest.mark.asyncio
    async def test_dead_provider_discarded_healthy_used(self):
        """Dead providers are drained; first healthy one is used."""
        mgr, _ = _make_manager(pool_agent="gideon")

        dead = _make_provider()
        dead.is_process_alive = MagicMock(return_value=False)
        healthy = _make_provider()
        healthy.is_process_alive = MagicMock(return_value=True)

        mgr._warm_pool.put_nowait((dead, time.monotonic()))
        mgr._warm_pool.put_nowait((healthy, time.monotonic()))

        pooled = await mgr._drain_and_claim("gideon")

        dead.shutdown.assert_awaited_once()
        assert pooled is healthy

    @pytest.mark.asyncio
    async def test_provider_without_is_alive_discarded(self):
        """Provider missing is_alive attribute is treated as dead."""
        mgr, _ = _make_manager(pool_agent="gideon")

        no_alive = _make_provider()
        del no_alive.is_process_alive
        healthy = _make_provider()

        mgr._warm_pool.put_nowait((no_alive, time.monotonic()))
        mgr._warm_pool.put_nowait((healthy, time.monotonic()))

        pooled = await mgr._drain_and_claim("gideon")

        no_alive.shutdown.assert_awaited_once()
        assert pooled is healthy


class TestClaimFromPool:
    def test_claim_matching_agent(self):
        mgr, _ = _make_manager(pool_agent="gideon")
        provider = _make_provider()
        mgr._warm_pool.put_nowait((provider, time.monotonic()))

        result = mgr._claim_from_pool("gideon")
        assert result[0] is provider
        assert mgr._warm_pool.qsize() == 0

    def test_claim_none_agent_matches_pool_agent(self):
        """None agent means 'use default' — matches pool_agent."""
        mgr, _ = _make_manager(pool_agent="gideon")
        provider = _make_provider()
        mgr._warm_pool.put_nowait((provider, time.monotonic()))

        result = mgr._claim_from_pool(None)
        assert result[0] is provider
        assert mgr._warm_pool.qsize() == 0

    def test_claim_empty_agent_matches_empty_pool_agent(self):
        """Empty agent matches empty pool_agent."""
        mgr, _ = _make_manager(pool_agent="")
        provider = _make_provider()
        mgr._warm_pool.put_nowait((provider, time.monotonic()))

        result = mgr._claim_from_pool(None)
        assert result[0] is provider

    def test_claim_mismatched_agent_returns_none(self):
        mgr, _ = _make_manager(pool_agent="gideon")
        mgr._warm_pool.put_nowait((_make_provider(), time.monotonic()))

        result = mgr._claim_from_pool("custom-agent")
        assert result is None
        assert mgr._warm_pool.qsize() == 1

    def test_claim_empty_pool_returns_none(self):
        mgr, _ = _make_manager()
        result = mgr._claim_from_pool("gideon")
        assert result is None

    def test_claim_nonempty_agent_rejected_when_pool_agent_empty(self):
        mgr, _ = _make_manager(pool_agent="")
        mgr._warm_pool.put_nowait((_make_provider(), time.monotonic()))
        result = mgr._claim_from_pool("some-agent")
        assert result is None
        assert mgr._warm_pool.qsize() == 1


class TestScheduleReplenish:
    @pytest.mark.asyncio
    async def test_replenish_creates_background_task(self):
        mgr, _ = _make_manager(pool_size=1)
        mgr._schedule_replenish()

        assert len(mgr._background_tasks) == 1
        await asyncio.gather(*list(mgr._background_tasks), return_exceptions=True)
        assert mgr._warm_pool.qsize() == 1

    @pytest.mark.asyncio
    async def test_replenish_noop_when_disabled(self):
        mgr, _ = _make_manager(pool_size=0)
        mgr._schedule_replenish()
        assert len(mgr._background_tasks) == 0


class TestPoolDrainOnShutdown:
    @pytest.mark.asyncio
    async def test_close_all_shuts_down_pool_providers(self):
        mgr, _ = _make_manager(pool_size=2)
        p1, p2 = _make_provider(), _make_provider()
        mgr._warm_pool.put_nowait((p1, time.monotonic()))
        mgr._warm_pool.put_nowait((p2, time.monotonic()))

        await mgr.close_all()

        p1.shutdown.assert_awaited_once()
        p2.shutdown.assert_awaited_once()
        assert mgr._warm_pool.qsize() == 0


class TestConfigWiring:
    def test_pool_size_from_config(self):
        mgr, _ = _make_manager(pool_size=5, pool_agent="custom")
        assert mgr._pool_size == 5
        assert mgr._pool_agent == "custom"

    def test_pool_agent_falls_back_to_default_agent(self):
        from gideon.engine.session import ConversationDirectory

        cfg = _make_cfg(pool_size=1, pool_agent="")
        cfg.default_agent = "fallback-agent"
        mgr = ConversationDirectory(cfg)
        assert mgr._pool_agent == "fallback-agent"

    def test_pool_disabled_by_default(self):
        from gideon.engine.session import ConversationDirectory

        cfg = _make_cfg(pool_size=0)
        mgr = ConversationDirectory(cfg)
        assert mgr._pool_size == 0

    def test_pool_size_capped_at_max(self):
        """pool_size > 10 is clamped to 10."""
        mgr, _ = _make_manager(pool_size=100)
        assert mgr._pool_size == 10


class TestGetOrCreatePoolIntegration:
    @pytest.mark.asyncio
    async def test_claims_from_pool_when_agent_matches(self):
        """get_or_create uses pooled provider, verifies rekey() called."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.resumed = False
        pooled.session_id = "fake-sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        provider, is_new, _ = await mgr.get_or_create(
            "test-key", agent="gideon", channel_id="ch-1"
        )

        assert provider is pooled
        pooled.set_session_key.assert_called_once_with("test-key", "ch-1")
        mgr._schedule_replenish.assert_called_once()
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_pool_when_resume_sid_set(self):
        """get_or_create skips pool when session has resume_sid."""
        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = _make_provider()
        mgr._warm_pool.put_nowait((pooled, time.monotonic()))
        mgr._drain_and_claim = AsyncMock(return_value=pooled)

        mgr._session_map.get = MagicMock(return_value="existing-sid")

        provider, is_new, _ = await mgr.get_or_create("test-key", agent="gideon")

        mgr._drain_and_claim.assert_not_awaited()
        factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_pool_when_cwd_set(self):
        """get_or_create skips pool when caller provides cwd.

        Pooled providers were spawned in the gateway's cwd and cannot be
        re-rooted; a caller requesting cwd must get a fresh cold-start
        process.  Forwarding cwd to the factory is verified separately.
        """
        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = _make_provider()
        mgr._warm_pool.put_nowait((pooled, time.monotonic()))
        mgr._drain_and_claim = AsyncMock(return_value=pooled)

        provider, is_new, _ = await mgr.get_or_create(
            "test-key", agent="gideon", cwd="/Users/alice/workspace/proj"
        )

        mgr._drain_and_claim.assert_not_awaited()
        factory.assert_called_once()
        assert factory.call_args.kwargs.get("cwd") == "/Users/alice/workspace/proj"

    @pytest.mark.asyncio
    async def test_claims_pool_with_model_override_and_switches(self):
        """get_or_create claims pool even with model_override, then calls set_model."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.set_model = AsyncMock()
        pooled.resumed = False
        pooled.session_id = "fake-sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        with patch.object(
            type(mgr), "_resolve_agent_model", return_value="default-model"
        ):
            provider, is_new, _ = await mgr.get_or_create(
                "test-key", agent="gideon", model="custom-model"
            )

        assert provider is pooled
        mgr._drain_and_claim.assert_awaited_once()
        factory.assert_not_called()
        pooled.set_model.assert_awaited_once_with("custom-model")


class TestTTLExpiration:
    @pytest.mark.asyncio
    async def test_stale_provider_discarded(self):
        """Provider older than TTL is discarded."""
        mgr, _ = _make_manager(pool_agent="gideon", pool_ttl_secs=60)
        stale = _make_provider()
        mgr._warm_pool.put_nowait((stale, time.monotonic() - 120))

        result = await mgr._drain_and_claim("gideon")

        assert result is None
        stale.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fresh_provider_used(self):
        """Provider within TTL is used."""
        mgr, _ = _make_manager(pool_agent="gideon", pool_ttl_secs=60)
        fresh = _make_provider()
        mgr._warm_pool.put_nowait((fresh, time.monotonic()))

        result = await mgr._drain_and_claim("gideon")

        assert result is fresh
        fresh.shutdown.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_check(self):
        """TTL=0 disables expiration check."""
        mgr, _ = _make_manager(pool_agent="gideon", pool_ttl_secs=0)
        old = _make_provider()
        mgr._warm_pool.put_nowait((old, time.monotonic() - 10000))

        result = await mgr._drain_and_claim("gideon")

        assert result is old
        old.shutdown.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_drain_triggers_replenish(self):
        """Discarding stale providers triggers pool replenish."""
        mgr, _ = _make_manager(pool_agent="gideon", pool_ttl_secs=60)
        stale = _make_provider()
        mgr._warm_pool.put_nowait((stale, time.monotonic() - 120))
        mgr._schedule_replenish = MagicMock()

        await mgr._drain_and_claim("gideon")

        mgr._schedule_replenish.assert_called_once()


class TestModelMatchesPoolDefault:
    """When the dashboard sends model == pool agent's default, treat as None
    so the pool isn't bypassed unnecessarily."""

    @pytest.mark.asyncio
    async def test_pool_claimed_when_model_matches_agent_default(self):
        """model='claude-opus-4.6' matching pool agent default → pool used, no set_model."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.set_model = AsyncMock()
        pooled.resumed = False
        pooled.session_id = "fake-sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        with patch.object(
            type(mgr), "_resolve_agent_model", return_value="claude-opus-4.6"
        ):
            provider, is_new, _ = await mgr.get_or_create(
                "test-key", agent="gideon", model="claude-opus-4.6"
            )

        assert provider is pooled
        mgr._drain_and_claim.assert_awaited_once()
        factory.assert_not_called()
        pooled.set_model.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pool_claimed_when_model_differs_with_post_switch(self):
        """model='claude-sonnet-4.6' != pool default → pool claimed, set_model called."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.set_model = AsyncMock()
        pooled.resumed = False
        pooled.session_id = "fake-sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        with patch.object(
            type(mgr), "_resolve_agent_model", return_value="claude-opus-4.6"
        ):
            provider, is_new, _ = await mgr.get_or_create(
                "test-key", agent="gideon", model="claude-sonnet-4.6"
            )

        assert provider is pooled
        mgr._drain_and_claim.assert_awaited_once()
        factory.assert_not_called()
        pooled.set_model.assert_awaited_once_with("claude-sonnet-4.6")

    @pytest.mark.asyncio
    async def test_pool_skipped_when_model_set_but_pool_disabled(self):
        """pool_size=0 → no model comparison, straight to cold start."""
        mgr, factory = _make_manager(pool_size=0, pool_agent="gideon")
        mgr._drain_and_claim = AsyncMock()

        with patch.object(
            type(mgr), "_resolve_agent_model", return_value="claude-opus-4.6"
        ):
            await mgr.get_or_create("test-key", agent="gideon", model="claude-opus-4.6")

        mgr._drain_and_claim.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_model_match_skipped_when_resume_sid_exists(self):
        """resume_sid takes priority — pool skipped even if model matches."""
        mgr, factory = _make_manager(pool_agent="gideon")
        mgr._drain_and_claim = AsyncMock()
        mgr._session_map.get = MagicMock(return_value="existing-sid")

        with patch.object(
            type(mgr), "_resolve_agent_model", return_value="claude-opus-4.6"
        ):
            await mgr.get_or_create("test-key", agent="gideon", model="claude-opus-4.6")

        mgr._drain_and_claim.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_model_still_claims_from_pool(self):
        """model=None (no explicit model) → pool used as before."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.resumed = False
        pooled.session_id = "fake-sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        provider, is_new, _ = await mgr.get_or_create(
            "test-key", agent="gideon", model=None
        )

        assert provider is pooled
        mgr._drain_and_claim.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_pool_agent_skips_model_resolution_on_claim(self):
        """No pool_agent configured → no model resolution on post-claim check."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.set_model = AsyncMock()
        pooled.resumed = False
        pooled.session_id = "fake-sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        with patch.object(type(mgr), "_resolve_agent_model") as mock_resolve:
            await mgr.get_or_create("test-key", agent=None, model="claude-opus-4.6")

        mock_resolve.assert_not_called()
        pooled.set_model.assert_not_awaited()


class TestStatelessSkipsPool:
    @pytest.mark.asyncio
    async def test_bg_session_skips_pool(self):
        """get_or_create for _bg must not claim from warm pool."""
        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = _make_provider()
        mgr._warm_pool.put_nowait((pooled, time.monotonic()))
        mgr._drain_and_claim = AsyncMock(return_value=pooled)

        provider, is_new, _ = await mgr.get_or_create("_bg", agent=None)

        mgr._drain_and_claim.assert_not_awaited()
        factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_stateless_prefix_skips_pool(self):
        """Stateless-prefixed keys (cron:, subagent:, etc.) skip pool."""
        for prefix in ("cron:job1", "subagent:abc"):
            mgr, factory = _make_manager(pool_agent="gideon")
            mgr._drain_and_claim = AsyncMock(return_value=_make_provider())

            await mgr.get_or_create(prefix, agent=None)

            mgr._drain_and_claim.assert_not_awaited()


class TestPoolDisabledSkipsClaim:
    @pytest.mark.asyncio
    async def test_pool_size_zero_skips_drain_and_claim(self):
        """pool_size=0 with no resume/model/stateless must still skip pool."""
        mgr, factory = _make_manager(pool_size=0, pool_agent="gideon")
        mgr._drain_and_claim = AsyncMock()

        await mgr.get_or_create("test-key", agent="gideon")

        mgr._drain_and_claim.assert_not_awaited()
        factory.assert_called_once()


class TestPoolHealthLoop:
    @pytest.mark.asyncio
    async def test_removes_dead_provider_and_replenishes(self):
        """Dead provider is removed during health sweep, replenish triggered."""
        mgr, _ = _make_manager(pool_agent="gideon")
        dead = _make_provider()
        dead.is_process_alive.return_value = False
        dead.exit_code = 1
        mgr._warm_pool.put_nowait((dead, time.monotonic()))
        mgr._schedule_replenish = MagicMock()

        call_count = 0

        async def _sleep_once(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=_sleep_once):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.empty()
        dead.shutdown.assert_awaited_once()
        mgr._schedule_replenish.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_expired_provider(self):
        """TTL-expired provider is removed during health sweep."""
        mgr, _ = _make_manager(pool_agent="gideon", pool_ttl_secs=60)
        stale = _make_provider()
        mgr._warm_pool.put_nowait((stale, time.monotonic() - 120))
        mgr._schedule_replenish = MagicMock()

        call_count = 0

        async def _sleep_once(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=_sleep_once):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.empty()
        stale.shutdown.assert_awaited_once()
        mgr._schedule_replenish.assert_called_once()

    @pytest.mark.asyncio
    async def test_keeps_healthy_provider(self):
        """Healthy provider survives health sweep."""
        mgr, _ = _make_manager(pool_agent="gideon")
        healthy = _make_provider()
        mgr._warm_pool.put_nowait((healthy, time.monotonic()))
        mgr._schedule_replenish = MagicMock()

        call_count = 0

        async def _sleep_once(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=_sleep_once):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.qsize() == 1
        healthy.shutdown.assert_not_awaited()
        mgr._schedule_replenish.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_pool_empty(self):
        """No crash when pool is empty during sweep."""
        mgr, _ = _make_manager(pool_agent="gideon")
        mgr._schedule_replenish = MagicMock()

        call_count = 0

        async def _sleep_once(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=_sleep_once):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        mgr._schedule_replenish.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_healthy_and_dead(self):
        """Only dead providers removed; healthy ones re-enqueued in order."""
        mgr, _ = _make_manager(pool_agent="gideon")
        healthy1 = _make_provider()
        dead = _make_provider()
        dead.is_process_alive.return_value = False
        healthy2 = _make_provider()
        mgr._warm_pool.put_nowait((healthy1, time.monotonic()))
        mgr._warm_pool.put_nowait((dead, time.monotonic()))
        mgr._warm_pool.put_nowait((healthy2, time.monotonic()))
        mgr._schedule_replenish = MagicMock()

        call_count = 0

        async def _sleep_once(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=_sleep_once):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.qsize() == 2
        dead.shutdown.assert_awaited_once()
        healthy1.shutdown.assert_not_awaited()
        healthy2.shutdown.assert_not_awaited()
        mgr._schedule_replenish.assert_called_once()


class TestPoolPids:
    def test_returns_pids_from_pool(self):
        """Extracts PIDs from pooled providers."""
        mgr, _ = _make_manager(pool_agent="gideon")
        p1 = _make_provider()
        p1.client = MagicMock()
        p1.client._pid = 1234
        p2 = _make_provider()
        p2.client = MagicMock()
        p2.client._pid = 5678
        mgr._warm_pool.put_nowait((p1, time.monotonic()))
        mgr._warm_pool.put_nowait((p2, time.monotonic()))

        pids = mgr._pool_pids()

        assert pids == {1234, 5678}
        assert mgr._warm_pool.qsize() == 2

    def test_empty_pool_returns_empty_set(self):
        mgr, _ = _make_manager(pool_agent="gideon")

        assert mgr._pool_pids() == set()

    def test_skips_provider_without_client(self):
        """Provider with no client attr is skipped, not crashed."""
        mgr, _ = _make_manager(pool_agent="gideon")
        p = _make_provider()
        del p.client
        mgr._warm_pool.put_nowait((p, time.monotonic()))

        pids = mgr._pool_pids()

        assert pids == set()
        assert mgr._warm_pool.qsize() == 1

    def test_skips_non_int_pid(self):
        """Provider with non-int PID is skipped."""
        mgr, _ = _make_manager(pool_agent="gideon")
        p = _make_provider()
        p.client = MagicMock()
        p.client._pid = None
        mgr._warm_pool.put_nowait((p, time.monotonic()))

        pids = mgr._pool_pids()

        assert pids == set()
        assert mgr._warm_pool.qsize() == 1

    def test_includes_sweep_pids_during_health_check(self):
        """PIDs temporarily out of queue during health sweep are still visible."""
        mgr, _ = _make_manager(pool_agent="gideon")
        mgr._pool_sweep_pids = {1111, 2222}

        pids = mgr._pool_pids()

        assert {1111, 2222} <= pids


class TestReloadProviderFactoryRefillsPool:
    @pytest.mark.asyncio
    async def test_reload_resets_pool_started_and_refills(self):
        """After reload_provider_factory, warm pool is replenished with new provider type."""
        mgr, factory = _make_manager(pool_size=1)
        mgr._pool_started = True
        old_provider = _make_provider()
        mgr._warm_pool.put_nowait((old_provider, time.monotonic()))

        with patch("gideon.engine.session.AppConfig.load") as mock_load:
            new_cfg = _make_cfg(pool_size=1)
            new_factory = MagicMock(side_effect=lambda *a, **kw: _make_provider())
            new_cfg.create_provider_factory = MagicMock(return_value=new_factory)
            new_cfg.agent.provider = "acp_agent"
            mock_load.return_value = new_cfg

            await mgr.reload_provider_factory()

        old_provider.shutdown.assert_awaited_once()
        assert mgr._pool_started is True

    @pytest.mark.asyncio
    async def test_reload_cancels_old_health_task(self):
        """Health loop task is cancelled on reload so a fresh one starts."""
        mgr, _ = _make_manager(pool_size=1)
        mgr._pool_started = True
        fake_task = MagicMock()
        fake_task.done.return_value = False
        fake_task.cancel = MagicMock()
        mgr._pool_health_task = fake_task

        with patch("gideon.engine.session.AppConfig.load") as mock_load:
            new_cfg = _make_cfg(pool_size=1)
            new_cfg.create_provider_factory = MagicMock(
                return_value=MagicMock(side_effect=lambda *a, **kw: _make_provider())
            )
            new_cfg.agent.provider = "acp"
            mock_load.return_value = new_cfg

            await mgr.reload_provider_factory()

        fake_task.cancel.assert_called_once()


class TestDefaultWorkspaceDir:
    def test_returns_realpath_of_workspace_dir(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with patch("gideon.core.config.loader.workspace_root", return_value=ws):
            from gideon.core.config.loader import default_workspace_dir

            result = default_workspace_dir()
        import os

        assert result == os.path.realpath(str(ws))

    def test_returns_empty_when_dir_missing(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with patch("gideon.core.config.loader.workspace_root", return_value=missing):
            from gideon.core.config.loader import default_workspace_dir

            result = default_workspace_dir()
        assert result == ""

    def test_returns_empty_when_sensitive(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with (
            patch("gideon.core.config.loader.workspace_root", return_value=ws),
            patch("gideon.security.security.is_sensitive_path", return_value=True),
        ):
            from gideon.core.config.loader import default_workspace_dir

            result = default_workspace_dir()
        assert result == ""

    def test_returns_empty_on_exception(self):
        with patch(
            "gideon.core.config.loader.workspace_root", side_effect=RuntimeError("boom")
        ):
            from gideon.core.config.loader import default_workspace_dir

            result = default_workspace_dir()
        assert result == ""


class TestPoolCwd:
    def test_pool_cwd_set_from_default_project_dir(self):
        from gideon.engine.session import ConversationDirectory

        cfg = _make_cfg()
        with patch(
            "gideon.engine.session.default_workspace_dir",
            return_value="/custom/workspace",
        ):
            mgr = ConversationDirectory(cfg)
        assert mgr._pool_cwd == "/custom/workspace"

    def test_pool_cwd_empty_when_no_workspace(self):
        from gideon.engine.session import ConversationDirectory

        cfg = _make_cfg()
        with patch("gideon.engine.session.default_workspace_dir", return_value=""):
            mgr = ConversationDirectory(cfg)
        assert mgr._pool_cwd == ""

    @pytest.mark.asyncio
    async def test_pool_claimed_when_cwd_matches_pool_cwd(self):
        """cwd == _pool_cwd should NOT bypass pool."""
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        mgr, factory = _make_manager(pool_agent="gideon")
        pooled = MagicMock(spec=AcpAgentProvider)
        pooled.start = AsyncMock()
        pooled.shutdown = AsyncMock()
        pooled.is_process_alive = MagicMock(return_value=True)
        pooled.resumed = False
        pooled.session_id = "sid"
        mgr._drain_and_claim = AsyncMock(return_value=pooled)
        mgr._schedule_replenish = MagicMock()

        provider, is_new, _ = await mgr.get_or_create(
            "test-key",
            agent="gideon",
            cwd="/home/user/.gideon/workspace",
        )

        assert provider is pooled
        mgr._drain_and_claim.assert_awaited_once()
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_pool_bypassed_when_pool_cwd_empty_and_cwd_set(self):
        """If _pool_cwd is empty, any cwd bypasses pool."""
        from gideon.engine.session import ConversationDirectory

        cfg = _make_cfg()
        factory = MagicMock(side_effect=lambda *a, **kw: _make_provider())
        with patch("gideon.engine.session.default_workspace_dir", return_value=""):
            mgr = ConversationDirectory(cfg, provider_factory=factory)

        pooled = _make_provider()
        mgr._warm_pool.put_nowait((pooled, time.monotonic()))
        mgr._drain_and_claim = AsyncMock(return_value=pooled)

        provider, is_new, _ = await mgr.get_or_create(
            "test-key",
            agent="gideon",
            cwd="/some/project",
        )

        mgr._drain_and_claim.assert_not_awaited()
        factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_fill_warm_pool_passes_pool_cwd(self):
        """Pool processes are spawned with _pool_cwd."""
        mgr, factory = _make_manager(pool_size=1)
        await mgr._fill_warm_pool()

        factory.assert_called_once()
        assert factory.call_args.kwargs.get("cwd") == "/home/user/.gideon/workspace"

    @pytest.mark.asyncio
    async def test_fill_warm_pool_passes_none_when_pool_cwd_empty(self):
        """Pool processes get cwd=None when _pool_cwd is empty."""
        from gideon.engine.session import ConversationDirectory

        cfg = _make_cfg(pool_size=1)
        factory = MagicMock(side_effect=lambda *a, **kw: _make_provider())
        with patch("gideon.engine.session.default_workspace_dir", return_value=""):
            mgr = ConversationDirectory(cfg, provider_factory=factory)

        await mgr._fill_warm_pool()

        factory.assert_called_once()
        assert factory.call_args.kwargs.get("cwd") is None
