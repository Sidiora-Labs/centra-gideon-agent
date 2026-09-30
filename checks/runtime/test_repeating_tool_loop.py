"""Recorded typed shell calls exercise the native runtime's real Bash dispatch."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.integrations.llm.events import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from gideon.security.guardrails import loop_breaker as lb


_ORDER = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 9, 13, 14, 15, 9, 16, 17, 18, 0, 19,
    20, 21, 22, 9, 17, 0, 23, 24, 25, 9, 0, 18, 26, 27, 28, 13, 29, 24, 24, 0, 17,
    30, 31, 17, 22, 32, 33, 34, 5, 35, 0, 26, 24, 17, 0, 24, 0, 36, 37, 35, 17, 22,
]
_REPEATED = {
    0: "ls -F",
    5: 'find . -maxdepth 3 -name ".git" -type d',
    9: "find . -name .git -type d",
    13: "ls -d */ | xargs -I {} find {} -name .git -type d",
    17: "ls -d */",
    18: "ls -R | grep feed | sort -u",
    22: 'ls -R | grep "feed" | cut -d/ -f1 | sort -u',
    24: 'ls -R | grep -E "feed|git" | cut -d/ -f1 | sort -u',
    25: "git -C . log --oneline",
    26: 'find . -name ".git" -type d',
    35: 'ls -R | grep -E "git" | cut -d/ -f1 | sort -u',
}


def _command(index: int) -> str:
    return _REPEATED.get(index, f"find . -maxdepth 2 -type d -name 'v{index}'")


def _runtime(workspace: Path) -> tuple[NativeAgentRuntime, NativeBuiltinToolProvider]:
    provider = NativeBuiltinToolProvider(cwd=workspace)
    definition = AgentRuntimeDefinition(name="loop-test", provider="native", model="")
    runtime = NativeAgentRuntime(
        definition=definition,
        model_provider=None,
        tool_providers=[provider],
        cwd=workspace,
        session_key="repeating-tool-loop-test",
    )
    bash = next(item for item in provider._all_tool_defs({"type": "object"}) if item.name == "bash")
    runtime._tool_defs = [bash]
    runtime._tool_index = {"bash": provider}
    runtime._tool_wire_to_canonical = {"bash": "bash"}
    runtime._tool_risk = {"bash": bash.risk_level}
    runtime._cancel.begin_turn()
    runtime._breaker.reset()
    return runtime, provider


async def _dispatch_bash(
    runtime: NativeAgentRuntime, index: int, command: str
) -> AgentEvent:
    call = AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=f"recorded-{index}",
        title="bash",
        tool_kind="command",
        tool_input=json.dumps({"command": command}),
    )
    prepared = runtime._prepare_call(call)
    result = None
    async for event in runtime._run_tool(prepared, prefetched=None):
        if event.kind == EVENT_PERMISSION_REQUEST:
            await runtime.approve_tool(event.request_id)
        elif event.kind == EVENT_TOOL_RESULT:
            result = event
    assert result is not None
    return result


def test_read_cycle_is_refused_and_stopped():
    async def scenario(workspace: Path) -> None:
        (workspace / "knowledge").mkdir()
        (workspace / "memory").mkdir()
        (workspace / "outbox").mkdir()
        (workspace / "HEARTBEAT.md").write_text("status\n")
        (workspace / "knowledge" / "feed.txt").write_text("feed entry\n")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)

        runtime, _ = _runtime(workspace)
        donor_results = []
        delivered = []
        for index in _ORDER:
            if runtime._cancelled:
                break
            command = _command(index)
            delivered.append(command)
            donor_results.append(await _dispatch_bash(runtime, index, command))

        refusals = [
            result
            for result in donor_results
            if (result.tool_meta or {}).get("loop_breaker_refusal") is True
        ]
        assert refusals, "the recorded read cycle was not refused"
        assert len(delivered) < len(_ORDER), "the run-level repeated-read circuit did not stop dispatch"
        assert runtime._cancelled
        assert runtime._breaker.repeat_circuit_message().startswith(
            "Run aborted by the loop breaker: "
        )
        assert "repeated an earlier read" in runtime._breaker.repeat_circuit_message()

        runtime._cancel.begin_turn()
        runtime._breaker.reset()
        state = workspace / "state.txt"
        state.write_text("before\n")
        key = lb.params_key("bash", {"command": "cat state.txt"})
        first = await _dispatch_bash(runtime, 100, "cat state.txt")
        second = await _dispatch_bash(runtime, 101, "cat state.txt")
        assert not (first.tool_meta or {}).get("loop_breaker_refusal")
        assert not (second.tool_meta or {}).get("loop_breaker_refusal")
        state.write_text("after\n")
        changed = await _dispatch_bash(runtime, 102, "cat state.txt")
        assert not (changed.tool_meta or {}).get("loop_breaker_refusal")
        assert runtime._breaker.repeat_count(key) == 1

        await _dispatch_bash(runtime, 103, "cat state.txt")
        assert runtime._breaker.repeat_count(key) == 2
        await _dispatch_bash(runtime, 104, "sleep 0")
        assert runtime._breaker.repeat_count(key) == 0

        poll_key = lb.params_key("bash", {"command": "git status"})
        repeat_total = runtime._breaker.total_repeats
        poll_results = [
            await _dispatch_bash(runtime, 110 + index, "git status")
            for index in range(12)
        ]
        assert not any(
            (result.tool_meta or {}).get("loop_breaker_refusal") for result in poll_results
        )
        assert runtime._breaker.repeat_count(poll_key) == 0
        assert runtime._breaker.total_repeats == repeat_total

        runtime._cancel.begin_turn()
        runtime._breaker.reset()
        write_results = [
            await _dispatch_bash(runtime, 130 + index, "echo changed >> writes.txt")
            for index in range(6)
        ]
        assert len(write_results) == 6
        assert not any(
            (result.tool_meta or {}).get("loop_breaker_refusal") for result in write_results
        )
        assert any(
            "same tool call produced the same result" in str(result.tool_output)
            for result in write_results
        )

    with TemporaryDirectory(prefix="gideon-read-loop-") as directory:
        asyncio.run(asyncio.wait_for(scenario(Path(directory)), timeout=300))
