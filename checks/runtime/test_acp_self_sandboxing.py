import asyncio
import inspect
import sys

import pytest

from gideon.engine.session import ConversationDirectory
from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.transport import AcpProcess
from gideon.integrations.acp_bundles._register import (
    register_acp_cli_entry,
    unregister_acp_cli_entry,
)
from gideon.integrations.llm.acp_agent import AcpAgentProvider, _factory
from gideon.integrations.llm.acp_provider_runtime import ProbePlan, options_sandbox_mode
from gideon.integrations.sandbox_providers.base import SandboxSpec
from gideon.integrations.sandbox_providers.none import NoneSandboxProvider


@pytest.mark.parametrize("self_sandboxing,expected", [(True, "off"), (False, "auto")])
def test_registered_runtime_launch_uses_declared_sandbox(self_sandboxing, expected):
    entry = register_acp_cli_entry(
        cli="sandbox-regression",
        dialect="default",
        command=[sys.executable],
        self_sandboxing=self_sandboxing,
    )
    try:
        assert options_sandbox_mode(entry.options) == expected
        provider = _factory(entry=entry)
        assert provider.client._transport._sandbox_mode == expected
        if self_sandboxing:
            handle = NoneSandboxProvider().wrap(
                SandboxSpec(mode=expected),
                [sys.executable],
            )
            assert handle.argv == [sys.executable]
            handle.cleanup()
    finally:
        unregister_acp_cli_entry("sandbox-regression")


@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, "auto"),
        ({"sandbox_mode": None}, "auto"),
        ({"sandbox_mode": ""}, "auto"),
        ({"sandbox_mode": "strict"}, "strict"),
        ({"sandbox_mode": "off"}, "off"),
    ],
)
def test_mode_defaults_and_explicit_values(options, expected):
    assert options_sandbox_mode(options) == expected


@pytest.mark.asyncio
async def test_readiness_and_discovery_read_the_same_mode(tmp_path):
    plan = ProbePlan.from_options(
        {
            "command": ["/bin/sh", "-c", "echo runtime-start-failure >&2; exit 7"],
            "cwd": str(tmp_path),
            "sandbox_mode": "off",
            "probe_timeout_secs": 5,
        }
    )
    callers = []

    def record(frame, event, arg):
        if event == "call" and frame.f_code is options_sandbox_mode.__code__:
            callers.append(frame.f_back.f_code.co_name)

    previous = sys.getprofile()
    sys.setprofile(record)
    try:
        status = await plan.readiness(AcpAgentProvider)
        with pytest.raises(Exception):
            await plan.snapshot()
    finally:
        sys.setprofile(previous)
    assert not status.ready
    assert "stderr: 'runtime-start-failure'" in status.detail
    assert callers == ["readiness", "snapshot"]


@pytest.mark.asyncio
async def test_first_stderr_tail_survives_repeated_teardown(tmp_path):
    process = AcpProcess(
        command=[sys.executable], work_dir=tmp_path, sandbox_mode="off"
    )
    for message in (b"first failure\n", b"second failure\n"):
        reader = asyncio.StreamReader()
        reader.feed_data(message)
        reader.feed_eof()
        await process._drain_stderr(reader)
        process.teardown()
    assert process.first_stderr_tail == "first failure"
    assert process.stderr_tail() == ""
    client = AcpClient(work_dir=tmp_path, command=[sys.executable], sandbox_mode="off")
    client._transport = process
    assert client.stderr_tail() == "first failure"
    plan = ProbePlan.from_options({"command": [sys.executable]})
    status = plan.failure(TimeoutError(), client.stderr_tail())
    assert status.state == "timeout"
    assert "stderr: 'first failure'" in status.detail


def test_concurrent_session_reader_uses_shared_mode_helper():
    source = inspect.getsource(ConversationDirectory._open_acp_concurrent)
    assert "sandbox_mode=options_sandbox_mode(options)" in source


def test_readiness_classifies_auth_evidence():
    plan = ProbePlan.from_options(
        {"command": [sys.executable], "login_command": ["agent", "login"]}
    )
    status = plan.failure(ConnectionError("EOF"), "Please log in")
    assert status.state == "needs_login"
    assert "stderr: 'Please log in'" in status.detail
    assert status.login_command == ["agent", "login"]
