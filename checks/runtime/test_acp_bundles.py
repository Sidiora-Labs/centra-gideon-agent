"""P1: the public removable ``acp:<cli>`` bundles (claude-code / codex).

Proves the bundle wiring matches how ACP agents are actually selected today:

* enabling a bundle registers an ``acp_agent`` ProviderEntry named ``acp:<cli>``
  with an EXPLICIT ``options.dialect`` + resolved ``options.command`` — so the
  entry surfaces in ``/api/agent-providers`` and resolves through the
  registry-build path an ``acp:<cli>`` agent uses;
* ``get_agent_provider_class("acp:<cli>")`` resolves the ``acp`` family →
  ``AcpAgentProvider`` (dialect is NOT inferred from the command basename);
* readiness probes cleanly: a faked-present binary is detected (state != not_found),
  an absent binary → not_found, and an absent codex never raises;
* the Claude config-isolation seed strips the auto-approve permission surface,
  writes 0600, and fails closed against the operator's real ~/.claude.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

from gideon.engine.agents.registry import get_agent_provider_class
from gideon.integrations.llm.acp_agent import ACP_AGENT_CAPABILITY
from gideon.integrations.llm.registry import (
    get_default_registry,
    reset_default_registry,
)

_APPS_DIR = Path(__file__).resolve().parents[3] / "apps"
if not _APPS_DIR.is_dir():
    pytest.skip(
        "workspace apps/ dir not present (standalone clone)", allow_module_level=True
    )


def _load_app_provider(app_name: str):
    path = _APPS_DIR / app_name / "provider.py"
    uniq = f"_gideon_app_{app_name.replace('-', '_')}__provider"
    spec = importlib.util.spec_from_file_location(uniq, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[uniq] = mod
    added = str(_APPS_DIR / app_name) not in sys.path
    if added:
        sys.path.insert(0, str(_APPS_DIR / app_name))
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(str(_APPS_DIR / app_name))
    return mod


claude_code = _load_app_provider("claude-code-agent")
codex = _load_app_provider("codex-agent")


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a fresh default registry (re-import the acp_agent type),
    then the process-wide singletons are RESTORED on teardown.

    ``importlib.reload(acp_agent)`` builds a NEW module + a NEW
    ``AcpAgentProvider`` class and re-registers it on both the model registry
    (``register_type``) and the agent registry (``register_agent_provider``).
    If we left that in place, a later test holding the ORIGINAL class via
    ``from … import AcpAgentProvider`` would fail an ``is`` identity check, and
    the model registry — emptied by ``reset_default_registry`` — would be
    missing every other provider (bedrock/anthropic/…) since ``import
    gideon.integrations.llm`` is already cached and won't re-run their registration.
    So snapshot everything we perturb and put it back. (#25c)
    """
    import importlib
    import sys

    import gideon.integrations.llm as _llm_pkg
    from gideon.engine.agents import registry as _agent_reg
    from gideon.integrations.llm import registry as _model_reg

    saved_registry = _model_reg._default_registry
    saved_module = sys.modules.get("gideon.integrations.llm.acp_agent")
    saved_pkg_attr = getattr(_llm_pkg, "acp_agent", None)
    saved_agent_providers = dict(_agent_reg._providers)

    reset_default_registry()
    import gideon.integrations.llm.acp_agent as _acp_agent

    importlib.reload(_acp_agent)
    try:
        yield
    finally:
        _model_reg.set_default_registry(saved_registry)
        if saved_module is not None:
            sys.modules["gideon.integrations.llm.acp_agent"] = saved_module
            _llm_pkg.acp_agent = saved_pkg_attr
        _agent_reg._providers.clear()
        _agent_reg._providers.update(saved_agent_providers)


