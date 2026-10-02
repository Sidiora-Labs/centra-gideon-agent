from __future__ import annotations

import os
import time
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.context_engine import (
    assemble_context,
    get_engine,
    prepare_context_turn,
    set_engine,
)
from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.lifecycle import _bind_hypermid_surface
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.config import (
    ContextConfig,
    ContextMode,
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.config_store import ContextConfigStore
from gideon.hypermid.foundation import Id
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment, attach_runtime
from gideon.hypermid.models import Scope
from gideon.hypermid.primary_engine import PrimaryContextEngine
from gideon.interfaces.dashboard.handlers.hypermid import register_hypermid_routes


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    target = os.environ.get("CARGO_TARGET_DIR")
    candidates = [Path(target) / "debug" / "hypermid-daemon"] if target else []
    candidates.append(Path("target/debug/hypermid-daemon"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise AssertionError("focused gate requires the built hypermid-daemon binary")


@pytest.mark.asyncio
async def test_runtime_turn_boundary_installs_real_primary_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    set_engine(None)
    configuration = AppConfig()
    configuration.hypermid = ContextConfig(mode=ContextMode.SHADOW)
    runtime = RuntimeCoordinator(
        configuration,
        no_dashboard=True,
        no_crons=True,
        no_open=True,
        port_override="auto",
    )
    runtime._init_services()

    run_dir = tmp_path / "hypermid"
    record = run_dir / "connection.json"
    scope = Scope("primary-owner", "primary-project", "primary-workspace")
    client = HypermidClient(record, scope=scope)
    store = ContextConfigStore(run_dir / "configuration.sqlite3")
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client, mode=ContextMode.SHADOW),
        DaemonConfig(
            transport=DaemonTransport.UNIX_SOCKET,
            endpoint=str(run_dir / "daemon.sock"),
            executable=str(_daemon_binary()),
            start_on_demand=True,
            auth=LocalAuthConfig(
                method=LocalAuthMethod.PEER_AND_HMAC,
                token_file=str(run_dir / "auth.token"),
                require_peer_identity=True,
            ),
        ).with_connection_record(str(record)),
        connection_record=record,
        enrollment=LocalEnrollment(
            scope=scope,
            credential_id=Id("primary-local-credential"),
            capability_id=Id("primary-summary-capability"),
            operations=("read",),
            resources=(Id("memory-list"),),
            expires_ms=time.time_ns() // 1_000_000 + 600_000,
        ),
        config_store=store,
        initial_config=configuration.hypermid,
        runtime=runtime,
    )
    attach_runtime(runtime, lifecycle)
    session_key = "primary-session"
    prompt = "Preserve this exact primary turn."
    try:
        started = await lifecycle.start()
        assert started.available and started.mode == "shadow"
        assert lifecycle.writer is not None
        assert lifecycle.writer.snapshot().writer == "gideon"
        assert get_engine() is lifecycle.adapter

        snapshot = store.snapshot()
        store.stage(
            ContextConfig(mode=ContextMode.PRIMARY),
            expected_revision=snapshot.policy_revision,
            expected_digest=snapshot.config_digest,
        )
        runtime.conv_log.append(session_key, "user", prompt)
        await prepare_context_turn(session_key)

        assert isinstance(get_engine(), PrimaryContextEngine)
        assert lifecycle.writer.snapshot().owns_writes
        assembled = assemble_context(
            runtime.ctx_builder,
            prompt,
            is_new_session=True,
            session_key=session_key,
            blocks_reads=True,
            blocks_writes=True,
            active_recall=False,
        )
        assert assembled.metadata["hypermid"]["serialized_digest"]
        source_session_id = str(
            lifecycle.context_bridge.sync_session(session_key).session_id
        )
        surface = SimpleNamespace()
        _bind_hypermid_surface(runtime, surface)
        app = web.Application()
        app["state"] = surface
        register_hypermid_routes(app)
        async with TestClient(TestServer(app)) as dashboard_client:
            response = await dashboard_client.get(
                f"/api/hypermid/sessions/{source_session_id}/primary-context"
            )
            assert response.status == 200
            inspection = await response.json()
        assert inspection["state"] == "complete"
        assert inspection["writer"] == "hypermid"
        assert inspection["scope"] == scope.to_wire()
        assert inspection["digest_health"]["state"] == "healthy"
        assert prompt not in json.dumps(inspection, sort_keys=True)

        primary = store.snapshot()
        store.stage(
            ContextConfig(mode=ContextMode.SHADOW),
            expected_revision=primary.policy_revision,
            expected_digest=primary.config_digest,
        )
        await prepare_context_turn(session_key)
        assert get_engine() is lifecycle.adapter
        assert lifecycle.writer.snapshot().writer == "gideon"
        assert lifecycle.adapter.status().writer == "gideon"
    finally:
        await lifecycle.stop()
        set_engine(None)
