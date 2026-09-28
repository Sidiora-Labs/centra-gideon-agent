from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gideon.engine.task_modes import (
    MUTATING,
    READ_ONLY,
    classify_invocation,
    resolve_effective_risk,
    task_mode_denies,
)
from gideon.security.owner_only import (
    OWNER_ONLY_OPERATION_MESSAGE,
    owner_only_command_reason,
    owner_only_path_reason,
)
from gideon.security.approval_brief import derive_blast_radius
from gideon.security.sandbox import wrap_argv


def test_tool_name_does_not_declare_read_semantics() -> None:
    assert classify_invocation("read_customer_data", "", {}) == MUTATING
    assert classify_invocation("read_customer_data", "read", {}) == READ_ONLY
    assert task_mode_denies("ask", "read_customer_data", "", {})
    assert not task_mode_denies("ask", "read_customer_data", "read", {})
    assert derive_blast_radius("read_customer_data") is None
    assert derive_blast_radius("fetch_record", risk="safe")["readOnly"] is False


@pytest.mark.parametrize(
    ("title", "kind", "arguments"),
    [
        ("read_and_write", "read", {}),
        ("fetch_record", "read", {}),
        ("safe_lookup", "read", {"command": "pwd; rm -rf x"}),
        ("terminal", "command", {"command": "pwd; touch x"}),
    ],
)
def test_write_network_or_shell_evidence_overrides_read(
    title: str, kind: str, arguments: object
) -> None:
    assert classify_invocation(title, kind, arguments) == MUTATING
    assert task_mode_denies("plan", title, kind, arguments)
    assert resolve_effective_risk("safe", title, kind, arguments) != "safe"


def test_shell_without_a_proven_read_command_is_not_effectively_safe() -> None:
    assert resolve_effective_risk("safe", "bash", "", {}) == "caution"


def test_home_fence_resolves_relative_and_symlinked_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "gideon"
    workspace = tmp_path / "workspace"
    (home / "hooks").mkdir(parents=True)
    (home / "grants").mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    link = workspace / "owner-hooks"
    link.symlink_to(home / "hooks", target_is_directory=True)

    assert owner_only_path_reason("../gideon/hooks/pre.py", cwd=workspace)
    assert owner_only_path_reason(str(link / "pre.py"), cwd=workspace)
    assert not owner_only_path_reason(str(workspace / "ordinary.txt"), cwd=workspace)
    assert owner_only_command_reason("cat ../gideon/grants/default.json", cwd=workspace)
    assert owner_only_command_reason("cd ../gideon; ls hooks", cwd=workspace)
    assert not owner_only_command_reason("pwd; ls -la", cwd=workspace)


def test_agent_file_and_shell_tools_refuse_reserved_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider

    home = tmp_path / "gideon"
    workspace = tmp_path / "workspace"
    (home / "hooks").mkdir(parents=True)
    (home / "grants").mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    provider = NativeBuiltinToolProvider(cwd=workspace, sandbox_mode="off")
    protected = home / "hooks" / "pre.py"

    write_result = asyncio.run(provider._t_write_file({"path": str(protected), "content": "x"}))
    read_result = asyncio.run(provider._t_read_file({"path": str(protected)}))
    shell_result = asyncio.run(
        provider._t_bash({"command": f"cat {protected}"})
    )

    assert not write_result.success and OWNER_ONLY_OPERATION_MESSAGE in write_result.error
    assert not read_result.success and OWNER_ONLY_OPERATION_MESSAGE in read_result.error
    assert not shell_result.success and OWNER_ONLY_OPERATION_MESSAGE in shell_result.error
    assert not protected.exists()


def test_sandbox_wrapper_checks_owner_paths_even_when_sandbox_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "gideon"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    with pytest.raises(PermissionError, match="owner-only"):
        wrap_argv(["bash", "-lc", "cat $GIDEON_HOME/hooks/pre.py"], "off")


def test_chat_permission_policy_refuses_owner_paths_before_offering_approval(tmp_path, monkeypatch):
    from gideon.integrations.llm.events import AgentEvent
    from gideon.interfaces.dashboard.chat_runner import _permission_policy_denial
    from gideon.interfaces.dashboard.state import _ChatSession

    home = tmp_path / "gideon"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    session = _ChatSession("policy-test", workspace_dir=str(workspace))
    session._task_mode = "ask"
    event = AgentEvent(
        kind="permission_request", title="read_document", tool_kind="read",
        tool_input={"path": "../gideon/grants/policy.json"},
    )
    reason, source = _permission_policy_denial(session, event)
    assert reason and source == "owner_only"
    event.tool_input = {"path": "ordinary.txt"}
    assert not _permission_policy_denial(session, event)[0]
    event.tool_kind = ""
    assert _permission_policy_denial(session, event)[0]