def _make_exec(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_on_path(monkeypatch, tmp_path, name: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    target = bindir / name
    _make_exec(target)
    monkeypatch.setenv("PATH", str(bindir))
    return target


def test_public_bundles_exist_as_apps():
    for name in ("claude-code-agent", "codex-agent"):
        assert (_APPS_DIR / name / "app.json").is_file(), f"{name} app.json missing"
        assert (
            _APPS_DIR / name / "provider.py"
        ).is_file(), f"{name} provider.py missing"


def test_manifests_are_agent_type_with_acp_capability():
    for name in ("claude-code-agent", "codex-agent"):
        m = json.loads((_APPS_DIR / name / "app.json").read_text())
        prov = m["provider"]
        assert prov["type"] == "agent"
        assert "acp" in prov["capabilities"]
        assert prov["implementation"] == "provider:create_provider"


def test_claude_registers_entry_with_explicit_dialect(monkeypatch, tmp_path):
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "0")
    monkeypatch.delenv("CLAUDE_CODE_ACP_BIN", raising=False)

    claude_code.create_provider({"model": "claude-opus-4-8"})

    entry = get_default_registry().get_entry("acp:claude-code")
    assert entry.type == "acp_agent"
    assert entry.model == "claude-opus-4-8"
    assert entry.options["dialect"] == "claude-code"
    assert entry.options["command"][0].endswith("claude-agent-acp")
    assert entry.declared_capabilities == ACP_AGENT_CAPABILITY.capabilities


def test_claude_dialect_explicit_even_when_command_is_npx(monkeypatch, tmp_path):
    """The provider_id pitfall: an npx launch must NOT make dialect=npx."""
    empty = tmp_path / "empty-home"
    empty.mkdir()
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setenv("GIDEON_HOME", str(empty / ".gideon"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CLAUDE_CODE_ACP_BIN", raising=False)
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "0")

    claude_code.create_provider({})

    entry = get_default_registry().get_entry("acp:claude-code")
    assert "npx" in entry.options["command"][0] or entry.options["command"][0].endswith(
        "npx"
    )
    assert entry.options["dialect"] == "claude-code"


def test_acp_family_resolves_to_acp_agent_provider():
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    assert get_agent_provider_class("acp:claude-code") is AcpAgentProvider
    assert get_agent_provider_class("acp:codex") is AcpAgentProvider


def test_claude_readiness_present_is_not_not_found(monkeypatch, tmp_path):
    """Faked-present binary → binary IS detected (the fake can't speak ACP, so
    the handshake fails → 'error', but crucially NOT 'not_found').

    The adapter delegates to the ``claude`` engine, so fake that on PATH too —
    otherwise the delegate gate (correctly) reports ``not_found`` for the missing
    engine before the handshake is ever attempted."""
    bindir = _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp").parent
    _make_exec(bindir / "claude")
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "0")
    monkeypatch.delenv("CLAUDE_CODE_ACP_BIN", raising=False)
    claude_code.create_provider({})
    entry = get_default_registry().get_entry("acp:claude-code")

    cls = get_agent_provider_class("acp:claude-code")
    status = asyncio.run(cls.probe_readiness(entry.options))
    assert status.state != "not_found"
    assert status.ready is False


def test_probe_uses_configured_dialect_not_default(monkeypatch, tmp_path):
    """Regression: probe_readiness must build its client with the options' dialect.

    A claude/codex adapter expects int protocolVersion; if the probe falls back
    to the default date-string shape, the adapter rejects initialize with
    -32602 and a healthy CLI looks broken. Pin that the dialect threads through."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    fake = _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    captured = {}
    real_init = AcpAgentProvider.__init__

    def spy_init(self, **kw):
        captured["dialect"] = kw.get("dialect")
        real_init(self, **kw)

    monkeypatch.setattr(AcpAgentProvider, "__init__", spy_init)
    options = {"command": [str(fake)], "dialect": "claude-code"}
    asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert captured["dialect"] == "claude-code"


def test_claude_readiness_absent_is_not_found(monkeypatch):
    """A configured command whose binary is absent → not_found, no raise."""
    cls = get_agent_provider_class("acp:claude-code")
    options = {"command": ["/nonexistent/claude-agent-acp"], "dialect": "claude-code"}
    status = asyncio.run(cls.probe_readiness(options))
    assert status.state == "not_found"
    assert status.ready is False


def test_codex_absent_probes_not_ready_without_raising(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    codex.create_provider({})
    entry = get_default_registry().get_entry("acp:codex")
    cls = get_agent_provider_class("acp:codex")
    status = asyncio.run(cls.probe_readiness(entry.options))
    assert status.ready is False
    assert status.state in ("not_found", "error", "needs_login", "timeout")


def test_npx_fallback_without_node_ge_is_clean_not_found(monkeypatch, tmp_path):
    """The durable-fix gate: an ``npx -y <pkg>`` command + NO Node >= 20 must
    report a clean ``not_found`` ("adapter not installed, can't provision")
    instead of spawning npx and dying on a raw EBADENGINE/fetch error."""
    npx = tmp_path / "npx"
    npx.write_text("#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        "gideon.integrations.acp.cli_resolve.resolve_node_ge", lambda *a, **k: None
    )
    cls = get_agent_provider_class("acp:codex")
    options = {
        "command": [str(npx), "-y", "@agentclientprotocol/codex-acp"],
        "dialect": "codex",
    }
    status = asyncio.run(cls.probe_readiness(options))
    assert status.ready is False
    assert status.state == "not_found"
    assert "Node >= 20" in status.detail or "not installed" in status.detail


def test_codex_forwards_underlying_cli(monkeypatch, tmp_path):
    """codex bundle resolves the host `codex` CLI and forwards it as CODEX_PATH —
    the EXACT env var the codex-acp adapter reads (it spawns `<CODEX_PATH ??
    "codex"> app-server` and inherits that codex's own auth). It does NOT read
    CODEX_EXECUTABLE, so forwarding under that name would be silently dropped and
    the adapter would fall back to its bundled OpenAI-auth codex → "Authentication
    required". Regression for that env-var-name mismatch."""
    _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\n")
    fake_codex.chmod(0o755)
    monkeypatch.setenv("CODEX_PATH", str(fake_codex))
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    codex.create_provider({})
    entry = get_default_registry().get_entry("acp:codex")
    assert entry.options.get("env", {}).get("CODEX_PATH") == str(fake_codex)
    assert "CODEX_EXECUTABLE" not in entry.options.get("env", {})


def test_codex_declares_engine_requirement(monkeypatch, tmp_path):
    """codex bundle records its delegate engine as ``requires_executable`` so the
    vendor-neutral probe can enforce it (vendor knowledge stays in the bundle).
    The declared env_var is CODEX_PATH — the override the adapter actually honors."""
    _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    monkeypatch.delenv("CODEX_PATH", raising=False)
    codex.create_provider({})
    req = (
        get_default_registry().get_entry("acp:codex").options.get("requires_executable")
    )
    assert req and req["label"] == "codex" and req["env_var"] == "CODEX_PATH"


def test_delegate_gate_absent_engine_is_not_found(monkeypatch, tmp_path):
    """A passing ACP handshake is NOT sufficient: when the adapter is present but
    its declared engine CLI is absent, the probe reports not_found UP FRONT (no
    spawn) instead of a hollow 'ready' that would die on the first prompt.

    Regression for the live codex false-positive: codex-acp resolves via npx and
    handshakes fine, but no `codex` engine exists on the machine."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    fake_adapter = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    options = {
        "command": [str(fake_adapter)],
        "dialect": "codex",
        "requires_executable": {"label": "codex", "env_var": "CODEX_PATH", "path": ""},
    }
    spawned = {"hit": False}
    orig_init = AcpAgentProvider.__init__

    def spy_init(self, **kw):
        spawned["hit"] = True
        orig_init(self, **kw)

    monkeypatch.setattr(AcpAgentProvider, "__init__", spy_init)
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state == "not_found"
    assert status.ready is False
    assert "codex" in status.detail
    assert spawned["hit"] is False


def test_delegate_gate_satisfied_by_declared_path(monkeypatch, tmp_path):
    """When the bundle resolved the engine (forwarded via its env var → recorded
    as requires_executable.path), the gate is satisfied and the probe proceeds to
    the handshake (which then fails on the fake adapter → not 'not_found')."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    fake_adapter = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    fake_engine = fake_adapter.parent / "codex"
    _make_exec(fake_engine)
    options = {
        "command": [str(fake_adapter)],
        "dialect": "codex",
        "requires_executable": {
            "label": "codex",
            "env_var": "CODEX_PATH",
            "path": str(fake_engine),
        },
    }
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state != "not_found"


def test_delegate_gate_satisfied_by_live_path(monkeypatch, tmp_path):
    """No declared path, but the engine is live-resolvable on PATH → gate passes."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    fake_adapter = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    _make_exec(fake_adapter.parent / "codex")
    options = {
        "command": [str(fake_adapter)],
        "dialect": "codex",
        "requires_executable": {"label": "codex", "env_var": "CODEX_PATH", "path": ""},
    }
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state != "not_found"


def test_no_delegate_declaration_skips_gate(monkeypatch, tmp_path):
    """A runtime whose binary IS the engine (a native CLI, not a Zed adapter
    delegating to a separate engine) declares no requires_executable, so the
    delegate gate is skipped entirely."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    fake = _fake_on_path(monkeypatch, tmp_path, "native-acp-cli")
    options = {"command": [str(fake), "acp"], "dialect": "default"}
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state != "not_found"


def _register_default_dialect_entry(monkeypatch, tmp_path, *, model: str = ""):
    """Register a neutral ``acp:test-cli`` default-dialect entry the way a bundle
    would, without depending on any specific vendor bundle. Returns the cli id."""
    from gideon.integrations.acp_bundles._register import register_acp_cli_entry

    fake = _fake_on_path(monkeypatch, tmp_path, "test-cli")
    register_acp_cli_entry(
        cli="test-cli",
        dialect="default",
        command=[str(fake), "acp"],
        model=model,
        extension="test-cli-agent",
    )
    return "test-cli"


def test_factory_honors_per_session_agent_model_mode(monkeypatch, tmp_path):
    """Regression: the acp _factory MUST honor the per-session agent (modeId),
    model, and acp_mode the bridge passes — not just the global entry defaults.
    Before the fix these kwargs were dropped, so selecting an ACP agent silently
    ran the backend default and never switched the modeId."""
    _register_default_dialect_entry(monkeypatch, tmp_path)
    reg = get_default_registry()
    entry = reg.get_entry("acp:test-cli")
    config = {"model": entry.model, **(entry.options or {})}
    config.update(agent="gpu-dev", model="claude-opus-4.8", acp_mode="plan")
    prov = reg.build("acp:test-cli", **config)
    assert prov._agent_name == "gpu-dev"
    assert prov._model == "claude-opus-4.8"
    assert prov._mode == "plan"
    assert prov.client._agent == "gpu-dev"
    assert prov.client._model == "claude-opus-4.8"
    assert prov.client._mode == "plan"


def test_factory_falls_back_to_entry_defaults_without_per_session(
    monkeypatch, tmp_path
):
    """With no per-session agent/model/mode, the factory uses the entry defaults.
    The agent axis falls back to EMPTY (no fabricated name) — ACP has no global
    default agent, so an unselected agent means the CLI uses its own built-in
    default (the dialect skips the set_mode activation for an empty agent)."""
    _register_default_dialect_entry(monkeypatch, tmp_path, model="glm-5")
    reg = get_default_registry()
    entry = reg.get_entry("acp:test-cli")
    prov = reg.build("acp:test-cli", **{"model": entry.model, **(entry.options or {})})
    assert prov._agent_name == ""
    assert prov._model == "glm-5"
    assert prov._mode == ""


def _stub_discovery_client(monkeypatch, session_new: dict):
    """Stub AcpConnection spawn + handshake so discover_agents reads `session_new`
    without launching a real process. Post-P9#7 discover_agents probes on a throwaway
    AcpConnection (spawn → initialize → new_session → last_session_new_snapshot)."""
    from unittest.mock import AsyncMock, MagicMock

    from gideon.integrations.acp import session as session_mod
    from gideon.integrations.llm import acp_agent as acp_mod

    fake_conn = MagicMock()
    fake_conn.initialize = AsyncMock(return_value={})
    fake_conn.new_session = AsyncMock(return_value=MagicMock())
    fake_conn.last_session_new_snapshot = session_new
    fake_conn.close = AsyncMock()

    async def fake_spawn(**kwargs):
        return fake_conn

    monkeypatch.setattr(session_mod.AcpConnection, "spawn", staticmethod(fake_spawn))
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda c: "/usr/bin/" + str(c))
    from gideon.engine.agents.provider import ReadinessStatus

    async def fake_probe(cls, options):
        return ReadinessStatus(ready=True, state="ready")

    monkeypatch.setattr(
        acp_mod.AcpAgentProvider, "probe_readiness", classmethod(fake_probe)
    )
    return acp_mod


@pytest.mark.asyncio
async def test_discover_agents_claude_effort(monkeypatch, tmp_path):
    """claude discovery → exactly ONE base agent; the backend's effort levels
    ride along as supported_efforts (per-turn setting), NOT effort-variant agents."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    snew = {
        "configOptions": [
            {"id": "model", "options": [{"value": "default"}, {"value": "opus"}]},
            {
                "id": "effort",
                "options": [
                    {"value": "default", "name": "Default"},
                    {"value": "high", "name": "High"},
                    {"value": "max", "name": "Max"},
                ],
            },
        ],
    }
    _stub_discovery_client(monkeypatch, snew)
    fake = _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    agents = await AcpAgentProvider.discover_agents(
        {
            "command": [str(fake)],
            "dialect": "claude-code",
            "runtime_id": "acp:claude-code",
            "runtime_label": "Claude",
        }
    )
    assert len(agents) == 1
    base = agents[0]
    assert base.id == "acp:claude-code" and base.name == "Claude"
    assert base.reasoning_effort == "" and base.models == ["default", "opus"]
    assert base.supported_efforts == [
        {"value": "high", "label": "High"},
        {"value": "max", "label": "Max"},
    ]


@pytest.mark.asyncio
async def test_discover_agents_absent_binary_returns_empty(monkeypatch):
    """No adapter on PATH → discovery returns [] (never raises)."""
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    agents = await AcpAgentProvider.discover_agents(
        {
            "command": ["/nonexistent/test-cli", "acp"],
            "dialect": "default",
            "runtime_id": "acp:test-cli",
        }
    )
    assert agents == []


@pytest.mark.asyncio
async def test_discover_agents_spawn_failure_returns_empty(monkeypatch):
    """A ready runtime whose probe spawn/handshake THROWS → [] (never raises).

    Regression: the failure branch's debug log referenced an undefined
    ``runtime_id`` name, so any handshake exception escaped discover_agents as
    a NameError instead of the contractual [] — breaking "discovery never
    raises into the API"."""
    import shutil as _shutil

    from gideon.engine.agents.provider import ReadinessStatus
    from gideon.integrations.acp import session as session_mod
    from gideon.integrations.llm import acp_agent as acp_mod
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    monkeypatch.setattr(_shutil, "which", lambda c: "/usr/bin/" + str(c))

    async def ready(cls, options):
        return ReadinessStatus(ready=True, state="ready")

    monkeypatch.setattr(acp_mod.AcpAgentProvider, "probe_readiness", classmethod(ready))

    async def boom(**kwargs):
        raise RuntimeError("handshake exploded")

    monkeypatch.setattr(session_mod.AcpConnection, "spawn", staticmethod(boom))

    agents = await AcpAgentProvider.discover_agents(
        {
            "command": ["test-cli", "acp"],
            "dialect": "default",
            "runtime_id": "acp:test-cli",
        }
    )
    assert agents == []


@pytest.mark.asyncio
async def test_discover_agents_not_ready_returns_empty(monkeypatch, tmp_path):
    """A runtime whose readiness probe fails (e.g. codex: adapter present but its
    engine CLI absent → not_found) contributes NO discovered agents — discovery
    and readiness never disagree."""
    import shutil as _shutil

    from gideon.engine.agents.provider import ReadinessStatus
    from gideon.integrations.llm import acp_agent as acp_mod
    from gideon.integrations.llm.acp_agent import AcpAgentProvider

    monkeypatch.setattr(_shutil, "which", lambda c: "/usr/bin/" + str(c))

    async def not_ready(cls, options):
        return ReadinessStatus(
            ready=False, state="not_found", detail="engine 'codex' not found"
        )

    monkeypatch.setattr(
        acp_mod.AcpAgentProvider, "probe_readiness", classmethod(not_ready)
    )

    fake = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    agents = await AcpAgentProvider.discover_agents(
        {
            "command": [str(fake)],
            "dialect": "codex",
            "runtime_id": "acp:codex",
            "requires_executable": {
                "label": "codex",
                "env_var": "CODEX_PATH",
                "path": "",
            },
        }
    )
    assert agents == []


def test_bundle_factories_return_none(monkeypatch, tmp_path):
    """Like native-agents, the factory returns None (config/registry-based)."""
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "0")
    assert claude_code.create_provider({}) is None
    assert codex.create_provider({}) is None


