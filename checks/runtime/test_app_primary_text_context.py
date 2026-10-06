"""Text-tier app context against the active authenticated Hypermid primary engine."""

import json
import time
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_text_app_task_alone_on_real_primary_writer(tmp_path, monkeypatch):
    home = tmp_path / "gideon"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    from gideon.cognition.context_engine import (
        get_engine,
        set_engine,
        prepare_context_turn,
    )
    from gideon.core.config.loader import AppConfig
    from gideon.engine.gateway import RuntimeCoordinator
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
    from gideon.hypermid.lifecycle import (
        HypermidLifecycle,
        LocalEnrollment,
        attach_runtime,
    )
    from gideon.hypermid.models import Scope
    from gideon.hypermid.primary_engine import PrimaryContextEngine
    from gideon.security.session_credentials import begin_turn, end_turn
    from gideon.security.approval_answer import app

    folder = home / "apps" / "text-app"
    folder.mkdir(parents=True)
    (folder / "installed.json").write_text(
        json.dumps({"name": "text-app", "enabled": True, "version": "1.0.0"})
    )
    (folder / "app.json").write_text(
        json.dumps(
            {
                "name": "text-app",
                "version": "1.0.0",
                "permissions": {"agent": "text", "memory": "shared"},
            }
        )
    )
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
    root = tmp_path / "hypermid"
    record = root / "connection.json"
    scope = Scope("actual-owner", "actual-project", "actual-workspace")
    client = HypermidClient(record, scope=scope)
    store = ContextConfigStore(root / "config.sqlite3")
    lifecycle = HypermidLifecycle(
        HypermidAdapter(client, mode=ContextMode.SHADOW),
        DaemonConfig(
            transport=DaemonTransport.UNIX_SOCKET,
            endpoint=str(root / "daemon.sock"),
            executable=str(Path("target/debug/hypermid-daemon").resolve()),
            start_on_demand=True,
            auth=LocalAuthConfig(
                method=LocalAuthMethod.PEER_AND_HMAC,
                token_file=str(root / "auth.token"),
                require_peer_identity=True,
            ),
        ).with_connection_record(str(record)),
        connection_record=record,
        enrollment=LocalEnrollment(
            scope=scope,
            credential_id=Id("app-text-credential"),
            capability_id=Id("app-text-summary"),
            operations=("read",),
            resources=(Id("memory-list"),),
            expires_ms=time.time_ns() // 1_000_000 + 600_000,
        ),
        config_store=store,
        initial_config=configuration.hypermid,
        runtime=runtime,
    )
    attach_runtime(runtime, lifecycle)
    credential = None
    try:
        assert (await lifecycle.start()).available
        snapshot = store.snapshot()
        store.stage(
            ContextConfig(mode=ContextMode.PRIMARY),
            expected_revision=snapshot.policy_revision,
            expected_digest=snapshot.config_digest,
        )
        runtime.conv_log.append("app-text-session", "user", "OWNER-PRIVATE-HISTORY")
        await prepare_context_turn("app-text-session")
        engine = get_engine()
        assert isinstance(engine, PrimaryContextEngine)
        assert lifecycle.writer.snapshot().owns_writes
        credential = begin_turn(
            "app-text-session",
            app("text-app"),
            turn_id="text-turn",
            created_by_app="text-app",
            memory_mode="persistent",
        )
        assembled = engine.assemble(
            runtime.ctx_builder,
            "APP-TASK-ONLY",
            is_new_session=True,
            session_key="app-text-session",
            agent="Gideon",
            system_prompt_override="OWNER-PRIVATE-SYSTEM",
            force_skill_ids=["private-skill"],
        )
        assert assembled.message == "APP-TASK-ONLY"
        assert assembled.injected_chars == 0
        assert [component.text for component in assembled.components] == [
            "APP-TASK-ONLY"
        ]
        assert engine._bridge_sequences == {}
        assert engine.scope == scope
        assert "OWNER-PRIVATE" not in json.dumps(assembled.metadata)
    finally:
        end_turn(credential)
        await lifecycle.stop()
        set_engine(None)
