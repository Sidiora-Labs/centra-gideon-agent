"""Native MCP leaf context remains bound to its own request."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from gideon.automation.workflows.engine import leaf_spawn_env
from gideon.automation.workflows.models import Node
from gideon.core.config.loader import config_path
from gideon.engine.agents.native.tools import InProcessMcpToolProvider
from gideon.extensions.apps import app_manager, manager
from gideon.extensions.providers import loader
from gideon.extensions.providers.provider_bridge import _build_native_runtime
from gideon.extensions.providers.registry import ProviderRegistry
from gideon.extensions.providers.use_cases import active_models_path
from gideon.integrations import mcp_automation, mcp_shared, mcp_subagents
from gideon.integrations.acp import translate
from gideon.integrations.acp.adapter import acp_event_to_agent_event
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.dialect import DefaultDialect
from gideon.integrations.acp.mcp_servers import core_tool_declaration
from gideon.integrations.acp.types import JsonRpcMessage
from gideon.integrations.llm.registry import sync_entries_from_config


def _stage_lineage(run_id: str, capability: str) -> dict[str, str]:
    node = Node.from_dict(
        {"kind": "stage", "id": "inspect", "config": {"prompt": "inspect"}}
    )
    env = leaf_spawn_env(
        node,
        {"prompt": "inspect", "capability": capability},
        run_id=run_id,
        depth=0,
    )
    return mcp_shared.leaf_lineage(env)


@pytest.mark.asyncio
async def test_leaf_lineage_is_request_scoped_and_read_only_posture_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in mcp_shared.LEAF_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIDEON_SESSION_KEY", "lineage-test")

    research = _stage_lineage("leaf0001", "research")
    mutating = _stage_lineage("leaf0002", "mutating")
    monkeypatch.setenv("__wf_run_id", "parent00")
    monkeypatch.setenv("__wf_depth", "3")

    provider_config = config_path()
    config = (
        json.loads(provider_config.read_text(encoding="utf-8"))
        if provider_config.exists()
        else {}
    )
    config["providers"] = [
        {
            "name": "lineage-local-ollama",
            "type": "ollama",
            "model": "offline-lineage-model",
            "options": {"endpoint": "http://127.0.0.1:11434"},
        }
    ]
    provider_config.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(manager, "config_dir", lambda: provider_config.parent)
    assert "ollama-models" in app_manager.seed_builtin_apps()
    manifest = app_manager._manifest_of("ollama-models")
    assert manifest is not None and manifest.native
    extension_registry = ProviderRegistry()
    extension_registry.register(manifest)
    extension = extension_registry.get("ollama-models")
    assert extension is not None
    bundle_factory = loader.load_factory(extension)
    bundled_provider = bundle_factory(
        {"model": "offline-lineage-model", "endpoint": "http://127.0.0.1:11434"}
    )
    assert type(bundled_provider).__name__ == "OllamaProvider"
    sync_entries_from_config()
    active_models_path().write_text(
        json.dumps({"chat": ["lineage-local-ollama:offline-lineage-model"]}),
        encoding="utf-8",
    )

    # Exercise the real provider bridge and runtime constructors. The arbitrary
    # inherited environment is deliberately present; only workflow leaf keys may
    # cross the constructor boundary, and an ordinary session gets an explicit
    # empty context rather than inheriting its parent's lineage.
    ambient = dict(os.environ)
    native_leaf = _build_native_runtime(
        use_case="chat",
        session_key="lineage-native-leaf",
        agent="gideon",
        model_override=None,
        cwd=None,
        extra_env=research,
    )
    native_parent = _build_native_runtime(
        use_case="chat",
        session_key="lineage-native-parent",
        agent="gideon",
        model_override=None,
        cwd=None,
        extra_env={},
    )
    assert native_leaf._leaf_lineage == mcp_shared.leaf_lineage(research)
    assert native_parent._leaf_lineage == {}
    assert type(native_leaf._model).__name__ == "OllamaProvider"
    assert type(native_parent._model).__name__ == "OllamaProvider"
    assert native_leaf._model._client is None

    acp_leaf = AcpClient(session_key="lineage-acp-leaf", extra_env=research)
    acp_parent = AcpClient(session_key="lineage-acp-parent", extra_env={})
    leaf_server_env = {
        item["name"]: item["value"] for item in acp_leaf._core_mcp_servers()[0]["env"]
    }
    parent_server_env = {
        item["name"]: item["value"] for item in acp_parent._core_mcp_servers()[0]["env"]
    }
    leaf_server_context = {
        key: value
        for key, value in leaf_server_env.items()
        if key in mcp_shared.LEAF_KEYS
    }
    assert leaf_server_context == mcp_shared.leaf_lineage(research)
    assert not (set(parent_server_env) & set(mcp_shared.LEAF_KEYS))
    assert dict(os.environ) == ambient

    async def observe(lineage: dict[str, str]) -> tuple[str, int, str, str, str, str]:
        token = mcp_shared.bind_leaf_lineage(lineage)
        try:
            await asyncio.sleep(0)
            denial = mcp_shared.leaf_tool_denial("automation_create")
            native_subagents = InProcessMcpToolProvider(
                module="gideon.integrations.mcp_subagents"
            )
            native_orchestration = await native_subagents.invoke(
                "subagent_run", {"task": "fan out"}
            )
            acp_write = mcp_automation._call_tool("automation_create", {})
            native_write = await InProcessMcpToolProvider(
                module="gideon.integrations.mcp_automation"
            ).invoke("automation_create", {})
            return (
                mcp_shared.leaf_run_id(),
                mcp_subagents._wf_depth(),
                denial,
                mcp_automation._resolve_resume_target({"resume_run_id": "self"})[0][
                    "run_id"
                ],
                native_orchestration.output,
                acp_write + "\n" + native_write.output,
            )
        finally:
            mcp_shared.reset_leaf_lineage(token)

    restricted, writable = await asyncio.gather(observe(research), observe(mutating))
    assert restricted[:2] == ("leaf0001", 1)
    assert "read-only" in restricted[2].lower()
    assert restricted[3] == "leaf0001"
    assert "orchestration tool" in restricted[4]
    assert "read-only" in restricted[5].lower()
    assert writable[:2] == ("leaf0002", 1)
    assert writable[2] == ""
    assert writable[3] == "leaf0002"
    assert "orchestration tool" in writable[4]
    assert "read-only" not in writable[5].lower()

    empty = mcp_shared.bind_leaf_lineage({})
    try:
        assert mcp_shared.leaf_run_id() == ""
        assert mcp_subagents._wf_depth() == 0
    finally:
        mcp_shared.reset_leaf_lineage(empty)

    assert mcp_shared.leaf_run_id() == "parent00"
    assert mcp_subagents._wf_depth() == 3

    inputs: dict[str, str] = {}
    seen: dict = {}
    stats: list[tuple[str, str]] = []
    call = translate.extract_tool_event(
        JsonRpcMessage(
            method="session/update",
            params={
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "notice-1",
                    "title": "mcp__gideon-core__notify",
                    "kind": "other",
                    "rawInput": {"text": "Run finished", "session": "channel"},
                    "status": "pending",
                }
            },
        ),
        inputs,
        seen,
        stats,
    )
    assert call is not None
    permission = translate.build_permission_event(
        JsonRpcMessage(
            id=1,
            method="session/request_permission",
            params={
                "toolCall": {
                    "toolCallId": "notice-1",
                    "title": "mcp__gideon-core__notify",
                },
                "options": [],
            },
        ),
        DefaultDialect(),
        inputs,
        seen,
        {},
    )
    adapted = acp_event_to_agent_event(permission)
    assert adapted.risk_level == "caution"
    assert adapted.tool_meta == {"tells_owner": True}

    assert core_tool_declaration(
        "mcp__gideon-core__notify", "other", {"text": "x", "session": "channel"}
    ) == ("caution", True)
    assert core_tool_declaration(
        "mcp__gideon-core__notify", "other", {"text": "x"}
    ) == ("caution", False)
    assert core_tool_declaration(
        "mcp__gideon-core__notify",
        "other",
        {"text": "x", "session": "channel", "channel": "C12345678"},
    ) == ("caution", False)
    assert core_tool_declaration(
        "mcp__gideon-core__notify",
        "other",
        {"text": "x", "session": "channel", "unexpected": "value"},
    ) == ("", False)
    assert core_tool_declaration(
        "mcp__foreign__notify", "other", {"text": "x", "session": "channel"}
    ) == ("", False)
    assert core_tool_declaration(
        "Terminal", "execute", {"command": "notify --session channel"}
    ) == ("", False)
