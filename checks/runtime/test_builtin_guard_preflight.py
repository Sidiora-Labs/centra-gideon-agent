"""Native preflight retains approval and existing filesystem/shell authority."""

import asyncio
import json
import shlex

import pytest
from test_native_runtime import _defn, _ScriptedModel

from gideon.engine.agents.native.builtin_tools import (
    NativeBuiltinToolProvider,
    create_platform_tools_provider,
)
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)


async def native_call(folder, tool, args, *, approve=False, before_answer=None):
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    title=tool,
                    tool_call_id="actual-guard",
                    tool_input=json.dumps(args),
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [
                AgentEvent(kind=EVENT_TEXT_CHUNK, text="The tool attempt is complete."),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
        ]
    )
    model.takes_local_turns = True
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[create_platform_tools_provider(cwd=folder)],
    )
    runtime.set_workspace(folder)
    await runtime.start()
    assert (
        tool in runtime._tool_index
    ), "actual native catalog must admit the filesystem owner"
    events = []
    async with asyncio.timeout(8):
        async for event in runtime.stream("perform this file operation"):
            events.append(event)
            if event.kind == EVENT_PERMISSION_REQUEST:
                if before_answer:
                    before_answer()
                if approve:
                    await runtime.approve_tool(event.request_id)
                else:
                    await runtime.reject_tool(event.request_id)
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["outside", "symlink", "null_content", "directory", "parent_file", "secret"]
)
async def test_bad_write_native_stream_never_asks_or_changes_files(tmp_path, mode):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = workspace / "result"
    content = "new"
    if mode == "outside":
        target = outside / "result"
    elif mode == "symlink":
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
        target = workspace / "escape" / "result"
    elif mode == "null_content":
        content = None
    elif mode == "directory":
        target = workspace
    elif mode == "parent_file":
        (workspace / "parent").write_text("kept")
        target = workspace / "parent" / "result"
    elif mode == "secret":
        target = workspace / "credentials.key"
    events = await native_call(
        workspace, "write_file", {"path": str(target), "content": content}
    )
    assert not any(event.kind == EVENT_PERMISSION_REQUEST for event in events)
    result = next(event for event in events if event.kind == EVENT_TOOL_RESULT)
    assert (
        result.tool_meta["ok"] is False
        and result.tool_meta["effect_state"] == "not_started"
    )
    assert not (outside / "result").exists()
    if target not in (workspace, workspace / "parent" / "result"):
        assert not target.exists()
    assert workspace.is_dir()
    if mode == "parent_file":
        assert (workspace / "parent").read_text() == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "missing_secret",
        "credential_path",
        "system_scheduler",
        "owner_path",
        "deny_pattern",
    ],
)
async def test_denied_native_shell_never_asks_or_spawns_effect(
    tmp_path, kind, monkeypatch
):
    marker = tmp_path / "effect"
    first = {
        "missing_secret": "printf %s {{secret:missing-preflight-value}}",
        "credential_path": "cat ~/.ssh/id_rsa",
        "system_scheduler": "crontab -e",
        "owner_path": f"cat {shlex.quote(str(__import__('os').environ['GIDEON_HOME']+'/grants/owned'))}",
        "deny_pattern": "echo $AWS_SECRET_ACCESS_KEY",
    }[kind]
    command = first + "; printf reached > " + shlex.quote(str(marker))
    events = await native_call(tmp_path, "bash", {"command": command})
    assert not any(event.kind == EVENT_PERMISSION_REQUEST for event in events)
    result = next(event for event in events if event.kind == EVENT_TOOL_RESULT)
    assert (
        result.tool_meta["ok"] is False
        and result.tool_meta["effect_state"] == "not_started"
    )
    assert not marker.exists()


@pytest.mark.asyncio
async def test_read_gate_staleness_and_noop_edit_preflight_retains_disk(tmp_path):
    path = tmp_path / "file"
    path.write_text("original")
    provider = NativeBuiltinToolProvider(cwd=tmp_path, session_key="file-guard")
    unread = await provider.preflight("write_file", {"path": "file", "content": "new"})
    assert unread is not None and unread.metadata["read_gate"]
    assert (await provider.invoke("read_file", {"path": "file"})).success
    for args in (
        {"path": "file", "old_str": "", "new_str": "new"},
        {"path": "file", "old_str": "original", "new_str": "original"},
    ):
        refused = await provider.preflight("edit_file", args)
        assert refused is not None and not refused.success
        assert refused.metadata["effect_state"] == "not_started"
    path.write_text("concurrent")
    stale = await provider.preflight("write_file", {"path": "file", "content": "new"})
    assert stale is not None and stale.metadata["read_gate"] == "changed_on_disk"
    assert path.read_text() == "concurrent"


@pytest.mark.asyncio
async def test_valid_new_write_still_asks_and_only_approved_execution_writes(tmp_path):
    marker = tmp_path / "approved"

    def untouched():
        assert not marker.exists()

    rejected = await native_call(
        tmp_path,
        "write_file",
        {"path": str(marker), "content": "value"},
        before_answer=untouched,
    )
    assert (
        any(event.kind == EVENT_PERMISSION_REQUEST for event in rejected)
        and not marker.exists()
    )
    approved = await native_call(
        tmp_path,
        "write_file",
        {"path": str(marker), "content": "value"},
        approve=True,
        before_answer=untouched,
    )
    assert any(event.kind == EVENT_PERMISSION_REQUEST for event in approved)
    result = next(event for event in approved if event.kind == EVENT_TOOL_RESULT)
    assert result.tool_meta["ok"] is True and marker.read_text() == "value"


@pytest.mark.asyncio
async def test_protected_folder_delete_remains_human_approval_not_preflight_denial(
    tmp_path,
):
    workspace = tmp_path / "protected-workspace"
    workspace.mkdir()
    marker = workspace / "kept"
    marker.write_text("safe")
    command = "rm -rf " + shlex.quote(str(workspace))
    provider = NativeBuiltinToolProvider(cwd=workspace, sandbox_mode="off")
    assert await provider.preflight("bash", {"command": command}) is None
    events = await native_call(workspace, "bash", {"command": command})
    assert any(event.kind == EVENT_PERMISSION_REQUEST for event in events)
    assert marker.read_text() == "safe"


@pytest.mark.asyncio
async def test_write_state_changed_while_approval_pending_is_rechecked_before_effect(
    tmp_path,
):
    marker = tmp_path / "pending"

    def concurrent_writer():
        assert not marker.exists()
        marker.write_text("concurrent owner change")

    events = await native_call(
        tmp_path,
        "write_file",
        {"path": str(marker), "content": "proposed replacement"},
        approve=True,
        before_answer=concurrent_writer,
    )
    assert any(event.kind == EVENT_PERMISSION_REQUEST for event in events)
    result = next(event for event in events if event.kind == EVENT_TOOL_RESULT)
    assert (
        result.tool_meta["ok"] is False
        and result.tool_meta["effect_state"] == "not_started"
    )
    assert marker.read_text() == "concurrent owner change"
