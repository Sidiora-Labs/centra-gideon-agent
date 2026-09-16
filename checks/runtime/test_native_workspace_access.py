"""Workspace provider integration using actual directories and shell output."""

import asyncio
import shlex
import sys

import pytest

from gideon.engine.agents.native.builtin_tools import (
    NativeBuiltinToolProvider,
    bind_tool_context,
    create_platform_tools_provider,
    current_project_id,
    reset_tool_context,
)


@pytest.mark.asyncio
async def test_shared_provider_keeps_concurrent_workspace_bindings_separate(tmp_path):
    provider = create_platform_tools_provider()
    workspaces = [tmp_path / "first", tmp_path / "second"]
    for path in workspaces:
        path.mkdir()
        (path / "value.txt").write_text(path.name)

    async def operate(directory):
        tokens = bind_tool_context(
            cwd=directory, agent=directory.name, project_id=directory.name
        )
        try:
            result = await provider.invoke("read_file", {"path": "value.txt"})
            assert result.success and result.output == directory.name
            await asyncio.sleep(0)
            assert current_project_id() == directory.name
            written = await provider.invoke(
                "write_file", {"path": "created.txt", "content": directory.name}
            )
            assert written.success
        finally:
            reset_tool_context(tokens)

    previous = current_project_id()
    await asyncio.gather(*(operate(path) for path in workspaces))
    assert current_project_id() == previous
    for path in workspaces:
        assert (path / "created.txt").read_text() == path.name


@pytest.mark.asyncio
async def test_symlink_to_outside_is_refused_before_read_or_write(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("outside content")
    (workspace / "link.txt").symlink_to(secret)
    provider = NativeBuiltinToolProvider(workspace)
    read = await provider.invoke("read_file", {"path": "link.txt"})
    write = await provider.invoke(
        "write_file", {"path": "link.txt", "content": "changed"}
    )
    assert not read.success and "escapes" in read.error
    assert not write.success and "escapes" in write.error
    assert secret.read_text() == "outside content"


@pytest.mark.asyncio
async def test_byte_capped_read_tracks_the_full_files_digest(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("observed bytes\nunseen tail\n")
    provider = NativeBuiltinToolProvider(tmp_path, session_key="byte-cap")
    observed = await provider.invoke("read_file", {"path": "file.txt", "max_bytes": 8})
    assert observed.output == "observed"
    overwrite = await provider.invoke(
        "write_file", {"path": "file.txt", "content": "new"}
    )
    assert overwrite.metadata["read_gate"] == "partial_observation"
    path.write_text("observed bytes\nmodified tail\n")
    edit = await provider.invoke(
        "edit_file", {"path": "file.txt", "old_str": "observed", "new_str": "changed"}
    )
    assert edit.metadata["read_gate"] == "changed_on_disk"
    assert path.read_text().endswith("modified tail\n")


@pytest.mark.asyncio
async def test_nonzero_shell_output_remains_retrievable(tmp_path):
    provider = NativeBuiltinToolProvider(
        tmp_path, session_key="shell-output", sandbox_mode="off"
    )
    program = "import sys; print('INFO: ordinary log line\\n' * 4000); print('ERROR: durable marker'); sys.exit(7)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"
    result = await provider.invoke("bash", {"command": command})
    assert not result.success and result.error.startswith("exit 7:\n")
    assert result.truncated and result.original_length > 80000
    assert "durable marker" in result.error
    recovered = await provider.invoke(
        "tool_result_get",
        {"result_id": result.metadata["raw_ref"], "grep": "durable marker"},
    )
    assert recovered.success and "durable marker" in recovered.output
    assert recovered.metadata["content_type"] == "generic"