def test_enable_is_idempotent(monkeypatch, tmp_path):
    """Re-running create_provider must not raise the duplicate-name guard."""
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "0")
    claude_code.create_provider({})
    claude_code.create_provider({})
    assert get_default_registry().get_entry("acp:claude-code") is not None


def test_isolation_strips_auto_approve_and_writes_0600(monkeypatch, tmp_path):
    home = tmp_path / "home"
    real_claude = home / ".claude"
    real_claude.mkdir(parents=True)
    (real_claude / "settings.json").write_text(
        json.dumps(
            {
                "awsCredentialExport": "aws configure export-credentials",
                "permissions": {
                    "allow": ["Bash(*)"],
                    "ask": ["Read"],
                    "defaultMode": "acceptEdits",
                    "deny": ["Bash(rm:*)"],
                },
                "enabledPlugins": {"x": True},
                "model": "claude-opus-4-8",
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "1")

    env = claude_code._build_env()
    cc_root = Path(env["CLAUDE_CONFIG_DIR"])
    seeded = cc_root / "settings.json"
    assert seeded.is_file()
    data = json.loads(seeded.read_text())

    assert "allow" not in data.get("permissions", {})
    assert "ask" not in data.get("permissions", {})
    assert "defaultMode" not in data.get("permissions", {})
    assert "enabledPlugins" not in data
    assert data["permissions"].get("deny") == ["Bash(rm:*)"]
    assert data.get("awsCredentialExport")
    assert data.get("model") == "claude-opus-4-8"

    mode = stat.S_IMODE(seeded.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_bundle_options_flow_into_client_dialect(monkeypatch, tmp_path):
    """The bundle's options.dialect must reach the AcpClient and drive the
    handshake — the load-bearing seam. Build the provider through the real
    factory (the path an acp:<cli> agent resolves) and assert the client got
    the claude-code dialect (int protocolVersion, set_config_option model)."""
    from gideon.integrations.acp.dialect import ClaudeCodeDialect
    from gideon.integrations.llm.acp_agent import _factory
    from gideon.integrations.llm.registry import ProviderEntry

    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "0")
    claude_code.create_provider({})
    entry: ProviderEntry = get_default_registry().get_entry("acp:claude-code")

    provider = _factory(entry=entry)
    dialect = provider.client._dialect
    assert isinstance(dialect, ClaudeCodeDialect)
    assert dialect.protocol_version() == 1
    sm = dialect.set_model_request(
        session_id="s", model="claude-opus-4-8", default_model=""
    )
    assert sm is not None and sm.method == "session/set_config_option"
    assert sm.params == {
        "sessionId": "s",
        "configId": "model",
        "value": "claude-opus-4-8",
    }
    assert dialect.activate_agent_request(session_id="s", agent="x") is None
    parsed = dialect.parse_permission_options(
        [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"}]
    )
    assert parsed == [{"id": "allow_once", "label": "Allow once", "kind": "allow_once"}]


def test_codex_options_flow_into_client_dialect(monkeypatch, tmp_path):
    from gideon.integrations.acp.dialect import CodexDialect
    from gideon.integrations.llm.acp_agent import _factory

    _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    codex.create_provider({})
    entry = get_default_registry().get_entry("acp:codex")
    provider = _factory(entry=entry)
    assert isinstance(provider.client._dialect, CodexDialect)
    assert provider.client._dialect.protocol_version() == 1


def test_isolation_fails_closed_when_root_is_real_claude(monkeypatch, tmp_path):
    """If CLAUDE_CONFIG_DIR resolves to ~/.claude, never strip/overwrite it."""
    home = tmp_path / "home"
    real_claude = home / ".claude"
    real_claude.mkdir(parents=True)
    original = {"permissions": {"allow": ["Bash(*)"]}}
    (real_claude / "settings.json").write_text(json.dumps(original))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real_claude))
    monkeypatch.setenv("GIDEON_CC_ISOLATE", "1")

    claude_code._build_env()
    assert json.loads((real_claude / "settings.json").read_text()) == original
